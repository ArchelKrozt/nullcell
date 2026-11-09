"""El buscador — §8's mechanisms and ranking, §16's threading rules."""

from __future__ import annotations

import asyncio
import time

import pytest

from nullcell.core.identity import Identity
from nullcell.search.indexer import Indexer, SearchHit, escape_fts
from nullcell.search.query import MAX_WORDS, Search, SearchProgress
from nullcell.search.rank import CONFIRMATION_CEILING, rank


@pytest.fixture
async def indexer(tmp_path):
    index = Indexer(tmp_path / "index.db")
    index.start()
    yield index
    index.close()


async def seeded(index: Indexer) -> Indexer:
    index.upsert(
        "a" * 56,
        title="La Panaderia",
        descr="Pan, pedidos y recetas",
        tags=["comida", "tienda"],
        lang="es",
    )
    index.upsert("b" * 56, title="Agora", descr="Chat abierto", tags=["chat"], kind="chat")
    index.upsert(
        "c" * 56, title="Archivo", descr="Ficheros compartidos", tags=["files"], kind="files"
    )
    await index.flush()
    return index


# --------------------------------------------------------------------- FTS5
async def test_full_text_search_finds_by_title_description_and_tags(indexer):
    index = await seeded(indexer)
    assert [h.title for h in await index.search("panaderia")] == ["La Panaderia"]
    assert [h.title for h in await index.search("chat")] == ["Agora"]
    assert [h.title for h in await index.search("tienda")] == ["La Panaderia"]


async def test_search_ignores_diacritics(indexer):
    indexer.upsert("a" * 56, title="La Panadería", descr="Pan recién hecho")
    await indexer.flush()
    assert await indexer.search("panaderia")
    assert await indexer.search("recien")


async def test_prefix_matching(indexer):
    index = await seeded(indexer)
    assert [h.title for h in await index.search("panad")] == ["La Panaderia"]


async def test_upsert_updates_in_place(indexer):
    indexer.upsert("a" * 56, title="Viejo")
    await indexer.flush()
    indexer.upsert("a" * 56, title="Nuevo")
    await indexer.flush()
    assert (await indexer.stats())["portals"] == 1
    assert [h.title for h in await indexer.search("nuevo")] == ["Nuevo"]
    assert await indexer.search("viejo") == []


async def test_forgetting_a_portal_removes_it_from_the_index(indexer):
    index = await seeded(indexer)
    index.forget("b" * 56)
    await index.flush()
    assert await index.search("chat") == []
    assert (await index.stats())["portals"] == 2


# ---------------------------------------------------------- hostile input §8
@pytest.mark.parametrize(
    "query",
    [
        "'; DROP TABLE portals; --",
        "pan OR *",
        "pan AND NOT chat",
        'pan" OR "1"="1',
        "^pan*",
        "(((",
        "NEAR(pan chat)",
    ],
)
async def test_query_syntax_cannot_be_injected(indexer, query):
    """A search box is not a query language. Operators a visitor typed are
    characters, and the index must survive anything."""
    index = await seeded(indexer)
    await index.search(query)  # must not raise
    assert (await index.stats())["portals"] == 3, "the index was damaged"


def test_escape_turns_text_into_quoted_prefix_terms():
    assert escape_fts("pan") == '"pan"*'
    assert escape_fts("pan recien") == '"pan"* "recien"*'
    assert escape_fts("  ") == ""
    assert "DROP" in escape_fts("'; DROP TABLE x; --")  # a word, not a statement
    assert ";" not in escape_fts("'; DROP TABLE x; --")


def test_escape_bounds_the_number_of_terms():
    assert len(escape_fts(" ".join(f"w{i}" for i in range(50))).split()) == 12


# ------------------------------------------------------------------ §16 rules
async def test_searching_does_not_block_the_event_loop(indexer):
    """§16: tens of milliseconds inside a coroutine freezes every circuit."""
    index = await seeded(indexer)
    for i in range(300):
        index.upsert(f"{i:056d}", title=f"portal {i}", descr="pan " * 50)
    await index.flush()

    delays: list[float] = []
    running = True

    async def heartbeat():
        while running:
            t0 = time.perf_counter()
            await asyncio.sleep(0.01)
            delays.append((time.perf_counter() - t0 - 0.01) * 1000)

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)  # let the heartbeat establish a baseline
    try:
        for _ in range(40):
            await index.search("pan")
    finally:
        await asyncio.sleep(0.02)
        running = False
        beat.cancel()

    assert delays, "the heartbeat never ran"
    assert max(delays) < 50, f"event loop stalled {max(delays):.0f} ms (budget 50 ms)"


async def test_writes_are_queued_not_awaited(indexer):
    """upsert() returns immediately; the writer thread does the work."""
    t0 = time.perf_counter()
    for i in range(200):
        indexer.upsert(f"{i:056d}", title=f"p{i}")
    assert (time.perf_counter() - t0) < 0.2
    await indexer.flush()
    assert (await indexer.stats())["portals"] == 200


# ------------------------------------------------------------------ ranking §8
def _hit(nid: str, *, bm25: float = 0.5, age: float = 0.0, confirmed: int = 1) -> SearchHit:
    return SearchHit(nid, "web", nid[:4], "", [], "", bm25, time.time() - age, confirmed)


def test_relevance_is_a_bonus_not_an_annihilator():
    """§8 writes the ranking as a product, and BM25 is about zero for a word in
    every document — which in a small network is most words. Used directly it
    zeroed freshness, confirmations and endorsements alike."""
    flat = rank([_hit("a" * 56, bm25=0.0, confirmed=8), _hit("b" * 56, bm25=0.0, confirmed=1)])
    assert flat[0].hit.nid == "a" * 56, "confirmations stopped mattering"
    assert flat[0].score > flat[1].score


def test_freshness_orders_a_stale_portal_below_a_live_one():
    fresh, stale = _hit("a" * 56), _hit("b" * 56, age=7200)
    ordered = rank([stale, fresh])
    assert ordered[0].hit.nid == "a" * 56
    assert ordered[1].freshness < 1.0


def test_confirmations_help_but_are_capped():
    """§10: otherwise a Sybil swarm ranks anything first by confirming it."""
    # rank() sorts, so the stronger one comes first.
    many, one = rank([_hit("a" * 56, confirmed=1), _hit("b" * 56, confirmed=10_000)])
    capped = rank([_hit("c" * 56, confirmed=CONFIRMATION_CEILING)])[0]
    assert many.hit.confirmed == 10_000
    assert many.confirmation > one.confirmation
    assert many.confirmation == pytest.approx(capped.confirmation)


def test_endorsements_count_only_from_cells_you_trust():
    """§8: no global authority. Two users can legitimately differ."""
    hits = [_hit("a" * 56), _hit("b" * 56)]
    trusted = "z" * 56
    endorsements = {"b" * 56: {trusted}}

    without = rank(hits, endorsements=endorsements)
    assert without[0].endorsement == without[1].endorsement == 1.0

    with_trust = rank(hits, trusted={trusted}, endorsements=endorsements)
    assert with_trust[0].hit.nid == "b" * 56


def test_ranking_is_deterministic():
    hits = [_hit(c * 56) for c in "abcde"]
    assert [r.hit.nid for r in rank(hits)] == [r.hit.nid for r in rank(list(reversed(hits)))]


def test_a_result_explains_itself():
    """§9 forbids inventing metrics, and one opaque number is how a ranking
    stops being explainable."""
    ranked = rank([_hit("a" * 56, age=7200, confirmed=6)])[0]
    explanation = ranked.explain()
    assert "relevancia" in explanation
    assert "confirmado por 6" in explanation
    assert "visto hace 120 min" in explanation


# ------------------------------------------------------------------ progress §9
async def test_progress_says_what_is_pending(indexer):
    index = await seeded(indexer)
    reports: list[str] = []
    search = Search(index)
    await search.run("pan recien hecho", on_progress=lambda p: reports.append(p.describe()))
    assert reports
    assert reports[-1].endswith("resultados")


def test_progress_counts_pending_words():
    progress = SearchProgress(words=["pan", "recien", "hecho", "molde", "trigo"])
    progress.resolved.add("pan")
    progress.resolved.add("recien")
    assert progress.pending == 3
    assert progress.describe() == "2 de 5 palabras resueltas, 3 pendientes"


def test_a_query_is_bounded_to_a_handful_of_words():
    """§7: every word is a circuit round trip, so the budget is per word."""
    from nullcell.search.query import _words

    assert len(_words(" ".join(f"w{i}" for i in range(50)))) == MAX_WORDS


# ------------------------------------------------------------------ harvesting
async def test_resolving_a_portal_improves_later_searches(indexer):
    """§8: searching gets better simply by using the network."""
    from nullcell.services.descriptor import ServiceDescriptor

    identity = Identity.generate()
    descriptor = ServiceDescriptor(
        pub=identity.public_key,
        x25519=identity.x25519_public,
        title="La Panaderia",
        desc="Pan recien hecho",
        tags=["comida"],
    )
    search = Search(indexer)
    assert await indexer.search("panaderia") == []

    search.ingest(descriptor)
    await indexer.flush()
    assert [h.nid for h in await indexer.search("panaderia")] == [identity.nid]

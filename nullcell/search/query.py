"""Searching — §8's three mechanisms, and §7's latency budget.

A search runs the local index and the DHT **at the same time**, and reports
results as they arrive:

* the **local FTS5 index** answers instantly and offline,
* the **DHT keyword index** answers in 1-3 s, because every lookup is a
  circuit round trip (§7). §8's wording was *"Buscar = N lookups"*; §7 corrects
  it to **N concurrent lookups with a deadline**, because in series the budget
  multiplies by the number of words.

Progressive reporting is not a nicety. §9 requires the interface not to invent
anything, and a spinner for eight seconds invents the impression that nothing
is happening. *"3 of 5 words resolved, 2 pending"* is true and useful.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from nullcell.core import ids
from nullcell.dht import records as rec
from nullcell.dht.kademlia import KademliaNode
from nullcell.dht.records import RecordType
from nullcell.search.indexer import Indexer, SearchHit, escape_fts
from nullcell.search.rank import Ranked, rank

__all__ = ["DEADLINE", "MAX_WORDS", "Search", "SearchProgress"]

#: §7 budgets a lookup at 1-3 s. Past this the partial answer is better than
#: the complete one, because nobody waits.
DEADLINE: Final[float] = 4.0
MAX_WORDS: Final[int] = 5
_MAX_PER_WORD: Final[int] = 20

log = logging.getLogger("nullcell.search")


@dataclass(slots=True)
class SearchProgress:
    """What the HUD shows while a search is still running (§9)."""

    words: list[str] = field(default_factory=list)
    resolved: set[str] = field(default_factory=set)
    results: int = 0
    done: bool = False

    @property
    def pending(self) -> int:
        return max(0, len(self.words) - len(self.resolved))

    def describe(self) -> str:
        if self.done:
            return f"{self.results} resultados"
        return (
            f"{len(self.resolved)} de {len(self.words)} palabras resueltas, "
            f"{self.pending} pendientes"
        )


class Search:
    """Runs a query against the local index and the directory."""

    def __init__(
        self,
        indexer: Indexer,
        dht: KademliaNode | None = None,
        *,
        trusted: set[str] | None = None,
        deadline: float = DEADLINE,
    ) -> None:
        self.indexer = indexer
        self.dht = dht
        self.trusted = trusted or set()
        self.deadline = deadline
        self.stats = {"queries": 0, "dht_lookups": 0, "dht_hits": 0}

    # ------------------------------------------------------------------ query
    async def run(
        self,
        query: str,
        *,
        limit: int = 25,
        on_progress: Callable[[SearchProgress], None] | None = None,
    ) -> list[Ranked]:
        """Search, reporting progress as answers arrive."""
        self.stats["queries"] += 1
        words = _words(query)
        progress = SearchProgress(words=words)

        local = await self.indexer.search(query, limit=limit * 2)
        by_nid: dict[str, SearchHit] = {hit.nid: hit for hit in local}
        progress.results = len(by_nid)
        if on_progress is not None:
            on_progress(progress)

        if self.dht is not None and words:
            await self._from_directory(words, by_nid, progress, on_progress)

        progress.done = True
        if on_progress is not None:
            on_progress(progress)

        return rank(list(by_nid.values()), trusted=self.trusted)[:limit]

    async def _from_directory(
        self,
        words: list[str],
        by_nid: dict[str, SearchHit],
        progress: SearchProgress,
        on_progress: Callable[[SearchProgress], None] | None,
    ) -> None:
        """Look every word up at once, and take whatever answered in time."""
        tasks = {asyncio.create_task(self._lookup(word)): word for word in words}
        self.stats["dht_lookups"] += len(tasks)
        started = time.monotonic()

        try:
            while tasks:
                remaining = self.deadline - (time.monotonic() - started)
                if remaining <= 0:
                    break
                done, _pending = await asyncio.wait(
                    tasks.keys(), timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    break
                for task in done:
                    word = tasks.pop(task)
                    progress.resolved.add(word)
                    try:
                        hits = task.result()
                    except Exception as exc:
                        log.debug("lookup for %r failed: %s", word, exc)
                        continue
                    for hit in hits:
                        known = by_nid.get(hit.nid)
                        if known is None:
                            by_nid[hit.nid] = hit
                        else:
                            # Several words pointing at one portal is evidence,
                            # not a duplicate.
                            known.confirmed += 1
                    self.stats["dht_hits"] += len(hits)
                    progress.results = len(by_nid)
                    if on_progress is not None:
                        on_progress(progress)
        finally:
            for task in tasks:
                task.cancel()

    async def _lookup(self, word: str) -> list[SearchHit]:
        """Fetch one word's entries from every shard it is spread over (§8)."""
        assert self.dht is not None
        found: list[SearchHit] = []
        shards = await asyncio.gather(
            *(
                self.dht.find_value(rec.keyword_key(word, shard))
                for shard in range(rec.KEYWORD_SHARDS)
            ),
            return_exceptions=True,
        )
        for shard in shards:
            if isinstance(shard, BaseException):
                continue
            for record in shard[:_MAX_PER_WORD]:
                hit = _hit_from_record(record)
                if hit is not None:
                    found.append(hit)
        return found

    # ------------------------------------------------------------- harvesting
    def ingest(self, descriptor) -> None:
        """Put a resolved portal into the local index (§8).

        Called whenever a descriptor is seen, so searching gets better simply
        by using the network.
        """
        self.indexer.upsert(
            descriptor.nid,
            kind=descriptor.kind,
            title=descriptor.title,
            descr=descriptor.desc,
            tags=descriptor.tags,
            lang=descriptor.lang,
        )


def _words(query: str) -> list[str]:
    expression = escape_fts(query)
    if not expression:
        return []
    return [part.strip('"*') for part in expression.split()][:MAX_WORDS]


def _hit_from_record(record) -> SearchHit | None:
    """A keyword entry is attacker-supplied: bound everything."""
    if record.type is not RecordType.KEYWORD:
        return None
    body = record.body
    if not isinstance(body, dict):
        return None
    nid = body.get("id")
    if not isinstance(nid, str):
        return None
    try:
        ids.decode_nid(nid)
    except ids.AddressError:
        return None  # an entry pointing at a non-address is noise
    title = body.get("title")
    kind = body.get("kind")
    return SearchHit(
        nid=ids.parse_hostname(nid),
        kind=kind if isinstance(kind, str) and len(kind) <= 16 else "other",
        title=title[:120] if isinstance(title, str) else "",
        descr="",
        tags=[],
        lang="",
        # A directory hit has no BM25 of its own: the word matched exactly, so
        # it starts neutral and is ordered by freshness and confirmations.
        bm25=1.0,
        last_seen=float(record.ts),
        confirmed=1,
    )

"""El rastreador y robots.txt — §8's politeness, as behaviour."""

from __future__ import annotations

import asyncio
import time

import pytest

from nullcell.core.identity import Identity
from nullcell.host.robots import DEFAULT_ROBOTS, MAX_ROBOTS_BYTES, RobotsPolicy
from nullcell.search.crawler import CrawlBudget, Crawler, extract_links, extract_text


# ------------------------------------------------------------------- robots
def test_an_empty_policy_allows_everything():
    assert RobotsPolicy().allows("/")
    assert RobotsPolicy().allows("/cualquier/cosa")


def test_an_unreadable_policy_refuses_everything():
    """§8 says polite. Reading a malformed policy as consent is the opposite."""
    assert not RobotsPolicy.forbidding().allows("/")
    assert not RobotsPolicy.parse(b"\xff\xfe not utf-8").allows("/")
    assert not RobotsPolicy.parse("x" * (MAX_ROBOTS_BYTES + 1)).allows("/")
    assert not RobotsPolicy.parse(b"A" * (MAX_ROBOTS_BYTES + 1)).allows("/")


def test_disallow_and_allow():
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /privado\nAllow: /privado/publico\n")
    assert policy.allows("/")
    assert not policy.allows("/privado/x")
    assert policy.allows("/privado/publico/y")  # longest rule wins


def test_an_empty_disallow_means_nothing_is_disallowed():
    """RFC 9309."""
    assert RobotsPolicy.parse("User-agent: *\nDisallow:\n").allows("/x")


def test_rules_for_other_agents_are_ignored():
    policy = RobotsPolicy.parse("User-agent: otro\nDisallow: /\n")
    assert policy.allows("/"), "a group for another agent was applied to us"


def test_our_own_name_is_honoured():
    policy = RobotsPolicy.parse("User-agent: nullcell\nDisallow: /secreto\n")
    assert not policy.allows("/secreto")


def test_comments_and_blank_lines_are_skipped():
    policy = RobotsPolicy.parse("# nada\n\nUser-agent: *\nDisallow: /x  # comentario\n")
    assert not policy.allows("/x")


def test_crawl_delay_is_read_and_bounded():
    assert RobotsPolicy.parse("User-agent: *\nCrawl-delay: 7\n").crawl_delay == 7.0
    assert RobotsPolicy.parse("User-agent: *\nCrawl-delay: 99999\n").crawl_delay == 300.0
    assert RobotsPolicy.parse("User-agent: *\nCrawl-delay: nada\n").crawl_delay is None


def test_the_default_policy_shipped_with_a_portal_is_valid():
    policy = RobotsPolicy.parse(DEFAULT_ROBOTS)
    assert policy.allows("/")
    assert policy.crawl_delay == 2.0


# -------------------------------------------------------------- extraction
def test_text_extraction_drops_scripts_and_styles():
    """So a page cannot pad the index with its own JavaScript."""
    html = b"<html><style>body{x:1}</style><script>alert('x')</script><h1>Pan</h1></html>"
    text = extract_text(html)
    assert "Pan" in text
    assert "alert" not in text
    assert "body{" not in text


def test_text_extraction_is_bounded():
    assert len(extract_text(b"<p>" + b"x" * 100_000, limit=500)) <= 500


def test_links_are_same_site_only():
    """A portal does not get to send the crawler elsewhere."""
    html = (
        '<a href="/uno">1</a>'
        '<a href="dos">2</a>'
        '<a href="https://evil.example/x">3</a>'
        '<a href="//evil.example/y">4</a>'
        '<a href="/../etc/passwd">5</a>'
    )
    assert extract_links(html) == ["/uno", "/dos"]


def test_links_are_bounded():
    html = "".join(f'<a href="/p{i}">x</a>' for i in range(200))
    assert len(extract_links(html, limit=10)) == 10


# ---------------------------------------------------------------- crawling
def _site(pages: dict[str, bytes]):
    """A fake portal, so the crawler's politeness can be observed."""
    calls: list[tuple[float, str]] = []

    async def fetch(nid: str, path: str) -> tuple[int, bytes]:
        calls.append((time.monotonic(), path))
        if path in pages:
            return 200, pages[path]
        return 404, b""

    return fetch, calls


async def test_a_portal_is_crawled_and_indexed(tmp_path):
    from nullcell.search.indexer import Indexer

    nid = Identity.generate().nid
    fetch, _calls = _site(
        {
            "/robots.txt": DEFAULT_ROBOTS.encode(),
            "/": b'<h1>La Panaderia</h1><a href="/recetas">recetas</a>',
            "/recetas": b"<p>Pan de masa madre</p>",
        }
    )
    index = Indexer(tmp_path / "i.db")
    index.start()
    try:
        crawler = Crawler(fetch, indexer=index, budget=CrawlBudget(delay=0.0))
        result = await crawler.crawl(nid)
        assert result.ok
        assert result.pages == 2
        assert "La Panaderia" in result.text
        assert "masa madre" in result.text

        await index.flush()
        assert [h.nid for h in await index.search("madre")] == [nid]
    finally:
        index.close()


async def test_disallowed_paths_are_not_fetched():
    nid = Identity.generate().nid
    fetch, calls = _site(
        {
            "/robots.txt": b"User-agent: *\nDisallow: /privado\n",
            "/": b'<a href="/privado/x">no</a><a href="/publico">si</a>',
            "/privado/x": b"secreto",
            "/publico": b"abierto",
        }
    )
    crawler = Crawler(fetch, budget=CrawlBudget(delay=0.0))
    result = await crawler.crawl(nid)
    fetched = [path for _, path in calls]
    assert "/privado/x" not in fetched
    assert "/publico" in fetched
    assert "secreto" not in result.text


async def test_an_unreadable_robots_refuses_the_whole_portal():
    nid = Identity.generate().nid
    fetch, calls = _site({"/robots.txt": b"\xff" * 10, "/": b"<h1>x</h1>"})
    crawler = Crawler(fetch, budget=CrawlBudget(delay=0.0))
    result = await crawler.crawl(nid)
    assert not result.ok
    assert "ilegible" in result.refused
    assert [path for _, path in calls] == ["/robots.txt"]


async def test_a_missing_robots_is_not_a_refusal():
    """RFC 9309: a site that never published a policy has refused nothing."""
    nid = Identity.generate().nid
    fetch, _calls = _site({"/": b"<h1>abierto</h1>"})
    result = await Crawler(fetch, budget=CrawlBudget(delay=0.0)).crawl(nid)
    assert result.ok


async def test_the_page_budget_is_respected():
    nid = Identity.generate().nid
    pages = {"/robots.txt": DEFAULT_ROBOTS.encode()}
    pages["/"] = "".join(f'<a href="/p{i}">x</a>' for i in range(50)).encode()
    for i in range(50):
        pages[f"/p{i}"] = b"<p>contenido</p>"
    fetch, _calls = _site(pages)
    result = await Crawler(fetch, budget=CrawlBudget(delay=0.0, max_pages=5)).crawl(nid)
    assert result.pages == 5


async def test_the_crawl_delay_is_honoured():
    """§8: one request every two seconds, or more if the portal asks."""
    nid = Identity.generate().nid
    fetch, calls = _site(
        {
            "/robots.txt": b"User-agent: *\nCrawl-delay: 0.2\n",
            "/": b'<a href="/a">a</a><a href="/b">b</a>',
            "/a": b"a",
            "/b": b"b",
        }
    )
    crawler = Crawler(fetch, budget=CrawlBudget(delay=0.0, max_pages=3))
    await crawler.crawl(nid)
    gaps = [b[0] - a[0] for a, b in zip(calls[1:], calls[2:], strict=False)]
    assert gaps, "not enough requests to measure a gap"
    assert min(gaps) >= 0.15, f"requests {min(gaps):.3f} s apart, delay was 0.2"


async def test_crawling_is_skipped_while_the_cell_is_busy():
    """§8: only while idle, so it never competes with the user."""
    nid = Identity.generate().nid
    fetch, calls = _site({"/": b"<h1>x</h1>"})
    crawler = Crawler(fetch, budget=CrawlBudget(delay=0.0), is_idle=lambda: False)
    result = await crawler.crawl(nid)
    assert not result.ok
    assert "ocupada" in result.refused
    assert calls == []


async def test_a_name_that_is_not_an_address_is_refused_before_any_request():
    fetch, calls = _site({"/": b"x"})
    result = await Crawler(fetch).crawl("google.com")
    assert "not an address" in result.refused
    assert calls == []


async def test_a_remote_fault_never_raises():
    async def broken(nid: str, path: str):
        raise ConnectionError("el circuito se cayo")

    result = await Crawler(broken, budget=CrawlBudget(delay=0.0)).crawl(Identity.generate().nid)
    assert not result.ok
    assert result.pages == 0


async def test_only_one_portal_at_a_time():
    """§8: every request is a six-hop round trip."""
    nid_a, nid_b = Identity.generate().nid, Identity.generate().nid
    inflight = 0
    peak = 0

    async def fetch(nid: str, path: str):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.02)
        inflight -= 1
        return (200, b"<h1>x</h1>") if path == "/" else (404, b"")

    crawler = Crawler(fetch, budget=CrawlBudget(delay=0.0))
    await asyncio.gather(crawler.crawl(nid_a), crawler.crawl(nid_b))
    assert peak == 1, f"{peak} requests in flight at once"


@pytest.mark.parametrize("status", [301, 404, 500, 503])
async def test_non_200_pages_are_skipped(status):
    async def fetch(nid: str, path: str):
        return status, b"cuerpo"

    result = await Crawler(fetch, budget=CrawlBudget(delay=0.0)).crawl(Identity.generate().nid)
    assert result.pages == 0

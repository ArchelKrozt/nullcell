"""El rastreador — §8 mechanism 3, and the reason it is the *complement*.

§8 lists a crawler third, and M2's measurements explain why: fetching pages
over six-hop circuits is slow by construction, so gossip is what scales and
this is what adds depth of text. The budget below is not conservatism, it is
the honest cost:

* **one portal at a time**, because every request is a six-hop round trip,
* **one request every two seconds**, or whatever the portal's ``Crawl-delay``
  asks for if it asks for more,
* ``robots.txt`` **mandatory**, and an unreadable one means refuse everything,
* **only while the cell is idle**, so crawling never competes with what the
  user is actually doing,
* and a **page and byte ceiling per portal**, so one site cannot occupy the
  crawler for ever.

Being rude here would also be self-defeating: a portal that gets hammered by
every cell on the network is a portal whose operator turns it off.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

from nullcell.core import ids
from nullcell.host.robots import RobotsPolicy

__all__ = ["CrawlBudget", "Crawler", "extract_text"]

_MAX_PAGE_BYTES: Final[int] = 256 * 1024
_SCRIPT_OR_STYLE: Final[re.Pattern[str]] = re.compile(
    r"<(script|style|template)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_TAGS: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")
_LINKS: Final[re.Pattern[str]] = re.compile(
    r"""<a\s[^>]*href\s*=\s*["']([^"'#?]+)""", re.IGNORECASE
)

log = logging.getLogger("nullcell.crawler")

#: ``(nid, path) -> (status, body)``. Supplied by the caller so the crawler has
#: no opinion about transports: in production it is a circuit, in tests a dict.
Fetcher = Callable[[str, str], Awaitable[tuple[int, bytes]]]


@dataclass(slots=True)
class CrawlBudget:
    """§8's politeness, as numbers."""

    delay: float = 2.0
    max_pages: int = 20
    max_bytes: int = 1024 * 1024
    concurrency: int = 1
    page_timeout: float = 30.0


@dataclass(slots=True)
class CrawlResult:
    nid: str
    pages: int = 0
    bytes_read: int = 0
    text: str = ""
    refused: str = ""
    paths: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused and self.pages > 0


def extract_text(html: bytes | str, *, limit: int = 20_000) -> str:
    """Readable text from a page, for the index.

    Scripts and styles are dropped before tags, so a page cannot pad the index
    with its own JavaScript. Everything is bounded: this is attacker-supplied
    input on its way into a search index.
    """
    if isinstance(html, bytes):
        html = html[:_MAX_PAGE_BYTES].decode("utf-8", errors="replace")
    without_code = _SCRIPT_OR_STYLE.sub(" ", html)
    text = _TAGS.sub(" ", without_code)
    return _WHITESPACE.sub(" ", text).strip()[:limit]


def extract_links(html: bytes | str, *, limit: int = 32) -> list[str]:
    """Same-site paths worth following. Absolute and off-site links ignored."""
    if isinstance(html, bytes):
        html = html[:_MAX_PAGE_BYTES].decode("utf-8", errors="replace")
    found: list[str] = []
    for href in _LINKS.findall(html):
        if "://" in href or href.startswith("//"):
            continue  # a portal does not get to send the crawler elsewhere
        path = href if href.startswith("/") else "/" + href
        if ".." in path or len(path) > 200:
            continue
        if path not in found:
            found.append(path)
        if len(found) >= limit:
            break
    return found


class Crawler:
    """Walks a portal politely and files the text in the index."""

    def __init__(
        self,
        fetch: Fetcher,
        *,
        indexer=None,
        budget: CrawlBudget | None = None,
        is_idle: Callable[[], bool] | None = None,
    ) -> None:
        self.fetch = fetch
        self.indexer = indexer
        self.budget = budget or CrawlBudget()
        #: §8: only while the cell is idle. Crawling must never compete with
        #: what the user is doing right now.
        self.is_idle = is_idle or (lambda: True)
        self._lock = asyncio.Semaphore(self.budget.concurrency)
        self.stats = {"portals": 0, "pages": 0, "bytes": 0, "refused": 0}

    # ------------------------------------------------------------------ crawl
    async def crawl(self, nid: str) -> CrawlResult:
        """Fetch a portal within the budget. Never raises for a remote fault."""
        nid = ids.parse_hostname(nid)
        result = CrawlResult(nid=nid)
        try:
            ids.decode_nid(nid)
        except ids.AddressError as exc:
            result.refused = f"not an address: {exc}"
            return result

        async with self._lock:
            if not self.is_idle():
                result.refused = "la celda está ocupada; §8 rastrea sólo en reposo"
                self.stats["refused"] += 1
                return result

            policy = await self._policy(nid)
            if not policy.readable:
                result.refused = "robots.txt ilegible: se rechaza todo (§8)"
                self.stats["refused"] += 1
                return result

            delay = max(self.budget.delay, policy.crawl_delay or 0.0)
            queue = ["/"]
            seen: set[str] = set()
            chunks: list[str] = []

            while queue and result.pages < self.budget.max_pages:
                path = queue.pop(0)
                if path in seen:
                    continue
                seen.add(path)
                if not policy.allows(path):
                    continue
                if result.bytes_read >= self.budget.max_bytes:
                    break

                body = await self._get(nid, path)
                if body is None:
                    continue
                result.pages += 1
                result.bytes_read += len(body)
                result.paths.append(path)
                chunks.append(extract_text(body))
                for link in extract_links(body):
                    if link not in seen and len(queue) < self.budget.max_pages:
                        queue.append(link)

                if queue and result.pages < self.budget.max_pages:
                    await asyncio.sleep(delay)

            result.text = " ".join(chunks)[:40_000]

        self.stats["portals"] += 1
        self.stats["pages"] += result.pages
        self.stats["bytes"] += result.bytes_read
        if result.ok and self.indexer is not None:
            self.indexer.upsert(nid, body=result.text)
        return result

    async def _policy(self, nid: str) -> RobotsPolicy:
        body = await self._get(nid, "/robots.txt")
        if body is None:
            # No robots.txt at all is not the same as an unreadable one: a site
            # that never published a policy has not refused anything (RFC 9309).
            return RobotsPolicy()
        return RobotsPolicy.parse(body)

    async def _get(self, nid: str, path: str) -> bytes | None:
        try:
            status, body = await asyncio.wait_for(
                self.fetch(nid, path), timeout=self.budget.page_timeout
            )
        except (TimeoutError, Exception) as exc:  # noqa: B014 - a remote fault is normal
            log.debug("crawl %s%s failed: %s", ids.short(nid), path, exc)
            return None
        if status != 200 or not body:
            return None
        return body[:_MAX_PAGE_BYTES]

    # ------------------------------------------------------------- background
    async def run_forever(self, portals: Callable[[], list[str]], *, interval: float = 600.0):
        """Crawl known portals in rotation, slowly, while idle (§8)."""
        while True:
            await asyncio.sleep(interval)
            for nid in portals()[:5]:
                if not self.is_idle():
                    break
                try:
                    await self.crawl(nid)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.debug("crawl round failed: %s", exc)
                await asyncio.sleep(self.budget.delay)


def _unused() -> float:  # pragma: no cover
    return time.monotonic()

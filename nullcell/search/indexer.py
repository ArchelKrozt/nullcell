"""Local full-text index — §8 mechanism 3, §16's threading rules.

SQLite with FTS5, in ``~/.nullcell/index.db``.

**Everything here runs off the event loop.** §16 is explicit: a query over a
large index costs tens of milliseconds, and spending those inside a coroutine
freezes every circuit, times out links and hangs the HUD. So:

* one **dedicated writer thread**, because SQLite serialises writes anyway and
  a single writer avoids `database is locked` entirely,
* **WAL mode**, so a reader never blocks on the writer,
* and reads in a thread executor.

The index holds attacker-supplied text. It is never interpolated into SQL —
every value is a bound parameter — and FTS5 query syntax is escaped, because a
search for ``pan OR *`` must be a search for those characters and not an
operator the user did not mean to use.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any, Final

__all__ = ["Indexer", "SearchHit", "escape_fts"]

_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS portals (
    nid        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL DEFAULT 'web',
    title      TEXT NOT NULL DEFAULT '',
    descr      TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '',
    lang       TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    confirmed  INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE IF NOT EXISTS portals_fts USING fts5(
    title, descr, tags, body,
    content='portals', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS portals_ai AFTER INSERT ON portals BEGIN
    INSERT INTO portals_fts(rowid, title, descr, tags, body)
    VALUES (new.rowid, new.title, new.descr, new.tags, new.body);
END;
CREATE TRIGGER IF NOT EXISTS portals_ad AFTER DELETE ON portals BEGIN
    INSERT INTO portals_fts(portals_fts, rowid, title, descr, tags, body)
    VALUES ('delete', old.rowid, old.title, old.descr, old.tags, old.body);
END;
CREATE TRIGGER IF NOT EXISTS portals_au AFTER UPDATE ON portals BEGIN
    INSERT INTO portals_fts(portals_fts, rowid, title, descr, tags, body)
    VALUES ('delete', old.rowid, old.title, old.descr, old.tags, old.body);
    INSERT INTO portals_fts(rowid, title, descr, tags, body)
    VALUES (new.rowid, new.title, new.descr, new.tags, new.body);
END;
"""

#: FTS5 treats these as syntax. A user searching for them means the characters.
_FTS_SPECIAL: Final[re.Pattern[str]] = re.compile(r"[^\w\s]", re.UNICODE)

log = logging.getLogger("nullcell.search")


def escape_fts(query: str) -> str:
    """Turn user text into a safe FTS5 MATCH expression.

    Each word becomes a quoted term with a prefix wildcard, so ``pan`` finds
    ``panadería``. Operators the user typed are stripped rather than honoured:
    a search box is not a query language, and treating it as one lets a visitor
    write expressions that were never meant to be available.
    """
    words = [w for w in _FTS_SPECIAL.sub(" ", query).split() if w]
    return " ".join(f'"{word}"*' for word in words[:12])


@dataclass(slots=True)
class SearchHit:
    """One result, before ranking (§8)."""

    nid: str
    kind: str
    title: str
    descr: str
    tags: list[str]
    lang: str
    bm25: float
    last_seen: float
    confirmed: int

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.last_seen)


class Indexer:
    """The local index. One writer thread, reads in an executor (§16)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._writes: Queue[tuple[str, tuple] | None] = Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: Exception | None = None

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._writer, name="ncell-index", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10.0)
        if self._error is not None:
            raise self._error

    def close(self) -> None:
        if self._thread is None:
            return
        self._writes.put(None)
        self._thread.join(timeout=5.0)
        self._thread = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        # WAL so a reader never blocks on the writer (§16).
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.row_factory = sqlite3.Row
        return connection

    def _writer(self) -> None:
        try:
            connection = self._connect()
            connection.executescript(_SCHEMA)
            connection.commit()
        except Exception as exc:  # pragma: no cover - surfaced by start()
            self._error = exc
            self._ready.set()
            return

        self._ready.set()
        while True:
            item = self._writes.get()
            if item is None:
                connection.close()
                return
            sql, params = item
            try:
                connection.execute(sql, params)
                connection.commit()
            except Exception as exc:
                log.warning("index write failed: %s", exc)

    # ------------------------------------------------------------------ write
    def upsert(
        self,
        nid: str,
        *,
        kind: str = "web",
        title: str = "",
        descr: str = "",
        tags: list[str] | None = None,
        lang: str = "",
        body: str = "",
        confirmed: int = 1,
    ) -> None:
        """Queue a portal for the index. Returns immediately."""
        now = time.time()
        self._writes.put(
            (
                """
                INSERT INTO portals
                    (nid, kind, title, descr, tags, lang, body, first_seen, last_seen, confirmed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(nid) DO UPDATE SET
                    kind=excluded.kind, title=excluded.title, descr=excluded.descr,
                    tags=excluded.tags, lang=excluded.lang,
                    body=CASE WHEN excluded.body != '' THEN excluded.body ELSE portals.body END,
                    last_seen=excluded.last_seen,
                    confirmed=MAX(portals.confirmed, excluded.confirmed)
                """,
                (
                    nid,
                    kind,
                    title,
                    descr,
                    " ".join(tags or []),
                    lang,
                    body,
                    now,
                    now,
                    confirmed,
                ),
            )
        )

    def forget(self, nid: str) -> None:
        self._writes.put(("DELETE FROM portals WHERE nid = ?", (nid,)))

    async def flush(self) -> None:
        """Wait for queued writes to land. For tests and orderly shutdown."""
        while not self._writes.empty():
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.01)

    # ------------------------------------------------------------------- read
    def _search_blocking(self, query: str, limit: int) -> list[SearchHit]:
        expression = escape_fts(query)
        if not expression:
            return []
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT p.nid, p.kind, p.title, p.descr, p.tags, p.lang,
                       p.last_seen, p.confirmed, bm25(portals_fts) AS score
                FROM portals_fts
                JOIN portals p ON p.rowid = portals_fts.rowid
                WHERE portals_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            log.debug("fts query refused: %s", exc)
            return []
        finally:
            connection.close()

        return [
            SearchHit(
                nid=row["nid"],
                kind=row["kind"],
                title=row["title"],
                descr=row["descr"],
                tags=row["tags"].split() if row["tags"] else [],
                lang=row["lang"],
                # bm25() is negative, better being more negative. Flipped here
                # so every score in the ranking of §8 means "higher is better".
                bm25=-float(row["score"] or 0.0),
                last_seen=float(row["last_seen"]),
                confirmed=int(row["confirmed"]),
            )
            for row in rows
        ]

    async def search(self, query: str, *, limit: int = 50) -> list[SearchHit]:
        """Full-text search, off the event loop (§16)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._search_blocking, query, limit)

    def _recent_blocking(self, limit: int) -> list[SearchHit]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT nid, kind, title, descr, tags, lang, last_seen, confirmed
                FROM portals ORDER BY last_seen DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [
            SearchHit(
                nid=row["nid"],
                kind=row["kind"],
                title=row["title"],
                descr=row["descr"],
                tags=row["tags"].split() if row["tags"] else [],
                lang=row["lang"],
                bm25=0.0,  # a listing has no relevance; it is not a search
                last_seen=float(row["last_seen"]),
                confirmed=int(row["confirmed"]),
            )
            for row in rows
        ]

    async def recent(self, *, limit: int = 64) -> list[SearchHit]:
        """Portals by recency, for the HUD's panel (§9).

        A plain query, not a search: an empty FTS5 MATCH matches nothing, and
        the first attempt at faking one with ``a OR e OR i OR o OR u`` turned
        into an implicit AND of five prefixes and matched nothing either.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._recent_blocking, limit)

    def _stats_blocking(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            total = connection.execute("SELECT COUNT(*) AS n FROM portals").fetchone()["n"]
            with_body = connection.execute(
                "SELECT COUNT(*) AS n FROM portals WHERE body != ''"
            ).fetchone()["n"]
        finally:
            connection.close()
        return {"portals": total, "crawled": with_body}

    async def stats(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._stats_blocking)

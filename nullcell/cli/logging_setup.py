"""Logging for a process that runs unattended — §12, RELAY_OP.

A relay is meant to run for months on a server nobody looks at, so the log is
the only way anyone finds out what it is doing. Three things follow:

* **stdout, not a file.** systemd and Docker both capture stdout, rotate it and
  make it searchable. A program that writes its own log file ends up either
  filling a disk or reinventing rotation.
* **No addresses by default.** A relay's log would otherwise become a record of
  who connected to it, which is exactly the thing an operator should not have
  if someone comes asking. Addresses appear only at ``debug``, and the default
  is ``info``.
* **One line per event, with the subsystem named**, so ``journalctl -u
  nullcell | grep circuit`` is useful.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final

__all__ = ["LEVELS", "setup"]

LEVELS: Final[dict[str, int]] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}

_FORMAT: Final[str] = "%(asctime)s %(levelname)-5s %(name)-18s %(message)s"
_DATEFMT: Final[str] = "%Y-%m-%dT%H:%M:%S"


class _NoAddresses(logging.Filter):
    """Keeps peer addresses out of the log unless debug was asked for.

    A relay cannot read what it carries (§5) and should not be able to say who
    carried it either. This is the difference between an operator who has
    nothing to hand over and one who does.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno <= logging.DEBUG:
            return True
        # Our *own* announced address is public by construction: it is in the
        # relay descriptor every cell can read. A line marked public says so
        # explicitly, which is how the seed line an operator most needs to see
        # stopped being swallowed by this filter.
        if getattr(record, "public", False):
            return True
        message = record.getMessage()
        # An IP:port belonging to somebody else. Dropped rather than redacted,
        # because a redacted line is still a record that a connection happened
        # at that second.
        return not (len(message) > 40 and message.count(".") >= 3 and ":" in message)


def setup(level: str = "info", *, journal: bool | None = None) -> None:
    """Configure logging once, for the whole process."""
    resolved = LEVELS.get(level.lower(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)

    # Under systemd or Docker the timestamp is added by the collector, so
    # printing our own duplicates it on every line.
    if journal is None:
        journal = bool(os.environ.get("JOURNAL_STREAM") or os.environ.get("INVOCATION_ID"))
    handler.setFormatter(
        logging.Formatter(
            "%(levelname)-5s %(name)-18s %(message)s" if journal else _FORMAT,
            datefmt=_DATEFMT,
        )
    )
    if resolved > logging.DEBUG:
        handler.addFilter(_NoAddresses())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved)

    # aioquic and asyncio are chatty at debug and have nothing to say at info.
    for noisy in ("aioquic", "asyncio", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(max(resolved, logging.WARNING))

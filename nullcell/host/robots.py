"""robots.txt — both sides of it, §8.

§8 asks for a crawler that is *educado*: ``robots.txt`` and a rate limit. This
parses it, and the parsing is deliberately **strict in the site's favour**: an
unreadable or oversized file is treated as *disallow everything*, not as
permission. A crawler that reads a malformed policy as consent is not polite,
it is a crawler with an excuse.

The same module writes a default policy for ``ncell host``, because a portal
should ship with one rather than leave visitors guessing.
"""

from __future__ import annotations

from typing import Final

__all__ = ["DEFAULT_ROBOTS", "MAX_ROBOTS_BYTES", "RobotsPolicy"]

#: Beyond this we stop reading and refuse everything. A robots.txt is a few
#: lines; a megabyte of it is either broken or bait.
MAX_ROBOTS_BYTES: Final[int] = 16 * 1024
_MAX_RULES: Final[int] = 200
_AGENT: Final[str] = "nullcell"

DEFAULT_ROBOTS: Final[str] = """\
# Generado por ncell host.
# El rastreador de Nullcell (§8) respeta esto: 1 petición cada 2 s, un portal
# a la vez, y sólo mientras la celda está inactiva.
User-agent: *
Allow: /
Crawl-delay: 2
"""


class RobotsPolicy:
    """What a portal allows a crawler to fetch."""

    __slots__ = ("allow", "disallow", "crawl_delay", "readable")

    def __init__(
        self,
        *,
        allow: list[str] | None = None,
        disallow: list[str] | None = None,
        crawl_delay: float | None = None,
        readable: bool = True,
    ) -> None:
        self.allow = allow or []
        self.disallow = disallow or []
        self.crawl_delay = crawl_delay
        self.readable = readable

    # ------------------------------------------------------------------ parse
    @classmethod
    def forbidding(cls) -> RobotsPolicy:
        """The policy for a site whose robots.txt we could not read.

        Everything refused. §8 says *educado*, and reading an unreadable
        policy as consent is the opposite.
        """
        return cls(disallow=["/"], readable=False)

    @classmethod
    def parse(cls, text: str | bytes) -> RobotsPolicy:
        if isinstance(text, bytes):
            if len(text) > MAX_ROBOTS_BYTES:
                return cls.forbidding()
            try:
                text = text.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return cls.forbidding()
        if len(text) > MAX_ROBOTS_BYTES:
            return cls.forbidding()

        allow: list[str] = []
        disallow: list[str] = []
        delay: float | None = None
        # Only the groups that apply to us: '*' and our own name. A group for
        # some other agent says nothing about what we may do.
        applies = False
        rules = 0

        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip()

            if field == "user-agent":
                applies = value == "*" or value.lower() == _AGENT
                continue
            if not applies or rules >= _MAX_RULES:
                continue
            if field == "disallow":
                # An empty Disallow means "nothing is disallowed" (RFC 9309).
                if value:
                    disallow.append(value)
                rules += 1
            elif field == "allow" and value:
                allow.append(value)
                rules += 1
            elif field == "crawl-delay":
                try:
                    delay = max(0.0, min(float(value), 300.0))
                except ValueError:
                    continue

        return cls(allow=allow, disallow=disallow, crawl_delay=delay)

    # ------------------------------------------------------------------ query
    def allows(self, path: str) -> bool:
        """Longest matching rule wins, Allow beating Disallow on a tie (RFC 9309)."""
        if not path.startswith("/"):
            path = "/" + path
        best_allow = max((len(p) for p in self.allow if path.startswith(p)), default=-1)
        best_deny = max((len(p) for p in self.disallow if path.startswith(p)), default=-1)
        if best_deny < 0:
            return True
        return best_allow >= best_deny

    def __repr__(self) -> str:  # pragma: no cover
        state = "readable" if self.readable else "unreadable, refusing everything"
        return f"<RobotsPolicy {state} allow={self.allow} disallow={self.disallow}>"

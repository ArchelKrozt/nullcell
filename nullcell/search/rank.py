"""Ranking — §8.

    BM25 × uptime observado × nº de nodos que confirman el descriptor
         × endosos firmados de nodos en los que *tú* confías

Four factors, and the fourth is the one that makes this different from a search
engine: **there is no global authority deciding what is good.** Endorsements
count only from cells this user chose to trust, so two people searching the
same word can legitimately get different orders. That is the design, not a
defect.

The score is returned **broken into its parts**, not as one number. §9 forbids
the interface from inventing metrics, and a single opaque score is how a
ranking stops being explainable: the HUD can say *"first because six cells
confirm it and you trust two of them"* instead of *"relevance 0.87"*.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Final

from nullcell.search.indexer import SearchHit

__all__ = ["CONFIRMATION_CEILING", "Ranked", "rank"]

#: Past this, more confirmations stop helping. Otherwise a Sybil swarm could
#: rank anything first simply by confirming it a thousand times (§10).
CONFIRMATION_CEILING: Final[int] = 10
#: A descriptor older than this is treated as stale rather than live (§9).
FRESH_WINDOW: Final[float] = 3600.0
_ENDORSEMENT_WEIGHT: Final[float] = 0.5


@dataclass(slots=True)
class Ranked:
    """A result with its score shown in parts, so it can be explained."""

    hit: SearchHit
    relevance: float
    freshness: float
    confirmation: float
    endorsement: float
    score: float = field(init=False)

    def __post_init__(self) -> None:
        self.score = self.relevance * self.freshness * self.confirmation * self.endorsement

    def explain(self) -> str:
        """Why this ranked where it did, in words the HUD can show (§9)."""
        parts = [f"relevancia {self.relevance:.2f}"]
        if self.freshness < 1.0:
            parts.append(f"visto hace {self.hit.age / 60:.0f} min")
        if self.hit.confirmed > 1:
            parts.append(f"confirmado por {self.hit.confirmed} celdas")
        if self.endorsement > 1.0:
            parts.append("endosado por celdas en las que confías")
        return " · ".join(parts)


def _relevance(bm25: float) -> float:
    """Text relevance as a **bonus over 1.0**, never a multiplier near zero.

    §8 writes the ranking as a product, and BM25 returns about zero for a word
    that appears in every document — which, in a network of a dozen portals, is
    most words. Used directly, a near-zero relevance annihilated freshness,
    confirmations and endorsements alike, and every result scored the same.

    As ``1 + bm25`` the text signal still orders results when it has something
    to say, and the other three factors of §8 decide when it does not.
    """
    return 1.0 + max(bm25, 0.0)


def _freshness(age: float) -> float:
    """§9: a descriptor renewed within its TTL means the service is up.

    Decays rather than cutting off, so a portal last seen an hour ago is ranked
    below a live one without disappearing. The HUD still shows the real age —
    this only orders results.
    """
    if age <= FRESH_WINDOW:
        return 1.0
    return max(0.1, FRESH_WINDOW / age)


def _confirmation(count: int) -> float:
    """More cells confirming a descriptor is evidence, with a ceiling (§10)."""
    bounded = min(max(count, 1), CONFIRMATION_CEILING)
    return 1.0 + math.log(bounded)


def rank(
    hits: list[SearchHit],
    *,
    trusted: set[str] | None = None,
    endorsements: dict[str, set[str]] | None = None,
    now: float | None = None,
) -> list[Ranked]:
    """Order *hits* by §8's formula.

    *endorsements* maps a portal's NID to the NIDs of cells that signed for it;
    only those in *trusted* count. An empty trust set means the fourth factor
    is neutral — which is the honest default for a cell that has not chosen
    anyone to trust yet.
    """
    trusted = trusted or set()
    endorsements = endorsements or {}
    now = time.time() if now is None else now

    ranked: list[Ranked] = []
    for hit in hits:
        age = max(0.0, now - hit.last_seen)
        vouchers = endorsements.get(hit.nid, set()) & trusted
        ranked.append(
            Ranked(
                hit=hit,
                relevance=_relevance(hit.bm25),
                freshness=_freshness(age),
                confirmation=_confirmation(hit.confirmed),
                endorsement=1.0 + _ENDORSEMENT_WEIGHT * len(vouchers),
            )
        )

    # Ties broken by address, so the same inputs always give the same order.
    ranked.sort(key=lambda r: (-r.score, r.hit.nid))
    return ranked

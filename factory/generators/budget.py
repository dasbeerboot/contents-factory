"""Per-item credit cap, enforced against the ledger before each paid call.

The cap lives in the format yaml (`credit_cap_per_item`) and the brief may
tighten it (`budget.credits`). Spend is read back out of the ledger rather than
held in memory, so a cap still holds when a video is built one stage per
command, across separate processes, hours apart.

The check happens before the call, against the provider's own quote. A stage
that would cross the cap is refused with `BUDGET_EXCEEDED` instead of being
started and cancelled, because a cancelled Higgsfield job still bills.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import ledger
from ..errors import FactoryError


@dataclass(frozen=True)
class Budget:
    home: Path
    ref: str
    cap: float
    track: str = "video"

    @property
    def capped(self) -> bool:
        return self.cap > 0

    def spent(self) -> float:
        """Credits already charged to this brief, from the ledger."""
        total = 0.0
        for entry in ledger.read(self.home):
            if str(entry.get("ref") or "") != self.ref:
                continue
            if str(entry.get("track") or "") != self.track:
                continue
            total += float(entry.get("credits") or 0)
        return round(total, 4)

    def remaining(self) -> float:
        return round(self.cap - self.spent(), 4) if self.capped else float("inf")

    def check(self, quoted: float, *, stage: str) -> float:
        """Raise unless `quoted` more credits still fit. Returns what is left."""
        if not self.capped:
            return float("inf")
        left = self.remaining()
        if quoted > left:
            raise FactoryError("BUDGET_EXCEEDED",
                               f"{stage}:need={quoted:g}:left={left:g}:cap={self.cap:g}")
        return round(left - quoted, 4)


def for_brief(home: Path, *, ref: str, format_cap: int, brief_cap: int = 0,
              track: str = "video") -> Budget:
    """The tighter of the format cap and the brief budget, zero meaning no cap."""
    caps = [float(value) for value in (format_cap, brief_cap) if value and value > 0]
    return Budget(home=Path(home), ref=ref, cap=min(caps) if caps else 0.0,
                  track=track)

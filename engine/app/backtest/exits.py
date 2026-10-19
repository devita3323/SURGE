"""Selected Bar model only; replay and personal commentary omitted."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional

@dataclass(frozen=True)
class Bar:
    d: date
    open: float
    high: float
    low: float
    close: float
    # Volume is OPTIONAL and defaults to None, which means "this feed did not
    # say", not "nothing traded". Zero is a real reading — a halted or
    # untraded session — and anything averaging volume has to be able to tell
    # the two apart. Every cache file written before this field existed reads
    # back as None, so the distinction is not hypothetical.
    volume: Optional[float] = None

    def __post_init__(self) -> None:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(f"incoherent bar on {self.d}: "
                             f"o={self.open} h={self.high} l={self.low} c={self.close}")


"""Selected true-range and ATR functions only."""
from __future__ import annotations
from typing import List, Optional, Sequence
from app.backtest.exits import Bar

def true_ranges(bars: Sequence[Bar]) -> List[Optional[float]]:
    """True range per bar. None for the first, which has no previous close.

    high-low alone understates a gap, and a gap is precisely the move an ATR
    is being consulted about.
    """
    out: List[Optional[float]] = [None]
    for i in range(1, len(bars)):
        pc = bars[i - 1].close
        b = bars[i]
        out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out


def atr(bars: Sequence[Bar], n: int = 14) -> List[Optional[float]]:
    """Wilder's ATR. First value at index `n`."""
    if n <= 0:
        raise ValueError("window must be positive")
    tr = true_ranges(bars)
    out: List[Optional[float]] = [None] * len(bars)
    if len(bars) <= n:
        return out
    prev = sum(tr[1: n + 1]) / n                                  # type: ignore[arg-type]
    out[n] = prev
    for i in range(n + 1, len(bars)):
        prev = (prev * (n - 1) + tr[i]) / n                        # type: ignore[operator]
        out[i] = prev
    return out


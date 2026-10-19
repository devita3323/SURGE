"""SURGE 2.1 point-in-time market-sequence evaluator.

This module is deliberately additive.  The legacy Ringer remains available as
an observation and comparison receipt; it is not used to manufacture a 2.1
tape transition.  SURGE 2.1 reports independent state, screenability and entry
geometry fields and never emits a master score.

The numerical cutoffs live in ``method/SURGE_2.1_CRITERIA.json``.  They are
owner-adopted priors, versioned and hashed, and are not represented as learned
probabilities until held-out validation exists.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from app.backtest.exits import Bar
from app.services.prices.technicals import atr, true_ranges

__all__ = [
    "CRITERIA_PATH", "CleanClass", "EntryWindow", "FloatConfidence",
    "FloatRange", "GammaAssessment", "GammaEvidenceLevel",
    "MethodInvariantError", "ScreenStatus", "SqueezeFuel",
    "Surge21Assessment", "TapeState", "assess_float_fuel", "assess_gamma",
    "criteria", "criteria_hash", "evaluate", "load_saved_bars",
    "validate_public_times",
]


ROOT = Path(__file__).resolve().parents[3]
CRITERIA_PATH = ROOT / "method" / "SURGE_2.1_CRITERIA.json"


class MethodInvariantError(ValueError):
    """A result would violate a non-negotiable method truth rule."""


class TapeState(str, Enum):
    DORMANT = "DORMANT"
    COMPRESSED = "COMPRESSED"
    WARMING = "WARMING"
    BULLISH_IGNITION = "BULLISH_IGNITION"
    BEARISH_IGNITION = "BEARISH_IGNITION"
    EXTENDED = "EXTENDED"
    FAILED_IGNITION = "FAILED_IGNITION"


class ScreenStatus(str, Enum):
    OK = "OK"
    UNSCREENED = "UNSCREENED"
    IMPAIRED = "IMPAIRED"


class EntryWindow(str, Enum):
    NOT_READY = "NOT_READY"
    EARLY_WINDOW = "EARLY_WINDOW"
    CONFIRMED_WINDOW = "CONFIRMED_WINDOW"
    CHASE_RISK = "CHASE_RISK"
    INVALIDATED = "INVALIDATED"


class CleanClass(str, Enum):
    SETUP_ONLY = "SETUP_ONLY"
    EARLY_ACTIVE = "EARLY_ACTIVE"
    CONFIRMED_ACTIVE = "CONFIRMED_ACTIVE"
    CLEAN_CONFLUENCE = "CLEAN_CONFLUENCE"
    ULTRA_CLEAN_CONFLUENCE = "ULTRA_CLEAN_CONFLUENCE"
    NO_CLEAN_SETUP = "NO_CLEAN_SETUP"


class FloatConfidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class GammaEvidenceLevel(str, Enum):
    OPTIONS_STRUCTURE = "OPTIONS_STRUCTURE"
    SCENARIO_GAMMA = "SCENARIO_GAMMA"
    SIGNED_DEALER_GAMMA = "SIGNED_DEALER_GAMMA"


def criteria() -> dict:
    """Load the owner-adopted criteria rather than duplicating cutoffs in code."""
    try:
        raw = json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MethodInvariantError(f"SURGE 2.1 criteria are unavailable: {exc}") from exc
    if raw.get("criteria_version") not in {"SURGE-2.1.0", "SURGE-2.1.1"}:
        raise MethodInvariantError("SURGE 2.1 criteria version is missing or unexpected")
    return raw


def criteria_hash() -> str:
    canonical = json.dumps(criteria(), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _enum_dict(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _enum_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_enum_dict(v) for v in value]
    return value


@dataclass(frozen=True)
class FloatRange:
    shares_outstanding_pit: Optional[float] = None
    float_provider_base: Optional[float] = None
    float_low: Optional[float] = None
    float_high: Optional[float] = None
    source: str = ""
    confidence: FloatConfidence = FloatConfidence.LOW
    as_of: str = ""
    method: str = ""

    def __post_init__(self) -> None:
        for name in ("shares_outstanding_pit", "float_provider_base",
                     "float_low", "float_high"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise MethodInvariantError(f"{name} must be positive when measured")
        if (self.float_low is not None and self.float_high is not None and
                self.float_low > self.float_high):
            raise MethodInvariantError("float_low cannot exceed float_high")
        if (self.float_provider_base is not None and self.float_low is not None and
                self.float_provider_base < self.float_low):
            raise MethodInvariantError("provider float lies below the documented range")
        if (self.float_provider_base is not None and self.float_high is not None and
                self.float_provider_base > self.float_high):
            raise MethodInvariantError("provider float lies above the documented range")

    @property
    def sensitivity_pp(self) -> Optional[float]:
        if not self.float_low or not self.float_high:
            return None
        return ((1.0 / self.float_low) - (1.0 / self.float_high)) * 100.0

    @property
    def robust(self) -> bool:
        return bool(self.float_low and self.float_provider_base and self.float_high)

    def to_dict(self) -> dict:
        out = _enum_dict(asdict(self))
        out["float_sensitivity_inverse_pp"] = self.sensitivity_pp
        out["robust"] = self.robust
        return out


@dataclass(frozen=True)
class SqueezeFuel:
    status: ScreenStatus = ScreenStatus.UNSCREENED
    si_pct_conservative: Optional[float] = None
    si_pct_base: Optional[float] = None
    si_pct_aggressive: Optional[float] = None
    tier: str = "UNSCREENED"
    ordinary_eligible: bool = False
    clean_eligible: bool = False
    float_sensitive: bool = False
    days_to_cover: Optional[float] = None
    gaps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _enum_dict(asdict(self))


@dataclass(frozen=True)
class GammaAssessment:
    evidence_level: GammaEvidenceLevel
    scenario_call_gamma: Optional[float] = None
    scenario_hedge_demand: Optional[float] = None
    net_dealer_gex: Optional[float] = None
    pending: bool = False
    label: str = "OPTIONS_STRUCTURE"
    assumptions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _enum_dict(asdict(self))


@dataclass
class SequenceReceipt:
    t0_dates: List[str] = field(default_factory=list)
    t1_date: str = ""
    t2_date: str = ""
    t1_clean: bool = False
    t2_clean: bool = False
    t1_age_sessions: Optional[int] = None
    t2_age_sessions: Optional[int] = None
    coil_sessions_10: int = 0
    rvol20: Optional[float] = None
    rvol60: Optional[float] = None
    volume_acceleration_3d: Optional[float] = None
    true_range_to_atr14: Optional[float] = None
    close_location: Optional[float] = None
    five_day_return: Optional[float] = None
    up_down_volume_ratio: Optional[float] = None
    close_to_t1_high: Optional[float] = None
    follow_through_2of3: Optional[bool] = None
    pivot: Optional[float] = None
    invalidation: Optional[float] = None
    extension_atr: Optional[float] = None
    risk_atr: Optional[float] = None
    five_day_move_atr: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Surge21Assessment:
    symbol: str
    as_of_time: str
    criteria_version: str
    criteria_hash: str
    tape_state: TapeState
    screen_status: ScreenStatus
    entry_window: EntryWindow
    clean_class: CleanClass
    data_quality: str
    coverage: Dict[str, float]
    sequence: SequenceReceipt
    direction: Dict[str, Any]
    corroborator_families: List[str]
    float_range: Optional[FloatRange]
    squeeze_fuel: SqueezeFuel
    hard_blockers: List[str]
    soft_warnings: List[str]
    gaps: List[str]
    master_score: None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "as_of_time": self.as_of_time,
            "criteria_version": self.criteria_version,
            "criteria_hash": self.criteria_hash,
            "tape_state": self.tape_state.value,
            "screen_status": self.screen_status.value,
            "entry_window": self.entry_window.value,
            "clean_class": self.clean_class.value,
            "data_quality": self.data_quality,
            "coverage": self.coverage,
            "sequence": self.sequence.to_dict(),
            "direction": self.direction,
            "corroborator_families": self.corroborator_families,
            "independent_corroborator_count": len(self.corroborator_families),
            "float_range": self.float_range.to_dict() if self.float_range else None,
            "squeeze_fuel": self.squeeze_fuel.to_dict(),
            "hard_blockers": self.hard_blockers,
            "soft_warnings": self.soft_warnings,
            "gaps": self.gaps,
            "master_score": None,
        }


def assess_float_fuel(short_shares: Optional[float],
                      float_range: Optional[FloatRange], *,
                      days_to_cover: Optional[float] = None) -> SqueezeFuel:
    """Measure SI against every honest float bound and fail closed for clean use."""
    cut = criteria()["squeeze"]
    gaps: List[str] = []
    warnings: List[str] = []
    if short_shares is None or short_shares < 0:
        gaps.append("Consolidated short-interest shares are missing.")
    if float_range is None:
        gaps.append("A sourced point-in-time float range is missing.")
    elif not float_range.robust:
        if float_range.float_high is not None:
            gaps.append("Only the conservative float upper bound is measured; provider float and a lower bound are required for clean confirmation.")
        else:
            gaps.append("Float range is incomplete; low, provider base, and high are required.")

    def pct(denom: Optional[float]) -> Optional[float]:
        if short_shares is None or denom is None or denom <= 0:
            return None
        return round(float(short_shares) / denom * 100.0, 3)

    conservative = pct(float_range.float_high if float_range else None)
    base = pct(float_range.float_provider_base if float_range else None)
    aggressive = pct(float_range.float_low if float_range else None)
    gate = float(cut["minimum_short_pct_float"])
    chosen = conservative if conservative is not None else base
    if chosen is None:
        tier = "UNSCREENED"
    elif chosen < gate:
        tier = "BELOW_SQUEEZE_GATE"
    elif chosen < float(cut["high_fuel_short_pct_float"]):
        tier = "SQUEEZE_ELIGIBLE"
    elif chosen < float(cut["extreme_fuel_short_pct_float"]):
        tier = "HIGH_FUEL"
    else:
        tier = "EXTREME_FUEL"

    values = [x for x in (conservative, base, aggressive) if x is not None]
    sensitive = bool(values and min(values) < gate <= max(values))
    if sensitive:
        warnings.append("Float uncertainty changes the 12% squeeze-eligibility result.")
    if float_range and float_range.confidence is not FloatConfidence.HIGH:
        warnings.append("Float confidence is below HIGH; clean squeeze is unavailable.")
    # A conservative percentage computed with a documented maximum float is
    # enough to enter the research lane.  It is not enough for clean status.
    ordinary = chosen is not None and chosen >= gate
    clean = bool(float_range and float_range.robust and
                 float_range.confidence is FloatConfidence.HIGH and
                 conservative is not None and conservative >= gate and
                 not sensitive and not gaps)
    status = ScreenStatus.OK if not gaps else ScreenStatus.UNSCREENED
    return SqueezeFuel(status=status, si_pct_conservative=conservative,
                       si_pct_base=base, si_pct_aggressive=aggressive,
                       tier=tier, ordinary_eligible=ordinary,
                       clean_eligible=clean, float_sensitive=sensitive,
                       days_to_cover=days_to_cover, gaps=gaps, warnings=warnings)


def assess_gamma(*, evidence_level: GammaEvidenceLevel,
                 public_oi: bool = True,
                 scenario_call_gamma: Optional[float] = None,
                 scenario_hedge_demand: Optional[float] = None,
                 net_dealer_gex: Optional[float] = None,
                 daily_snapshots: int = 0,
                 sessions_to_expiry: Optional[int] = None,
                 wall_distance_atr: Optional[float] = None,
                 concentration_elevated: bool = False,
                 oi_growth_elevated: bool = False,
                 turnover_extreme: bool = False,
                 iv_or_skew_rising: bool = False) -> GammaAssessment:
    """Keep public OI scenarios distinct from observed signed dealer books."""
    if public_oi and net_dealer_gex is not None:
        raise MethodInvariantError(
            "Public OI cannot populate net_dealer_gex; signed dealer positions are required")
    if (evidence_level is not GammaEvidenceLevel.SIGNED_DEALER_GAMMA and
            net_dealer_gex is not None):
        raise MethodInvariantError("net_dealer_gex requires SIGNED_DEALER_GAMMA evidence")
    cut = criteria()["gamma"]
    pending = bool(
        daily_snapshots >= int(cut["minimum_daily_snapshots"]) and
        sessions_to_expiry is not None and
        sessions_to_expiry <= int(cut["maximum_sessions_to_expiry"]) and
        wall_distance_atr is not None and
        wall_distance_atr <= float(cut["wall_distance_atr_max"]) and
        concentration_elevated and (oi_growth_elevated or turnover_extreme) and
        iv_or_skew_rising
    )
    label = ("SIGNED_DEALER_GAMMA" if evidence_level is GammaEvidenceLevel.SIGNED_DEALER_GAMMA
             else "SCENARIO_GAMMA_PENDING" if pending
             else evidence_level.value)
    assumptions = []
    if public_oi:
        assumptions.append("Dealer sign is unobserved; gamma and hedge demand are scenarios.")
    if turnover_extreme and not oi_growth_elevated:
        assumptions.append("Option turnover does not prove opening trades; next OI snapshot required.")
    return GammaAssessment(evidence_level=evidence_level,
                           scenario_call_gamma=scenario_call_gamma,
                           scenario_hedge_demand=scenario_hedge_demand,
                           net_dealer_gex=net_dealer_gex, pending=pending,
                           label=label, assumptions=assumptions)


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise MethodInvariantError(f"Invalid public_time {value!r}") from exc
    if dt.tzinfo is None:
        raise MethodInvariantError("public_time must include a timezone")
    return dt.astimezone(timezone.utc)


def validate_public_times(records: Iterable[Mapping[str, Any]],
                          as_of_time: datetime) -> None:
    """Hard-stop a replay the instant future-public information is present."""
    if as_of_time.tzinfo is None:
        raise MethodInvariantError("as_of_time must include a timezone")
    cutoff = as_of_time.astimezone(timezone.utc)
    for row in records:
        if not row.get("public_time"):
            raise MethodInvariantError(
                f"{row.get('source', 'input')} has no public_time and cannot enter replay")
        published = _parse_time(row["public_time"])
        if published > cutoff:
            raise MethodInvariantError(
                f"Future data from {row.get('source', 'input')}: "
                f"{published.isoformat()} > {cutoff.isoformat()}")


def _present_fraction(values: Sequence[Any]) -> float:
    return round(sum(v is not None for v in values) / len(values), 3) if values else 0.0


def _median_volume(bars: Sequence[Bar], lo: int, hi: int) -> Optional[float]:
    values = [bars[i].volume for i in range(max(0, lo), min(len(bars), hi))
              if bars[i].volume is not None]
    expected = max(0, min(len(bars), hi) - max(0, lo))
    if not values or len(values) < max(1, int(expected * .9)):
        return None
    return float(median(values))


def _rvol(bars: Sequence[Bar], i: int, n: int) -> Optional[float]:
    if i < n or bars[i].volume is None:
        return None
    base = _median_volume(bars, i - n, i)
    return None if base is None or base <= 0 else float(bars[i].volume) / base


def _close_location(bar: Bar) -> Optional[float]:
    width = bar.high - bar.low
    return None if width <= 0 else (bar.close - bar.low) / width


def _return(bars: Sequence[Bar], i: int, n: int) -> Optional[float]:
    if i < n or bars[i - n].close <= 0:
        return None
    return bars[i].close / bars[i - n].close - 1.0


def _up_down_ratio(bars: Sequence[Bar], i: int, n: int = 20) -> Optional[float]:
    start = max(1, i - n + 1)
    ups: List[float] = []
    downs: List[float] = []
    for j in range(start, i + 1):
        if bars[j].volume is None:
            continue
        if bars[j].close > bars[j - 1].close:
            ups.append(float(bars[j].volume))
        elif bars[j].close < bars[j - 1].close:
            downs.append(float(bars[j].volume))
    if len(ups) < 3 or len(downs) < 3:
        return None
    down = median(downs)
    return None if down <= 0 else median(ups) / down


def _features(bars: Sequence[Bar], i: int, atrs: Sequence[Optional[float]],
              trs: Sequence[Optional[float]]) -> Dict[str, Optional[float]]:
    rv20 = _rvol(bars, i, 20)
    rv60 = _rvol(bars, i, 60)
    base20 = _median_volume(bars, i - 22, i - 2) if i >= 22 else None
    recent3 = _median_volume(bars, i - 2, i + 1) if i >= 2 else None
    accel = (recent3 / base20 if recent3 is not None and base20 and base20 > 0
             else None)
    prior_atr = atrs[i - 1] if i > 0 else None
    tr_atr = (trs[i] / prior_atr if trs[i] is not None and prior_atr and prior_atr > 0
              else None)
    return {
        "rvol20": rv20, "rvol60": rv60,
        "volume_acceleration_3d": accel,
        "true_range_to_atr14": tr_atr,
        "close_location": _close_location(bars[i]),
        "five_day_return": _return(bars, i, 5),
        "up_down_volume_ratio": _up_down_ratio(bars, i),
    }


def _coil_session(bars: Sequence[Bar], i: int,
                  atrs: Sequence[Optional[float]],
                  trs: Sequence[Optional[float]], cut: Mapping[str, Any]) -> bool:
    if i < 60 or bars[i].volume is None or atrs[i - 1] is None or trs[i] is None:
        return False
    baseline = _median_volume(bars, i - 60, i)
    if baseline is None or baseline <= 0 or not atrs[i - 1]:
        return False
    return (float(bars[i].volume) / baseline <= float(cut["volume_5d_to_prior_55_max"])
            and float(trs[i]) / float(atrs[i - 1]) <=
            float(cut["true_range_to_atr14_max"]))


def _coil_indices_before(bars: Sequence[Bar], i: int,
                         atrs: Sequence[Optional[float]],
                         trs: Sequence[Optional[float]],
                         cut: Mapping[str, Any]) -> List[int]:
    n = int(cut["lookback_sessions"])
    return [j for j in range(max(0, i - n), i)
            if _coil_session(bars, j, atrs, trs, cut)]


def _t1(features: Mapping[str, Optional[float]], clean: bool,
        cut: Mapping[str, Any]) -> bool:
    rv20, rv60 = features["rvol20"], features["rvol60"]
    accel, tr_atr = features["volume_acceleration_3d"], features["true_range_to_atr14"]
    close = features["close_location"]
    if None in (rv20, tr_atr, close):
        return False
    if rv20 < float(cut["rvol20_min"]) or tr_atr < float(cut["true_range_to_atr14_min"]):
        return False
    if close < float(cut["close_location_clean_min" if clean else "close_location_base_min"]):
        return False
    if clean and not ((rv60 is not None and rv60 >= float(cut["rvol60_min"])) or
                      (accel is not None and accel >= float(cut["volume_acceleration_3d_min"]))):
        return False
    return True


def _t2(bars: Sequence[Bar], i: int, t1_i: int,
        features: Mapping[str, Optional[float]], cut: Mapping[str, Any]) -> tuple[bool, bool]:
    rv20 = features["rvol20"]
    close_loc = features["close_location"]
    ret5 = features["five_day_return"]
    ud = features["up_down_volume_ratio"]
    hold = bars[i].close / bars[t1_i].high if bars[t1_i].high > 0 else None
    checks = [
        close_loc is not None and close_loc >= float(cut["close_location_clean_min"]),
        ret5 is not None and ret5 > float(cut["five_day_return_min"]),
        ud is not None and ud > float(cut["up_down_volume_ratio_min"]),
    ]
    follow = sum(checks) >= int(cut["follow_through_required_of_three"])
    base = bool(rv20 is not None and rv20 >= float(cut["rvol20_min"]) and
                close_loc is not None and close_loc >= float(cut["close_location_base_min"]) and
                hold is not None and hold >= float(cut["hold_t1_high_fraction"]) and follow)
    measured = _present_fraction([close_loc, ret5, ud, hold])
    clean = bool(base and measured >= .90)
    return base, clean


def _float_from_legacy(raw: Optional[Mapping[str, Any]]) -> Optional[FloatRange]:
    if not raw:
        return None
    conf = str(raw.get("confidence") or "LOW").upper()
    try:
        confidence = FloatConfidence(conf)
    except ValueError:
        confidence = FloatConfidence.LOW
    base = raw.get("float_provider_base", raw.get("float_shares"))
    return FloatRange(
        shares_outstanding_pit=raw.get("shares_outstanding_pit", raw.get("outstanding_shares")),
        float_provider_base=base,
        float_low=raw.get("float_low"),
        float_high=raw.get("float_high"),
        source=str(raw.get("source") or ""), confidence=confidence,
        as_of=str(raw.get("as_of") or ""),
        method=str(raw.get("float_method") or raw.get("method") or
                   "legacy single-point float; bounds not measured"),
    )


def evaluate(symbol: str, bars: Sequence[Bar], *,
             as_of_time: Optional[datetime] = None,
             short_shares: Optional[float] = None,
             days_to_cover: Optional[float] = None,
             float_range: Optional[FloatRange | Mapping[str, Any]] = None,
             corroborator_families: Iterable[str] = (),
             hard_blockers: Iterable[str] = (),
             soft_warnings: Iterable[str] = (),
             median_spread_pct: Optional[float] = None,
             position_size_usd: Optional[float] = None) -> Surge21Assessment:
    """Evaluate one symbol from point-in-time daily bars without a master score."""
    cfg = criteria()
    coil_cut, t1_cut, t2_cut = cfg["coil"], cfg["t1"], cfg["t2"]
    entry_cut, squeeze_cut = cfg["entry_geometry"], cfg["squeeze"]
    now = as_of_time or (datetime.combine(max((b.d for b in bars), default=date.today()),
                                           datetime.max.time(), tzinfo=timezone.utc))
    if now.tzinfo is None:
        raise MethodInvariantError("as_of_time must include a timezone")
    rows = sorted(list(bars), key=lambda b: b.d)
    blockers = list(dict.fromkeys(str(x) for x in hard_blockers if str(x).strip()))
    warnings = list(dict.fromkeys(str(x) for x in soft_warnings if str(x).strip()))
    families = sorted({str(x).strip().lower() for x in corroborator_families if str(x).strip()})
    allowed = {"short_structure", "insider", "catalyst", "gamma", "control"}
    families = [x for x in families if x in allowed]

    fr = (_float_from_legacy(float_range) if isinstance(float_range, Mapping)
          else float_range)
    fuel = assess_float_fuel(short_shares, fr, days_to_cover=days_to_cover)
    warnings.extend(x for x in fuel.warnings if x not in warnings)
    gaps: List[str] = []
    if len(rows) < 61:
        gaps.append(f"At least 61 daily bars are required; {len(rows)} are available.")
    if rows:
        stale_days = (now.date() - rows[-1].d).days
        if stale_days > int(cfg["freshness"]["daily_bars_max_calendar_days"]):
            gaps.append(f"Daily bars are {stale_days} calendar days stale; refresh prices before screening.")
    volume_coverage = (sum(b.volume is not None for b in rows[-61:]) / min(61, len(rows))
                       if rows else 0.0)
    if volume_coverage < .90:
        gaps.append("At least 90% of the latest 61 daily bars need reported volume.")

    if blockers:
        status = ScreenStatus.IMPAIRED
    elif gaps:
        status = ScreenStatus.UNSCREENED
    else:
        status = ScreenStatus.OK

    seq = SequenceReceipt()
    state = TapeState.DORMANT
    entry = EntryWindow.NOT_READY
    direction: Dict[str, Any] = {
        "positive": False, "coverage": 0.0,
        "five_day_return": None, "up_down_volume_ratio": None,
        "close_location": None, "close_to_t1_high": None,
        "note": "Direction is measured from price/volume follow-through; catalyst and insider facts cannot substitute for it.",
    }
    setup_coverage = 0.0
    heat_coverage = 0.0
    direction_coverage = 0.0

    if rows:
        atrs = atr(rows, 14)
        trs = true_ranges(rows)
        last_i = len(rows) - 1
        all_coils = [i for i in range(len(rows)) if _coil_session(rows, i, atrs, trs, coil_cut)]
        recent_coils = [i for i in all_coils if i >= last_i - int(coil_cut["lookback_sessions"]) + 1]
        seq.coil_sessions_10 = len(recent_coils)
        seq.t0_dates = [rows[i].d.isoformat() for i in recent_coils]

        candidates: List[tuple[int, Dict[str, Optional[float]], bool]] = []
        for i in range(max(0, last_i - 12), last_i + 1):
            prior_coils = _coil_indices_before(rows, i, atrs, trs, coil_cut)
            if len(prior_coils) < int(coil_cut["minimum_sessions_in_10"]):
                continue
            feat = _features(rows, i, atrs, trs)
            if _t1(feat, False, t1_cut):
                candidates.append((i, feat, _t1(feat, True, t1_cut)))

        # The first expansion after the current coil begins the episode. Later
        # hot bars belong to that episode; they cannot silently reset its pivot.
        active = candidates[0] if candidates else None
        if active:
            t1_i, t1_feat, t1_clean = active
            seq.t1_date = rows[t1_i].d.isoformat()
            seq.t1_clean = t1_clean
            seq.t1_age_sessions = last_i - t1_i
            seq.pivot = rows[t1_i].high
            seq.invalidation = rows[t1_i].low
            seq.rvol20 = t1_feat["rvol20"]
            seq.rvol60 = t1_feat["rvol60"]
            seq.volume_acceleration_3d = t1_feat["volume_acceleration_3d"]
            seq.true_range_to_atr14 = t1_feat["true_range_to_atr14"]
            seq.close_location = t1_feat["close_location"]
            state = TapeState.WARMING

            t2_i: Optional[int] = None
            t2_feat: Optional[Dict[str, Optional[float]]] = None
            t2_clean = False
            deadline = int(t2_cut["confirmation_deadline_sessions"])
            for j in range(t1_i + 1, min(last_i, t1_i + deadline) + 1):
                feat = _features(rows, j, atrs, trs)
                base, clean = _t2(rows, j, t1_i, feat, t2_cut)
                if base:
                    t2_i, t2_feat, t2_clean = j, feat, clean
                    break
            failed = (rows[last_i].close < rows[t1_i].low or
                      (last_i >= t1_i + deadline and t2_i is None))
            if failed:
                state = TapeState.FAILED_IGNITION
            elif t2_i is not None and t2_feat is not None:
                state = TapeState.BULLISH_IGNITION
                seq.t2_date = rows[t2_i].d.isoformat()
                seq.t2_age_sessions = last_i - t2_i
                seq.t2_clean = t2_clean
                seq.close_location = t2_feat["close_location"]
                seq.rvol20 = t2_feat["rvol20"]
                seq.rvol60 = t2_feat["rvol60"]
                seq.volume_acceleration_3d = t2_feat["volume_acceleration_3d"]
                seq.true_range_to_atr14 = t2_feat["true_range_to_atr14"]
                seq.five_day_return = t2_feat["five_day_return"]
                seq.up_down_volume_ratio = t2_feat["up_down_volume_ratio"]
                seq.close_to_t1_high = rows[t2_i].close / rows[t1_i].high
                follow = [
                    (t2_feat["close_location"] is not None and
                     t2_feat["close_location"] >= float(t2_cut["close_location_clean_min"])),
                    (t2_feat["five_day_return"] is not None and
                     t2_feat["five_day_return"] > float(t2_cut["five_day_return_min"])),
                    (t2_feat["up_down_volume_ratio"] is not None and
                     t2_feat["up_down_volume_ratio"] > float(t2_cut["up_down_volume_ratio_min"])),
                ]
                seq.follow_through_2of3 = sum(follow) >= int(t2_cut["follow_through_required_of_three"])

            atr_at_t1 = atrs[t1_i - 1] if t1_i > 0 else None
            if atr_at_t1 and atr_at_t1 > 0:
                seq.extension_atr = (rows[last_i].close - rows[t1_i].high) / atr_at_t1
                seq.risk_atr = (rows[last_i].close - rows[t1_i].low) / atr_at_t1
                if last_i >= 5:
                    seq.five_day_move_atr = (rows[last_i].close - rows[last_i - 5].close) / atr_at_t1
            if state is TapeState.BULLISH_IGNITION and (
                    (seq.extension_atr is not None and
                     seq.extension_atr > float(entry_cut["chase_extension_atr_min"])) or
                    (seq.five_day_move_atr is not None and
                     seq.five_day_move_atr >= float(entry_cut["chase_five_day_move_atr_min"])) or
                    (seq.t1_age_sessions is not None and
                     seq.t1_age_sessions >= int(entry_cut["chase_t1_age_min"]) and
                     seq.t2_age_sessions is not None and seq.t2_age_sessions > 1)):
                state = TapeState.EXTENDED

            direction = {
                "positive": state is TapeState.BULLISH_IGNITION,
                "coverage": _present_fraction([
                    seq.close_location, seq.five_day_return,
                    seq.up_down_volume_ratio, seq.close_to_t1_high]),
                "five_day_return": seq.five_day_return,
                "up_down_volume_ratio": seq.up_down_volume_ratio,
                "close_location": seq.close_location,
                "close_to_t1_high": seq.close_to_t1_high,
                "note": "Direction uses measured price/volume follow-through. A market/sector residual is not yet supplied in this slice.",
            }
        elif len(recent_coils) >= int(coil_cut["minimum_sessions_in_10"]):
            state = TapeState.COMPRESSED

        setup_coverage = _present_fraction([
            1.0 if len(rows) >= 61 else None,
            volume_coverage if volume_coverage >= .90 else None,
            seq.coil_sessions_10 if len(rows) >= 61 else None,
            seq.pivot if active else (1.0 if state in (TapeState.DORMANT, TapeState.COMPRESSED) else None),
        ])
        heat_coverage = _present_fraction([
            seq.rvol20, seq.true_range_to_atr14,
            seq.close_location, seq.rvol60 if seq.rvol60 is not None else seq.volume_acceleration_3d,
        ])
        direction_coverage = float(direction["coverage"])

        adv_values = [b.close * float(b.volume) for b in rows[-20:] if b.volume is not None]
        adv20 = median(adv_values) if len(adv_values) >= 18 else None
        if state in (TapeState.WARMING, TapeState.BULLISH_IGNITION):
            if adv20 is None:
                warnings.append("ADV20 could not be measured for capacity.")
            elif adv20 < float(squeeze_cut["basic_adv20_usd_min"]):
                blockers.append("ADV20 is below the basic tradeability floor.")
            if median_spread_pct is None:
                warnings.append("Median spread is unmeasured; clean squeeze tradeability is unavailable.")
            elif median_spread_pct > float(squeeze_cut["basic_spread_pct_max"]):
                blockers.append("Median spread exceeds the basic tradeability ceiling.")
            if position_size_usd is not None and adv20:
                pct_adv = position_size_usd / adv20 * 100.0
                if pct_adv > float(squeeze_cut["position_to_adv_pct_max"]):
                    blockers.append("Planned position exceeds 2% of ADV20.")

    if blockers:
        status = ScreenStatus.IMPAIRED

    if status is ScreenStatus.OK:
        if state is TapeState.WARMING:
            ext = seq.extension_atr
            risk = seq.risk_atr
            if (ext is not None and 0 <= ext <= float(entry_cut["early_extension_atr_max"])
                    and risk is not None and risk <= float(entry_cut["early_risk_atr_max"])
                    and seq.t1_age_sessions is not None
                    and seq.t1_age_sessions <= int(entry_cut["early_t1_age_max"])):
                entry = EntryWindow.EARLY_WINDOW
        elif state is TapeState.BULLISH_IGNITION:
            if (seq.extension_atr is not None
                    and seq.extension_atr <= float(entry_cut["confirmed_extension_atr_max"])
                    and seq.risk_atr is not None
                    and seq.risk_atr <= float(entry_cut["confirmed_risk_atr_max"])
                    and seq.t2_age_sessions is not None
                    and seq.t2_age_sessions <= int(entry_cut["confirmed_t2_age_max"])):
                entry = EntryWindow.CONFIRMED_WINDOW
        elif state is TapeState.EXTENDED:
            entry = EntryWindow.CHASE_RISK
        elif state is TapeState.FAILED_IGNITION:
            entry = EntryWindow.INVALIDATED

    cov = {"setup": setup_coverage, "heat": heat_coverage,
           "direction": direction_coverage,
           "corroborator": 1.0 if families else 0.0}
    high = (setup_coverage >= float(cfg["coverage"]["clean"]["setup"]) and
            (state in (TapeState.DORMANT, TapeState.COMPRESSED) or
             heat_coverage >= float(cfg["coverage"]["clean"]["heat"])) and
            (state not in (TapeState.BULLISH_IGNITION, TapeState.EXTENDED) or
             direction_coverage >= float(cfg["coverage"]["clean"]["direction"])))
    data_quality = "HIGH" if high else "MEDIUM" if setup_coverage >= .70 else "LOW"

    if status is not ScreenStatus.OK:
        clean = CleanClass.NO_CLEAN_SETUP
    elif state in (TapeState.DORMANT, TapeState.COMPRESSED):
        clean = CleanClass.SETUP_ONLY
    elif state is TapeState.WARMING:
        clean = (CleanClass.CLEAN_CONFLUENCE if families and
                 entry is EntryWindow.EARLY_WINDOW and data_quality == "HIGH"
                 else CleanClass.EARLY_ACTIVE)
    elif state is TapeState.BULLISH_IGNITION:
        if (len(families) >= 2 and data_quality == "HIGH" and not warnings and
                entry is EntryWindow.CONFIRMED_WINDOW):
            clean = CleanClass.ULTRA_CLEAN_CONFLUENCE
        elif (families and data_quality == "HIGH" and
              entry is EntryWindow.CONFIRMED_WINDOW):
            clean = CleanClass.CLEAN_CONFLUENCE
        else:
            clean = CleanClass.CONFIRMED_ACTIVE
    else:
        clean = CleanClass.NO_CLEAN_SETUP

    # Final invariant wall: entry and clean status may never contradict state.
    if state is TapeState.FAILED_IGNITION:
        entry, clean = EntryWindow.INVALIDATED, CleanClass.NO_CLEAN_SETUP
    if state is TapeState.EXTENDED:
        entry, clean = EntryWindow.CHASE_RISK, CleanClass.NO_CLEAN_SETUP
    if status is ScreenStatus.UNSCREENED:
        clean = CleanClass.NO_CLEAN_SETUP

    return Surge21Assessment(
        symbol=str(symbol).strip().upper(), as_of_time=now.isoformat(),
        criteria_version=cfg["criteria_version"], criteria_hash=criteria_hash(),
        tape_state=state, screen_status=status, entry_window=entry,
        clean_class=clean, data_quality=data_quality, coverage=cov,
        sequence=seq, direction=direction,
        corroborator_families=families, float_range=fr,
        squeeze_fuel=fuel, hard_blockers=blockers,
        soft_warnings=list(dict.fromkeys(warnings)), gaps=gaps,
    )


def load_saved_bars(prices_root: Path, symbol: str) -> List[Bar]:
    """Read the freshest retained vendor CSV without making a provider request."""
    sym = str(symbol).strip().upper()
    candidates = sorted(Path(prices_root).glob(f"*/{sym}.csv"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
            keys = {str(k).lower(): k for k in (rows[0].keys() if rows else [])}
            if not all(k in keys for k in ("date", "open", "high", "low", "close")):
                continue
            out: List[Bar] = []
            for row in rows:
                raw_volume = row.get(keys.get("volume", ""), "")
                out.append(Bar(date.fromisoformat(row[keys["date"]][:10]),
                               float(row[keys["open"]]), float(row[keys["high"]]),
                               float(row[keys["low"]]), float(row[keys["close"]]),
                               None if raw_volume in (None, "") else float(raw_volume)))
            return sorted(out, key=lambda b: b.d)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return []

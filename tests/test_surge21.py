"""SURGE 2.1 method invariants from the owner-adopted setup guide."""
from datetime import date, datetime, timedelta, timezone

from unittest import TestCase
_assertions = TestCase()

from app.backtest.exits import Bar
from app.radar.surge21 import (
    CleanClass,
    EntryWindow,
    FloatConfidence,
    FloatRange,
    GammaEvidenceLevel,
    MethodInvariantError,
    ScreenStatus,
    TapeState,
    assess_float_fuel,
    assess_gamma,
    criteria_hash,
    evaluate,
    validate_public_times,
)


def _bar(day, close=100.0, spread=1.0, volume=10_000.0):
    return Bar(day, close - spread / 4, close + spread / 2,
               close - spread / 2, close + spread / 4, volume)


def dormant_bars(n=75):
    start = date(2026, 1, 2)
    return [_bar(start + timedelta(days=i), close=100 + (i % 3) * .05,
                 spread=1.0, volume=10_000.0) for i in range(n)]


def warming_bars():
    bars = dormant_bars(65)
    start = bars[-1].d + timedelta(days=1)
    for i in range(5):
        bars.append(_bar(start + timedelta(days=i), close=100 + i * .05,
                         spread=.35, volume=5_000.0))
    # T1: expansion after a sustained coil, with a strong close.
    bars.append(Bar(start + timedelta(days=5), 100.9, 101.8, 100.8, 101.8, 26_000.0))
    return bars


def confirmed_bars():
    bars = warming_bars()
    # T2 holds the T1 high, expands on volume, and closes near its high.
    bars.append(Bar(bars[-1].d + timedelta(days=1), 101.7, 102.2, 101.5, 102.1, 19_000.0))
    return bars


def robust_float():
    return FloatRange(
        shares_outstanding_pit=12_000_000,
        float_provider_base=9_000_000,
        float_low=8_000_000,
        float_high=10_000_000,
        source="point-in-time fixture",
        confidence=FloatConfidence.HIGH,
        as_of="2026-03-01",
        method="provider plus restricted-holder bounds",
    )


def test_criteria_are_versioned_and_hashed():
    assert len(criteria_hash()) == 64
    assert criteria_hash() == criteria_hash()


def test_high_short_interest_and_dead_tape_cannot_be_bullish_ignition():
    result = evaluate("DEAD", dormant_bars(), short_shares=4_000_000,
                      float_range=robust_float())
    assert result.tape_state is not TapeState.BULLISH_IGNITION
    assert result.clean_class not in {CleanClass.CLEAN_CONFLUENCE,
                                      CleanClass.ULTRA_CLEAN_CONFLUENCE}


def test_huge_insider_buy_and_dead_tape_cannot_be_bullish_ignition():
    result = evaluate("DEAD", dormant_bars(),
                      corroborator_families={"insider"})
    assert result.tape_state is TapeState.DORMANT
    assert result.clean_class is CleanClass.SETUP_ONLY


def test_confirmed_catalyst_without_heat_cannot_be_bullish_ignition():
    result = evaluate("DEAD", dormant_bars(),
                      corroborator_families={"catalyst"})
    assert result.tape_state is TapeState.DORMANT
    assert result.entry_window is EntryWindow.NOT_READY


def test_missing_float_cannot_create_a_clean_squeeze():
    result = evaluate("COIL", confirmed_bars(), short_shares=2_000_000,
                      corroborator_families={"short_structure"})
    assert result.squeeze_fuel.clean_eligible is False
    assert result.squeeze_fuel.status is ScreenStatus.UNSCREENED
    assert "float range" in " ".join(result.squeeze_fuel.gaps).lower()


def test_float_range_crossing_gate_is_float_sensitive_not_clean():
    fr = FloatRange(shares_outstanding_pit=13_000_000,
                    float_provider_base=10_000_000,
                    float_low=8_000_000, float_high=12_000_000,
                    source="fixture", confidence=FloatConfidence.HIGH,
                    as_of="2026-03-01", method="bounded fixture")
    fuel = assess_float_fuel(1_300_000, fr)
    assert fuel.float_sensitive is True
    assert fuel.clean_eligible is False
    assert fuel.si_pct_conservative < 12 <= fuel.si_pct_base


def test_public_open_interest_cannot_be_labeled_signed_dealer_gamma():
    gamma = assess_gamma(evidence_level=GammaEvidenceLevel.SCENARIO_GAMMA,
                         public_oi=True, scenario_call_gamma=123.0,
                         scenario_hedge_demand=456.0)
    assert gamma.net_dealer_gex is None
    with _assertions.assertRaises(MethodInvariantError):
        assess_gamma(evidence_level=GammaEvidenceLevel.SCENARIO_GAMMA,
                     public_oi=True, net_dealer_gex=999.0)


def test_future_public_time_is_a_hard_replay_error():
    as_of = datetime(2026, 3, 5, 15, tzinfo=timezone.utc)
    with _assertions.assertRaises(MethodInvariantError):
        validate_public_times([{"source": "fixture", "public_time":
                               "2026-03-05T15:00:01+00:00"}], as_of)


def test_t1_and_t2_are_separate_transitions():
    warm = evaluate("WARM", warming_bars())
    assert warm.tape_state is TapeState.WARMING
    assert warm.entry_window is EntryWindow.EARLY_WINDOW
    confirmed = evaluate("CONF", confirmed_bars(),
                         corroborator_families={"catalyst"})
    assert confirmed.tape_state is TapeState.BULLISH_IGNITION
    assert confirmed.entry_window is EntryWindow.CONFIRMED_WINDOW
    assert confirmed.clean_class is CleanClass.CLEAN_CONFLUENCE


def test_failed_ignition_cannot_remain_clean():
    bars = warming_bars()
    t1 = bars[-1]
    for i in range(3):
        bars.append(Bar(t1.d + timedelta(days=i + 1), 100.1, 100.4,
                        98.0, 98.4, 70.0))
    result = evaluate("FAIL", bars, corroborator_families={"catalyst", "insider"})
    assert result.tape_state is TapeState.FAILED_IGNITION
    assert result.entry_window is EntryWindow.INVALIDATED
    assert result.clean_class is CleanClass.NO_CLEAN_SETUP


def test_extended_state_cannot_receive_a_clean_entry_window():
    bars = confirmed_bars()
    bars.append(Bar(bars[-1].d + timedelta(days=1), 108.0, 112.0, 107.5, 111.5, 260.0))
    result = evaluate("LATE", bars, corroborator_families={"catalyst", "insider"})
    assert result.tape_state is TapeState.EXTENDED
    assert result.entry_window is EntryWindow.CHASE_RISK
    assert result.clean_class is CleanClass.NO_CLEAN_SETUP


def test_hard_blocker_impairs_without_erasing_underlying_tape_state():
    bars = confirmed_bars()
    clean = evaluate("KEEP", bars, corroborator_families={"catalyst"})
    blocked = evaluate("KEEP", bars, corroborator_families={"catalyst"},
                       hard_blockers=["priced offering"])
    assert clean.tape_state is blocked.tape_state
    assert blocked.screen_status is ScreenStatus.IMPAIRED
    assert blocked.clean_class is CleanClass.NO_CLEAN_SETUP


def test_missing_volume_is_unscreened_not_a_low_score():
    bars = [Bar(b.d, b.open, b.high, b.low, b.close, None) for b in dormant_bars()]
    result = evaluate("MISS", bars)
    assert result.screen_status is ScreenStatus.UNSCREENED
    assert result.clean_class is CleanClass.NO_CLEAN_SETUP
    assert result.master_score is None

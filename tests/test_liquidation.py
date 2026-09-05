
from volatility_calc.drawdown_analyzer import (
    analyze_extremes, SideStats,
)
from volatility_calc.liquidation import (
    assess_liquidation_risk, RiskLevel,
)
import pandas as pd
import pytest


def make_stats(p99_long=10.0, p99_short=8.0):
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC"),
        "open": [100.0] * 5, "high": [101.0] * 5, "low": [99.0] * 5,
        "close": [100.0] * 5, "volume": [1.0] * 5,
    })
    s = analyze_extremes(df, horizons_hours=[168])
    # Override п99 directly via monkey
    h = s.get(168)
    h.long = SideStats(0, 0, 0, 0, 0, -p99_long, -p99_long)
    h.short = SideStats(0, 0, 0, 0, 0, p99_short, p99_short)
    return s


def test_liq_distance_2x():
    s = make_stats(p99_long=0, p99_short=0)
    a = assess_liquidation_risk(s, leverage=2, maintenance_margin_rate=0.005, horizon_h=168)
    assert a.liq_distance_pct == pytest.approx(49.5, abs=0.01)


def test_safe_buffer():
    s = make_stats(p99_long=14.0, p99_short=14.0)
    a = assess_liquidation_risk(s, leverage=2, maintenance_margin_rate=0.005)
    assert a.level == RiskLevel.SAFE
    assert a.buffer_pct == pytest.approx(35.5, abs=0.1)


def test_warning_buffer():
    s = make_stats(p99_long=47.0, p99_short=47.0)
    a = assess_liquidation_risk(s, leverage=2, maintenance_margin_rate=0.005)
    assert a.level == RiskLevel.WARNING


def test_critical_buffer():
    s = make_stats(p99_long=55.0, p99_short=55.0)
    a = assess_liquidation_risk(s, leverage=2, maintenance_margin_rate=0.005)
    assert a.level == RiskLevel.CRITICAL
    assert a.buffer_pct < 0


def test_max_safe_leverage():
    s = make_stats(p99_long=14.0, p99_short=14.0)
    a = assess_liquidation_risk(s, leverage=2, maintenance_margin_rate=0.005)
    assert a.max_safe_leverage >= 4
    assert a.max_safe_leverage_buffer_pct > 10.0


def test_worst_p99_uses_max_side():
    s = make_stats(p99_long=20.0, p99_short=5.0)
    a = assess_liquidation_risk(s, leverage=2)
    assert a.worst_p99_dd == pytest.approx(20.0, abs=0.01)

import pandas as pd
import pytest

from volatility_calc.drawdown_analyzer import (
    analyze_extremes,
)


def make_df(prices, high_offset=1.0, low_offset=1.0):
    n = len(prices)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": prices,
        "high": [p + high_offset for p in prices],
        "low": [p - low_offset for p in prices],
        "close": prices,
        "volume": [10.0] * n,
    })


def test_synthetic_long_dd_known():
    # close = 100, на t+1 low=80 → long dd = +20% (величина просадки)
    prices = [100] * 5
    df = make_df(prices, high_offset=1, low_offset=1)
    df.loc[1, "low"] = 80
    stats = analyze_extremes(df, horizons_hours=[3])
    h = stats.get(3)
    assert h.long.max == pytest.approx(20.0, abs=0.01)
    assert h.long.p99 >= 0
    assert h.long.mean > 0


def test_synthetic_short_dd_known():
    prices = [100] * 5
    df = make_df(prices)
    df.loc[1, "high"] = 120
    stats = analyze_extremes(df, horizons_hours=[3])
    h = stats.get(3)
    assert h.short.max == pytest.approx(20.0, abs=0.01)
    assert h.short.mean > 0


def test_multiple_horizons():
    prices = [100 + i for i in range(50)]
    df = make_df(prices)
    stats = analyze_extremes(df, horizons_hours=[5, 10])
    assert len(stats.horizons) == 2
    assert stats.get(5).horizon_h == 5
    assert stats.get(10).horizon_h == 10


def test_thresholds_populated():
    df = make_df([100] * 30)
    df.loc[2, "low"] = 85     # 15% long dd
    df.loc[3, "high"] = 111   # 11% short dd (> 10.0)
    stats = analyze_extremes(df, horizons_hours=[5], thresholds=[5.0, 10.0])
    h = stats.get(5)
    assert 5.0 in h.long_above_thresholds
    assert h.long_above_thresholds[5.0] > 0
    assert h.short_above_thresholds[10.0] > 0


def test_missing_horizon_raises():
    df = make_df([100, 101, 102])
    stats = analyze_extremes(df, horizons_hours=[24])
    with pytest.raises(KeyError):
        stats.get(99)

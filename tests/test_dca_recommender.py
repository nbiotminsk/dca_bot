import pandas as pd
import pytest

from volatility_calc.drawdown_analyzer import analyze_extremes, SideStats
from volatility_calc.dca_recommender import (
    recommend_all, GridConfig, CurrentSettings, FullRecommendation,
    _actual_coverage, _search_grid, _volume_scale_from_tail,
)


def make_stats(p95_long=8.0, p99_long=12.0, p95_short=7.0, p99_short=10.0):
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC"),
        "open": [100.0] * 5, "high": [101.0] * 5, "low": [99.0] * 5,
        "close": [100.0] * 5, "volume": [1.0] * 5,
    })
    s = analyze_extremes(df, horizons_hours=[168])
    h = s.get(168)
    h.long = SideStats(-5, -5, 1, -7, -p95_long, -p99_long, -p99_long)
    h.short = SideStats(5, 5, 1, 6, p95_short, p99_short, p99_short)
    return s


def make_df_with_moves():
    # close растёт на 0.1% раз в час → медианный полож. ход 0.1% → TP ≈ 0.12% (но tp multiplier 1.2 -> warning low)
    prices = [100 * (1 + 0.001 * i) for i in range(50)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=50, freq="1h", tz="UTC"),
        "open": prices, "high": [p + 1 for p in prices], "low": [p - 1 for p in prices],
        "close": prices, "volume": [10.0] * 50,
    })


def test_actual_coverage():
    # ps=1.5, n=3 → 1 - (1/1.5)^2 = 1 - 0.444 = 0.556
    assert _actual_coverage(3, 1.5) == pytest.approx(0.5556, abs=0.01)


def test_search_grid_finds_minimal():
    cfg = GridConfig()
    n, ps, rat = _search_grid(0.10, cfg)
    assert n >= cfg.orders_range[0]
    assert _actual_coverage(n, ps) >= 0.10 - 1e-9


def test_search_grid_unreachable_uses_max():
    cfg = GridConfig(orders_range=(3, 4), price_scale_range=(1.1, 1.2))
    n, ps, rat = _search_grid(0.95, cfg)
    assert n == 4 and ps == 1.2
    assert any("недостижим" in r for r in rat)


def test_volume_scale_tail():
    cfg = GridConfig()
    assert _volume_scale_from_tail(11.0, 10.0, cfg) == 1.20
    assert _volume_scale_from_tail(18.0, 10.0, cfg) == 1.15
    assert _volume_scale_from_tail(25.0, 10.0, cfg) == 1.10


def test_recommend_all_long_short_tp():
    s = make_stats()
    cfg = GridConfig()
    cur = (CurrentSettings(5, 0.18, 1.4, 1.2, 0.04),
           CurrentSettings(3, 0.12, 1.3, 1.1, 0.03))
    rec = recommend_all(s, cfg, cur, horizon_h=168, df=make_df_with_moves())
    assert isinstance(rec, FullRecommendation)
    assert rec.long.orders >= 3
    assert rec.short.orders >= 3
    assert rec.tp > 0
    assert rec.horizon_used == 168
    assert len(rec.rationale) > 0
    assert _actual_coverage(rec.long.orders, rec.long.price_scale) + 1e-9 >= rec.long.coverage
    assert _actual_coverage(rec.short.orders, rec.short.price_scale) + 1e-9 >= rec.short.coverage


def test_tp_warning_when_too_low():
    s = make_stats()
    cfg = GridConfig(tp_multiplier=0.0)  # forced TP=0 → warning
    cur = (CurrentSettings(5, 0.18, 1.4, 1.2, 0.04),
           CurrentSettings(3, 0.12, 1.3, 1.1, 0.03))
    rec = recommend_all(s, cfg, cur, horizon_h=168, df=make_df_with_moves())
    assert any("WARNING" in r for r in rec.rationale)
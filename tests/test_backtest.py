"""Тесты для backtest: комиссии, equity, non-overlap, liq, walk-forward."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from volatility_calc.backtest import (
    TradeResult,
    build_equity_curve,
    coverage_to_ps,
    portfolio_metrics,
    simulate_long,
    simulate_short,
    summarize,
    grid_search,
    walk_forward,
)


@pytest.fixture
def sample_df():
    np.random.seed(42)
    n = 500
    close = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.1
    volume = np.random.rand(n) * 1000 + 100
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class TestFees:
    def test_fee_reduces_pnl(self, sample_df):
        results_no_fee = simulate_long(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, fee_pct=0.0, non_overlapping=False, step=5,
        )
        results_with_fee = simulate_long(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, fee_pct=0.0004, non_overlapping=False, step=5,
        )
        assert len(results_no_fee) == len(results_with_fee)
        for r_no, r_with in zip(results_no_fee, results_with_fee):
            assert r_with.pnl_pct < r_no.pnl_pct
            assert r_with.fee_pct > 0

    def test_fee_pct_positive_and_scales(self, sample_df):
        low = simulate_long(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, fee_pct=0.0002, non_overlapping=False, step=10,
        )
        high = simulate_long(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, fee_pct=0.0008, non_overlapping=False, step=10,
        )
        assert all(r.fee_pct > 0 for r in low)
        assert np.mean([r.fee_pct for r in high]) > np.mean([r.fee_pct for r in low])

    def test_short_fee_reduces_pnl(self, sample_df):
        results_no_fee = simulate_short(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, fee_pct=0.0, non_overlapping=False, step=5,
        )
        results_with_fee = simulate_short(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, fee_pct=0.0004, non_overlapping=False, step=5,
        )
        assert len(results_no_fee) == len(results_with_fee)
        for r_no, r_with in zip(results_no_fee, results_with_fee):
            assert r_with.pnl_pct < r_no.pnl_pct


class TestNonOverlapping:
    def test_non_overlap_no_intersect(self, sample_df):
        results = simulate_long(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, non_overlapping=True, horizon_h=48,
        )
        for a, b in zip(results, results[1:]):
            assert a.exit_idx < b.entry_idx

    def test_overlap_has_more_trades(self, sample_df):
        ov = simulate_long(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, non_overlapping=False, step=1, horizon_h=48,
        )
        no = simulate_long(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, non_overlapping=True, horizon_h=48,
        )
        assert len(ov) > len(no)


class TestCoverageToPs:
    def test_actual_coverage_meets_target(self):
        for n, cov in [(3, 0.18), (5, 0.30), (4, 0.50)]:
            ps = coverage_to_ps(n, cov)
            actual = 1.0 - (1.0 / ps) ** (n - 1)
            assert actual + 1e-9 >= cov


class TestEquityCurve:
    def test_empty_results(self):
        curve = build_equity_curve([], initial_balance=100.0)
        assert len(curve) == 1
        assert curve[0] == 100.0

    def test_all_wins_monotonic_growth(self):
        results = [
            TradeResult(0, 10, 1, 100.0, 101.0, 1.0, True, False, 10),
            TradeResult(20, 30, 1, 100.0, 102.0, 2.0, True, False, 10),
            TradeResult(40, 50, 1, 100.0, 103.0, 3.0, True, False, 10),
        ]
        curve = build_equity_curve(
            results, initial_balance=100.0, position_size_pct=1.0, compound=False
        )
        assert list(curve) == pytest.approx([100.0, 101.0, 103.0, 106.0])
        for i in range(1, len(curve)):
            assert curve[i] > curve[i - 1]

    def test_compound_grows_faster(self):
        results = [
            TradeResult(0, 10, 1, 100.0, 110.0, 10.0, True, False, 10),
            TradeResult(20, 30, 1, 100.0, 110.0, 10.0, True, False, 10),
        ]
        fixed = build_equity_curve(
            results, initial_balance=100.0, position_size_pct=1.0, compound=False
        )
        comp = build_equity_curve(
            results, initial_balance=100.0, position_size_pct=1.0, compound=True
        )
        assert comp[-1] > fixed[-1]

    def test_position_size_scaling(self):
        results = [
            TradeResult(0, 10, 1, 100.0, 110.0, 10.0, True, False, 10),
        ]
        curve_full = build_equity_curve(
            results, initial_balance=100.0, position_size_pct=1.0, compound=False
        )
        curve_half = build_equity_curve(
            results, initial_balance=100.0, position_size_pct=0.5, compound=False
        )
        assert curve_full[1] == 110.0
        assert curve_half[1] == 105.0


class TestPortfolioMetrics:
    def test_empty_curve(self):
        metrics = portfolio_metrics(np.array([100.0]))
        assert metrics["max_drawdown_pct"] == 0.0
        assert metrics["sharpe_ratio"] == 0.0

    def test_max_drawdown(self):
        curve = np.array([100.0, 110.0, 105.0, 95.0, 100.0])
        metrics = portfolio_metrics(curve)
        expected_dd = (95.0 - 110.0) / 110.0 * 100.0
        assert abs(metrics["max_drawdown_pct"] - expected_dd) < 1e-6

    def test_max_drawdown_duration(self):
        curve = np.array([100.0, 110.0, 105.0, 100.0, 95.0, 100.0, 110.0])
        metrics = portfolio_metrics(curve)
        assert metrics["max_drawdown_duration"] == 4

    def test_profit_factor(self):
        curve = np.array([100.0, 110.0, 105.0, 115.0, 110.0])
        metrics = portfolio_metrics(curve)
        returns = np.diff(curve) / curve[:-1]
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        expected_pf = np.sum(wins) / np.abs(np.sum(losses))
        assert abs(metrics["profit_factor"] - expected_pf) < 1e-6


class TestSummarize:
    def test_summarize_includes_new_metrics(self, sample_df):
        results = simulate_long(
            sample_df, n_orders=3, price_scale=1.1, volume_scale=1.2,
            tp_pct=0.01, fee_pct=0.0004, non_overlapping=True,
        )
        summary = summarize(results)
        assert hasattr(summary, "max_drawdown_pct")
        assert hasattr(summary, "sharpe_ratio")
        assert hasattr(summary, "sortino_ratio")
        assert hasattr(summary, "profit_factor")

    def test_summarize_empty(self):
        summary = summarize([])
        assert summary.n_trades == 0
        assert summary.max_drawdown_pct == 0.0


class TestGridSearch:
    def test_grid_search_includes_fee(self, sample_df):
        df = grid_search(
            sample_df,
            cov_values=[0.18],
            tp_values=[1.0],
            n_orders=3,
            fee_pct=0.0004,
            step=10,
            non_overlapping=True,
        )
        assert len(df) == 1
        assert "max_dd" in df.columns
        assert "sharpe" in df.columns
        assert "profit_factor" in df.columns

    def test_grid_search_fee_reduces_pnl(self, sample_df):
        df_no_fee = grid_search(
            sample_df, cov_values=[0.18], tp_values=[1.0],
            n_orders=3, fee_pct=0.0, step=10, non_overlapping=True,
        )
        df_with_fee = grid_search(
            sample_df, cov_values=[0.18], tp_values=[1.0],
            n_orders=3, fee_pct=0.0004, step=10, non_overlapping=True,
        )
        assert df_with_fee["total_pnl"].iloc[0] < df_no_fee["total_pnl"].iloc[0]


class TestWalkForward:
    def test_walk_forward_runs(self, sample_df):
        def param_fn(train_df):
            return {
                "n_orders": 3,
                "price_scale": 1.15,
                "volume_scale": 1.1,
                "tp_pct": 0.01,
                "side": "long",
                "horizon_h": 24,
                "non_overlapping": True,
            }

        out = walk_forward(sample_df, param_fn, n_folds=4, horizon_h=24)
        assert out["n_folds_evaluated"] >= 1
        assert "oos_summary" in out
        assert out["oos_n_trades"] >= 0


class TestShortParity:
    def test_short_accepts_filter_and_trailing(self, sample_df):
        results = simulate_short(
            sample_df,
            n_orders=3,
            price_scale=1.1,
            volume_scale=1.2,
            tp_pct=0.01,
            filter_mode="ema_price",
            tp_type="trailing",
            trail_pct=0.005,
            non_overlapping=True,
            horizon_h=48,
        )
        assert isinstance(results, list)

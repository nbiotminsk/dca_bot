"""Тесты DCA Fitness Score (DFS) и оптимизатора."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from volatility_calc.backtest import BacktestSummary
from volatility_calc.optimizer import (
    ParamGrid,
    score_strategy,
    run_optimization,
    best_per_side,
    sensitivity_analysis,
)


class TestScoringFormula:
    """Тесты формулы DCA Fitness Score."""

    def _good_summary(self) -> BacktestSummary:
        return BacktestSummary(
            n_trades=1000,
            n_wins=970,
            n_losses=30,
            n_liquidations=0,
            win_rate=97.0,
            total_pnl_pct=700.0,
            avg_pnl_pct=0.7,
            median_pnl_pct=0.7,
            max_pnl_pct=2.0,
            min_pnl_pct=-3.0,
            avg_hold_hours=18.0,
            avg_entries=1.1,
            max_drawdown_pct=-1.5,
            sharpe_ratio=50.0,
            sortino_ratio=60.0,
            profit_factor=8.0,
        )

    def test_perfect_strategy_high_score(self):
        """Идеальная стратегия должна иметь высокий score."""
        s = self._good_summary()
        fit = score_strategy(s)

        assert fit.score > 100
        assert fit.component_safety == pytest.approx(1.0)
        assert fit.component_profit > 0.5
        assert fit.component_consistency > 0.9

    def test_zero_pnl_zero_score(self):
        """Нулевой PnL должен давать околонулевое score."""
        s = BacktestSummary(
            n_trades=100, n_wins=50, n_losses=50, n_liquidations=0,
            win_rate=50.0, total_pnl_pct=0.0, avg_pnl_pct=0.0,
            median_pnl_pct=0.0, max_pnl_pct=0.0, min_pnl_pct=0.0,
            avg_hold_hours=10, avg_entries=1.0,
        )
        fit = score_strategy(s)
        assert fit.score == pytest.approx(0.0, abs=1e-9)

    def test_liquidations_kill_score(self):
        """Ликвидации должны резко снижать score."""
        s_good = self._good_summary()
        s_bad = BacktestSummary(**{**s_good.__dict__, "n_liquidations": 10})

        fit_good = score_strategy(s_good)
        fit_bad = score_strategy(s_bad)

        assert fit_bad.score < fit_good.score * 0.01
        assert fit_bad.component_safety < 1e-6

    def test_single_liquidation_significant_penalty(self):
        """Одна ликвидация снижает score более чем в 5 раз (safety factor)."""
        s_good = self._good_summary()
        s_one_liq = BacktestSummary(**{**s_good.__dict__, "n_liquidations": 1})

        fit_good = score_strategy(s_good)
        fit_one = score_strategy(s_one_liq)

        expected_ratio = math.exp(-2.0)  # exp(-LIQ_PENALTY)
        actual_ratio = fit_one.component_safety / fit_good.component_safety

        assert actual_ratio == pytest.approx(expected_ratio, rel=1e-4)

    def test_low_win_rate_reduces_consistency(self):
        """Низкий win_rate сильно снижает consistency."""
        s_high_wr = self._good_summary()

        s_low_wr = BacktestSummary(**{**s_high_wr.__dict__, "win_rate": 60.0})

        fit_high = score_strategy(s_high_wr)
        fit_low = score_strategy(s_low_wr)

        assert fit_low.component_consistency < fit_high.component_consistency
        expected_ratio = (60.0 / 97.0) ** 2
        actual_ratio = fit_low.component_consistency / fit_high.component_consistency
        assert actual_ratio == pytest.approx(expected_ratio, rel=1e-4)

    def test_higher_trade_count_increases_significance(self):
        """Больше сделок = выше significance."""
        s_few = BacktestSummary(
            n_trades=100, n_wins=50, n_losses=50, n_liquidations=0,
            win_rate=50.0, total_pnl_pct=100.0, avg_pnl_pct=1.0,
            median_pnl_pct=1.0, max_pnl_pct=2.0, min_pnl_pct=-1.0,
            avg_hold_hours=10, avg_entries=1.0,
            max_drawdown_pct=-2.0, sharpe_ratio=1.0, profit_factor=1.5,
        )
        s_many = BacktestSummary(**{**s_few.__dict__, "n_trades": 4000})

        fit_few = score_strategy(s_few)
        fit_many = score_strategy(s_many)

        assert fit_many.component_significance > fit_few.component_significance

    def test_no_trades_zero_score(self):
        """Без сделок score = 0."""
        s = BacktestSummary(
            n_trades=0, n_wins=0, n_losses=0, n_liquidations=0,
            win_rate=0.0, total_pnl_pct=0.0, avg_pnl_pct=0.0,
            median_pnl_pct=0.0, max_pnl_pct=0.0, min_pnl_pct=0.0,
            avg_hold_hours=0.0, avg_entries=0.0,
        )
        fit = score_strategy(s)
        assert fit.score == pytest.approx(0.0, abs=1e-9)
        assert fit.component_significance == 0.0

    def test_components_sum_described(self):
        """Все компоненты должны быть неотрицательными."""
        s = self._good_summary()
        fit = score_strategy(s)

        assert fit.component_profit >= 0
        assert fit.component_consistency >= 0
        assert fit.component_risk > 0   # risk всегда > 0
        assert fit.component_safety > 0
        assert fit.component_significance >= 0
        assert fit.component_efficiency >= 0


@pytest.fixture
def sample_df():
    np.random.seed(42)
    n = 500
    close = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    return pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.rand(n) * 1000,
    })


class TestParamGrid:
    """Тесты сетки параметров."""

    def test_combination_count(self):
        grid = ParamGrid(
            orders=(3, 5),
            coverages=(0.2, 0.4),
            volume_scales=(1.0, 1.1),
            tp_pcts=(0.8, 1.0),
            sides=("long",),
        )
        assert grid.n_combinations() == 2 * 2 * 2 * 2 * 1

    def test_iteration_count(self):
        grid = ParamGrid(
            orders=(3, 5),
            coverages=(0.2, 0.4),
            volume_scales=(1.0, 1.1),
            tp_pcts=(0.8, 1.0),
            sides=("long", "short"),
        )
        items = list(grid.iterations())
        assert len(items) == grid.n_combinations()
        assert all("side" in p for p in items)
        assert all("n_orders" in p for p in items)

    def test_default_grid_reasonable(self):
        grid = ParamGrid()
        assert len(grid.orders) >= 3
        assert len(grid.volume_scales) >= 4
        assert grid.fee_pct >= 0


class TestRunOptimization:
    """Интеграционные тесты оптимизатора."""

    def test_returns_dataframe(self, sample_df):
        grid = ParamGrid(
            orders=(3,),
            coverages=(0.20,),
            volume_scales=(1.0, 1.1),
            tp_pcts=(1.0,),
            sides=("long",),
            horizon_h=24,
            step=10,
        )
        df = run_optimization(sample_df, grid)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert "score" in df.columns
        assert df["score"].notna().all()
        assert not df.empty

    def test_score_sorted_descending(self, sample_df):
        grid = ParamGrid(
            orders=(3, 5),
            coverages=(0.2, 0.4),
            volume_scales=(1.0,),
            tp_pcts=(1.0,),
            sides=("long",),
            horizon_h=24,
            step=10,
        )
        df = run_optimization(sample_df, grid)
        scores = df["score"].tolist()
        assert scores == sorted(scores, reverse=True)

    def test_liquidations_get_lower_score(self, sample_df):
        grid = ParamGrid(
            orders=(3,),
            coverages=(0.2,),
            volume_scales=(1.0,),
            tp_pcts=(1.0,),
            sides=("long",),
            leverage=10,  # force liquidations
            horizon_h=24,
            step=10,
        )
        df_10x = run_optimization(sample_df, grid)

        grid_1x = ParamGrid(**{**grid.__dict__, "leverage": 1})
        df_1x = run_optimization(sample_df, grid_1x)

        if len(df_10x) > 0 and len(df_1x) > 0:
            assert df_1x["score"].max() >= df_10x["score"].max()

    def test_best_per_side(self, sample_df):
        grid = ParamGrid(
            orders=(3,),
            coverages=(0.2,),
            volume_scales=(1.0,),
            tp_pcts=(1.0,),
            sides=("long", "short"),
            horizon_h=24,
            step=10,
        )
        df = run_optimization(sample_df, grid)
        best = best_per_side(df, n=1)
        assert "long" in best
        assert "short" in best
        assert len(best["long"]) == 1
        assert len(best["short"]) == 1

    def test_sensitivity_analysis(self, sample_df):
        grid = ParamGrid(
            orders=(3, 5),
            coverages=(0.2, 0.4),
            volume_scales=(1.0, 1.1),
            tp_pcts=(1.0,),
            sides=("long",),
            horizon_h=24,
            step=10,
        )
        df = run_optimization(sample_df, grid)
        sens = sensitivity_analysis(df, "n_orders")

        assert "mean" in sens.columns
        assert "max" in sens.columns
        assert len(sens) == 2  # 2 unique orders values
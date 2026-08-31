"""Мультипараметрическая оптимизация DCA-стратегии.

DCA Fitness Score (DFS) — log-sum компонентов (устойчивее product→0):

    Score = 100 * sum_i w_i * log1p(c_i)

Компоненты нормированы в [0, +∞), safety/profit могут обнулять вклад.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd

from .backtest import (
    BacktestSummary,
    coverage_to_ps,
    simulate,
    summarize,
    walk_forward,
)

logger = logging.getLogger(__name__)

PNL_SCALE: float = 500.0
CONSISTENCY_POW: float = 2.0
DD_SCALE: float = 5.0
LIQ_PENALTY: float = 2.0
TRADE_SCALE: float = 1000.0
BASE_SCORE_MULT: float = 100.0

# веса log-sum (сумма не обязана = 1)
W_PROFIT = 1.0
W_CONSIST = 1.0
W_RISK = 1.0
W_SAFETY = 1.5
W_SIGNIF = 0.8
W_EFF = 1.0


@dataclass(frozen=True)
class DCAFitness:
    score: float
    component_profit: float
    component_consistency: float
    component_risk: float
    component_safety: float
    component_significance: float
    component_efficiency: float

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "c_profit": round(self.component_profit, 4),
            "c_consist": round(self.component_consistency, 4),
            "c_risk": round(self.component_risk, 4),
            "c_safety": round(self.component_safety, 6),
            "c_signif": round(self.component_significance, 4),
            "c_eff": round(self.component_efficiency, 4),
        }


def score_strategy(summary: BacktestSummary, *, n_orders: int = 0) -> DCAFitness:
    """DCA Fitness Score для BacktestSummary."""
    # avg pnl * sqrt(n) — нормализация на число сделок
    if summary.n_trades > 0:
        scaled_pnl = summary.avg_pnl_pct * math.sqrt(summary.n_trades)
    else:
        scaled_pnl = 0.0
    profit = math.tanh(scaled_pnl / (PNL_SCALE / 20.0))
    if summary.total_pnl_pct < 0:
        profit = -abs(profit)

    consistency = (max(summary.win_rate, 0.0) / 100.0) ** CONSISTENCY_POW
    risk = 1.0 / (1.0 + abs(summary.max_drawdown_pct) / DD_SCALE)
    safety = math.exp(-LIQ_PENALTY * summary.n_liquidations)

    if summary.n_trades > 0:
        significance = math.sqrt(summary.n_trades) / math.sqrt(TRADE_SCALE)
    else:
        significance = 0.0

    sharpe_pos = max(summary.sharpe_ratio, 0.0)
    pf_pos = max(summary.profit_factor, 0.0)
    efficiency = math.log1p(sharpe_pos) + math.log1p(pf_pos)

    # log-sum: отрицательный profit → штраф
    parts = [
        (W_PROFIT, max(profit, 0.0) if profit >= 0 else 0.0),
        (W_CONSIST, consistency),
        (W_RISK, risk),
        (W_SAFETY, safety),
        (W_SIGNIF, significance),
        (W_EFF, efficiency),
    ]
    total = sum(w * math.log1p(c) for w, c in parts) * BASE_SCORE_MULT
    # multiplicative safety: 0 liq → 1.0, 1 → ~0.14, 10 → ~0
    total *= safety
    if summary.n_trades == 0 or abs(summary.total_pnl_pct) < 1e-12:
        total = 0.0
    elif profit < 0:
        total *= 0.1

    return DCAFitness(
        score=total,
        component_profit=profit,
        component_consistency=consistency,
        component_risk=risk,
        component_safety=safety,
        component_significance=significance,
        component_efficiency=efficiency,
    )


@dataclass(frozen=True)
class ParamGrid:
    orders: tuple[int, ...] = (3, 4, 5, 6, 7, 8)
    coverages: tuple[float, ...] = (0.20, 0.30, 0.40, 0.50, 0.60)
    volume_scales: tuple[float, ...] = (1.0, 1.03, 1.05, 1.08, 1.10, 1.12, 1.15, 1.20)
    tp_pcts: tuple[float, ...] = (0.8, 0.9, 1.0)
    base_qty: float = 1.0
    fee_pct: float = 0.0004
    funding_rate_8h: float = 0.0
    leverage: int = 1
    horizon_h: int = 168
    step: int = 4
    sides: tuple[str, ...] = ("long", "short")
    non_overlapping: bool = True
    filter_mode: str = "none"

    def n_combinations(self) -> int:
        n = (
            len(self.orders)
            * len(self.coverages)
            * len(self.volume_scales)
            * len(self.tp_pcts)
        )
        return n * len(self.sides)

    def iterations(self) -> Iterable[dict]:
        for side in self.sides:
            for orders in self.orders:
                for cov in self.coverages:
                    for vs in self.volume_scales:
                        for tp in self.tp_pcts:
                            yield {
                                "side": side,
                                "n_orders": orders,
                                "coverage": cov,
                                "volume_scale": vs,
                                "tp_pct": tp,
                            }


@dataclass
class OptimizationRow:
    side: str
    n_orders: int
    coverage: float
    price_scale: float
    volume_scale: float
    tp_pct: float
    n_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    min_pnl: float
    n_liquidations: int
    avg_hold: float
    avg_entries: float
    max_dd: float
    sharpe: float
    profit_factor: float
    score: float
    components: dict = field(default_factory=dict)


def run_optimization(
    df: pd.DataFrame,
    grid: ParamGrid,
    progress_every: int = 25,
) -> pd.DataFrame:
    """Полный перебор сетки параметров с DFS."""
    rows: list[OptimizationRow] = []
    total = grid.n_combinations()
    processed = 0

    logger.info("optimization start: %s combinations", total)

    for params in grid.iterations():
        side = params["side"]
        n_orders = params["n_orders"]
        cov = params["coverage"]
        vs = params["volume_scale"]
        tp = params["tp_pct"]
        ps = coverage_to_ps(n_orders, cov)

        results = simulate(
            df,
            n_orders=n_orders,
            price_scale=ps,
            volume_scale=vs,
            tp_pct=tp / 100.0,
            leverage=grid.leverage,
            horizon_h=grid.horizon_h,
            base_qty=grid.base_qty,
            step=grid.step,
            side=side,
            fee_pct=grid.fee_pct,
            funding_rate_8h=grid.funding_rate_8h,
            non_overlapping=grid.non_overlapping,
            filter_mode=grid.filter_mode,
        )

        s = summarize(results)
        fitness = score_strategy(s, n_orders=n_orders)

        rows.append(
            OptimizationRow(
                side=side,
                n_orders=n_orders,
                coverage=round(cov, 2),
                price_scale=round(ps, 2),
                volume_scale=vs,
                tp_pct=tp,
                n_trades=s.n_trades,
                win_rate=round(s.win_rate, 1),
                total_pnl=round(s.total_pnl_pct, 2),
                avg_pnl=round(s.avg_pnl_pct, 4),
                min_pnl=round(s.min_pnl_pct, 2),
                n_liquidations=s.n_liquidations,
                avg_hold=round(s.avg_hold_hours, 1),
                avg_entries=round(s.avg_entries, 2),
                max_dd=round(s.max_drawdown_pct, 2),
                sharpe=round(s.sharpe_ratio, 2),
                profit_factor=round(s.profit_factor, 2),
                score=round(fitness.score, 2),
                components=fitness.as_dict(),
            )
        )

        processed += 1
        if progress_every and processed % progress_every == 0:
            logger.info("optimization progress: %s/%s", processed, total)

    logger.info("optimization done: %s configs", processed)

    df_out = pd.DataFrame([r.__dict__ for r in rows])
    return df_out.sort_values("score", ascending=False).reset_index(drop=True)


def best_per_side(df_results: pd.DataFrame, n: int = 10) -> dict[str, pd.DataFrame]:
    return {
        side: df_results[df_results["side"] == side].head(n).reset_index(drop=True)
        for side in df_results["side"].unique()
    }


def sensitivity_analysis(df_results: pd.DataFrame, param: str) -> pd.DataFrame:
    return (
        df_results.groupby(param)["score"]
        .agg(["mean", "median", "max", "std"])
        .round(2)
        .sort_values("max", ascending=False)
    )


def run_walk_forward_optimization(
    df: pd.DataFrame,
    grid: ParamGrid,
    *,
    n_folds: int = 4,
    side: str = "long",
) -> dict:
    """Walk-forward: на каждом train-fold grid search, OOS на test."""

    def param_fn(train_df: pd.DataFrame) -> dict:
        sub = ParamGrid(
            orders=grid.orders,
            coverages=grid.coverages,
            volume_scales=grid.volume_scales,
            tp_pcts=grid.tp_pcts,
            base_qty=grid.base_qty,
            fee_pct=grid.fee_pct,
            funding_rate_8h=grid.funding_rate_8h,
            leverage=grid.leverage,
            horizon_h=grid.horizon_h,
            step=grid.step,
            sides=(side,),
            non_overlapping=grid.non_overlapping,
            filter_mode=grid.filter_mode,
        )
        res = run_optimization(train_df, sub, progress_every=0)
        if res.empty:
            return {
                "n_orders": grid.orders[0],
                "price_scale": coverage_to_ps(grid.orders[0], grid.coverages[0]),
                "volume_scale": grid.volume_scales[0],
                "tp_pct": grid.tp_pcts[0] / 100.0,
                "side": side,
            }
        best = res.iloc[0]
        return {
            "n_orders": int(best["n_orders"]),
            "price_scale": float(best["price_scale"]),
            "volume_scale": float(best["volume_scale"]),
            "tp_pct": float(best["tp_pct"]) / 100.0,
            "side": side,
            "leverage": grid.leverage,
            "horizon_h": grid.horizon_h,
            "fee_pct": grid.fee_pct,
            "funding_rate_8h": grid.funding_rate_8h,
            "non_overlapping": grid.non_overlapping,
            "filter_mode": grid.filter_mode,
            "step": grid.step,
        }

    return walk_forward(
        df,
        param_fn,
        n_folds=n_folds,
        leverage=grid.leverage,
        horizon_h=grid.horizon_h,
        fee_pct=grid.fee_pct,
        funding_rate_8h=grid.funding_rate_8h,
        non_overlapping=grid.non_overlapping,
        filter_mode=grid.filter_mode,
        step=grid.step,
        side=side,
    )

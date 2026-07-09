"""Мультипараметрическая оптимизация DCA-стратегии.

Формула скоринга (DCA Fitness Score, DFS):

    Score = Profitability x Consistency x RiskAdjusted x Safety x Significance x Efficiency

Каждый множитель — безразмерный, спроектирован так, что:
    > 1.0 — превосходит эталон
    < 1.0 — хуже эталона
    = 0.0 — неприемлемо (ликвидации, нулевой PnL)

Компоненты:

    Profitability  = tanh(TotalPnL / PNL_SCALE)
        — насыщающаяся функция прибыли, PNL_SCALE=500 (500% PnL -> ~0.88)

    Consistency    = (WinRate / 100) ^ CONSISTENCY_POW
        — возведение в степень 2 акцентирует высокий win_rate
        (97% -> 0.94,  50% -> 0.25)

    RiskAdjusted   = 1 / (1 + |MaxDD| / DD_SCALE)
        — штраф за просадку, DD_SCALE=5 (5% DD -> 0.5)

    Safety         = exp(-LIQ_PENALTY * N_liquidations)
        — асимметричный риск: 0 ликвидаций -> 1.0
        1 ликвидация -> 0.14,  10 -> ~0

    Significance   = sqrt(N_trades) / sqrt(TRADE_SCALE)
        — больше сделок = выше статистическая значимость
        1000 сделок -> 1.0

    Efficiency     = log(1 + max(Sharpe, 0)) + log(1 + max(ProfitFactor, 0))
        — вознаграждает эффективность с поправкой на риск

Итоговая оценка умножается на 100 для удобства чтения.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from .backtest import (
    BacktestSummary,
    coverage_to_ps,
    simulate,
    summarize,
)


# ── Константы формулы (эталоны нормирования) ──────────────────────────
PNL_SCALE: float = 500.0          # 500% PnL ~ 0.88 по tanh
CONSISTENCY_POW: float = 2.0      # квадрат win_rate
DD_SCALE: float = 5.0             # 5% max DD -> множитель 0.5
LIQ_PENALTY: float = 2.0          # 1 ликвидация -> множитель 0.14
TRADE_SCALE: float = 1000.0       # 1000 сделок -> множитель 1.0
BASE_SCORE_MULT: float = 100.0    # итоговый масштаб


@dataclass(frozen=True)
class DCAFitness:
    """Результат оценки одной конфигурации DCA."""

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
    """Вычислить DCA Fitness Score для BacktestSummary.

    Parameters
    ----------
    summary : BacktestSummary
        Метрики бэктеста.
    n_orders : int
        Число ордеров (не используется напрямую, но доступно для расширения).
    """
    profit = math.tanh(summary.total_pnl_pct / PNL_SCALE)

    consistency = (summary.win_rate / 100.0) ** CONSISTENCY_POW

    risk = 1.0 / (1.0 + abs(summary.max_drawdown_pct) / DD_SCALE)

    safety = math.exp(-LIQ_PENALTY * summary.n_liquidations)

    if summary.n_trades > 0:
        significance = math.sqrt(summary.n_trades) / math.sqrt(TRADE_SCALE)
    else:
        significance = 0.0

    sharpe_pos = max(summary.sharpe_ratio, 0.0)
    pf_pos = max(summary.profit_factor, 0.0)
    efficiency = math.log1p(sharpe_pos) + math.log1p(pf_pos)

    total = (
        profit
        * consistency
        * risk
        * safety
        * significance
        * efficiency
        * BASE_SCORE_MULT
    )

    return DCAFitness(
        score=total,
        component_profit=profit,
        component_consistency=consistency,
        component_risk=risk,
        component_safety=safety,
        component_significance=significance,
        component_efficiency=efficiency,
    )


# ── Перебор параметров ─────────────────────────────────────────────────

@dataclass(frozen=True)
class ParamGrid:
    """Сетка параметров для оптимизации."""

    orders: tuple[int, ...] = (3, 4, 5, 6, 7, 8)
    coverages: tuple[float, ...] = (0.20, 0.30, 0.40, 0.50, 0.60)
    volume_scales: tuple[float, ...] = (1.0, 1.03, 1.05, 1.08, 1.10, 1.12, 1.15, 1.20)
    tp_pcts: tuple[float, ...] = (0.8, 0.9, 1.0)
    base_qty: float = 1.0
    fee_pct: float = 0.0004
    leverage: int = 1
    horizon_h: int = 168
    step: int = 4
    sides: tuple[str, ...] = ("long", "short")

    def n_combinations(self) -> int:
        n = len(self.orders) * len(self.coverages) * len(self.volume_scales) * len(self.tp_pcts)
        return n * len(self.sides)

    def iterations(self) -> Iterable[dict]:
        """Генератор всех комбинаций параметров."""
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
    """Одна строка результата оптимизации."""

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


def run_optimization(df: pd.DataFrame, grid: ParamGrid) -> pd.DataFrame:
    """Полный перебор сетки параметров с DCA Fitness Score.

    Returns
    -------
    pd.DataFrame
        Каждая строка — одна конфигурация с метриками и итоговым score.
    """
    rows: list[OptimizationRow] = []

    total = grid.n_combinations()
    processed = 0

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
        )

        s = summarize(results)
        fitness = score_strategy(s, n_orders=n_orders)

        row = OptimizationRow(
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
        rows.append(row)

        processed += 1

    df_out = pd.DataFrame([{k: v for k, v in r.__dict__.items()} for r in rows])
    df_out = df_out.sort_values("score", ascending=False).reset_index(drop=True)
    return df_out


def best_per_side(df_results: pd.DataFrame, n: int = 10) -> dict[str, pd.DataFrame]:
    """Топ-N конфигураций для каждой стороны."""
    return {
        side: df_results[df_results["side"] == side].head(n).reset_index(drop=True)
        for side in df_results["side"].unique()
    }


def sensitivity_analysis(df_results: pd.DataFrame, param: str) -> pd.DataFrame:
    """Анализ чувствительности score к одному параметру.

    Группирует по параметру и возвращает средний/медианный/макс score.
    """
    return (
        df_results.groupby(param)["score"]
        .agg(["mean", "median", "max", "std"])
        .round(2)
        .sort_values("max", ascending=False)
    )
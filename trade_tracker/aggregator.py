"""Агрегаты по портфелю сделок."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from .calculator import compute_metrics, TradeMetrics
from .models import Trade

ZERO = Decimal(0)


@dataclass
class PortfolioStats:
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    avg_pnl: Decimal
    median_pnl: Decimal
    total_pnl: Decimal
    cumulative_pnl: list[Decimal]
    avg_mae: Decimal | None
    max_mae: Decimal | None
    dca_distribution: dict[int, int] = field(default_factory=dict)
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    avg_win: Decimal = Decimal(0)
    avg_loss: Decimal = Decimal(0)


def aggregate(trades: list[Trade], risk_free_rate: float = 0.0) -> PortfolioStats:
    if not trades:
        return PortfolioStats(
            n_trades=0, n_wins=0, n_losses=0, win_rate=0.0,
            avg_pnl=ZERO, median_pnl=ZERO, total_pnl=ZERO,
            cumulative_pnl=[], avg_mae=None, max_mae=None,
            dca_distribution={},
        )

    metrics: list[TradeMetrics] = [compute_metrics(t) for t in trades]
    pnls = [m.gross_pnl for m in metrics]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    total = sum(pnls, ZERO)
    cum = []
    running = ZERO
    for p in pnls:
        running += p
        cum.append(running)

    mae_vals = [m.mae_pct for m in metrics if m.mae_pct is not None]
    avg_mae = sum(mae_vals, ZERO) / Decimal(len(mae_vals)) if mae_vals else None
    max_mae = min(mae_vals) if mae_vals else None

    dca_counter = Counter(m.dca_used for m in metrics)

    pnl_floats = [float(p) for p in pnls]
    avg_pnl_float = sum(pnl_floats) / len(pnl_floats)
    std_pnl = (sum((p - avg_pnl_float) ** 2 for p in pnl_floats) / len(pnl_floats)) ** 0.5

    sharpe = 0.0
    if std_pnl > 0:
        sharpe = (avg_pnl_float - risk_free_rate) / std_pnl

    downside = [p for p in pnl_floats if p < 0]
    sortino = 0.0
    if downside:
        downside_std = (sum(p ** 2 for p in downside) / len(downside)) ** 0.5
        if downside_std > 0:
            sortino = (avg_pnl_float - risk_free_rate) / downside_std

    max_dd = 0.0
    peak = 0.0
    for c in cum:
        c_float = float(c)
        if c_float > peak:
            peak = c_float
        dd = peak - c_float
        if dd > max_dd:
            max_dd = dd

    calmar = 0.0
    if max_dd > 0:
        calmar = avg_pnl_float / max_dd

    win_pnls = [p for p in pnls if p > 0]
    loss_pnls = [p for p in pnls if p < 0]
    avg_win = sum(win_pnls, ZERO) / Decimal(len(win_pnls)) if win_pnls else ZERO
    avg_loss = sum(loss_pnls, ZERO) / Decimal(len(loss_pnls)) if loss_pnls else ZERO

    return PortfolioStats(
        n_trades=len(trades),
        n_wins=wins,
        n_losses=losses,
        win_rate=wins / len(trades) * 100.0,
        avg_pnl=total / Decimal(len(trades)),
        median_pnl=Decimal(str(median(pnl_floats))),
        total_pnl=total,
        cumulative_pnl=cum,
        avg_mae=avg_mae,
        max_mae=max_mae,
        dca_distribution=dict(dca_counter),
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        avg_win=avg_win,
        avg_loss=avg_loss,
    )

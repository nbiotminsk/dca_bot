"""Риск ликвидации: buffer + максимальное безопасное плечо."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .drawdown_analyzer import MultiHorizonStats


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class LiquidationAssessment:
    leverage: int
    maintenance_margin_rate: float
    liq_distance_pct: float          # расстояние до ликвидации, %
    p99_long_dd: float
    p99_short_dd: float
    worst_p99_dd: float
    buffer_pct: float
    level: RiskLevel
    max_safe_leverage: int
    max_safe_leverage_buffer_pct: float


def _distance_to_liquidation(leverage: int, mmr: float) -> float:
    """Прибыль/убыток, при котором наступает ликвидация: 1/L - MMR (%)."""
    return (1.0 / leverage - mmr) * 100.0


def _max_safe_leverage(worst_p99_dd_pct: float, mmr: float,
                       min_buffer_pct: float = 10.0) -> tuple[int, float]:
    """Максимальное плечо, при котором buffer > min_buffer%."""
    best = 1
    best_buffer = 0.0
    for lev in range(1, 101):
        dist = _distance_to_liquidation(lev, mmr)
        buffer = dist - worst_p99_dd_pct
        if buffer > min_buffer_pct:
            best = lev
            best_buffer = buffer
        else:
            break
    return best, best_buffer


def assess_liquidation_risk(
    stats: MultiHorizonStats,
    leverage: int = 2,
    maintenance_margin_rate: float = 0.005,
    horizon_h: int = 168,
) -> LiquidationAssessment:
    """Оценка риска ликвидации по p99 просадке для заданного горизонта."""
    h = stats.get(horizon_h)
    p99_long = abs(h.long.p99)
    p99_short = abs(h.short.p99)
    worst_p99 = max(p99_long, p99_short)
    dist = _distance_to_liquidation(leverage, maintenance_margin_rate)
    buffer = dist - worst_p99

    if buffer < 0:
        level = RiskLevel.CRITICAL
    elif buffer < 5.0:
        level = RiskLevel.WARNING
    else:
        level = RiskLevel.SAFE

    max_lev, max_lev_buffer = _max_safe_leverage(worst_p99, maintenance_margin_rate)

    return LiquidationAssessment(
        leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate,
        liq_distance_pct=dist,
        p99_long_dd=p99_long,
        p99_short_dd=p99_short,
        worst_p99_dd=worst_p99,
        buffer_pct=buffer,
        level=level,
        max_safe_leverage=max_lev,
        max_safe_leverage_buffer_pct=max_lev_buffer,
    )

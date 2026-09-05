"""DCA-рекомендация: coverage + orders + price_scale + volume_scale + TP."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .drawdown_analyzer import MultiHorizonStats

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GridConfig:
    orders_range: tuple[int, int] = (3, 7)
    price_scale_range: tuple[float, float] = (1.1, 1.5)
    price_scale_step: float = 0.05
    safety_factor: float = 1.2
    volume_scale_conservative: float = 2.0
    volume_scale_moderate: float = 1.5
    tp_horizon_h: int = 24
    tp_floor_pct: float = 0.5
    tp_ceil_pct: float = 2.0
    tp_multiplier: float = 1.2


@dataclass(frozen=True)
class CurrentSettings:
    orders: int
    coverage: float
    price_scale: float
    volume_scale: float
    base_qty: float


@dataclass
class GridRecommendation:
    orders: int
    coverage: float              # целевой coverage (рекомендованный), доля 0..1
    price_scale: float
    volume_scale: float
    rationale: list[str] = field(default_factory=list)


@dataclass
class FullRecommendation:
    long: GridRecommendation
    short: GridRecommendation
    tp: float
    horizon_used: int
    rationale: list[str] = field(default_factory=list)
    kelly_fraction: Optional[float] = None
    kelly_rationale: str = ""


def _compute_kelly(win_rate: float, avg_win: float, avg_loss: float) -> tuple[float, str]:
    """Критерий Келли: оптимальная доля капитала на сделку.

    f* = (p * b - q) / b
    где p — вероятность выигрыша, q = 1 - p, b = avg_win / avg_loss

    Returns:
        (kelly_fraction, rationale)
    """
    if win_rate <= 0 or avg_win <= 0 or avg_loss <= 0:
        return 0.0, "Недостаточно данных для расчёта Келли"

    p = win_rate / 100.0
    q = 1.0 - p
    b = avg_win / abs(avg_loss)

    kelly = (p * b - q) / b

    if kelly <= 0:
        rationale = f"Kelly ≤ 0 ({kelly:.3f}): стратегия убыточна, не торговать"
        return 0.0, rationale

    half_kelly = kelly / 2.0
    rationale = (
        f"Kelly criterion: f*={kelly:.3f} (win_rate={win_rate:.1f}%, "
        f"avg_win/loss={b:.2f}). Рекомендуется half-Kelly={half_kelly:.3f} "
        f"для снижения волатильности"
    )

    return half_kelly, rationale


def _actual_coverage(n: int, ps: float) -> float:
    """Coverage сетки DCA: 1 - (1/ps)^(n-1)."""
    return 1.0 - (1.0 / ps) ** (n - 1)


def _search_grid(target_coverage: float, cfg: GridConfig,
                 current_orders: int | None = None,
                 current_ps: float | None = None) -> tuple[int, float, list[str]]:
    """Минимальные orders / price_scale, чтобы actual_coverage >= target."""
    rationale: list[str] = []
    best: tuple[int, float] | None = None
    lo_n, hi_n = cfg.orders_range
    lo_ps, hi_ps = cfg.price_scale_range
    ps_values = np.arange(lo_ps, hi_ps + 1e-9, cfg.price_scale_step)
    ps_values = [round(float(v), 4) for v in ps_values]
    for n in range(lo_n, hi_n + 1):
        for ps in ps_values:
            if _actual_coverage(n, ps) + 1e-9 >= target_coverage:
                if best is None or n < best[0] or (n == best[0] and ps < best[1]):
                    best = (n, ps)
        if best is not None and best[0] == n:
            break
    if best is None:
        # 不能 покрыть даже максимальной сеткой — взять максимальную.
        best = (hi_n, hi_ps)
        rationale.append(
            f"Целевой coverage {target_coverage:.3f} недостижим в рамках диапазона; "
            f"взята максимальная сетка n={hi_n}, ps={hi_ps}"
        )
    n, ps = best
    rationale.append(
        f"Найдена минимальная сетка: orders={n}, price_scale={ps} "
        f"(actual_coverage={_actual_coverage(n, ps):.3f} >= target {target_coverage:.3f})"
    )
    return n, ps, rationale


def _volume_scale_from_tail(p99: float, p95: float, cfg: GridConfig) -> float:
    ratio = (p99 / p95) if p95 and p95 > 0 else 1.0
    if ratio < cfg.volume_scale_moderate:
        return 1.20
    if ratio < cfg.volume_scale_conservative:
        return 1.15
    return 1.10


def _compute_tp(df: pd.DataFrame, horizon_h: int, multiplier: float) -> tuple[float, list[str]]:
    """TP = медиана положительных ходов close[t+H]/close[t]-1, умноженная на multiplier."""
    rationale: list[str] = []
    close = df["close"].to_numpy(dtype=float)
    n = len(close)
    moves = np.full(n, np.nan)
    for i in range(n - horizon_h):
        moves[i] = (close[i + horizon_h] - close[i]) / close[i] * 100.0
    positive = moves[moves > 0]
    if positive.size == 0:
        rationale.append("Нет положительных ходов для TP; использован fallback 0.5%")
        return 0.5, rationale
    median_move = float(np.median(positive))
    tp = median_move * multiplier
    rationale.append(f"Медиана положит. хода за {horizon_h}ч = {median_move:.3f}% × {multiplier} → TP={tp:.3f}%")
    return tp, rationale


def recommend_all(
    stats: MultiHorizonStats,
    config: GridConfig,
    current: tuple[CurrentSettings, CurrentSettings],
    horizon_h: int = 168,
    df: pd.DataFrame | None = None,
    historical_stats: Optional[dict] = None,
) -> FullRecommendation:
    """Полная DCA-рекомендация (long/short/TP) для горизонта."""
    h = stats.get(horizon_h)
    rationale: list[str] = []

    long_target = abs(h.long.p95) / 100.0 * config.safety_factor
    short_target = abs(h.short.p95) / 100.0 * config.safety_factor
    rationale.append(
        f"Горизонт {horizon_h}ч: p95 long={h.long.p95:.3f}%, p95 short={h.short.p95:.3f}%; "
        f"safety={config.safety_factor} → target coverage long={long_target:.3f}, short={short_target:.3f}"
    )

    cur_long, cur_short = current

    long_n, long_ps, long_rationale = _search_grid(long_target, config,
                                                    cur_long.orders, cur_long.price_scale)
    short_n, short_ps, short_rationale = _search_grid(short_target, config,
                                                      cur_short.orders, cur_short.price_scale)

    long_vs = _volume_scale_from_tail(abs(h.long.p99), abs(h.long.p95), config)
    short_vs = _volume_scale_from_tail(abs(h.short.p99), abs(h.short.p95), config)

    long_rec = GridRecommendation(
        orders=long_n,
        coverage=long_target,
        price_scale=long_ps,
        volume_scale=long_vs,
        rationale=long_rationale,
    )
    short_rec = GridRecommendation(
        orders=short_n,
        coverage=short_target,
        price_scale=short_ps,
        volume_scale=short_vs,
        rationale=short_rationale,
    )

    if df is not None:
        tp, tp_rationale = _compute_tp(df, config.tp_horizon_h, config.tp_multiplier)
    else:
        tp = 0.5
        tp_rationale = ["OHLCV не передан; TP по умолчанию 0.5%"]

    if tp < config.tp_floor_pct:
        rationale.append(f"WARNING: TP={tp:.3f}% < {config.tp_floor_pct}% — мало для покрытия комиссий")
    if tp > config.tp_ceil_pct:
        rationale.append(f"WARNING: TP={tp:.3f}% > {config.tp_ceil_pct}% — слишком жадно")

    rationale.extend(long_rationale)
    rationale.extend(short_rationale)
    rationale.extend(tp_rationale)

    kelly_fraction = 0.0
    kelly_rationale = ""
    if historical_stats:
        win_rate = historical_stats.get("win_rate", 0.0)
        avg_win = historical_stats.get("avg_win", 0.0)
        avg_loss = historical_stats.get("avg_loss", 0.0)
        kelly_fraction, kelly_rationale = _compute_kelly(win_rate, avg_win, avg_loss)
        rationale.append(kelly_rationale)

    return FullRecommendation(
        long=long_rec,
        short=short_rec,
        tp=tp,
        horizon_used=horizon_h,
        rationale=rationale,
        kelly_fraction=kelly_fraction,
        kelly_rationale=kelly_rationale,
    )

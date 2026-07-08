"""Сравнение эпох (A/B), со сравнением с историей и с config."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from .aggregator import aggregate, PortfolioStats
from .calculator import compute_metrics
from .models import Trade, BotSettingsSnapshot

ZERO = Decimal(0)


@dataclass
class EpochStats:
    key: object
    n: int
    win_rate: float
    avg_pnl: Decimal
    avg_mae: Decimal | None

    def to_dict(self) -> dict:
        return {
            "key": str(self.key),
            "n": self.n,
            "win_rate": self.win_rate,
            "avg_pnl": str(self.avg_pnl),
            "avg_mae": None if self.avg_mae is None else str(self.avg_mae),
        }


@dataclass
class EpochComparison:
    field: str
    epochs: list[EpochStats]
    deltas: list[dict] = field(default_factory=list)


def _field_getter(field_name: str) -> Callable[[Trade], object]:
    """Поддержка bot_long_coverage и других полей (`bot.<attr>`)."""
    aliases = {
        "bot_long_orders": ("bot", "long_orders"),
        "bot_long_coverage": ("bot", "long_coverage"),
        "bot_long_price_scale": ("bot", "long_price_scale"),
        "bot_long_volume_scale": ("bot", "long_volume_scale"),
        "bot_short_orders": ("bot", "short_orders"),
        "bot_short_coverage": ("bot", "short_coverage"),
        "bot_tp": ("bot", "tp"),
        "bot_leverage": ("bot", "leverage"),
        "side": ("side",),
        "symbol": ("symbol",),
    }
    parts = aliases.get(field_name)
    if parts is None:
        raise ValueError(f"Неподдерживаемое поле для группировки: {field_name!r}")

    def getter(t: Trade) -> object:
        obj: object = t
        for p in parts:
            obj = getattr(obj, p)
        return obj
    return getter


def group_by_epoch(trades: list[Trade], field: str) -> dict[object, EpochStats]:
    getter = _field_getter(field)
    groups: dict[object, list[Trade]] = defaultdict(list)
    for t in trades:
        groups[getter(t)].append(t)
    out: dict[object, EpochStats] = {}
    for key, group in groups.items():
        ps = aggregate(group)
        out[key] = EpochStats(
            key=key,
            n=len(group),
            win_rate=ps.win_rate,
            avg_pnl=ps.avg_pnl,
            avg_mae=ps.avg_mae,
        )
    return out


def compare_epochs(trades: list[Trade], field: str) -> EpochComparison:
    epochs = sorted(group_by_epoch(trades, field).values(), key=lambda e: str(e.key))
    deltas: list[dict] = []
    for i in range(1, len(epochs)):
        prev, cur = epochs[i - 1], epochs[i]
        deltas.append({
            "from": str(prev.key),
            "to": str(cur.key),
            "win_rate_delta": cur.win_rate - prev.win_rate,
            "avg_pnl_delta": cur.avg_pnl - prev.avg_pnl,
        })
    return EpochComparison(field=field, epochs=epochs, deltas=deltas)


@dataclass
class ComparisonReport:
    matches_config: bool
    diffs: list[dict] = field(default_factory=list)
    vs_history: dict | None = None


def compare_with_config(trades: list[Trade],
                         current_settings: BotSettingsSnapshot,
                         historical_stats: PortfolioStats | None = None) -> ComparisonReport:
    """Сравнить последние настройки сделок с config."""
    diffs: list[dict] = []
    if not trades:
        return ComparisonReport(matches_config=True, diffs=diffs,
                                  vs_history=_vs_history(historical_stats, None))
    latest = trades[-1]
    bot = latest.bot
    for attr in ("long_orders", "long_coverage", "long_price_scale",
                  "long_volume_scale", "short_orders", "short_coverage",
                  "short_price_scale", "short_volume_scale", "tp", "leverage"):
        cur = getattr(bot, attr)
        cfg = getattr(current_settings, attr)
        if cur != cfg:
            diffs.append({"field": attr, "current": str(cur), "config": str(cfg)})

    vs_history = _vs_history(historical_stats, latest)
    return ComparisonReport(matches_config=not diffs, diffs=diffs, vs_history=vs_history)


def _vs_history(history: PortfolioStats | None, trade: Trade | None) -> dict | None:
    if history is None or trade is None:
        return None
    m = compute_metrics(trade)
    return {
        "trade_pnl_pct": str(m.pnl_pct),
        "history_avg_pnl": str(history.avg_pnl),
        "history_win_rate": history.win_rate,
        "above_avg": m.pnl_pct > history.avg_pnl,
    }
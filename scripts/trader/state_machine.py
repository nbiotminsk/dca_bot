"""Конечный автомат (State Machine) мониторинга торговых позиций.

Точка входа: process_monitor_step().
Логика состояний вынесена в отдельные модули:
  - grid_states.py      — TRAILING, AWAITING_MAJOR_0382, O1_FILLED, O2_FILLED, O3_FILLED
  - reclaim_states.py   — AWAITING_BREAK_BELOW, AWAITING_SWEEP_CLOSE, SWEEP_RECLAIM_ACTIVE
  - manipulation_states.py — MANIPULATION_ACTIVE
  - idle_scanner.py     — IDLE + is_peer_layer_active
"""

from typing import Optional

from rich.console import Console

from indicators.pybit_client import BybitClient
from scripts.trader.config import TradeConfig
from scripts.trader.models import ActiveTradeMonitor
from scripts.trader.trade_journal import (
    save_completed_impulse as _real_save_completed_impulse,
)

from scripts.trader.grid_states import (
    handle_trailing,
    handle_awaiting_major_0382,
    handle_o1_filled,
    handle_o2_filled,
    handle_o3_filled,
)
from scripts.trader.reclaim_states import (
    handle_awaiting_break_below,
    handle_awaiting_sweep_close,
    handle_sweep_reclaim_active,
)
from scripts.trader.manipulation_states import handle_manipulation_active
from scripts.trader.idle_scanner import handle_idle, is_peer_layer_active


def save_completed_impulse(*args, **kwargs):
    import sys
    bt = sys.modules.get("scripts.bybit_trader")
    fn = getattr(bt, "save_completed_impulse", None) if bt else None
    if fn is not None and fn is not save_completed_impulse:
        return fn(*args, **kwargs)
    return _real_save_completed_impulse(*args, **kwargs)


console = Console()


def process_monitor_step(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list[ActiveTradeMonitor]] = None,
) -> None:
    """Выполняет один шаг конечного автомата (State Machine) для заданной монеты."""
    if m.state == "TRAILING":
        handle_trailing(m, client, cfg, interval, is_live, all_monitors)

    elif m.state == "AWAITING_MAJOR_0382":
        handle_awaiting_major_0382(m, client, cfg, interval, is_live, all_monitors)

    elif m.state == "AWAITING_BREAK_BELOW":
        handle_awaiting_break_below(m, client, cfg, interval, is_live, all_monitors)

    elif m.state == "O1_FILLED":
        handle_o1_filled(m, client, cfg, interval, is_live, all_monitors)

    elif m.state in ("O2_FILLED", "BOTH_FILLED"):
        handle_o2_filled(m, client, cfg, interval, is_live, all_monitors)

    elif m.state == "O3_FILLED":
        handle_o3_filled(m, client, cfg, interval, is_live, all_monitors)

    elif m.state == "AWAITING_SWEEP_CLOSE":
        handle_awaiting_sweep_close(m, client, cfg, interval, is_live, all_monitors)

    elif m.state == "SWEEP_RECLAIM_ACTIVE":
        handle_sweep_reclaim_active(m, client, cfg, interval, is_live, all_monitors)

    elif m.state == "MANIPULATION_ACTIVE":
        handle_manipulation_active(m, client, cfg, interval, is_live, all_monitors)

    elif m.state == "IDLE":
        handle_idle(m, client, cfg, interval, is_live, all_monitors)

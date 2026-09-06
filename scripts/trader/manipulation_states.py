"""Обработчик состояния MANIPULATION_ACTIVE: активная сетка манипуляции."""

from typing import Optional

import pandas as pd
from rich.console import Console

from indicators.pybit_client import BybitClient
from scripts.trader.config import TradeConfig
from scripts.trader.models import ActiveTradeMonitor
from scripts.trader.order_manager import cancel_monitor_orders

console = Console()


def _save_completed_impulse(*args, **kwargs):
    """Делегирует вызов к save_completed_impulse из state_machine (поддержка monkey-patching)."""
    import sys
    sm = sys.modules.get("scripts.trader.state_machine")
    fn = getattr(sm, "save_completed_impulse", None) if sm else None
    if fn is not None:
        return fn(*args, **kwargs)
    from scripts.trader.trade_journal import save_completed_impulse as _real
    return _real(*args, **kwargs)


def handle_manipulation_active(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние MANIPULATION_ACTIVE: активная сетка манипуляции."""
    pos = client.get_position(m.symbol, "Buy") if is_live else None
    pos_size = float(pos.get("size", 0.0)) if pos else 0.0

    if pos_size > 0:
        m.position_was_open = True
        # Проверяем налитие 2-го ордера (1.618 Fib) для переноса корзины в TP = 1.414
        if m.has_o2 and m.q2 > 0 and pos_size >= (m.q1 + 0.5 * m.q2):
            if not m.tp_basket_applied:
                console.print(f"\n[bold green]⚡ [{m.symbol}] Налиты оба ордера манипуляции (1.414 и 1.618)! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим общий Take-Profit корзины на уровень 1.414 Fib (${m.cur_e1})...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_e1, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
                        if "not modified" in str(err).lower() or "34040" in str(err):
                            m.tp_basket_applied = True
                        else:
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP корзины на 1.414: {err}")
                else:
                    m.tp_basket_applied = True
        return

    if m.position_was_open and pos_size == 0:
        console.print(f"\n[bold green]🏁 [{m.symbol}] Сетка Манипуляции закрыта (TP или SL). Работа с данной Фибоначчи полностью завершена.[/bold green]")
        if is_live:
            cancel_monitor_orders(client, m)
        _save_completed_impulse({
            "symbol": m.symbol,
            "peak_price": m.cur_peak,
            "imp_start_price": m.imp_start_price,
            "imp_start_time": str(m.imp_start_time) if m.imp_start_time else "",
            "imp_end_time": str(m.imp_end_time) if m.imp_end_time else "",
            "exit_price": m.cur_tp1,
            "exit_time": str(pd.Timestamp.now(tz="UTC")),
            "exit_reason": "MANIPULATION_CLOSED",
            "layer": m.layer,
        })
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            m.position_was_open = False
            console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
            return
        m.state = "IDLE"
        m.last_skipped_imp_time = m.imp_end_time
        m.position_was_open = False

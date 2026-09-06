"""Обработчики состояний сетки: TRAILING, AWAITING_MAJOR_0382, O1_FILLED, O2_FILLED, O3_FILLED."""

from typing import TYPE_CHECKING, Any, Optional

import pandas as pd
from rich.console import Console

from indicators.pybit_client import BybitClient
from scripts.backtest_strategy_interactive import calc_fib
from scripts.trader.config import TradeConfig
from scripts.trader.models import ActiveTradeMonitor
from scripts.trader.order_manager import (
    cancel_monitor_orders,
    is_entry_missed,
    make_order_link_id,
)

if TYPE_CHECKING:
    pass

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


def handle_trailing(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние TRAILING: трейлинг сетки за новыми максимумами."""
    pos = client.get_position(m.symbol, "Buy") if is_live else None
    pos_size = float(pos.get("size", 0.0)) if pos else 0.0

    # Проверка тайм-аута свежести для незаполненной сетки в режиме TRAILING
    effective_timeout = cfg.major_timeout_hours if m.layer == "major" else cfg.timeout_hours
    if pos_size == 0 and effective_timeout > 0 and m.imp_end_time is not None:
        now_ts = pd.Timestamp.now(tz="UTC")
        imp_ts = pd.to_datetime(m.imp_end_time, utc=True)
        elapsed_hours = (now_ts - imp_ts).total_seconds() / 3600.0
        if elapsed_hours > effective_timeout:
            layer_log = "[MAJOR]" if m.layer == "major" else "[MINOR]"
            console.print(f"\n[bold yellow]⏰ [{m.symbol}] {layer_log} Истек тайм-аут свежести импульса ({elapsed_hours:.1f}ч > {effective_timeout}ч без коррекции к 0.500).[/bold yellow]")
            console.print(f"  ➜ Снимаем ордера сетки и переводим {m.symbol} в режим ожидания нового импульса (IDLE).")
            if is_live:
                cancel_monitor_orders(client, m)
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                console.print(f"🏁 [{m.symbol}] Тайм-аут сетки в режиме Close-Only. Монета завершает работу.")
                return
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time
            m.o1_id = None
            m.o2_id = None
            m.o3_id = None
            m.has_o2 = False
            m.has_o3 = False
            return

    if pos_size > 0:
        # Если o1_id выставлен, проверяем, не остался ли он еще открытым в стакане
        o1_still_open = False
        if m.o1_id and hasattr(client, "get_open_orders"):
            try:
                open_ords = client.get_open_orders(m.symbol)
                open_ids = {o.get("orderId") for o in open_ords}
                if m.o1_id in open_ids:
                    o1_still_open = True
            except Exception:
                pass

        if not o1_still_open:
            m.position_was_open = True
            # Проверяем, налило ли сразу 3 ордера, 2 ордера или 1 ордер
            if m.has_o3 and m.q3 > 0 and pos_size >= (m.q1 + m.q2 + 0.5 * m.q3):
                m.state = "O3_FILLED"
                m.tp_basket_applied = False
                console.print(f"\n[bold green]⚡ [{m.symbol}] Налиты все 3 ордера (0.500, 0.618, 0.786)! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
                        if "not modified" in str(err).lower() or "34040" in str(err):
                            m.tp_basket_applied = True
                        else:
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
            elif m.has_o2 and m.q2 > 0 and pos_size >= (m.q1 + 0.5 * m.q2):
                m.state = "O2_FILLED"
                m.tp_basket_applied = False
                console.print(f"\n[bold green]⚡ [{m.symbol}] Налиты 2 ордера (0.500 и 0.618)! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.382 Fib (${m.cur_tp2}). Ордер 3 в стакане (${m.cur_e3})...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp2, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
                        if "not modified" in str(err).lower() or "34040" in str(err):
                            m.tp_basket_applied = True
                        else:
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.382: {err}")
            else:
                m.state = "O1_FILLED"
                console.print(f"\n[bold cyan]🎉 [{m.symbol}] Ордер 1 (0.500) вошел в позицию! Объем: {pos_size}.[/bold cyan]")
                console.print(f"  ➜ Тейк-профит на 0.236 Fib (${m.cur_tp1}). Ордер 2 (${m.cur_e2}) и Ордер 3 (${m.cur_e3}) активны в стакане.")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp1, stop_loss=m.sl)
                        m.tp_basket_applied = True
                        console.print(f"  ✓ [{m.symbol}] Take-Profit (${m.cur_tp1}) и Stop-Loss (${m.sl}) установлены внутри сделки (Position TP/SL).")
                    except Exception as err:
                        if "not modified" in str(err).lower() or "34040" in str(err):
                            m.tp_basket_applied = True
                            console.print(f"  ✓ [{m.symbol}] Take-Profit (${m.cur_tp1}) и Stop-Loss (${m.sl}) уже активны внутри сделки.")
                        else:
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка установки Position TP/SL: {err}")
                return

    # Если позиция еще не открыта — сдвигаем сетку за новыми максимумами
    df_now = client.fetch_klines(m.symbol, interval=interval, limit=10)
    if len(df_now) == 0:
        return
    latest_h = float(df_now["high"].iloc[-1])

    if latest_h > m.cur_peak:
        new_peak = latest_h
        new_pct = (new_peak - m.imp_start_price) / m.imp_start_price * 100.0

        new_e1 = client.round_price(
            calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale)
            * (1.0 + cfg.entry_buffer_0500_pct / 100.0), m.symbol
        )
        new_tp1 = client.round_price(
            calc_fib(new_peak, m.imp_start_price, 0.236, is_long=True, scale=cfg.scale)
            * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol
        )
        new_e2 = client.round_price(
            calc_fib(new_peak, m.imp_start_price, 0.618, is_long=True, scale=cfg.scale)
            * (1.0 + cfg.entry_buffer_0618_pct / 100.0), m.symbol
        ) if m.has_o2 else None
        new_tp2 = client.round_price(
            calc_fib(new_peak, m.imp_start_price, 0.382, is_long=True, scale=cfg.scale)
            * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol
        ) if m.has_o2 else None

        new_e3 = client.round_price(
            calc_fib(new_peak, m.imp_start_price, 0.786, is_long=True, scale=cfg.scale)
            * (1.0 + cfg.entry_buffer_0786_pct / 100.0), m.symbol
        ) if m.has_o3 else None
        new_tp3 = client.round_price(
            calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale)
            * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol
        ) if m.has_o3 else None

        # Сдвигаем Ордер 1
        if new_e1 != m.cur_e1 or new_tp1 != m.cur_tp1:
            console.print(f"\n[bold green]🚀 [{m.symbol}] Новый максимум ${new_peak} (+{new_pct:.2f}%). Сдвигаем уровни...[/bold green]")
            if is_live and m.o1_id:
                try:
                    client.amend_order(m.symbol, m.o1_id, price=new_e1, take_profit=new_tp1, stop_loss=m.sl)
                    m.cur_e1 = new_e1
                    m.cur_tp1 = new_tp1
                    console.print(f"  ✓ [{m.symbol}] Ордер 1 сдвинут: Вход ${new_e1}, TP ${new_tp1}")
                except Exception as err:
                    if "order not modified" not in str(err).lower():
                        console.print(f"  ⚠️ [{m.symbol}] Не удалось изменить Ордер 1: {err}")
            else:
                m.cur_e1 = new_e1
                m.cur_tp1 = new_tp1

        # Сдвигаем Ордер 2
        if m.has_o2 and new_e2 and new_tp2 and (new_e2 != m.cur_e2 or new_tp2 != m.cur_tp2):
            if is_live and m.o2_id:
                try:
                    client.amend_order(m.symbol, m.o2_id, price=new_e2, take_profit=new_tp2, stop_loss=m.sl)
                    m.cur_e2 = new_e2
                    m.cur_tp2 = new_tp2
                    console.print(f"  ✓ [{m.symbol}] Ордер 2 сдвинут: Вход ${new_e2}, TP ${new_tp2}")
                except Exception as err:
                    if "order not modified" not in str(err).lower():
                        console.print(f"  ⚠️ [{m.symbol}] Не удалось изменить Ордер 2: {err}")
            else:
                m.cur_e2 = new_e2
                m.cur_tp2 = new_tp2

        # Сдвигаем Ордер 3
        if m.has_o3 and new_e3 and new_tp3 and (new_e3 != m.cur_e3 or new_tp3 != m.cur_tp3):
            if is_live and m.o3_id:
                try:
                    client.amend_order(m.symbol, m.o3_id, price=new_e3, take_profit=new_tp3, stop_loss=m.sl)
                    m.cur_e3 = new_e3
                    m.cur_tp3 = new_tp3
                    console.print(f"  ✓ [{m.symbol}] Ордер 3 сдвинут: Вход ${new_e3}, TP ${new_tp3}")
                except Exception as err:
                    if "order not modified" not in str(err).lower():
                        console.print(f"  ⚠️ [{m.symbol}] Не удалось изменить Ордер 3: {err}")
            else:
                m.cur_e3 = new_e3
                m.cur_tp3 = new_tp3

        m.cur_peak = new_peak
        m.imp_end_time = pd.Timestamp.now(tz="UTC")


def handle_awaiting_major_0382(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние AWAITING_MAJOR_0382: ожидание пробоя 0.382 (большая фиба)."""
    if m.close_only:
        m.state = "FINISHED"
        m.done = True
        return

    df_now = client.fetch_klines(m.symbol, interval=interval, limit=15)
    if len(df_now) == 0:
        return

    latest_h = float(df_now["high"].iloc[-1])
    latest_l = float(df_now["low"].iloc[-1])

    # 1. Трейлинг вершины: если цена обновила максимум
    if latest_h > m.cur_peak:
        new_peak = latest_h
        m.cur_peak = new_peak
        m.p_0382 = calc_fib(new_peak, m.imp_start_price, 0.382, is_long=True, scale=cfg.scale)
        m.cur_e1 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale) * (1.0 + cfg.entry_buffer_0500_pct / 100.0), m.symbol)
        m.cur_tp1 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.236, is_long=True, scale=cfg.scale) * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol)
        m.cur_e2 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.618, is_long=True, scale=cfg.scale) * (1.0 + cfg.entry_buffer_0618_pct / 100.0), m.symbol) if m.has_o2 else None
        m.cur_tp2 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.382, is_long=True, scale=cfg.scale) * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol) if m.has_o2 else None
        m.cur_e3 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.786, is_long=True, scale=cfg.scale) * (1.0 + cfg.entry_buffer_0786_pct / 100.0), m.symbol) if m.has_o3 else None
        m.cur_tp3 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale) * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol) if m.has_o3 else None
        m.imp_end_time = pd.Timestamp.now(tz="UTC")
        console.print(f"📈 [{m.symbol}] [MAJOR] Новый максимум ${new_peak}! Уровень 0.382 скорректирован до ${m.p_0382:.4f}.")

    # 2. Проверка тайм-аута свежести:
    if cfg.major_timeout_hours > 0 and m.imp_end_time is not None:
        now_ts = pd.Timestamp.now(tz="UTC")
        imp_ts = pd.to_datetime(m.imp_end_time, utc=True)
        elapsed_hours = (now_ts - imp_ts).total_seconds() / 3600.0
        if elapsed_hours > cfg.major_timeout_hours:
            console.print(f"\n[bold yellow]⏰ [{m.symbol}] [MAJOR] Истек тайм-аут свежести ({elapsed_hours:.1f}ч > {cfg.major_timeout_hours}ч без отката к 0.382). Переход в IDLE.[/bold yellow]")
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time
            return

    # 3. Проверка пробоя уровня 0.382 (Long: low <= p_0382)
    if m.p_0382 is not None and latest_l <= m.p_0382:
        console.print(f"\n[bold green]🎯 [{m.symbol}] [MAJOR] Цена (${latest_l}) пробила/коснулась уровня 0.382 (${m.p_0382:.4f})![/bold green]")
        if cfg.mutual_exclusion:
            from scripts.trader.idle_scanner import is_peer_layer_active
            is_active, peer_desc = is_peer_layer_active(m.symbol, m.layer, all_monitors, client, is_live)
            if is_active:
                console.print(f"  [yellow]🔒 [{m.symbol}] [MAJOR] Взаимное исключение (Priority Lock): на монете активен {peer_desc}. Выставление сетки Major заблокировано до завершения Minor.[/yellow]")
                return

        cur_p = client.get_ticker_price(m.symbol) if hasattr(client, "get_ticker_price") else latest_l
        if cur_p <= 0:
            cur_p = latest_l
        if is_entry_missed(m.cur_e1, cur_p, is_long=True):
            console.print(f"  [yellow]⚠️ [{m.symbol}] [MAJOR] Вход на 0.500 (${m.cur_e1}) уже упущен (рыночная цена ${cur_p} <= ${m.cur_e1}). Полностью пропускаем сетап по этой монете до появления нового импульса.[/yellow]")
            m.last_skipped_imp_time = m.imp_end_time or (df_now["timestamp"].iloc[-1] if len(df_now) > 0 else None)
            m.state = "IDLE"
            return

        console.print("  ➜ Большая фиба АКТИВИРОВАНА. Выставляем тройную сетку в стакан Bybit...")
        m.touched_0382 = True

        sym_short = m.symbol.replace("USDT.P", "").replace("USDT", "")
        layer_tag = "MAJ"
        setup_risk = cfg.major_risk_usd

        if m.cur_e3 and m.cur_e2:
            q1, q2, q3, _, _, _ = client.calc_triple_grid_order_sizes(
                m.cur_e1, m.cur_e2, m.cur_e3, m.sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=False, weights=cfg.grid_weights,
                is_long=(m.side == "long"), fee_maker_pct=cfg.fee_maker_pct, fee_taker_pct=cfg.fee_taker_pct, slippage_buffer_pct=cfg.slippage_buffer_pct,
            )
        elif m.cur_e2:
            q1, q2, _, _ = client.calc_dual_grid_order_sizes(
                m.cur_e1, m.cur_e2, m.sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=True,
                is_long=(m.side == "long"), fee_maker_pct=cfg.fee_maker_pct, fee_taker_pct=cfg.fee_taker_pct, slippage_buffer_pct=cfg.slippage_buffer_pct,
            )
            q3 = 0.0
        else:
            fee_open = cfg.fee_maker_pct / 100.0
            fee_close = cfg.fee_taker_pct / 100.0
            slip = cfg.slippage_buffer_pct / 100.0
            worst_sl = m.sl * (1.0 - slip) if m.side == "long" else m.sl * (1.0 + slip)
            unit_loss1 = abs(m.cur_e1 - worst_sl) + m.cur_e1 * fee_open + worst_sl * fee_close
            raw_q1 = setup_risk / unit_loss1 if unit_loss1 > 0 else 0.0
            q1 = client.round_qty(raw_q1, m.symbol) if raw_q1 > 0 else 0.0
            q2 = 0.0
            q3 = 0.0

        specs = client.get_specs(m.symbol)
        if q1 <= 0 or (specs.min_notional > 0 and q1 * m.cur_e1 < specs.min_notional):
            console.print(f"  [yellow]⚠️ [{m.symbol}] [MAJOR] Расчетный объем ({q1 * m.cur_e1:.2f}) ниже minNotional (${specs.min_notional}) при лимите риска ${setup_risk:.2f}. Сделка пропущена для защиты лимита риска.[/yellow]")
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time or (df_now["timestamp"].iloc[-1] if len(df_now) > 0 else None)
            return

        m.q1, m.q2, m.q3 = q1, q2, q3
        m.has_o2 = (m.cur_e2 is not None and q2 > 0)
        m.has_o3 = (m.cur_e3 is not None and q3 > 0)

        o1_id, o2_id, o3_id = None, None, None
        if is_live:
            # Проверка маржи перед размещением сетки
            if hasattr(client, "get_available_balance") and hasattr(client, "calc_required_margin"):
                avail_m = client.get_available_balance()
                req_m = client.calc_required_margin(m.symbol, q1, m.cur_e1)
                if m.cur_e2 and q2 > 0:
                    req_m += client.calc_required_margin(m.symbol, q2, m.cur_e2)
                if m.cur_e3 and q3 > 0:
                    req_m += client.calc_required_margin(m.symbol, q3, m.cur_e3)
                if avail_m < req_m * 1.05:
                    console.print(f"[yellow]⏸️ [{m.symbol}] [MAJOR] Недостаточно свободной маржи (${avail_m:.2f} < ${req_m * 1.05:.2f}). Откладываем выставление сетки.[/yellow]")
                    return

            placed_attempt_ids = []
            try:
                r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1, price=m.cur_e1, take_profit=m.cur_tp1, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O1"))
                o1_id = r1.get("orderId")
                placed_attempt_ids.append(o1_id)
                if m.cur_e2 and q2 > 0 and m.cur_tp2:
                    r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2, price=m.cur_e2, take_profit=m.cur_tp2, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O2"))
                    o2_id = r2.get("orderId")
                    placed_attempt_ids.append(o2_id)
                if m.cur_e3 and q3 > 0 and m.cur_tp3:
                    r3 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q3, price=m.cur_e3, take_profit=m.cur_tp3, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O3"))
                    o3_id = r3.get("orderId")
                    placed_attempt_ids.append(o3_id)
                console.print(f"  ✓ [{m.symbol}] [MAJOR] Размещена сетка: Вход 1 ${m.cur_e1}, Вход 2 ${m.cur_e2 or '-'}, Вход 3 ${m.cur_e3 or '-'}")
            except Exception as err:
                console.print(f"[red]❌ [{m.symbol}] [MAJOR] Ошибка размещения сетки: {err}[/red]")
                for cancel_oid in placed_attempt_ids:
                    if cancel_oid:
                        try:
                            client.cancel_order(m.symbol, cancel_oid)
                            console.print(f"[yellow][{m.symbol}] [MAJOR] Откачен ордер {cancel_oid}.[/yellow]")
                        except Exception:
                            pass
                return

        m.o1_id = o1_id
        m.o2_id = o2_id
        m.o3_id = o3_id
        m.state = "TRAILING"
        return


def handle_o1_filled(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние O1_FILLED: ожидание Ордера 2/3 или тейк-профита 0.236."""
    pos = client.get_position(m.symbol, "Buy") if is_live else None
    pos_size = float(pos.get("size", 0.0)) if pos else 0.0

    if pos_size > 0:
        m.position_was_open = True
        # Проверяем, налился ли Ордер 3 или Ордер 2 при проливе
        if m.has_o3 and m.q3 > 0 and pos_size >= (m.q1 + m.q2 + 0.5 * m.q3):
            m.state = "O3_FILLED"
            m.tp_basket_applied = False
            console.print(f"\n[bold green]🎯 [{m.symbol}] Глубокий пролив: исполнены Ордера 2 и 3! Позиция: {pos_size}.[/bold green]")
            console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
            if is_live:
                try:
                    client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                    m.tp_basket_applied = True
                except Exception as err:
                    if "not modified" in str(err).lower() or "34040" in str(err):
                        m.tp_basket_applied = True
                    else:
                        console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
        elif m.has_o2 and m.q2 > 0 and pos_size >= (m.q1 + 0.5 * m.q2):
            m.state = "O2_FILLED"
            m.tp_basket_applied = False
            console.print(f"\n[bold green]🎯 [{m.symbol}] Добор: Ордер 2 (0.618) исполнен! Позиция: {pos_size}.[/bold green]")
            console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.382 Fib (${m.cur_tp2}). Ордер 3 (${m.cur_e3}) активен в стакане...")
            if is_live:
                try:
                    client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp2, stop_loss=m.sl)
                    m.tp_basket_applied = True
                except Exception as err:
                    if "not modified" in str(err).lower() or "34040" in str(err):
                        m.tp_basket_applied = True
                    else:
                        console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.382: {err}")
        return

    # Если pos_size == 0 — проверяем тейк или стоп
    df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
    latest_h = float(df_now["high"].iloc[-1])
    latest_l = float(df_now["low"].iloc[-1])
    is_long = (m.side == "long")

    hit_tp = (latest_h >= m.cur_tp1 * 0.999) if is_long else (latest_l <= m.cur_tp1 * 1.001)
    hit_sl = (latest_l <= m.sl) if is_long else (latest_h >= m.sl)

    if hit_tp:
        console.print(f"\n[bold green]💰 [{m.symbol}] ТЕЙК-ПРОФИТ 0.236 ДОСТИГНУТ! Позиция закрыта в прибыль.[/bold green]")
        if is_live:
            cancelled = cancel_monitor_orders(client, m)
            console.print(f"[dim][{m.symbol}] Сняты висящие ордера 2 и 3 [отменено: {len(cancelled)}]. Сделка успешно завершена.[/dim]")
        _save_completed_impulse({
            "symbol": m.symbol,
            "peak_price": m.cur_peak,
            "imp_start_price": m.imp_start_price,
            "imp_start_time": str(m.imp_start_time) if m.imp_start_time else "",
            "imp_end_time": str(m.imp_end_time) if m.imp_end_time else "",
            "exit_price": m.cur_tp1,
            "exit_time": str(pd.Timestamp.now(tz="UTC")),
            "exit_reason": "TP_0236",
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
        return
    elif hit_sl and m.sl > 0:
        console.print(f"\n[bold red]🛑 [{m.symbol}] Стоп-лосс на уровне 1.000 (${m.sl}) сработал![/bold red]")
        if is_live:
            cancel_monitor_orders(client, m)
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            m.position_was_open = False
            console.print(f"[bold red]🏁 [{m.symbol}] Сделка закрыта по стоп-лоссу. Монета находилась в режиме Close-Only и завершает работу.[/bold red]")
            return
        m.stop_bar_time = df_now["timestamp"].iloc[-1]
        m.stop_sweep_low = min(latest_l, m.sl) if is_long else max(latest_h, m.sl)
        m.state = "AWAITING_SWEEP_CLOSE"
        m.position_was_open = False
        console.print(f"[bold yellow]⏳ [{m.symbol}] Ожидаем закрытия часовой свечи ({m.stop_bar_time}) для проверки Ложного пробоя или Сетки манипуляции...[/bold yellow]")
        return
    elif m.position_was_open:
        # Позиция закрыта пользователем вручную (цена не доходила до SL 1.000)
        latest_c = float(df_now["close"].iloc[-1])
        console.print(f"\n[bold cyan]👋 [{m.symbol}] Позиция закрыта вручную пользователем (цена ${latest_c:.4f}, SL был на ${m.sl:.4f}).[/bold cyan]")
        if is_live:
            cancelled = cancel_monitor_orders(client, m)
            console.print(f"[dim][{m.symbol}] Сняты висящие ордера сетки [отменено: {len(cancelled)}]. Сделка завершена.[/dim]")
        _save_completed_impulse({
            "symbol": m.symbol,
            "peak_price": m.cur_peak,
            "imp_start_price": m.imp_start_price,
            "imp_start_time": str(m.imp_start_time) if m.imp_start_time else "",
            "imp_end_time": str(m.imp_end_time) if m.imp_end_time else "",
            "exit_price": latest_c,
            "exit_time": str(pd.Timestamp.now(tz="UTC")),
            "exit_reason": "MANUAL_CLOSE",
            "layer": m.layer,
        })
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            m.position_was_open = False
            console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта вручную. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
            return
        m.state = "IDLE"
        m.last_skipped_imp_time = m.imp_end_time
        m.position_was_open = False
        return
    else:
        # Ордер 1 пропущен при старте, Ордер 2 и 3 в стакане ждут налития
        return


def handle_o2_filled(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние O2_FILLED / BOTH_FILLED: выход на 0.382 или добор 3-го ордера."""
    pos = client.get_position(m.symbol, "Buy") if is_live else None
    pos_size = float(pos.get("size", 0.0)) if pos else 0.0

    if pos_size > 0:
        m.position_was_open = True
        # Проверяем, налился ли Ордер 3 (0.786)
        if m.has_o3 and m.q3 > 0 and pos_size >= (m.q1 + m.q2 + 0.5 * m.q3):
            m.state = "O3_FILLED"
            m.tp_basket_applied = False
            console.print(f"\n[bold green]🎯 [{m.symbol}] Добор: Ордер 3 (0.786) исполнен! Позиция: {pos_size}.[/bold green]")
            console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
            if is_live:
                try:
                    client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                    m.tp_basket_applied = True
                except Exception as err:
                    if "not modified" in str(err).lower() or "34040" in str(err):
                        m.tp_basket_applied = True
                    else:
                        console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
        elif not m.tp_basket_applied and is_live:
            try:
                client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp2, stop_loss=m.sl)
                m.tp_basket_applied = True
            except Exception as err:
                if "not modified" in str(err).lower() or "34040" in str(err):
                    m.tp_basket_applied = True
                else:
                    console.print(f"  ⚠️ [{m.symbol}] Ошибка установки TP на 0.382: {err}")
        return

    # Если pos_size == 0 — проверяем тейк или стоп
    df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
    latest_h = float(df_now["high"].iloc[-1])
    latest_l = float(df_now["low"].iloc[-1])
    is_long = (m.side == "long")

    hit_tp = (latest_h >= m.cur_tp2 * 0.999) if is_long else (latest_l <= m.cur_tp2 * 1.001)
    hit_sl = (latest_l <= m.sl) if is_long else (latest_h >= m.sl)

    if hit_tp:
        console.print(f"\n[bold green]💰 [{m.symbol}] КОРЗИННЫЙ ТЕЙК-ПРОФИТ 0.382 ДОСТИГНУТ! Ордера 1 и 2 закрыты в плюс.[/bold green]")
        if is_live:
            cancelled = cancel_monitor_orders(client, m)
            console.print(f"[dim][{m.symbol}] Снят висящий Ордер 3 (0.786) [отменено: {len(cancelled)}].[/dim]")
        _save_completed_impulse({
            "symbol": m.symbol,
            "peak_price": m.cur_peak,
            "imp_start_price": m.imp_start_price,
            "imp_start_time": str(m.imp_start_time) if m.imp_start_time else "",
            "imp_end_time": str(m.imp_end_time) if m.imp_end_time else "",
            "exit_price": m.cur_tp2,
            "exit_time": str(pd.Timestamp.now(tz="UTC")),
            "exit_reason": "TP_0382",
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
        return
    elif hit_sl and m.sl > 0:
        console.print(f"\n[bold red]🛑 [{m.symbol}] Стоп-лосс на уровне 1.000 (${m.sl}) сработал![/bold red]")
        if is_live:
            cancel_monitor_orders(client, m)
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            m.position_was_open = False
            console.print(f"[bold red]🏁 [{m.symbol}] Сделка закрыта по стоп-лоссу. Монета находилась в режиме Close-Only и завершает работу.[/bold red]")
            return
        m.stop_bar_time = df_now["timestamp"].iloc[-1]
        m.stop_sweep_low = min(latest_l, m.sl) if is_long else max(latest_h, m.sl)
        m.state = "AWAITING_SWEEP_CLOSE"
        m.position_was_open = False
        console.print(f"[bold yellow]⏳ [{m.symbol}] Ожидаем закрытия часовой свечи ({m.stop_bar_time}) для проверки Ложного пробоя или Сетки манипуляции...[/bold yellow]")
        return
    elif m.position_was_open:
        # Позиция закрыта пользователем вручную (цена не доходила до SL 1.000)
        latest_c = float(df_now["close"].iloc[-1])
        console.print(f"\n[bold cyan]👋 [{m.symbol}] Позиция закрыта вручную пользователем (цена ${latest_c:.4f}, SL был на ${m.sl:.4f}).[/bold cyan]")
        if is_live:
            cancelled = cancel_monitor_orders(client, m)
            console.print(f"[dim][{m.symbol}] Снят висящий Ордер 3 (0.786) [отменено: {len(cancelled)}]. Сделка завершена.[/dim]")
        _save_completed_impulse({
            "symbol": m.symbol,
            "peak_price": m.cur_peak,
            "imp_start_price": m.imp_start_price,
            "imp_start_time": str(m.imp_start_time) if m.imp_start_time else "",
            "imp_end_time": str(m.imp_end_time) if m.imp_end_time else "",
            "exit_price": latest_c,
            "exit_time": str(pd.Timestamp.now(tz="UTC")),
            "exit_reason": "MANUAL_CLOSE",
            "layer": m.layer,
        })
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            m.position_was_open = False
            console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта вручную. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
            return
        m.state = "IDLE"
        m.last_skipped_imp_time = m.imp_end_time
        m.position_was_open = False
        return
    else:
        return


def handle_o3_filled(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние O3_FILLED: выход всей тройки на 0.500."""
    pos = client.get_position(m.symbol, "Buy") if is_live else None
    pos_size = float(pos.get("size", 0.0)) if pos else 0.0

    if pos_size > 0:
        m.position_was_open = True
        if not m.tp_basket_applied and is_live:
            try:
                client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                m.tp_basket_applied = True
            except Exception as err:
                if "not modified" in str(err).lower() or "34040" in str(err):
                    m.tp_basket_applied = True
                else:
                    console.print(f"  ⚠️ [{m.symbol}] Ошибка установки TP на 0.500: {err}")
        return

    # Если pos_size == 0 — проверяем тейк или стоп
    df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
    latest_h = float(df_now["high"].iloc[-1])
    latest_l = float(df_now["low"].iloc[-1])
    is_long = (m.side == "long")

    hit_tp = (latest_h >= m.cur_tp3 * 0.999) if is_long else (latest_l <= m.cur_tp3 * 1.001)
    hit_sl = (latest_l <= m.sl) if is_long else (latest_h >= m.sl)

    if hit_tp:
        console.print(f"\n[bold green]💰 [{m.symbol}] СУПЕР-ТЕЙК-ПРОФИТ 0.500 ДОСТИГНУТ! Все 3 ордера закрыты (Ордер 3 в макси-плюс, Ордер 2 в плюс, Ордер 1 в БУ).[/bold green]")
        if is_live:
            cancel_monitor_orders(client, m)
        _save_completed_impulse({
            "symbol": m.symbol,
            "peak_price": m.cur_peak,
            "imp_start_price": m.imp_start_price,
            "imp_start_time": str(m.imp_start_time) if m.imp_start_time else "",
            "imp_end_time": str(m.imp_end_time) if m.imp_end_time else "",
            "exit_price": m.cur_tp3,
            "exit_time": str(pd.Timestamp.now(tz="UTC")),
            "exit_reason": "TP_0500",
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
        return
    elif hit_sl and m.sl > 0:
        console.print(f"\n[bold red]🛑 [{m.symbol}] Стоп-лосс на уровне 1.000 (${m.sl}) сработал![/bold red]")
        if is_live:
            cancel_monitor_orders(client, m)
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            m.position_was_open = False
            console.print(f"[bold red]🏁 [{m.symbol}] Сделка закрыта по стоп-лоссу. Монета находилась в режиме Close-Only и завершает работу.[/bold red]")
            return
        m.stop_bar_time = df_now["timestamp"].iloc[-1]
        m.stop_sweep_low = min(latest_l, m.sl) if is_long else max(latest_h, m.sl)
        m.state = "AWAITING_SWEEP_CLOSE"
        m.position_was_open = False
        console.print(f"[bold yellow]⏳ [{m.symbol}] Ожидаем закрытия часовой свечи ({m.stop_bar_time}) для проверки Ложного пробоя или Сетки манипуляции...[/bold yellow]")
        return
    elif m.position_was_open:
        # Позиция закрыта пользователем вручную (цена не доходила до SL 1.000)
        latest_c = float(df_now["close"].iloc[-1])
        console.print(f"\n[bold cyan]👋 [{m.symbol}] Позиция закрыта вручную пользователем (цена ${latest_c:.4f}, SL был на ${m.sl:.4f}).[/bold cyan]")
        if is_live:
            cancel_monitor_orders(client, m)
        _save_completed_impulse({
            "symbol": m.symbol,
            "peak_price": m.cur_peak,
            "imp_start_price": m.imp_start_price,
            "imp_start_time": str(m.imp_start_time) if m.imp_start_time else "",
            "imp_end_time": str(m.imp_end_time) if m.imp_end_time else "",
            "exit_price": latest_c,
            "exit_time": str(pd.Timestamp.now(tz="UTC")),
            "exit_reason": "MANUAL_CLOSE",
            "layer": m.layer,
        })
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            m.position_was_open = False
            console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта вручную. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
            return
        m.state = "IDLE"
        m.last_skipped_imp_time = m.imp_end_time
        m.position_was_open = False
        return
    else:
        return

"""Обработчики состояний свипа/рекламации: AWAITING_BREAK_BELOW, AWAITING_SWEEP_CLOSE, SWEEP_RECLAIM_ACTIVE."""

from typing import Optional

import pandas as pd
from rich.console import Console

from indicators.macd import calculate_macd
from indicators.pybit_client import BybitClient
from scripts.backtest_strategy_interactive import calc_fib
from scripts.trader.config import TradeConfig
from scripts.trader.models import ActiveTradeMonitor
from scripts.trader.order_manager import (
    cancel_monitor_orders,
    cleanup_orphan_orders_for_layer,
    make_order_link_id,
)

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


def handle_awaiting_break_below(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние AWAITING_BREAK_BELOW: ожидание пробоя 1.000 без возврата к 0.382."""
    if m.close_only:
        m.state = "FINISHED"
        m.done = True
        return

    if is_live:
        cleanup_orphan_orders_for_layer(client, m.symbol, m.layer)

    df_now = client.fetch_klines(m.symbol, interval=interval, limit=15)
    if len(df_now) == 0:
        return

    latest_h = float(df_now["high"].iloc[-1])
    latest_l = float(df_now["low"].iloc[-1])
    is_long = (m.side == "long")

    # 1. Трейлинг вершины: если цена обновила вершину импульса — импульс перебит новым
    if (latest_h > m.cur_peak) if is_long else (latest_l < m.cur_peak):
        console.print(f"📈 [{m.symbol}] [{m.layer.upper()}] Новый экстремум цены. Импульс завершен, переход в IDLE.")
        m.state = "IDLE"
        m.last_skipped_imp_time = df_now["timestamp"].iloc[-1] if len(df_now) > 0 else None
        return

    # 2. Проверка тайм-аута свежести:
    if m.timeout_hours and m.timeout_hours > 0 and m.imp_end_time is not None:
        now_ts = pd.Timestamp.now(tz="UTC")
        imp_ts = pd.to_datetime(m.imp_end_time, utc=True)
        elapsed_hours = (now_ts - imp_ts).total_seconds() / 3600.0
        if elapsed_hours > m.timeout_hours:
            console.print(f"\n[bold yellow]⏰ [{m.symbol}] [{m.layer.upper()}] Истек тайм-аут свежести ({elapsed_hours:.1f}ч > {m.timeout_hours}ч). Переход в IDLE.[/bold yellow]")
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time
            return

    # 3. Если цена вернулась и протестировала 0.382 — отскок завершен, сетап исчерпан!
    tested_0382 = (latest_h >= m.p_0382) if is_long else (latest_l <= m.p_0382)
    if m.p_0382 is not None and tested_0382:
        console.print(f"\n[yellow]ℹ️ [{m.symbol}] [{m.layer.upper()}] Цена вернулась и протестировала 0.382 (${m.p_0382:.4f}). Отскок завершен без пробоя 1.000, переход в IDLE.[/yellow]")
        m.state = "IDLE"
        m.last_skipped_imp_time = m.imp_end_time or (df_now["timestamp"].iloc[-1] if len(df_now) > 0 else None)
        return

    # 4. Если цена упала ниже 1.000 без возврата к 0.382:
    broken_1000 = (latest_l <= m.sl) if is_long else (latest_h >= m.sl)
    if broken_1000:
        console.print(f"\n[bold cyan]⚡ [{m.symbol}] [{m.layer.upper()}] Цена ({latest_l if is_long else latest_h}) пробила 1.000 (${m.sl:.4f}) без возврата к 0.382! Переход в AWAITING_SWEEP_CLOSE...[/bold cyan]")
        m.state = "AWAITING_SWEEP_CLOSE"
        m.stop_sweep_low = min(latest_l, m.sl) if is_long else max(latest_h, m.sl)
        m.stop_bar_time = df_now["timestamp"].iloc[-1]
        return


def handle_awaiting_sweep_close(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние AWAITING_SWEEP_CLOSE: ожидание закрытия свечи свипа 1.000."""
    df_now = client.fetch_klines(m.symbol, interval=interval, limit=15)
    if len(df_now) < 2:
        return
    curr_l = float(df_now["low"].iloc[-1])
    if curr_l < m.stop_sweep_low:
        m.stop_sweep_low = curr_l

    latest_time = df_now["timestamp"].iloc[-1]
    # Свеча закрылась, если время текущей формирующейся свечи больше времени свечи стопа
    if m.stop_bar_time is not None and latest_time <= m.stop_bar_time:
        return

    # Свеча закрылась! Находим закрытую свечу пробоя
    closed_matches = df_now[df_now["timestamp"] == m.stop_bar_time]
    if len(closed_matches) > 0:
        closed_bar = closed_matches.iloc[0]
    else:
        closed_bar = df_now.iloc[-2]

    bar_close = float(closed_bar["close"])
    bar_low = float(closed_bar["low"])
    sweep_low = min(bar_low, m.stop_sweep_low)
    p_1000 = m.imp_start_price
    swp_pct = abs(p_1000 - sweep_low) / p_1000 * 100.0 if p_1000 > 0 else 0.0

    # Расчет индикатора MACD
    macd_df = calculate_macd(df_now["close"])
    hist = macd_df["hist"].values
    macd_div = (len(hist) >= 2 and (hist[-1] > hist[-2] or hist[-1] > -0.01))

    # Проверка условий Варианта 1 (Ложный пробой) vs Варианта 3 (Манипуляция)
    if bar_close >= p_1000 and swp_pct <= cfg.reclaim_max_sweep_pct and macd_div:
        console.print(f"\n[bold green]🟢 [{m.symbol}] ЛОЖНЫЙ ПРОБОЙ ПОДТВЕРЖДЕН (SWEEP RECLAIM)![/bold green]")
        console.print(f"  Закрытие ${bar_close} >= ${p_1000}, свип {swp_pct:.2f}% (<= {cfg.reclaim_max_sweep_pct}%), MACD разворот.")
        reclaim_entry = bar_close
        reclaim_sl = client.round_price(sweep_low * 0.998, m.symbol)
        p_0618 = calc_fib(m.cur_peak, m.imp_start_price, 0.618, is_long=True, scale=cfg.scale)
        reclaim_tp = client.round_price(p_0618 * (1.0 - cfg.reclaim_tp_buffer_pct / 100.0), m.symbol)
        be_trig = client.round_price(calc_fib(m.cur_peak, m.imp_start_price, cfg.reclaim_be_trigger_fib, is_long=True, scale=cfg.scale), m.symbol)
        be_price = client.round_price(reclaim_entry * (1.0 + cfg.reclaim_be_offset_pct / 100.0), m.symbol)
        dist = abs(reclaim_entry - reclaim_sl)
        specs = client.get_specs(m.symbol)
        setup_risk = cfg.major_risk_usd if m.layer == "major" else cfg.minor_risk_usd
        q_reclaim = client.round_qty(setup_risk / dist if dist > 0 else specs.min_qty, m.symbol)
        if q_reclaim < specs.min_qty:
            q_reclaim = specs.min_qty

        if is_live:
            try:
                resp = client.place_order(
                    symbol=m.symbol,
                    side="Buy",
                    order_type="Market",
                    qty=q_reclaim,
                    take_profit=reclaim_tp,
                    stop_loss=reclaim_sl,
                )
                m.o1_id = resp.get("orderId")
                console.print(f"  ✓ Вход по рынку: {q_reclaim} @ ${reclaim_entry}, TP: ${reclaim_tp}, SL: ${reclaim_sl}")
            except Exception as err:
                console.print(f"  ❌ Ошибка входа в Sweep Reclaim: {err}")
                m.state = "IDLE"
                return

        m.state = "SWEEP_RECLAIM_ACTIVE"
        m.cur_e1 = reclaim_entry
        m.cur_tp1 = reclaim_tp
        m.sl = reclaim_sl
        m.be_trigger = be_trig
        m.be_price = be_price
        m.be_applied = False
        m.position_was_open = True
    else:
        # Сетка Манипуляции (Вариант 3)
        reason = f"закрытие свечи ниже 1.000 (${bar_close} < ${p_1000})" if bar_close < p_1000 else f"глубокий свип ({swp_pct:.2f}% > {cfg.reclaim_max_sweep_pct}%)"
        console.print(f"\n[bold magenta]🟣 [{m.symbol}] МАНИПУЛЯЦИЯ ({reason}). ВЫСТАВЛЯЕМ СЕТКУ 1.414 & 1.618...[/bold magenta]")

        p_1414 = calc_fib(m.cur_peak, m.imp_start_price, 1.414, is_long=True, scale=cfg.scale)
        p_1618 = calc_fib(m.cur_peak, m.imp_start_price, 1.618, is_long=True, scale=cfg.scale)
        p_2414 = calc_fib(m.cur_peak, m.imp_start_price, 2.414, is_long=True, scale=cfg.scale)

        e_1414 = client.round_price(p_1414 * (1.0 + cfg.entry_buffer_1414_pct / 100.0), m.symbol)
        e_1618 = client.round_price(p_1618 * (1.0 + cfg.entry_buffer_1618_pct / 100.0), m.symbol)
        tp_1000 = client.round_price(p_1000 * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol)
        sl_2414 = client.round_price(p_2414, m.symbol)

        # На каждый ордер выделяется cfg.manipulation_risk_usd ($2.0), на корзину 2 * manipulation_risk_usd ($4.0)
        q1_m, q2_m, l1, l2 = client.calc_dual_grid_order_sizes(
            e_1414,
            e_1618,
            sl_2414,
            total_risk_usd=cfg.manipulation_risk_usd * 2.0,
            symbol=m.symbol,
            equal_weight=True,
            fee_maker_pct=cfg.fee_maker_pct,
            fee_taker_pct=cfg.fee_taker_pct,
            slippage_buffer_pct=cfg.slippage_buffer_pct,
        )

        specs = client.get_specs(m.symbol) if hasattr(client, "get_specs") else None
        min_notional = specs.min_notional if specs else 0.0
        min_qty = specs.min_qty if specs else 0.0

        # Защита Manipulation от minNotional/minQty: обязательный ордер q1_m (1.414)
        if q1_m < min_qty or (min_notional > 0 and (q1_m * e_1414) < min_notional):
            console.print(f"  [yellow]⚠️ [{m.symbol}] [MANIPULATION] Объем первого ордера ({q1_m} @ ${e_1414}, notional: ${q1_m * e_1414:.2f}) не проходит minQty ({min_qty}) / minNotional (${min_notional}). Переход в безопасный IDLE.[/yellow]")
            m.state = "IDLE"
            return

        if is_live:
            if hasattr(client, "get_available_balance") and hasattr(client, "calc_required_margin"):
                avail_m = client.get_available_balance()
                req_m = (client.calc_required_margin(m.symbol, q1_m, e_1414) + client.calc_required_margin(m.symbol, q2_m, e_1618)) * 1.05
                if avail_m < req_m:
                    console.print(f"  [yellow]⏸️ [{m.symbol}] Недостаточно свободной маржи (${avail_m:.2f} < ${req_m:.2f}). Откладываем выставление сетки манипуляции.[/yellow]")
                    m.state = "IDLE"
                    return

            placed_attempt_ids = []
            try:
                cancel_monitor_orders(client, m)
                layer_tag = "MAJ" if m.layer == "major" else "MIN"
                sym_short = m.symbol.replace("USDT.P", "").replace("USDT", "")
                r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1_m, price=e_1414, take_profit=tp_1000, stop_loss=sl_2414, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "M1"))
                m.o1_id = r1.get("orderId")
                placed_attempt_ids.append(m.o1_id)
                console.print(f"  ✓ Ордер 1: Limit Buy {q1_m} @ ${e_1414}, TP: ${tp_1000}, SL: ${sl_2414}")

                if q2_m >= min_qty and (min_notional <= 0 or (q2_m * e_1618) >= min_notional):
                    r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2_m, price=e_1618, take_profit=e_1414, stop_loss=sl_2414, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "M2"))
                    m.o2_id = r2.get("orderId")
                    placed_attempt_ids.append(m.o2_id)
                    console.print(f"  ✓ Ордер 2: Limit Buy {q2_m} @ ${e_1618}, TP: ${e_1414}, SL: ${sl_2414}")
            except Exception as err:
                console.print(f"  ❌ Ошибка выставления сетки манипуляции: {err}")
                for cancel_oid in placed_attempt_ids:
                    if cancel_oid:
                        try:
                            client.cancel_order(m.symbol, cancel_oid)
                            console.print(f"[yellow][{m.symbol}] [MANIPULATION] Откачен ордер {cancel_oid}.[/yellow]")
                        except Exception:
                            pass
                m.o1_id = None
                m.o2_id = None
                m.state = "IDLE"
                return

        m.state = "MANIPULATION_ACTIVE"
        m.cur_e1 = e_1414
        m.cur_tp1 = tp_1000
        m.cur_e2 = e_1618
        m.cur_tp2 = e_1414
        m.sl = sl_2414
        m.q1 = q1_m
        m.q2 = q2_m
        m.has_o2 = True
        m.tp_basket_applied = False
        m.position_was_open = False


def handle_sweep_reclaim_active(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние SWEEP_RECLAIM_ACTIVE: активный ложный пробой (следим за БУ и выходом)."""
    pos = client.get_position(m.symbol, "Buy") if is_live else None
    pos_size = float(pos.get("size", 0.0)) if pos else 0.0

    if pos_size > 0:
        m.position_was_open = True
        # Проверяем триггер переноса в безубыток
        if m.be_trigger and m.be_price and not m.be_applied:
            df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
            latest_h = float(df_now["high"].iloc[-1])
            if latest_h >= m.be_trigger:
                if is_live:
                    success = client.update_stop_loss(m.symbol, m.o1_id, m.be_price)
                else:
                    success = True
                if success:
                    m.be_applied = True
                    console.print(f"\n[bold green]🛡️ [{m.symbol}] Достигнут уровень БУ (${latest_h} >= ${m.be_trigger})! SL перенесен в ${m.be_price}.[/bold green]")
        return

    if m.position_was_open and pos_size == 0:
        console.print(f"\n[bold green]🏁 [{m.symbol}] Сделка по Ложному пробою закрыта (TP или SL). Фибоначчи завершена.[/bold green]")
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
            "exit_reason": "SWEEP_CLOSED",
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

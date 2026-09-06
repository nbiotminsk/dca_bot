"""Обработчик состояния IDLE и вспомогательная функция is_peer_layer_active."""

from typing import Any, Optional

import pandas as pd
from rich.console import Console

from indicators.pybit_client import BybitClient
from scripts.backtest_strategy_interactive import calc_fib
from scripts.trader.config import TradeConfig
from scripts.trader.models import ActiveTradeMonitor
from scripts.trader.order_manager import (
    cancel_monitor_orders,
    cleanup_orphan_orders_for_layer,
    is_entry_missed,
    make_order_link_id,
)
from scripts.trader.setup_scanner import find_active_setup
from scripts.trader.trade_journal import load_completed_impulses

console = Console()


def is_peer_layer_active(
    symbol: str,
    my_layer: str,
    all_monitors: Optional[list[ActiveTradeMonitor]] = None,
    client: Optional[Any] = None,
    is_live: bool = False,
) -> tuple[bool, str]:
    """
    Проверяет, активна ли сетка или открыта ли позиция другого слоя на том же символе
    (взаимное исключение позиций / Priority Lock).
    Возвращает (is_active, description).
    """
    # 1. Проверка по мониторам в памяти
    if all_monitors:
        for other in all_monitors:
            if other.symbol == symbol and other.layer != my_layer and other.is_active:
                return True, f"слой {other.layer.upper()} в состоянии {other.state}"

    # 2. Если live-режим, дополнительно проверяем ордера и позицию на бирже
    if is_live and client is not None:
        try:
            peer_tag = "-MIN-" if my_layer == "major" else "-MAJ-"
            if hasattr(client, "get_open_orders"):
                open_ords = client.get_open_orders(symbol)
                for o in open_ords:
                    link_id = str(o.get("orderLinkId", ""))
                    if peer_tag in link_id:
                        return True, f"активные ордера {peer_tag} на бирже ({link_id})"
            if hasattr(client, "get_position"):
                pos = client.get_position(symbol, "Buy")
                pos_sz = float(pos.get("size", 0.0)) if pos else 0.0
                if pos_sz > 0:
                    return True, f"открытая позиция {pos_sz} шт. на бирже"
        except Exception:
            pass

    return False, ""


def handle_idle(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
    all_monitors: Optional[list] = None,
) -> None:
    """Состояние IDLE: поиск новых импульсов на закрытии свечи."""
    if m.close_only:
        m.state = "FINISHED"
        m.done = True
        return

    # Защита от спама запросов: если текущая свеча еще не завершилась, новая свеча точно не появилась
    if m.last_candle_time is not None:
        now_ts = pd.Timestamp.now(tz="UTC")
        last_ts = pd.to_datetime(m.last_candle_time, utc=True)
        tf_seconds = {
            "1": 60, "3": 180, "5": 300, "15": 900, "30": 1800,
            "60": 3600, "120": 7200, "240": 14400, "D": 86400, "W": 604800,
        }.get(str(interval), 3600)
        if (now_ts - last_ts).total_seconds() < tf_seconds:
            return  # Текущая свеча еще не закрылась

    df_now = client.fetch_klines(m.symbol, interval=interval, limit=max(140, cfg.lookback_bars + 20))
    if len(df_now) < 15:
        return
    latest_time = df_now["timestamp"].iloc[-1]
    if m.last_candle_time is not None and latest_time == m.last_candle_time:
        return  # Новая свеча еще не появилась

    m.last_candle_time = latest_time
    is_major = (m.layer == "major")
    min_bars = (cfg.minor_max_impulse_bars + 1) if is_major else None
    max_bars = cfg.major_max_impulse_bars if is_major else cfg.minor_max_impulse_bars

    setup_timeout = cfg.major_timeout_hours if is_major else cfg.minor_timeout_hours
    completed_trades_list = load_completed_impulses()
    setup = find_active_setup(
        df_now,
        min_pct=cfg.min_impulse_pct,
        lookback_bars=cfg.lookback_bars,
        preferred_side=cfg.preferred_side,
        scale=cfg.scale,
        max_sweep_pct=cfg.reclaim_max_sweep_pct,
        allow_close_below=cfg.reclaim_allow_close_below,
        entry_buffer_pct=cfg.entry_buffer_pct,
        entry_buffer_0500_pct=cfg.entry_buffer_0500_pct,
        entry_buffer_0618_pct=cfg.entry_buffer_0618_pct,
        entry_buffer_0786_pct=cfg.entry_buffer_0786_pct,
        entry_buffer_1414_pct=cfg.entry_buffer_1414_pct,
        entry_buffer_1618_pct=cfg.entry_buffer_1618_pct,
        tp_buffer_pct=cfg.tp_buffer_pct,
        reclaim_tp_buffer_pct=cfg.reclaim_tp_buffer_pct,
        reclaim_be_trigger_fib=cfg.reclaim_be_trigger_fib,
        reclaim_be_offset_pct=cfg.reclaim_be_offset_pct,
        atr_multiplier=cfg.atr_multiplier,
        timeout_hours=setup_timeout,
        min_impulse_bars=min_bars,
        max_impulse_bars=max_bars,
        layer=m.layer,
        symbol=m.symbol,
        completed_impulses=completed_trades_list,
    )

    if setup is not None:
        # Если этот импульс уже был пропущен (вход на 0.500 упущен) — ждем появления более свежего импульса
        if m.last_skipped_imp_time is not None and setup.imp_end_time <= m.last_skipped_imp_time:
            return

        layer_tag_log = "[MAJOR]" if is_major else "[MINOR]"
        console.print(f"\n[bold green]✨ [{m.symbol}] {layer_tag_log} На закрытии свечи обнаружен импульс:[/bold green] {setup.description}")

        # Если это большая фиба и цена еще выше 0.382 — не выставляем лимитки, ждем 0.382
        if is_major and not setup.touched_0382:
            console.print("  ➜ Переход в режим ожидания 0.382 (AWAITING_MAJOR_0382). Лимитки не выставляются, маржа свободна.")
            if is_live:
                cleanup_orphan_orders_for_layer(client, m.symbol, m.layer)
            m.setup_type = setup.setup_type
            m.state = "AWAITING_MAJOR_0382"
            m.cur_peak = setup.imp_peak_price
            m.p_0382 = setup.p_0382
            m.cur_e1 = client.round_price(setup.entry_1, m.symbol)
            m.cur_tp1 = client.round_price(setup.tp_1, m.symbol)
            m.cur_e2 = client.round_price(setup.entry_2, m.symbol) if setup.entry_2 else None
            m.cur_tp2 = client.round_price(setup.tp_2, m.symbol) if setup.tp_2 else None
            m.cur_e3 = client.round_price(setup.entry_3, m.symbol) if setup.entry_3 else None
            m.cur_tp3 = client.round_price(setup.tp_3, m.symbol) if setup.tp_3 else None
            m.sl = client.round_price(setup.stop_loss, m.symbol)
            m.imp_start_price = setup.imp_start_price
            m.imp_start_time = setup.imp_start_time
            m.imp_end_time = setup.imp_end_time
            m.touched_0382 = False
            m.timeout_hours = setup_timeout
            return

        if setup.setup_type == "AWAITING_BREAK_BELOW":
            console.print(f"  ➜ [{m.symbol}] [{layer_tag_log}] Вход на 0.500 упущен, но 0.382 не протестирован. Переход в режим ожидания пробоя 1.000 (AWAITING_BREAK_BELOW). Маржа свободна.")
            if is_live:
                cleanup_orphan_orders_for_layer(client, m.symbol, m.layer)
            m.setup_type = "AWAITING_BREAK_BELOW"
            m.state = "AWAITING_BREAK_BELOW"
            m.cur_peak = setup.imp_peak_price
            m.imp_start_price = setup.imp_start_price
            m.imp_start_time = setup.imp_start_time
            m.p_0382 = setup.p_0382
            m.cur_e1 = client.round_price(setup.entry_1, m.symbol)
            m.cur_tp1 = client.round_price(setup.tp_1, m.symbol)
            m.cur_e2 = client.round_price(setup.entry_2, m.symbol) if setup.entry_2 else None
            m.cur_tp2 = client.round_price(setup.tp_2, m.symbol) if setup.tp_2 else None
            m.cur_e3 = client.round_price(setup.entry_3, m.symbol) if setup.entry_3 else None
            m.cur_tp3 = client.round_price(setup.tp_3, m.symbol) if setup.tp_3 else None
            m.sl = client.round_price(setup.stop_loss, m.symbol)
            m.imp_end_time = setup.imp_end_time
            m.timeout_hours = setup_timeout
            return

        if cfg.mutual_exclusion:
            is_active, peer_desc = is_peer_layer_active(m.symbol, m.layer, all_monitors, client, is_live)
            if is_active:
                console.print(f"  [yellow]🔒 [{m.symbol}] [{layer_tag_log}] Взаимное исключение позиций: на монете уже активен {peer_desc}. Выставление новой сетки заблокировано.[/yellow]")
                return

        e1 = client.round_price(setup.entry_1, m.symbol)
        tp1 = client.round_price(setup.tp_1, m.symbol)
        sl = client.round_price(setup.stop_loss, m.symbol)
        e2 = client.round_price(setup.entry_2, m.symbol) if setup.entry_2 else None
        tp2 = client.round_price(setup.tp_2, m.symbol) if setup.tp_2 else None
        e3 = client.round_price(setup.entry_3, m.symbol) if setup.entry_3 else None
        tp3 = client.round_price(setup.tp_3, m.symbol) if setup.tp_3 else None

        if setup.setup_type == "MANIPULATION":
            setup_risk = cfg.manipulation_risk_usd * 2.0
        else:
            setup_risk = cfg.major_risk_usd if is_major else cfg.minor_risk_usd

        if e3 is not None and e2 is not None:
            q1, q2, q3, _, _, _ = client.calc_triple_grid_order_sizes(
                e1, e2, e3, sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=False, weights=cfg.grid_weights,
                is_long=(setup.side == "long"), fee_maker_pct=cfg.fee_maker_pct, fee_taker_pct=cfg.fee_taker_pct, slippage_buffer_pct=cfg.slippage_buffer_pct,
            )
        elif e2 is not None:
            q1, q2, _, _ = client.calc_dual_grid_order_sizes(
                e1, e2, sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=True,
                is_long=(setup.side == "long"), fee_maker_pct=cfg.fee_maker_pct, fee_taker_pct=cfg.fee_taker_pct, slippage_buffer_pct=cfg.slippage_buffer_pct,
            )
            q3 = 0.0
        else:
            fee_open = cfg.fee_maker_pct / 100.0
            fee_close = cfg.fee_taker_pct / 100.0
            slip = cfg.slippage_buffer_pct / 100.0
            worst_sl = sl * (1.0 - slip) if setup.side == "long" else sl * (1.0 + slip)
            unit_loss1 = abs(e1 - worst_sl) + e1 * fee_open + worst_sl * fee_close
            raw_q1 = setup_risk / unit_loss1 if unit_loss1 > 0 else 0.0
            q1 = client.round_qty(raw_q1, m.symbol) if raw_q1 > 0 else 0.0
            q2 = 0.0
            q3 = 0.0

        specs = client.get_specs(m.symbol)
        if q1 <= 0 or (specs.min_notional > 0 and q1 * e1 < specs.min_notional):
            console.print(f"  [yellow]⚠️ [{m.symbol}] [{layer_tag_log}] Расчетный объем ({q1 * e1:.2f}) ниже minNotional (${specs.min_notional}) при риске ${setup_risk:.2f}. Сделка пропущена для защиты лимита риска.[/yellow]")
            return

        # Фильтр минимального чистого R:R (если задан)
        if cfg.min_net_rr is not None:
            fee_open = cfg.fee_maker_pct / 100.0
            fee_close = cfg.fee_taker_pct / 100.0
            slip = cfg.slippage_buffer_pct / 100.0
            worst_sl = sl * (1.0 - slip) if setup.side == "long" else sl * (1.0 + slip)
            net_reward = abs(tp1 - e1) - (e1 * fee_open + tp1 * fee_open)
            net_risk = abs(e1 - worst_sl) + (e1 * fee_open + worst_sl * fee_close)
            net_rr = net_reward / net_risk if net_risk > 0 else 0.0
            if net_rr < cfg.min_net_rr:
                console.print(f"  [yellow]⚠️ [{m.symbol}] [{layer_tag_log}] Чистый R:R ({net_rr:.2f}) ниже порога min_net_rr ({cfg.min_net_rr:.2f}). Сделка пропущена.[/yellow]")
                return

        layer_tag = "MAJ" if is_major else "MIN"
        sym_short = m.symbol.replace("USDT.P", "").replace("USDT", "")
        o1_id, o2_id, o3_id = None, None, None
        if is_live:
            # Проверяем, нет ли уже открытой позиции перед выставлением новой сетки
            pos = client.get_position(m.symbol, "Buy")
            pos_size = float(pos.get("size", 0.0)) if pos else 0.0
            if pos_size > 0:
                console.print(f"ℹ️ [{m.symbol}] Позиция уже открыта ({pos_size} шт.). Подключаем монитор без выставления новой сетки.")
                m.state = "O1_FILLED"
                m.position_was_open = True
                return

            # Проверка текущей цены относительно уровней входа (защита от покупок выше рынка)
            cur_p = client.get_ticker_price(m.symbol) if hasattr(client, "get_ticker_price") else 0.0
            if cur_p <= 0:
                try:
                    cur_p = float(df_now["close"].iloc[-1])
                except Exception:
                    cur_p = e1

            is_fib_grid = setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION")
            is_long = (setup.side == "long")
            o1_missed = getattr(setup, "o1_filled", False) or is_entry_missed(e1, cur_p, is_long=is_long)
            o2_missed = getattr(setup, "o2_filled", False) or (e2 is not None and is_entry_missed(e2, cur_p, is_long=is_long))
            o3_missed = bool(e3 is not None and is_entry_missed(e3, cur_p, is_long=is_long))

            # Если ВСЕ доступные уровни сетки уже пройдены (ордер 1, 2 и 3 ниже входа):
            if setup.setup_type == "AWAITING_BREAK_BELOW" or (is_fib_grid and o1_missed and o2_missed and (e3 is None or o3_missed)):
                reason = "все уровни сетки (0.500, 0.618, 0.786) уже пройдены" if is_fib_grid else "уровень 0.500 уже протестирован"
                console.print(f"  [yellow]⚠️ [{m.symbol}] [{layer_tag}] {reason}. Переход в AWAITING_BREAK_BELOW (маржа свободна).[/yellow]")
                if is_live:
                    cleanup_orphan_orders_for_layer(client, m.symbol, m.layer)
                m.setup_type = "AWAITING_BREAK_BELOW"
                m.state = "AWAITING_BREAK_BELOW"
                m.cur_peak = setup.imp_peak_price
                m.imp_start_price = setup.imp_start_price
                m.imp_start_time = setup.imp_start_time
                m.p_0382 = setup.p_0382
                m.cur_e1 = e1
                m.cur_tp1 = tp1
                m.cur_e2 = e2
                m.cur_tp2 = tp2
                m.cur_e3 = e3
                m.cur_tp3 = tp3
                m.sl = sl
                m.imp_end_time = setup.imp_end_time
                m.timeout_hours = setup_timeout
                return

            place_o1 = not o1_missed
            place_o2 = bool(e2 and q2 > 0 and tp2 and not o2_missed)
            place_o3 = bool(e3 and q3 > 0 and tp3 and not o3_missed)

            if not place_o1:
                console.print(f"  [yellow]ℹ️ [{m.symbol}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 1 (${e1}). Ордер 1 пропущен, выставляем последующие ордера.[/yellow]")
            if not place_o2 and e2 and q2 > 0:
                console.print(f"  [yellow]ℹ️ [{m.symbol}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 2 (${e2}). Ордер 2 пропущен.[/yellow]")
            if not place_o3 and e3 and q3 > 0:
                console.print(f"  [yellow]ℹ️ [{m.symbol}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 3 (${e3}). Ордер 3 пропущен.[/yellow]")

            if not (place_o1 or place_o2 or place_o3):
                console.print(f"  [yellow]⚠️ [{m.symbol}] [{layer_tag}] Все уровни сетки выше текущей цены (${cur_p}). Пропуск выставления.[/yellow]")
                m.last_skipped_imp_time = setup.imp_end_time
                m.state = "IDLE"
                return

            # Проверка свободной маржи
            if hasattr(client, "get_available_balance") and hasattr(client, "calc_required_margin"):
                avail_m = client.get_available_balance()
                req_m = 0.0
                if place_o1:
                    req_m += client.calc_required_margin(m.symbol, q1, e1)
                if place_o2:
                    req_m += client.calc_required_margin(m.symbol, q2, e2)
                if place_o3:
                    req_m += client.calc_required_margin(m.symbol, q3, e3)
                if avail_m < req_m * 1.05:
                    console.print(f"[yellow]⏸️ [{m.symbol}] [{layer_tag}] Недостаточно свободной маржи (${avail_m:.2f} < ${req_m * 1.05:.2f}). Откладываем выставление новой сетки.[/yellow]")
                    return

            placed_attempt_ids = []
            try:
                if place_o1:
                    r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1, price=e1, take_profit=tp1, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O1"))
                    o1_id = r1.get("orderId")
                    placed_attempt_ids.append(o1_id)
                if place_o2:
                    r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2, price=e2, take_profit=tp2, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O2"))
                    o2_id = r2.get("orderId")
                    placed_attempt_ids.append(o2_id)
                if place_o3:
                    r3 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q3, price=e3, take_profit=tp3, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O3"))
                    o3_id = r3.get("orderId")
                    placed_attempt_ids.append(o3_id)
                console.print(f"  ✓ [{m.symbol}] [{layer_tag}] Размещена новая сетка: Вход 1 ${e1 if place_o1 else '(пропущен)'}, Вход 2 ${e2 if place_o2 else '(пропущен)'}, Вход 3 ${e3 if place_o3 else '(пропущен)'}")
            except Exception as err:
                console.print(f"[red]❌ [{m.symbol}] [{layer_tag}] Ошибка выставления новой сетки: {err}[/red]")
                for cancel_oid in placed_attempt_ids:
                    if cancel_oid:
                        try:
                            client.cancel_order(m.symbol, cancel_oid)
                            console.print(f"[yellow][{m.symbol}] [{layer_tag}] Откачен ордер {cancel_oid}.[/yellow]")
                        except Exception:
                            pass
                m.state = "IDLE"
                return

        m.setup_type = setup.setup_type
        if is_fib_grid and o1_missed:
            m.state = "O2_FILLED" if o2_missed else "O1_FILLED"
        else:
            m.state = "TRAILING" if setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION") else setup.setup_type
        m.o1_id = o1_id
        m.o2_id = o2_id
        m.o3_id = o3_id
        m.cur_peak = setup.imp_peak_price
        m.cur_e1 = e1
        m.cur_tp1 = tp1
        m.cur_e2 = e2 if e2 else 0.0
        m.cur_tp2 = tp2 if tp2 else 0.0
        m.cur_e3 = e3 if e3 else 0.0
        m.cur_tp3 = tp3 if tp3 else 0.0
        m.imp_start_price = setup.imp_start_price
        m.imp_start_time = setup.imp_start_time
        m.imp_end_time = setup.imp_end_time
        m.sl = sl
        m.q1 = q1
        m.q2 = q2
        m.q3 = q3
        m.has_o2 = (e2 is not None and q2 > 0)
        m.has_o3 = (e3 is not None and q3 > 0)
        m.position_was_open = False
        m.be_applied = False
        m.tp_basket_applied = False
        m.touched_0382 = True

"""Конечный автомат (State Machine) мониторинга торговых позиций."""

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
    is_entry_missed,
    make_order_link_id,
)
from scripts.trader.setup_scanner import find_active_setup
from scripts.trader.trade_journal import (
    load_completed_impulses,
    save_completed_impulse as _real_save_completed_impulse,
)


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
) -> None:
    """Выполняет один шаг конечного автомата (State Machine) для заданной монеты."""
    # ─── 1. Состояние: ТРЕЙЛИНГ СЕТКИ ──────────────────────────────────────────
    if m.state == "TRAILING":
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
                    console.print(f"\n[bold green]⚡ [{m.symbol}] Налиты все 3 ордера (0.500, 0.618, 0.786)! Позиция: {pos_size}.[/bold green]")
                    console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
                    if is_live:
                        try:
                            client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                            m.tp_basket_applied = True
                        except Exception as err:
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
                elif m.has_o2 and m.q2 > 0 and pos_size >= (m.q1 + 0.5 * m.q2):
                    m.state = "O2_FILLED"
                    console.print(f"\n[bold green]⚡ [{m.symbol}] Налиты 2 ордера (0.500 и 0.618)! Позиция: {pos_size}.[/bold green]")
                    console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.382 Fib (${m.cur_tp2}). Ордер 3 в стакане (${m.cur_e3})...")
                    if is_live:
                        try:
                            client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp2, stop_loss=m.sl)
                            m.tp_basket_applied = True
                        except Exception as err:
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

    # ─── 1.1 Состояние: ОЖИДАНИЕ ПРОБОЯ 0.382 (БОЛЬШАЯ ФИБА) ────────────────────
    elif m.state == "AWAITING_MAJOR_0382":
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
                    m.cur_e1, m.cur_e2, m.cur_e3, m.sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=False, weights=cfg.grid_weights
                )
            elif m.cur_e2:
                q1, q2, _, _ = client.calc_dual_grid_order_sizes(m.cur_e1, m.cur_e2, m.sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=True)
                q3 = 0.0
            else:
                dist1 = abs(m.cur_e1 - m.sl)
                specs = client.get_specs(m.symbol)
                q1 = client.round_qty(setup_risk / dist1 if dist1 > 0 else specs.min_qty, m.symbol)
                q2 = 0.0
                q3 = 0.0

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

                try:
                    r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1, price=m.cur_e1, take_profit=m.cur_tp1, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O1"))
                    o1_id = r1.get("orderId")
                    if m.cur_e2 and q2 > 0 and m.cur_tp2:
                        r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2, price=m.cur_e2, take_profit=m.cur_tp2, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O2"))
                        o2_id = r2.get("orderId")
                    if m.cur_e3 and q3 > 0 and m.cur_tp3:
                        r3 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q3, price=m.cur_e3, take_profit=m.cur_tp3, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O3"))
                        o3_id = r3.get("orderId")
                    console.print(f"  ✓ [{m.symbol}] [MAJOR] Размещена сетка: Вход 1 ${m.cur_e1}, Вход 2 ${m.cur_e2 or '-'}, Вход 3 ${m.cur_e3 or '-'}")
                except Exception as err:
                    console.print(f"[red]❌ [{m.symbol}] [MAJOR] Ошибка размещения сетки: {err}[/red]")
                    return

            m.o1_id = o1_id
            m.o2_id = o2_id
            m.o3_id = o3_id
            m.state = "TRAILING"
            return

    # ─── 1.2 Состояние: ОЖИДАНИЕ ПРОБОЯ 1.000 БЕЗ ВОЗВРАТА К 0.382 ─────────────
    elif m.state == "AWAITING_BREAK_BELOW":
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

    # ─── 2. Состояние: НАЛИТ ОРДЕР 1 (Ожидание Ордера 2/3 или TP 0.236) ────────
    elif m.state == "O1_FILLED":
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        if pos_size > 0:
            m.position_was_open = True
            # Проверяем, налился ли Ордер 3 или Ордер 2 при проливе
            if m.has_o3 and m.q3 > 0 and pos_size >= (m.q1 + m.q2 + 0.5 * m.q3):
                m.state = "O3_FILLED"
                console.print(f"\n[bold green]🎯 [{m.symbol}] Глубокий пролив: исполнены Ордера 2 и 3! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
                        console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
            elif m.has_o2 and m.q2 > 0 and pos_size >= (m.q1 + 0.5 * m.q2):
                m.state = "O2_FILLED"
                console.print(f"\n[bold green]🎯 [{m.symbol}] Добор: Ордер 2 (0.618) исполнен! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.382 Fib (${m.cur_tp2}). Ордер 3 (${m.cur_e3}) активен в стакане...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp2, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
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
            save_completed_impulse({
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
            save_completed_impulse({
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

    # ─── 3. Состояние: НАЛИТЫ ОРДЕРА 1 И 2 (Выход на 0.382 или добор 3) ────────
    elif m.state in ("O2_FILLED", "BOTH_FILLED"):
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        if pos_size > 0:
            m.position_was_open = True
            # Проверяем, налился ли Ордер 3 (0.786)
            if m.has_o3 and m.q3 > 0 and pos_size >= (m.q1 + m.q2 + 0.5 * m.q3):
                m.state = "O3_FILLED"
                console.print(f"\n[bold green]🎯 [{m.symbol}] Добор: Ордер 3 (0.786) исполнен! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
                        console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
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
            save_completed_impulse({
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
            save_completed_impulse({
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

    # ─── 4. Состояние: НАЛИТЫ ВСЕ 3 ОРДЕРА (Выход всей тройки на 0.500) ─────────
    elif m.state == "O3_FILLED":
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        if pos_size > 0:
            m.position_was_open = True
            if not m.tp_basket_applied and is_live:
                try:
                    client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                    m.tp_basket_applied = True
                except Exception as err:
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
            save_completed_impulse({
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
            save_completed_impulse({
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

    # ─── 5. Состояние: ОЖИДАНИЕ ЗАКРЫТИЯ СВЕЧИ СВИПА 1.000 ────────────────────
    elif m.state == "AWAITING_SWEEP_CLOSE":
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
                e_1414, e_1618, sl_2414, total_risk_usd=cfg.manipulation_risk_usd * 2.0, symbol=m.symbol, equal_weight=True
            )

            if is_live:
                if hasattr(client, "get_available_balance") and hasattr(client, "calc_required_margin"):
                    avail_m = client.get_available_balance()
                    req_m = (client.calc_required_margin(m.symbol, q1_m, e_1414) + client.calc_required_margin(m.symbol, q2_m, e_1618)) * 1.05
                    if avail_m < req_m:
                        console.print(f"  [yellow]⏸️ [{m.symbol}] Недостаточно свободной маржи (${avail_m:.2f} < ${req_m:.2f}). Откладываем выставление сетки манипуляции.[/yellow]")
                        m.state = "IDLE"
                        return

                try:
                    cancel_monitor_orders(client, m)
                    layer_tag = "MAJ" if m.layer == "major" else "MIN"
                    sym_short = m.symbol.replace("USDT.P", "").replace("USDT", "")
                    r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1_m, price=e_1414, take_profit=tp_1000, stop_loss=sl_2414, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "M1"))
                    r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2_m, price=e_1618, take_profit=e_1414, stop_loss=sl_2414, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "M2"))
                    m.o1_id = r1.get("orderId")
                    m.o2_id = r2.get("orderId")
                    console.print(f"  ✓ Ордер 1: Limit Buy {q1_m} @ ${e_1414}, TP: ${tp_1000}, SL: ${sl_2414}")
                    console.print(f"  ✓ Ордер 2: Limit Buy {q2_m} @ ${e_1618}, TP: ${e_1414}, SL: ${sl_2414}")
                except Exception as err:
                    console.print(f"  ❌ Ошибка выставления сетки манипуляции: {err}")
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

    # ─── 6. Состояние: АКТИВНЫЙ ЛОЖНЫЙ ПРОБОЙ (Следим за БУ и выходом) ─────────
    elif m.state == "SWEEP_RECLAIM_ACTIVE":
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
            save_completed_impulse({
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

    # ─── 7. Состояние: АКТИВНАЯ СЕТКА МАНИПУЛЯЦИИ ──────────────────────────────
    elif m.state == "MANIPULATION_ACTIVE":
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
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP корзины на 1.414: {err}")
                    else:
                        m.tp_basket_applied = True
            return

        if m.position_was_open and pos_size == 0:
            console.print(f"\n[bold green]🏁 [{m.symbol}] Сетка Манипуляции закрыта (TP или SL). Работа с данной Фибоначчи полностью завершена.[/bold green]")
            if is_live:
                cancel_monitor_orders(client, m)
            save_completed_impulse({
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

    # ─── 8. Состояние: IDLE (Поиск новых импульсов на закрытии свечи) ───────────
    elif m.state == "IDLE":
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            return
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
                    e1, e2, e3, sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=False, weights=cfg.grid_weights
                )
            elif e2 is not None:
                q1, q2, _, _ = client.calc_dual_grid_order_sizes(e1, e2, sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=True)
                q3 = 0.0
            else:
                dist1 = abs(e1 - sl)
                specs = client.get_specs(m.symbol)
                q1 = client.round_qty(setup_risk / dist1 if dist1 > 0 else specs.min_qty, m.symbol)
                q2 = 0.0
                q3 = 0.0

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

                try:
                    if place_o1:
                        r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1, price=e1, take_profit=tp1, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O1"))
                        o1_id = r1.get("orderId")
                    if place_o2:
                        r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2, price=e2, take_profit=tp2, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O2"))
                        o2_id = r2.get("orderId")
                    if place_o3:
                        r3 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q3, price=e3, take_profit=tp3, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O3"))
                        o3_id = r3.get("orderId")
                    console.print(f"  ✓ [{m.symbol}] [{layer_tag}] Размещена новая сетка: Вход 1 ${e1 if place_o1 else '(пропущен)'}, Вход 2 ${e2 if place_o2 else '(пропущен)'}, Вход 3 ${e3 if place_o3 else '(пропущен)'}")
                except Exception as err:
                    console.print(f"[red]❌ [{m.symbol}] [{layer_tag}] Ошибка выставления новой сетки: {err}[/red]")
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


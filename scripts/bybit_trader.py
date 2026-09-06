#!/usr/bin/env python3
"""
Интерактивный терминал торговли по стратегии Fibonacci Dual Grid на Bybit V5.

Функции:
  1. Запрос монеты у пользователя (например, ZEC -> ZECUSDT).
  2. Загрузка свечей через Bybit V5 API (pybit).
  3. Поиск актуального импульса (глубина 60 свечей, мин. размах 2.0%, лог. шкала):
     - Растущий импульс без коррекции -> Трейлинг сетки (сдвиг входов и тейков вверх за ценой).
     - Идет коррекция -> Выставление ордеров 0.500 и 0.618 с тейками 0.236 и 0.382, стоп 1.000.
     - Пробой 1.000 со свипом и дивергенцией MACD -> Вход в Reclaim со стопом за шпильку.
     - Манипуляция -> Сетка на 1.618 и 2.000 со стопом на 2.414 Fib.
  4. Точный расчет объема под риск $1.00 с учетом ограничений Bybit (minOrderQty, tickSize).
  5. Поддержка Dry-Run (безопасный предпросмотр) и Live Execution.
"""

import argparse
import sys
import time
from pathlib import Path

# Обеспечиваем доступность корневых пакетов проекта
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from rich.console import Console
from rich.panel import Panel

from indicators.pybit_client import BybitClient
from scripts.trader.config import TradeConfig, load_trade_config
from scripts.trader.display import build_setup_table
from scripts.trader.models import ActiveTradeMonitor, SetupSignal
from scripts.trader.order_manager import (
    cancel_monitor_orders,
    cleanup_orphan_orders_for_layer,
    is_entry_missed,
    make_order_link_id,
)
from scripts.trader.setup_scanner import find_active_setup
from scripts.trader.state_machine import process_monitor_step
from scripts.trader.trade_journal import (
    is_impulse_disqualified,
    load_completed_impulses,
    save_completed_impulse,
)
from scripts.trader.utils import format_symbol

__all__ = [
    "TradeConfig",
    "load_trade_config",
    "ActiveTradeMonitor",
    "SetupSignal",
    "format_symbol",
    "find_active_setup",
    "process_monitor_step",
    "load_completed_impulses",
    "save_completed_impulse",
    "is_impulse_disqualified",
    "cancel_monitor_orders",
    "cleanup_orphan_orders_for_layer",
    "is_entry_missed",
    "make_order_link_id",
    "build_setup_table",
    "main",
]

console = Console()


def main():
    parser = argparse.ArgumentParser(description="Bybit Fibonacci Dual Grid Trader")
    parser.add_argument("--symbol", "--symbols", dest="symbols", type=str, default=None, help="Монета или список монет через запятую (например, SUIUSDT.P,BTCUSDT)")
    parser.add_argument("--interval", type=str, default=None, help="Интервал свечей (15m, 1h, 4h, 1d)")
    parser.add_argument("--risk", type=float, default=None, help="Суммарный риск на сделку ($)")
    parser.add_argument("--entry-buffer", type=float, default=None, help="Буфер входа (%%)")
    parser.add_argument("--tp-buffer", type=float, default=None, help="Буфер тейка (%%)")
    parser.add_argument("--config", type=str, default=None, help="Путь к файлу конфигурации (по умолчанию config/trade_config.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Режим симуляции (без выставления ордеров)")
    parser.add_argument("--live", action="store_true", help="Боевой режим выставления ордеров")
    parser.add_argument("-y", "--yes", action="store_true", help="Автоматическое подтверждение выставления ордеров без интерактивного вопроса")
    parser.add_argument("--once", action="store_true", help="Одиночный проход без непрерывного фонового цикла")
    parser.add_argument("--atr-mult", type=float, default=None, help="Множитель ATR для динамического порога импульса (например, 2.5)")
    parser.add_argument("--timeout-hours", type=int, default=None, help="Тайм-аут свежести импульса в часах (например, 24)")
    args = parser.parse_args()

    cfg = load_trade_config(args.config)
    if args.risk is not None:
        cfg.total_risk_usd = args.risk
        cfg.minor_risk_usd = args.risk
    if args.entry_buffer is not None:
        cfg.entry_buffer_pct = args.entry_buffer
        cfg.entry_buffer_0500_pct = args.entry_buffer
        cfg.entry_buffer_0618_pct = args.entry_buffer
        cfg.entry_buffer_0786_pct = args.entry_buffer
        cfg.entry_buffer_1414_pct = args.entry_buffer
        cfg.entry_buffer_1618_pct = args.entry_buffer
    if args.tp_buffer is not None:
        cfg.tp_buffer_pct = args.tp_buffer
    if args.interval:
        cfg.timeframe = args.interval
    if args.atr_mult is not None:
        cfg.atr_multiplier = args.atr_mult
    if args.timeout_hours is not None:
        cfg.timeout_hours = args.timeout_hours

    atr_desc = f"{cfg.atr_multiplier:.1f}x" if cfg.atr_multiplier > 0 else "выкл"
    to_desc = f"{cfg.timeout_hours}ч" if cfg.timeout_hours > 0 else "выкл"
    buf_desc = (
        f"+{cfg.entry_buffer_pct:.2f}%"
        if (cfg.entry_buffer_0500_pct == cfg.entry_buffer_0618_pct == cfg.entry_buffer_0786_pct == cfg.entry_buffer_pct)
        else f"+{cfg.entry_buffer_0500_pct:.2f}%/+{cfg.entry_buffer_0618_pct:.2f}%/+{cfg.entry_buffer_0786_pct:.2f}%"
    )
    console.print(Panel.fit(
        "[bold cyan]🤖 Bybit Fibonacci Dual Grid & Trailing Trader[/bold cyan]\n"
        f"[dim]Конфиг: {Path(cfg.config_path).name if cfg.config_path else 'default'} | Стоп Minor: ${cfg.minor_risk_usd:.2f} | Стоп Major: ${cfg.major_risk_usd:.2f} | Стоп манипуляции: ${cfg.manipulation_risk_usd:.2f}/ордер | Вход: {buf_desc} | Тейк: -{cfg.tp_buffer_pct:.2f}% | ATR: {atr_desc} | Таймаут: {to_desc}[/dim]",
        border_style="cyan",
    ))

    # 1. Запрос монет: CLI -> Конфиг -> Интерактивный ввод
    if args.symbols:
        raw_coins = [s.strip() for s in args.symbols.split(",") if s.strip()]
        console.print(f"[bold yellow]Монеты (из CLI):[/bold yellow] [green]{', '.join(raw_coins)}[/green]")
    elif cfg.symbols:
        raw_coins = cfg.symbols
        console.print(f"[bold yellow]Монеты (из конфига):[/bold yellow] [green]{', '.join(raw_coins)}[/green]")
    else:
        console.print(Panel(
            "[bold red]❌ Ошибка: В конфигурационном файле (trade_config.yaml) не указан список монет для торговли![/bold red]\n\n"
            "Пожалуйста, добавьте монеты в раздел [cyan]strategy.symbols[/cyan] в [bold]config/trade_config.yaml[/bold], например:\n"
            "[green]strategy:\n  symbols:\n    - \"SUIUSDT.P\"\n    - \"BNBUSDT.P\"\n    - \"ICPUSDT.P\"[/green]\n\n"
            "Или укажите монеты через флаг командной строки: [yellow]--symbols SUI,BNB,ICP[/yellow]",
            title="⚠️ Монеты не заданы",
            border_style="red",
        ))
        return

    # Приводим к формату Bybit Linear (с поддержкой .P, ZEC -> ZECUSDT) и удаляем дубликаты
    symbols = list(dict.fromkeys([format_symbol(c) for c in raw_coins]))

    # 2. Запрос таймфрейма
    tf_map = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}
    if args.interval:
        interval = tf_map.get(args.interval.lower(), args.interval)
        console.print(f"[bold yellow]Таймфрейм свечей:[/bold yellow] [green]{args.interval}[/green]")
    elif args.symbols or cfg.symbols:
        interval = tf_map.get(cfg.timeframe.lower(), "60")
        console.print(f"[bold yellow]Таймфрейм свечей:[/bold yellow] [green]{cfg.timeframe} (из конфига)[/green]")
    else:
        tf_input = console.input(f"[bold yellow]Таймфрейм свечей [{cfg.timeframe}][/bold yellow] (15m, 1h, 4h, 1d): ").strip().lower()
        interval = tf_map.get(tf_input, tf_map.get(cfg.timeframe.lower(), "60"))

    # 3. Запрос риска
    if args.risk is not None:
        total_risk = args.risk
        console.print(f"[bold yellow]Суммарный риск на сделку:[/bold yellow] [green]${total_risk:.2f}[/green]")
    elif args.symbols or cfg.symbols:
        total_risk = cfg.total_risk_usd
        console.print(f"[bold yellow]Суммарный риск на сделку:[/bold yellow] [green]${total_risk:.2f} (из конфига)[/green]")
    else:
        risk_input = console.input(f"[bold yellow]Суммарный риск на сделку ($) [{cfg.total_risk_usd:.1f}]: [/bold yellow]").strip()
        try:
            total_risk = float(risk_input) if risk_input else cfg.total_risk_usd
        except ValueError:
            total_risk = cfg.total_risk_usd

    # 4. Режим
    if args.live:
        is_live = True
        console.print("[bold red]Режим: LIVE (боевые ордера)[/bold red]")
    elif args.dry_run:
        is_live = False
        console.print("[bold green]Режим: DRY-RUN (симуляция)[/bold green]")
    else:
        mode_input = console.input("[bold yellow]Режим работы: 1) Dry-Run (предпросмотр)  2) Live (боевые ордера) [1]: [/bold yellow]").strip()
        is_live = mode_input == "2"

    console.print("\n[dim]Подключение к Bybit V5...[/dim]")
    try:
        client = BybitClient()
    except Exception as e:
        console.print(f"[bold red]❌ Ошибка инициализации Bybit клиента:[/bold red] {e}")
        return

    # Сканирование монет по двум независимым слоям: Minor (локальная) и Major (старшая)
    actionable_setups = []
    awaiting_major_setups = []
    active_monitors: list[ActiveTradeMonitor] = []
    completed_trades_list = load_completed_impulses()

    layers = [
        ("minor", None, cfg.minor_max_impulse_bars, cfg.minor_risk_usd),
        ("major", cfg.minor_max_impulse_bars + 1, cfg.major_max_impulse_bars, cfg.major_risk_usd),
    ]

    for symbol in symbols:
        console.print(f"\n[bold cyan]─── Анализ {symbol} ───[/bold cyan]")
        try:
            specs = client.get_specs(symbol)
        except Exception as e:
            console.print(f"[red]❌ Ошибка получения спецификации {symbol}: {e}. Пропускаем.[/red]")
            continue

        try:
            df = client.fetch_klines(symbol, interval=interval, limit=max(140, cfg.lookback_bars + 20))
        except Exception as e:
            console.print(f"[red]❌ Ошибка загрузки свечей {symbol}: {e}. Пропускаем.[/red]")
            continue

        if len(df) == 0:
            console.print(f"[yellow]⚠️ Нет свечей для {symbol}. Пропускаем.[/yellow]")
            continue

        cur_price = df["close"].iloc[-1]
        console.print(f"[dim]{symbol}: Tick: {specs.tick_size}, Step: {specs.qty_step}, MinQty: {specs.min_qty}, MinNotional: ${specs.min_notional}, Цена: {cur_price}[/dim]")

        for layer_name, min_bars, max_bars, layer_risk in layers:
            is_major = (layer_name == "major")
            layer_tag_title = f"[MAJOR FIB {min_bars}-{max_bars} свечей]" if is_major else f"[MINOR FIB <= {max_bars} свечей]"

            setup_timeout = cfg.major_timeout_hours if is_major else cfg.minor_timeout_hours
            setup = find_active_setup(
                df,
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
                layer=layer_name,
                symbol=symbol,
                completed_impulses=completed_trades_list,
            )

            if setup is None:
                console.print(f"  [dim]{layer_tag_title} Нет активного сетапа.[/dim]")
                continue

            e1 = client.round_price(setup.entry_1, symbol)
            tp1 = client.round_price(setup.tp_1, symbol)
            sl = client.round_price(setup.stop_loss, symbol)
            e2 = client.round_price(setup.entry_2, symbol) if setup.entry_2 else None
            tp2 = client.round_price(setup.tp_2, symbol) if setup.tp_2 else None
            e3 = client.round_price(setup.entry_3, symbol) if setup.entry_3 else None
            tp3 = client.round_price(setup.tp_3, symbol) if setup.tp_3 else None

            # Если AWAITING_BREAK_BELOW — маржа свободна, монитор без ордеров
            if setup.setup_type == "AWAITING_BREAK_BELOW":
                console.print(f"  {layer_tag_title} [yellow]Все уровни сетки пройдены. Ожидание пробоя 1.000 (${sl}) без возврата к 0.382 (маржа свободна).[/yellow]")
                active_monitors.append(ActiveTradeMonitor(
                    symbol=symbol,
                    setup_type="AWAITING_BREAK_BELOW",
                    state="AWAITING_BREAK_BELOW",
                    layer=layer_name,
                    side=setup.side,
                    cur_peak=setup.imp_peak_price,
                    imp_start_price=setup.imp_start_price,
                    imp_start_time=setup.imp_start_time,
                    p_0382=setup.p_0382,
                    cur_e1=e1,
                    cur_tp1=tp1,
                    cur_e2=e2 if e2 else 0.0,
                    cur_tp2=tp2 if tp2 else 0.0,
                    cur_e3=e3 if e3 else 0.0,
                    cur_tp3=tp3 if tp3 else 0.0,
                    sl=sl,
                    imp_end_time=setup.imp_end_time,
                    last_candle_time=df["timestamp"].iloc[-1] if len(df) > 0 else None,
                    timeout_hours=setup_timeout,
                ))
                continue

            # Расчет лотов
            if setup.setup_type == "MANIPULATION":
                setup_risk = cfg.manipulation_risk_usd * 2.0
            else:
                setup_risk = layer_risk

            if e3 is not None and e2 is not None:
                q1, q2, q3, loss1, loss2, loss3 = client.calc_triple_grid_order_sizes(
                    e1, e2, e3, sl, total_risk_usd=setup_risk, symbol=symbol, equal_weight=False, weights=cfg.grid_weights
                )
                tot_loss = loss1 + loss2 + loss3
            elif e2 is not None:
                q1, q2, loss1, loss2 = client.calc_dual_grid_order_sizes(e1, e2, sl, total_risk_usd=setup_risk, symbol=symbol, equal_weight=True)
                q3 = 0.0
                loss3 = 0.0
                tot_loss = loss1 + loss2
            else:
                dist1 = abs(e1 - sl)
                q1 = client.round_qty(setup_risk / dist1 if dist1 > 0 else specs.min_qty, symbol)
                if q1 < specs.min_qty:
                    q1 = specs.min_qty
                loss1 = q1 * dist1
                q2 = 0.0
                loss2 = 0.0
                q3 = 0.0
                loss3 = 0.0
                tot_loss = loss1

            t = build_setup_table(
                symbol=symbol,
                layer_name=layer_name,
                setup=setup,
                cfg=cfg,
                cur_price=cur_price,
                e1=e1, tp1=tp1, e2=e2, tp2=tp2, e3=e3, tp3=tp3, sl=sl,
                q1=q1, q2=q2, q3=q3,
                loss1=loss1, loss2=loss2, loss3=loss3, tot_loss=tot_loss,
                setup_risk=setup_risk,
                client=client,
                is_major=is_major,
                min_bars=min_bars,
                max_bars=max_bars,
                setup_timeout=setup_timeout,
            )
            console.print(t)

            if q1 * e1 < specs.min_notional:
                console.print(f"[yellow]⚠️ Внимание: Notional Ордера 1 (${q1 * e1:.2f}) меньше биржевого минимума ${specs.min_notional}![/yellow]")

            setup_item = {
                "symbol": symbol,
                "layer": layer_name,
                "setup": setup,
                "specs": specs,
                "e1": e1,
                "tp1": tp1,
                "e2": e2,
                "tp2": tp2,
                "e3": e3,
                "tp3": tp3,
                "sl": sl,
                "q1": q1,
                "q2": q2,
                "q3": q3,
                "setup_risk": setup_risk,
            }

            if is_major and not setup.touched_0382:
                if is_live:
                    cleanup_orphan_orders_for_layer(client, symbol, layer_name)
                awaiting_major_setups.append(setup_item)
            else:
                actionable_setups.append(setup_item)

    # Если режим Dry-Run — завершаем после отображения всех сетапов
    if not is_live:
        console.print(f"\n[bold green]Режим Dry-Run завершен.[/bold green] Найдено: [bold cyan]{len(actionable_setups)} активных сетапов, {len(awaiting_major_setups)} в ожидании 0.382[/bold cyan].")
        return

    # В Live режиме — если нет активных сетапов и нет ожидающих сетапов и запрошен одиночный проход
    if not actionable_setups and not awaiting_major_setups and args.once:
        console.print(f"\n[yellow]Нет активных сетапов для выставления ордеров среди монет: {', '.join(symbols)}.[/yellow]")
        return

    if actionable_setups:
        symbols_to_trade_str = ", ".join(f"{item['symbol']} ({item['layer'].upper()})" for item in actionable_setups)
        if not args.yes:
            confirm = console.input(f"\n[bold red]ВЫСТАВИТЬ ОРДЕРА НА BYBIT ДЛЯ {symbols_to_trade_str}?[/bold red] (y/N): ").strip().lower()
            if confirm != "y":
                console.print("[yellow]Отменено пользователем.[/yellow]")
                return
        else:
            console.print(f"[bold green]Автоподтверждение (-y): выставляем ордера для {symbols_to_trade_str}...[/bold green]")

    # Выставляем ордера для всех подтвержденных сетапов
    # Проверяем доступную свободную маржу на аккаунте Bybit перед выставлением ордеров
    available_margin = client.get_available_balance() if (is_live and hasattr(client, "get_available_balance")) else 999999.0
    if is_live:
        console.print(f"\n[cyan]💰 Свободная маржа на Unified аккаунте: ${available_margin:.2f}[/cyan]")

    for item in actionable_setups:
        sym = item["symbol"]
        layer_name = item["layer"]
        setup = item["setup"]
        e1, tp1, e2, tp2, e3, tp3, sl = item["e1"], item["tp1"], item["e2"], item["tp2"], item["e3"], item["tp3"], item["sl"]
        q1, q2, q3 = item["q1"], item["q2"], item["q3"]
        layer_tag = "MAJ" if layer_name == "major" else "MIN"

        console.print(f"\n[bold cyan]Проверка/выставление ордеров для {sym} [{layer_name.upper()}]...[/bold cyan]")
        if is_live and hasattr(client, "get_available_balance"):
            available_margin = client.get_available_balance()
        o1_id = None
        o2_id = None
        o3_id = None
        pos_open_initially = False
        initial_state = "TRAILING"
        place_o1 = True
        place_o2 = bool(e2 is not None and q2 > 0)
        place_o3 = bool(e3 is not None and q3 > 0)
        try:
            side_str = "Buy" if setup.side == "long" else "Sell"

            # 1. Проверяем, есть ли уже открытая позиция
            curr_pos = client.get_position(sym, side=side_str)
            pos_sz = float(curr_pos.get("size", "0")) if curr_pos else 0.0
            pos_open = pos_sz > 0
            if pos_open:
                pos_open_initially = True

            # 2. Ищем существующие ордера ТОЛЬКО для своего слоя
            all_open = client.get_open_orders(sym)
            existing_orders = [
                o for o in all_open
                if o.get("side") == side_str and o.get("orderType") == "Limit" and (
                    layer_tag in str(o.get("orderLinkId", "")) or (layer_name == "minor" and "-MAJ-" not in str(o.get("orderLinkId", "")))
                )
            ]
            existing_orders.sort(key=lambda x: float(x.get("price", 0.0)), reverse=(setup.side == "long"))

            sym_short = sym.replace("USDT.P", "").replace("USDT", "")
            link_id_1 = make_order_link_id(sym_short, layer_tag, side_str, "O1")
            link_id_2 = make_order_link_id(sym_short, layer_tag, side_str, "O2")
            link_id_3 = make_order_link_id(sym_short, layer_tag, side_str, "O3")

            setup_risk = item["setup_risk"]
            specs = client.get_specs(sym)
            setup_timeout = cfg.major_timeout_hours if layer_name == "major" else cfg.minor_timeout_hours

            if pos_open_initially:
                # ─── СИТУАЦИЯ 1: Позиция уже открыта на Bybit ───────────
                avg_p = float(curr_pos.get("avgPrice", 0.0))
                current_risk = pos_sz * abs(avg_p - sl)
                remaining_risk = max(0.0, setup_risk - current_risk)
                console.print(f"ℹ️ [{sym}] [{layer_tag}] Открыта позиция: {pos_sz} шт. @ {avg_p}. Задействованный риск: ${current_risk:.2f} из лимита ${setup_risk:.2f}.")
                if is_live and tp1 is not None and sl is not None:
                    try:
                        client.set_position_tp_sl(sym, take_profit=tp1, stop_loss=sl)
                        console.print(f"  ✓ [{sym}] [{layer_tag}] TP (${tp1}) и SL (${sl}) подтверждены внутри сделки (Position TP/SL).")
                    except Exception:
                        pass

                if remaining_risk <= 0.05:
                    console.print(f"🛡️ [{sym}] [{layer_tag}] Лимит риска ${setup_risk:.2f} уже исчерпан открытой позицией (${current_risk:.2f}). Новые ордера блокируются!")
                    if existing_orders:
                        console.print(f"  ➜ Снимаем {len(existing_orders)} лишних лимитных ордеров своего слоя...")
                        for o in existing_orders:
                            if o.get("orderId"):
                                client.cancel_order(sym, o.get("orderId"))
                        existing_orders = []
                    initial_state = "O1_FILLED"
                else:
                    console.print(f"ℹ️ [{sym}] [{layer_tag}] Остаточный бюджет риска на добор: ${remaining_risk:.2f}.")
                    q2_res, q3_res, _, _, _ = client.calc_residual_order_sizes(
                        pos_sz, avg_p, e2, e3, sl, total_risk_usd=setup_risk, symbol=sym, weights=cfg.grid_weights
                    )

                    # Проверяем, соответствуют ли существующие ордера добора новому расчету риска
                    res_match = False
                    tol2 = max(specs.qty_step, 0.05 * q2_res) if q2_res > 0 else specs.qty_step
                    tol3 = max(specs.qty_step, 0.05 * q3_res) if q3_res > 0 else specs.qty_step

                    if len(existing_orders) >= 2 and e2 is not None and e3 is not None:
                        eq2 = float(existing_orders[0].get("qty", 0.0))
                        eq3 = float(existing_orders[1].get("qty", 0.0))
                        ep2 = float(existing_orders[0].get("price", 0.0))
                        ep3 = float(existing_orders[1].get("price", 0.0))
                        if abs(eq2 - q2_res) <= tol2 and abs(eq3 - q3_res) <= tol3 and abs(ep2 - e2) / e2 < 0.002 and abs(ep3 - e3) / e3 < 0.002:
                            res_match = True
                            o2_id = existing_orders[0].get("orderId")
                            o3_id = existing_orders[1].get("orderId")
                            console.print(f"  ✓ [{layer_tag}] Подключены существующие ордера добора: Ордер 2 ID {o2_id} @ {e2} (qty {eq2}), Ордер 3 ID {o3_id} @ {e3} (qty {eq3})")
                    elif len(existing_orders) == 1 and e2 is not None and (e3 is None or q3_res <= 0):
                        eq2 = float(existing_orders[0].get("qty", 0.0))
                        ep2 = float(existing_orders[0].get("price", 0.0))
                        if abs(eq2 - q2_res) <= tol2 and abs(ep2 - e2) / e2 < 0.002:
                            res_match = True
                            o2_id = existing_orders[0].get("orderId")
                            console.print(f"  ✓ [{layer_tag}] Подключен существующий ордер: Ордер 2 ID {o2_id} @ {e2} (qty {eq2})")

                    if not res_match and existing_orders:
                        console.print(f"🔄 [{sym}] [{layer_tag}] Параметры существующих ордеров добора не соответствуют новому риску. Снимаем для актуализации...")
                        for o in existing_orders:
                            if o.get("orderId"):
                                client.cancel_order(sym, o.get("orderId"))
                        existing_orders = []
                        if is_live and hasattr(client, "get_available_balance"):
                            available_margin = client.get_available_balance()

                    initial_state = "O1_FILLED"

                    if not res_match:
                        cur_p = client.get_ticker_price(sym) if hasattr(client, "get_ticker_price") else 0.0
                        if cur_p <= 0:
                            cur_p = avg_p

                        place_o2 = bool(e2 is not None and q2_res > 0 and tp2 is not None and (e2 < cur_p * 0.9995))
                        place_o3 = bool(e3 is not None and q3_res > 0 and tp3 is not None and (e3 < cur_p * 0.9995))

                        if e2 is not None and q2_res > 0 and not place_o2:
                            console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 2 (${e2}). Добор 2 пропущен.[/yellow]")
                        if e3 is not None and q3_res > 0 and not place_o3:
                            console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 3 (${e3}). Добор 3 пропущен.[/yellow]")

                        needed_margin = 0.0
                        if place_o2 and hasattr(client, "calc_required_margin"):
                            needed_margin += client.calc_required_margin(sym, q2_res, e2)
                        if place_o3 and hasattr(client, "calc_required_margin"):
                            needed_margin += client.calc_required_margin(sym, q3_res, e3)
                        needed_margin *= 1.05

                        if is_live and available_margin < needed_margin:
                            console.print(f"[yellow]⏸️ [{sym}] [{layer_tag}] Недостаточно свободной маржи для добора (${available_margin:.2f} < ${needed_margin:.2f}). Ордера добора пропущены.[/yellow]")
                        else:
                            if place_o2 and e2 is not None and tp2 is not None:
                                resp2 = client.place_order(symbol=sym, side=side_str, order_type="Limit", qty=q2_res, price=e2, take_profit=tp2, stop_loss=sl, order_link_id=link_id_2)
                                o2_id = resp2.get("orderId")
                                console.print(f"  ✅ [{layer_tag}] [Остаточный риск] Ордер 2 размещен: ID {o2_id} (Limit {side_str} {q2_res} @ {e2})")
                            if place_o3 and e3 is not None and tp3 is not None:
                                resp3 = client.place_order(symbol=sym, side=side_str, order_type="Limit", qty=q3_res, price=e3, take_profit=tp3, stop_loss=sl, order_link_id=link_id_3)
                                o3_id = resp3.get("orderId")
                                console.print(f"  ✅ [{layer_tag}] [Остаточный риск] Ордер 3 размещен: ID {o3_id} (Limit {side_str} {q3_res} @ {e3})")
                            if is_live:
                                available_margin = max(0.0, available_margin - needed_margin)
            else:
                # ─── СИТУАЦИЯ 2: Позиции нет (размещение или синхронизация сетки) ─
                match_existing = False
                cur_p = client.get_ticker_price(sym) if hasattr(client, "get_ticker_price") else 0.0
                if cur_p <= 0:
                    try:
                        df_tmp = client.fetch_klines(sym, interval=interval, limit=2)
                        cur_p = float(df_tmp["close"].iloc[-1])
                    except Exception:
                        cur_p = e1

                tol1 = max(specs.qty_step, 0.05 * q1) if q1 > 0 else specs.qty_step
                tol2 = max(specs.qty_step, 0.05 * q2) if q2 > 0 else specs.qty_step
                tol3 = max(specs.qty_step, 0.05 * q3) if q3 > 0 else specs.qty_step

                if len(existing_orders) == 3 and e2 is not None and e3 is not None:
                    p1 = float(existing_orders[0].get("price", 0.0))
                    p2 = float(existing_orders[1].get("price", 0.0))
                    p3 = float(existing_orders[2].get("price", 0.0))
                    qty1 = float(existing_orders[0].get("qty", 0.0))
                    qty2 = float(existing_orders[1].get("qty", 0.0))
                    qty3 = float(existing_orders[2].get("qty", 0.0))
                    sl1 = float(existing_orders[0].get("stopLoss") or 0.0)

                    price_ok = (abs(p1 - e1) / e1 < 0.002 and abs(p2 - e2) / e2 < 0.002 and abs(p3 - e3) / e3 < 0.002)
                    qty_ok = (abs(qty1 - q1) <= tol1 and abs(qty2 - q2) <= tol2 and abs(qty3 - q3) <= tol3)
                    sl_ok = (sl <= 0 or sl1 <= 0 or abs(sl1 - sl) / sl < 0.002)

                    if price_ok and qty_ok and sl_ok:
                        match_existing = True
                        o1_id = existing_orders[0].get("orderId")
                        o2_id = existing_orders[1].get("orderId")
                        o3_id = existing_orders[2].get("orderId")
                        initial_state = "TRAILING" if setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION") else setup.setup_type
                        console.print(f"ℹ️ [{sym}] [{layer_tag}] Найдена готовая сетка из 3 ордеров на Bybit (e1={p1}, e2={p2}, e3={p3} | q1={qty1}, q2={qty2}, q3={qty3}). Подключаем к мониторингу без перевыставления!")
                    elif price_ok and not qty_ok:
                        console.print(f"🔄 [{sym}] [{layer_tag}] Риск в конфиге изменился! Существующие объемы ({qty1}, {qty2}, {qty3}) не совпадают с новыми ({q1}, {q2}, {q3}). Перевыставляем сетку под новый риск...")

                elif len(existing_orders) == 2 and e2 is not None and e3 is None and setup.setup_type == "MANIPULATION":
                    p1 = float(existing_orders[0].get("price", 0.0))
                    p2 = float(existing_orders[1].get("price", 0.0))
                    qty1 = float(existing_orders[0].get("qty", 0.0))
                    qty2 = float(existing_orders[1].get("qty", 0.0))
                    price_ok = (abs(p1 - e1) / e1 < 0.002 and abs(p2 - e2) / e2 < 0.002)
                    qty_ok = (abs(qty1 - q1) <= tol1 and abs(qty2 - q2) <= tol2)
                    if price_ok and qty_ok:
                        match_existing = True
                        o1_id = existing_orders[0].get("orderId")
                        o2_id = existing_orders[1].get("orderId")
                        initial_state = "MANIPULATION_ACTIVE"
                        console.print(f"ℹ️ [{sym}] [{layer_tag}] Найдена готовая сетка манипуляции на Bybit. Подключаем к мониторингу без перевыставления!")
                    elif price_ok and not qty_ok:
                        console.print(f"🔄 [{sym}] [{layer_tag}] Риск манипуляции изменился! Существующие объемы ({qty1}, {qty2}) не совпадают с новыми ({q1}, {q2}). Перевыставляем сетку...")

                if not match_existing:
                    if existing_orders:
                        console.print(f"🔄 [{sym}] [{layer_tag}] Найдено {len(existing_orders)} старых ордеров своего слоя. Снимаем их для актуализации сетки...")
                        for o in existing_orders:
                            if o.get("orderId"):
                                client.cancel_order(sym, o.get("orderId"))
                        existing_orders = []
                        if is_live and hasattr(client, "get_available_balance"):
                            available_margin = client.get_available_balance()

                    # Проверка цен ордеров относительно текущей рыночной цены (защита от покупки выше рынка)
                    is_fib_grid = setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION")
                    is_long = (setup.side == "long")
                    o1_missed = getattr(setup, "o1_filled", False) or is_entry_missed(e1, cur_p, is_long=is_long)
                    o2_missed = getattr(setup, "o2_filled", False) or (e2 is not None and is_entry_missed(e2, cur_p, is_long=is_long))
                    o3_missed = bool(e3 is not None and is_entry_missed(e3, cur_p, is_long=is_long))

                    # Если ВСЕ доступные уровни сетки уже пройдены и позиции нет:
                    if not pos_open and (setup.setup_type == "AWAITING_BREAK_BELOW" or (is_fib_grid and o1_missed and o2_missed and (e3 is None or o3_missed))):
                        console.print(f"  [yellow]⚠️ [{sym}] [{layer_tag}] Все входы сетки (0.500, 0.618, 0.786) уже пройдены. Ожидание пробоя 1.000 (${sl}) без возврата к 0.382 (маржа свободна).[/yellow]")
                        if is_live:
                            cleanup_orphan_orders_for_layer(client, sym, layer_name)
                        active_monitors.append(ActiveTradeMonitor(
                            symbol=sym,
                            setup_type="AWAITING_BREAK_BELOW",
                            state="AWAITING_BREAK_BELOW",
                            layer=layer_name,
                            side=setup.side,
                            cur_peak=setup.imp_peak_price,
                            imp_start_price=setup.imp_start_price,
                            imp_start_time=setup.imp_start_time,
                            p_0382=setup.p_0382,
                            cur_e1=e1,
                            cur_tp1=tp1,
                            cur_e2=e2 if e2 else 0.0,
                            cur_tp2=tp2 if tp2 else 0.0,
                            cur_e3=e3 if e3 else 0.0,
                            cur_tp3=tp3 if tp3 else 0.0,
                            sl=sl,
                            imp_end_time=setup.imp_end_time,
                            timeout_hours=setup_timeout,
                        ))
                        continue

                    place_o1 = not o1_missed
                    place_o2 = bool(e2 is not None and q2 > 0 and tp2 is not None and not o2_missed)
                    place_o3 = bool(e3 is not None and q3 > 0 and tp3 is not None and not o3_missed)

                    if not place_o1:
                        console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Ордер 1 (0.500: ${e1}) уже налит/пройден. Выставляются Ордер 2 и/или Ордер 3.[/yellow]")
                    if not place_o2 and e2 is not None and q2 > 0:
                        console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Ордер 2 (0.618: ${e2}) уже пройден.[/yellow]")
                    if not place_o3 and e3 is not None and q3 > 0:
                        console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Ордер 3 (0.786: ${e3}) уже пройден.[/yellow]")

                    if not (place_o1 or place_o2 or place_o3):
                        console.print(f"  [yellow]⚠️ [{sym}] [{layer_tag}] Все уровни сетки выше текущей цены (${cur_p}). Сетка не выставляется, монета переходит в IDLE.[/yellow]")
                        active_monitors.append(ActiveTradeMonitor(
                            symbol=sym,
                            setup_type="IDLE",
                            state="IDLE",
                            layer=layer_name,
                            last_skipped_imp_time=setup.imp_end_time,
                        ))
                        continue

                    # Проверка свободной маржи
                    needed_margin = 0.0
                    if place_o1 and hasattr(client, "calc_required_margin"):
                        needed_margin += client.calc_required_margin(sym, q1, e1)
                    if place_o2 and hasattr(client, "calc_required_margin"):
                        needed_margin += client.calc_required_margin(sym, q2, e2)
                    if place_o3 and hasattr(client, "calc_required_margin"):
                        needed_margin += client.calc_required_margin(sym, q3, e3)
                    needed_margin *= 1.05

                    if is_live and available_margin < needed_margin:
                        console.print(f"[yellow]⏸️ [{sym}] [{layer_tag}] Недостаточно свободной маржи (${available_margin:.2f} < ${needed_margin:.2f}). Пропуск выставления сетки.[/yellow]")
                        active_monitors.append(ActiveTradeMonitor(symbol=sym, setup_type="IDLE", state="IDLE", layer=layer_name))
                        continue

                    if place_o1:
                        resp1 = client.place_order(
                            symbol=sym, side=side_str, order_type="Limit", qty=q1, price=e1, take_profit=tp1, stop_loss=sl, order_link_id=link_id_1
                        )
                        o1_id = resp1.get("orderId")
                        console.print(f"✅ [{sym}] [{layer_tag}] Ордер 1 размещен: ID {o1_id} (Limit {side_str} {q1} @ {e1}, TP {tp1}, SL {sl})")

                    if place_o2 and e2 is not None and tp2 is not None:
                        resp2 = client.place_order(
                            symbol=sym, side=side_str, order_type="Limit", qty=q2, price=e2, take_profit=tp2, stop_loss=sl, order_link_id=link_id_2
                        )
                        o2_id = resp2.get("orderId")
                        console.print(f"✅ [{sym}] [{layer_tag}] Ордер 2 размещен: ID {o2_id} (Limit {side_str} {q2} @ {e2}, TP {tp2}, SL {sl})")

                    if place_o3 and e3 is not None and tp3 is not None:
                        resp3 = client.place_order(
                            symbol=sym, side=side_str, order_type="Limit", qty=q3, price=e3, take_profit=tp3, stop_loss=sl, order_link_id=link_id_3
                        )
                        o3_id = resp3.get("orderId")
                        console.print(f"✅ [{sym}] [{layer_tag}] Ордер 3 размещен: ID {o3_id} (Limit {side_str} {q3} @ {e3}, TP {tp3}, SL {sl})")

                    if is_live:
                        available_margin = max(0.0, available_margin - needed_margin)

                    if is_fib_grid and o1_missed:
                        initial_state = "O2_FILLED" if o2_missed else "O1_FILLED"
                    else:
                        initial_state = "TRAILING" if setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION") else setup.setup_type

            active_monitors.append(ActiveTradeMonitor(
                symbol=sym,
                setup_type=setup.setup_type,
                state=initial_state,
                layer=layer_name,
                side=setup.side,
                o1_id=o1_id,
                o2_id=o2_id,
                o3_id=o3_id,
                cur_peak=setup.imp_peak_price,
                p_0382=setup.p_0382,
                cur_e1=e1,
                cur_tp1=tp1,
                cur_e2=e2 if e2 else 0.0,
                cur_tp2=tp2 if tp2 else 0.0,
                cur_e3=e3 if e3 else 0.0,
                cur_tp3=tp3 if tp3 else 0.0,
                imp_start_price=setup.imp_start_price,
                imp_start_time=setup.imp_start_time,
                sl=sl,
                q1=q1,
                q2=q2,
                q3=q3,
                has_o2=(o2_id is not None),
                has_o3=(o3_id is not None),
                be_trigger=client.round_price(setup.be_trigger, sym) if setup.be_trigger is not None else None,
                be_price=client.round_price(setup.be_price, sym) if setup.be_price is not None else None,
                position_was_open=pos_open_initially,
                imp_end_time=setup.imp_end_time,
                touched_0382=True,
                timeout_hours=setup_timeout,
            ))

            if is_live:
                active_ids = [o for o in (o1_id, o2_id, o3_id) if o]
                cleanup_orphan_orders_for_layer(client, sym, layer_name, active_order_ids=active_ids)

        except Exception as e:
            console.print(f"[bold red]❌ [{sym}] [{layer_tag}] Ошибка выставления ордеров:[/bold red] {e}")
            if o1_id:
                try:
                    client.cancel_order(sym, o1_id)
                    console.print(f"[yellow][{sym}] [{layer_tag}] Ордер 1 {o1_id} отменен.[/yellow]")
                except Exception:
                    pass

    # 2. Подключаем мониторы AWAITING_MAJOR_0382 (Большие фибы выше 0.382 — без выставления ордеров)
    for item in awaiting_major_setups:
        active_monitors.append(ActiveTradeMonitor(
            symbol=item["symbol"],
            setup_type=item["setup"].setup_type,
            state="AWAITING_MAJOR_0382",
            layer="major",
            cur_peak=item["setup"].imp_peak_price,
            p_0382=item["setup"].p_0382,
            cur_e1=item["e1"],
            cur_tp1=item["tp1"],
            cur_e2=item["e2"] if item["e2"] else 0.0,
            cur_tp2=item["tp2"] if item["tp2"] else 0.0,
            cur_e3=item["e3"] if item["e3"] else 0.0,
            cur_tp3=item["tp3"] if item["tp3"] else 0.0,
            sl=item["sl"],
            q1=item["q1"],
            q2=item["q2"],
            q3=item["q3"],
            has_o2=(item["e2"] is not None and item["q2"] > 0),
            has_o3=(item["e3"] is not None and item["q3"] > 0),
            imp_start_price=item["setup"].imp_start_price,
            imp_start_time=item["setup"].imp_start_time,
            imp_end_time=item["setup"].imp_end_time,
            touched_0382=False,
        ))
        p_0382_val = f"${item['setup'].p_0382:.4f}" if item['setup'].p_0382 else "-"
        console.print(f"⏳ [{item['symbol']}] [MAJOR] Добавлен монитор в режиме AWAITING_MAJOR_0382 (ожидание касания 0.382: {p_0382_val}). Маржа свободна.")

    # 3. Для монет без сетапа на определенном слое создаем IDLE-монитор для автопоиска
    monitored_pairs = {(m.symbol, m.layer) for m in active_monitors}
    for sym in symbols:
        sym_has_monitor = any(m.symbol == sym for m in active_monitors)
        if is_live and not sym_has_monitor:
            try:
                pos_info = client.get_position(sym)
                pos_size = float(pos_info.get("size", 0)) if pos_info else 0.0
                if pos_size > 0:
                    tp_val = float(pos_info.get("takeProfit", 0)) if pos_info.get("takeProfit") else None
                    sl_val = float(pos_info.get("stopLoss", 0)) if pos_info.get("stopLoss") else None
                    console.print(f"ℹ️ [{sym}] Обнаружена открытая позиция {pos_size} шт. (TP: {tp_val}, SL: {sl_val}). Подключаем к мониторингу!")
                    active_monitors.append(ActiveTradeMonitor(
                        symbol=sym,
                        setup_type="TRIPLE_GRID_TRAILING",
                        state="O1_FILLED",
                        layer="minor",
                        position_was_open=True,
                        cur_e1=float(pos_info.get("avgPrice", 0)),
                        cur_tp1=tp_val or 0.0,
                        sl=sl_val or 0.0,
                    ))
                    monitored_pairs.add((sym, "minor"))
            except Exception as err:
                console.print(f"⚠️ [{sym}] Ошибка проверки позиции для неактивной монеты: {err}")

        for layer_name in ("minor", "major"):
            if (sym, layer_name) not in monitored_pairs:
                active_monitors.append(ActiveTradeMonitor(
                    symbol=sym,
                    setup_type="IDLE",
                    state="IDLE",
                    layer=layer_name,
                ))

    # Проверяем наличие открытых позиций на Bybit по монетам вне списка торговли (Close-Only режим)
    if is_live:
        try:
            resp_all = client.session.get_positions(category="linear", settleCoin="USDT")
            pos_list = resp_all.get("result", {}).get("list", [])
            monitored_symbols = {m.symbol for m in active_monitors}
            for p in pos_list:
                p_sym = p.get("symbol", "")
                p_size = float(p.get("size", 0.0))
                if p_size > 0 and p_sym not in monitored_symbols:
                    tp_val = float(p.get("takeProfit", 0)) if p.get("takeProfit") else None
                    sl_val = float(p.get("stopLoss", 0)) if p.get("stopLoss") else None
                    avg_p = float(p.get("avgPrice", 0))
                    console.print(f"ℹ️ [{p_sym}] Обнаружена открытая позиция {p_size} шт. вне списка торговли (TP: {tp_val}, SL: {sl_val}). Подключаем в режиме Close-Only (сопровождение до закрытия без новых сделок)!")
                    active_monitors.append(ActiveTradeMonitor(
                        symbol=p_sym,
                        setup_type="TRIPLE_GRID_TRAILING",
                        state="O1_FILLED",
                        layer="minor",
                        position_was_open=True,
                        cur_e1=avg_p,
                        cur_tp1=tp_val or 0.0,
                        sl=sl_val or 0.0,
                        close_only=True,
                    ))
        except Exception as err:
            console.print(f"⚠️ Ошибка проверки открытых позиций вне списка торговли: {err}")

    if not active_monitors:
        console.print("[yellow]Нет монет для мониторинга. Завершение.[/yellow]")
        return

    # Единый цикл мониторинга для всех монет
    mode_desc = "Одиночный (--once)" if args.once else "Непрерывный фоновый (Daemon / автопоиск новых импульсов)"
    monitor_items_str = ", ".join(f"{m.symbol} [{m.layer.upper()}:{m.state}]" + (" [Close-Only]" if m.close_only else "") for m in active_monitors)
    console.print(Panel(
        f"[bold yellow]Запущен автоматический мониторинг {len(active_monitors)} слоев по монетам:[/bold yellow]\n"
        f"[green]{monitor_items_str}[/green]\n"
        "Бот отслеживает трейлинг, налив ордеров, закрытие по SL/TP, ложный пробой и сетку манипуляции.\n"
        f"Режим: [cyan]{mode_desc}[/cyan].\n"
        "[dim]Для остановки нажмите Ctrl+C.[/dim]",
        border_style="yellow",
    ))

    config_file = Path(cfg.config_path) if cfg.config_path else (root_dir / "config" / "trade_config.yaml")
    last_cfg_mtime = config_file.stat().st_mtime if config_file.exists() else 0.0

    try:
        while True:
            time.sleep(15)

            # Горячая перезагрузка параметров стратегии и риска при изменении файла конфига
            if config_file.exists():
                try:
                    cur_mtime = config_file.stat().st_mtime
                    if cur_mtime > last_cfg_mtime:
                        last_cfg_mtime = cur_mtime
                        old_risk = cfg.total_risk_usd
                        cfg = load_trade_config(config_file)
                        console.print(f"\n[bold magenta]⚡ Обнаружено изменение файла {config_file.name}![/bold magenta]")
                        console.print(f"  ➜ Риск обновлен: ${old_risk:.2f} -> ${cfg.total_risk_usd:.2f} (Minor: ${cfg.minor_risk_usd:.2f}, Major: ${cfg.major_risk_usd:.2f}).")
                except Exception:
                    pass

            for m in active_monitors:
                if m.done:
                    continue
                try:
                    process_monitor_step(m, client, cfg, interval, is_live=is_live)
                except Exception as sym_err:
                    console.print(f"[red]⚠️ [{m.symbol}] Ошибка мониторинга: {sym_err}[/red]")
                # Плавная пауза между мониторами во избежание пиковых всплесков запросов
                time.sleep(0.12)

            if args.once and all(m.done or m.state == "IDLE" for m in active_monitors):
                break

    except KeyboardInterrupt:
        console.print("\n[yellow]Мониторинг остановлен пользователем.[/yellow]")

    console.print("\n[bold green]Завершено.[/bold green]")


if __name__ == "__main__":
    main()

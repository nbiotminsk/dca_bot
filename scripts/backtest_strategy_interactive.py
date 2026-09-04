"""Универсальный бэктестер стратегии «Манипуляция на часе» (из index.php).

Поддерживает:
- Ввод любой монеты Bybit Linear Futures (HYPEUSDT, UNIUSDT, BTCUSDT, CAKEUSDT и др.)
- Выбор таймфрейма свечей от 5m до 1d (5m, 15m, 30m, 1h, 4h, 1d)
- Выбор периода теста: 90, 180, 365, 548 (1.5 года), 730 (2 года) или произвольное число дней
- Порог импульса от 0.5% (0.5, 1.0, 1.5, 2.0 и т.д.)
- Произвольные уровни входа (0.618, 0.500, 1.618 и любые другие)
- Произвольные уровни выхода/тейка (0.382, 0.500 и любые другие)
- Настраиваемый уровень стоп-лосса (0.860, 1.000, 2.000, 2.618 и др.)
- Режим шкалы: логарифмическая (Log Fib, как в index.php) или линейная
- Направление торговли: LONG, SHORT или ОБА (BOTH)
- Опциональный добор DCA (сетка 1x + 2x)
- Интерактивный режим мастера (при запуске без параметров) и CLI-режим

Примеры запуска:
    python3 scripts/backtest_strategy_interactive.py
    python3 scripts/backtest_strategy_interactive.py HYPEUSDT --timeframe 1h --days 180 --impulse 1.5 --entry 0.618 --tp 0.382 --sl 0.860
    python3 scripts/backtest_strategy_interactive.py UNIUSDT -tf 15m -d 365 --impulse 1.0 --entry 1.618 --tp 0.500 --sl 2.395
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from indicators.filter_manager import FilterManager

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    USE_RICH = True
    console = Console()
except ImportError:
    USE_RICH = False
    console = None


# ─── Расчет уровней Фибоначчи ──────────────────────────────────────────────────

def calc_fib(
    high: float,
    low: float,
    level: float,
    is_long: bool = True,
    scale: Literal["log", "linear"] = "log"
) -> float:
    """Расчет уровня Фибоначчи.

    По умолчанию используется ЛОГАРИФМИЧЕСКАЯ шкала строго по формулам из index.php:
    - Long:  exp(ln(high) - level * (ln(high) - ln(low)))
    - Short: exp(ln(low) + level * (ln(high) - ln(low)))
    """
    if high <= 0 or low <= 0:
        return 0.0

    if scale == "linear":
        if is_long:
            return float(high - level * (high - low))
        else:
            return float(low + level * (high - low))

    # Логарифмическая шкала (как в index.php)
    lh = math.log(high)
    ll = math.log(low)
    diff = lh - ll
    if is_long:
        return float(math.exp(lh - level * diff))
    else:
        return float(math.exp(ll + level * diff))


# ─── Структуры данных ─────────────────────────────────────────────────────────

@dataclass
class Impulse:
    start_idx: int
    end_idx: int
    high: float
    low: float
    pct: float
    is_long: bool
    start_time: pd.Timestamp
    end_time: pd.Timestamp


@dataclass
class TradeRecord:
    trade_id: int
    side: Literal["long", "short"]
    impulse_start_time: pd.Timestamp
    impulse_end_time: pd.Timestamp
    impulse_high: float
    impulse_low: float
    impulse_pct: float
    entry_fib: float
    tp_fib: float
    sl_fib: float | None
    entry_idx: int
    entry_time: pd.Timestamp
    entry_price: float
    exit_idx: int
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: Literal["tp", "sl", "timeout"]
    gross_pnl_pct: float
    net_pnl_pct: float
    hold_candles: int
    hold_hours: float
    has_dca: bool = False


# ─── Детектор импульсов ───────────────────────────────────────────────────────

def detect_impulses(
    df: pd.DataFrame,
    min_pct: float = 0.5,
    max_pct: float | None = None,
    side: Literal["long", "short", "both"] = "long",
    scale: Literal["log", "linear"] = "log",
    tolerance_pct: float = 0.0,
    allow_internal: bool = False,
) -> list[Impulse]:
    """Поиск подтвержденных импульсов с фильтрацией по диапазону размаха [min_pct, max_pct].
    
    tolerance_pct: допуск погрешности в % цены при касании уровня 0.500 (напр. 0.1 для 0.1%).
    allow_internal: если True, ищет внутренние (вложенные) импульсы внутри более крупных трендов.
    """
    highs = df["high"].values
    lows = df["low"].values
    times = df["timestamp"].values
    n = len(df)
    impulses: list[Impulse] = []
    tol_mult = tolerance_pct / 100.0

    # 1. Поиск LONG импульсов
    if side in ("long", "both"):
        i = 0
        while i < n - 2:
            l_s = lows[i]
            h_s = highs[i]
            cur_h = h_s
            is_imp = False
            broken = False
            end_idx = i

            j = i + 1
            while j < n:
                l_j = lows[j]
                h_j = highs[j]

                if not is_imp:
                    if l_j < l_s:
                        broken = True
                        break
                    fib_05 = calc_fib(h_s, l_s, 0.500, is_long=True, scale=scale)
                    if l_j <= fib_05:
                        broken = True
                        break
                    if h_j > h_s:
                        is_imp = True
                        cur_h = h_j
                        end_idx = j
                else:
                    fib_05 = calc_fib(cur_h, l_s, 0.500, is_long=True, scale=scale)
                    eff_fib_05 = fib_05 * (1.0 + tol_mult)
                    if l_j <= eff_fib_05:
                        # Касание 0.500 -> пик зафиксирован, импульс завершен
                        break
                    if h_j > cur_h:
                        cur_h = h_j
                        end_idx = j
                j += 1

            if is_imp and not broken:
                pct = (cur_h - l_s) / l_s * 100.0
                if pct >= min_pct and (max_pct is None or pct <= max_pct):
                    impulses.append(
                        Impulse(
                            start_idx=i,
                            end_idx=end_idx,
                            high=cur_h,
                            low=l_s,
                            pct=pct,
                            is_long=True,
                            start_time=pd.to_datetime(times[i]),
                            end_time=pd.to_datetime(times[end_idx]),
                        )
                    )
                    if not allow_internal:
                        i = end_idx + 1
                        continue
            i += 1

    # 2. Поиск SHORT импульсов (дампов)
    if side in ("short", "both"):
        i = 0
        while i < n - 2:
            h_s = highs[i]
            l_s = lows[i]
            cur_l = l_s
            is_dump = False
            broken = False
            end_idx = i

            j = i + 1
            while j < n:
                l_j = lows[j]
                h_j = highs[j]

                if not is_dump:
                    if h_j > h_s:
                        broken = True
                        break
                    fib_05 = calc_fib(h_s, l_s, 0.500, is_long=False, scale=scale)
                    if h_j >= fib_05:
                        broken = True
                        break
                    if l_j < l_s:
                        is_dump = True
                        cur_l = l_j
                        end_idx = j
                else:
                    fib_05 = calc_fib(h_s, cur_l, 0.500, is_long=False, scale=scale)
                    eff_fib_05 = fib_05 * (1.0 - tol_mult)
                    if h_j >= eff_fib_05:
                        # Откат вверх к 0.500 -> дно зафиксировано
                        break
                    if l_j < cur_l:
                        cur_l = l_j
                        end_idx = j
                j += 1

            if is_dump and not broken:
                pct = (h_s - cur_l) / h_s * 100.0
                if pct >= min_pct and (max_pct is None or pct <= max_pct):
                    impulses.append(
                        Impulse(
                            start_idx=i,
                            end_idx=end_idx,
                            high=h_s,
                            low=cur_l,
                            pct=pct,
                            is_long=False,
                            start_time=pd.to_datetime(times[i]),
                            end_time=pd.to_datetime(times[end_idx]),
                        )
                    )
                    if not allow_internal:
                        i = end_idx + 1
                        continue
            i += 1

    # Сортировка по времени начала
    impulses.sort(key=lambda x: x.start_idx)
    return impulses


# ─── Симулятор сделок ─────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    impulses: list[Impulse],
    entry_fib: float = 0.618,
    tp_fib: float = 0.382,
    sl_fib: float | None = 0.860,
    timeout_candles: int = 720,
    scale: Literal["log", "linear"] = "log",
    fee_pct: float = 0.04,
    non_overlapping: bool = True,
    dca_entry_fib: float | None = None,
    dca_mult: float = 2.0,
    dca_tp_fib: float | None = None,
    filter_manager: FilterManager | None = None,
    tolerance_pct: float = 0.0,
) -> list[TradeRecord]:
    """Симуляция исполнения сделок по стратегии с учетом индикаторов-фильтров и допуска погрешности."""
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    times = df["timestamp"].values
    n = len(df)
    tol_mult = tolerance_pct / 100.0

    trades: list[TradeRecord] = []
    last_exit_idx = -1
    trade_counter = 0

    for imp in impulses:
        # Если включен режим без пересечений (non-overlapping)
        if non_overlapping and imp.end_idx <= last_exit_idx:
            continue

        p_entry = calc_fib(imp.high, imp.low, entry_fib, is_long=imp.is_long, scale=scale)
        p_tp = calc_fib(imp.high, imp.low, tp_fib, is_long=imp.is_long, scale=scale)
        p_sl = (
            calc_fib(imp.high, imp.low, sl_fib, is_long=imp.is_long, scale=scale)
            if sl_fib is not None
            else None
        )

        p_dca = (
            calc_fib(imp.high, imp.low, dca_entry_fib, is_long=imp.is_long, scale=scale)
            if dca_entry_fib is not None
            else None
        )
        p_dca_tp = (
            calc_fib(imp.high, imp.low, dca_tp_fib, is_long=imp.is_long, scale=scale)
            if dca_tp_fib is not None
            else p_tp
        )

        # 1. Поиск входа в позицию (до таймаута)
        entry_idx: int | None = None
        start_search = max(imp.end_idx + 1, last_exit_idx + 1 if non_overlapping else 0)
        max_search = min(imp.end_idx + timeout_candles, n)
        eff_entry = p_entry * (1.0 + tol_mult) if imp.is_long else p_entry * (1.0 - tol_mult)

        for k in range(start_search, max_search):
            if imp.is_long:
                # Отмена неисполненного ордера, если цена обновила максимум импульса (новый хай)
                if highs[k] > imp.high:
                    break
                if lows[k] <= eff_entry:
                    if filter_manager is not None and not filter_manager.is_entry_allowed(k, "long", df):
                        continue
                    entry_idx = k
                    break
            else:  # Short
                # Отмена неисполненного ордера, если цена обновила минимум импульса (новый лой)
                if lows[k] < imp.low:
                    break
                if highs[k] >= eff_entry:
                    if filter_manager is not None and not filter_manager.is_entry_allowed(k, "short", df):
                        continue
                    entry_idx = k
                    break

        if entry_idx is None:
            continue

        # 2. Сопровождение открытой позиции
        has_dca = False
        current_entry_price = p_entry
        current_tp_price = p_tp
        exit_idx: int | None = None
        exit_price: float | None = None
        exit_reason: Literal["tp", "sl", "timeout"] = "timeout"
        max_hold = min(entry_idx + timeout_candles, n)

        for m in range(entry_idx + 1, max_hold):
            if imp.is_long:
                # Проверка DCA (если настроен и еще не активирован)
                eff_dca = p_dca * (1.0 + tol_mult) if p_dca is not None else None
                if eff_dca is not None and not has_dca and lows[m] <= eff_dca:
                    has_dca = True
                    # Взвешенная средняя цена: 1 лот по p_entry + dca_mult лотов по p_dca
                    current_entry_price = (1.0 * p_entry + dca_mult * p_dca) / (1.0 + dca_mult)
                    current_tp_price = p_dca_tp

                eff_tp = current_tp_price * (1.0 - tol_mult)
                sl_hit = (p_sl is not None and lows[m] <= p_sl)
                tp_hit = (highs[m] >= eff_tp)

                if sl_hit and tp_hit:
                    exit_idx = m
                    exit_price = p_sl
                    exit_reason = "sl"
                    break
                elif sl_hit:
                    exit_idx = m
                    exit_price = p_sl
                    exit_reason = "sl"
                    break
                elif tp_hit:
                    exit_idx = m
                    exit_price = current_tp_price
                    exit_reason = "tp"
                    break
            else:  # Short
                eff_dca = p_dca * (1.0 - tol_mult) if p_dca is not None else None
                if eff_dca is not None and not has_dca and highs[m] >= eff_dca:
                    has_dca = True
                    current_entry_price = (1.0 * p_entry + dca_mult * p_dca) / (1.0 + dca_mult)
                    current_tp_price = p_dca_tp

                eff_tp = current_tp_price * (1.0 + tol_mult)
                sl_hit = (p_sl is not None and highs[m] >= p_sl)
                tp_hit = (lows[m] <= eff_tp)

                if sl_hit and tp_hit:
                    exit_idx = m
                    exit_price = p_sl
                    exit_reason = "sl"
                    break
                elif sl_hit:
                    exit_idx = m
                    exit_price = p_sl
                    exit_reason = "sl"
                    break
                elif tp_hit:
                    exit_idx = m
                    exit_price = current_tp_price
                    exit_reason = "tp"
                    break

        # Если позиция не закрылась по TP/SL до конца горизонта
        if exit_idx is None:
            exit_idx = max_hold - 1
            exit_price = float(closes[exit_idx])
            exit_reason = "timeout"

        hold_candles = exit_idx - entry_idx
        # Вычисление времени удержания в часах
        t_entry = pd.to_datetime(times[entry_idx])
        t_exit = pd.to_datetime(times[exit_idx])
        hold_hours = (t_exit - t_entry).total_seconds() / 3600.0

        # PnL расчет
        if imp.is_long:
            gross_pnl = (exit_price - current_entry_price) / current_entry_price * 100.0
        else:
            gross_pnl = (current_entry_price - exit_price) / current_entry_price * 100.0

        net_pnl = gross_pnl - fee_pct

        trade_counter += 1
        trades.append(
            TradeRecord(
                trade_id=trade_counter,
                side="long" if imp.is_long else "short",
                impulse_start_time=imp.start_time,
                impulse_end_time=imp.end_time,
                impulse_high=imp.high,
                impulse_low=imp.low,
                impulse_pct=imp.pct,
                entry_fib=entry_fib,
                tp_fib=tp_fib,
                sl_fib=sl_fib,
                entry_idx=entry_idx,
                entry_time=t_entry,
                entry_price=current_entry_price,
                exit_idx=exit_idx,
                exit_time=t_exit,
                exit_price=exit_price,
                exit_reason=exit_reason,
                gross_pnl_pct=gross_pnl,
                net_pnl_pct=net_pnl,
                hold_candles=hold_candles,
                hold_hours=hold_hours,
                has_dca=has_dca,
            )
        )
        last_exit_idx = exit_idx

    return trades


# ─── Расчет сводных метрик ───────────────────────────────────────────────────

def compute_statistics(trades: list[TradeRecord]) -> dict:
    if not trades:
        return {
            "n_trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0,
            "median_pnl": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0,
            "tp_count": 0, "sl_count": 0, "timeout_count": 0,
            "avg_hold_hours": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
        }

    pnls = np.array([t.net_pnl_pct for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    n_trades = len(trades)
    win_rate = (len(wins) / n_trades) * 100.0 if n_trades > 0 else 0.0
    total_pnl = float(np.sum(pnls))
    avg_pnl = float(np.mean(pnls))
    median_pnl = float(np.median(pnls))

    sum_wins = float(np.sum(wins)) if len(wins) > 0 else 0.0
    sum_losses = abs(float(np.sum(losses))) if len(losses) > 0 else 0.0
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else (float("inf") if sum_wins > 0 else 0.0)

    # Максимальная просадка (кумулятивная)
    cum_pnl = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum_pnl)
    drawdowns = peak - cum_pnl
    max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    tp_c = sum(1 for t in trades if t.exit_reason == "tp")
    sl_c = sum(1 for t in trades if t.exit_reason == "sl")
    to_c = sum(1 for t in trades if t.exit_reason == "timeout")
    avg_hold = float(np.mean([t.hold_hours for t in trades]))
    best_trade = float(np.max(pnls))
    worst_trade = float(np.min(pnls))

    return {
        "n_trades": n_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "median_pnl": median_pnl,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "tp_count": tp_c,
        "sl_count": sl_c,
        "timeout_count": to_c,
        "avg_hold_hours": avg_hold,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
    }


# ─── Вывод отчета в консоль ───────────────────────────────────────────────────

def display_report(
    trades: list[TradeRecord],
    symbol: str,
    timeframe: str,
    days: int,
    impulse_pct: float,
    entry_fib: float,
    tp_fib: float,
    sl_fib: float | None,
    side: str,
    scale: str,
    candles_count: int,
    max_impulse_pct: float | None = None,
    dca_entry_fib: float | None = None,
    filter_manager: FilterManager | None = None,
    tolerance_pct: float = 0.0,
) -> None:
    stats = compute_statistics(trades)

    imp_range_str = f"{impulse_pct}% - {max_impulse_pct}%" if max_impulse_pct is not None else f"≥ {impulse_pct}%"
    tol_str = f" | Допуск: {tolerance_pct}%" if tolerance_pct > 0 else ""

    if USE_RICH and console:
        # Заголовок
        header_text = (
            f"[bold cyan]ТЕСТ СТРАТЕГИИ «МАНИПУЛЯЦИЯ НА ЧАСЕ»[/bold cyan]\n"
            f"[bold yellow]Монета:[/bold yellow] {symbol}  |  "
            f"[bold yellow]Таймфрейм:[/bold yellow] {timeframe}  |  "
            f"[bold yellow]Период:[/bold yellow] {days} дней ({candles_count} свечей)\n"
            f"[dim]Импульс: {imp_range_str} | Вход Fib: {entry_fib} | Тейк Fib: {tp_fib} | "
            f"Стоп Fib: {sl_fib if sl_fib is not None else 'Нет'} | Направление: {side.upper()} | Шкала: {scale.upper()}{tol_str}[/dim]"
        )
        if dca_entry_fib is not None:
            header_text += f"\n[magenta]DCA Добор активен: уровень {dca_entry_fib} Fib[/magenta]"
        if filter_manager is not None and filter_manager.has_filters():
            header_text += f"\n[bold green]Фильтры индикаторов:[/bold green] [yellow]{filter_manager.describe()}[/yellow]"

        console.print(Panel(header_text, border_style="cyan"))

        # Таблица метрик
        t_metrics = Table(title="📈 СВОДНЫЕ РЕЗУЛЬТАТЫ БЭКТЕСТА", header_style="bold green")
        t_metrics.add_column("Показатель", style="dim", width=28)
        t_metrics.add_column("Значение", justify="right", style="bold white", width=20)

        pnl_style = "bold green" if stats["total_pnl"] >= 0 else "bold red"
        wr_style = "bold green" if stats["win_rate"] >= 65 else ("yellow" if stats["win_rate"] >= 50 else "red")

        t_metrics.add_row("Всего сделок", str(stats["n_trades"]))
        t_metrics.add_row("Винрейт (Win Rate)", f"[{wr_style}]{stats['win_rate']:.1f}%[/{wr_style}]")
        t_metrics.add_row("Общий PnL (Total PnL)", f"[{pnl_style}]{stats['total_pnl']:+.2f}%[/{pnl_style}]")
        t_metrics.add_row("Средний PnL на сделку", f"[{pnl_style}]{stats['avg_pnl']:+.3f}%[/{pnl_style}]")
        t_metrics.add_row("Медианный PnL", f"{stats['median_pnl']:+.3f}%")
        pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"
        t_metrics.add_row("Профит-фактор (Profit Factor)", pf_str)
        t_metrics.add_row("Макс. просадка (Max Drawdown)", f"[red]{stats['max_drawdown']:.2f}%[/red]")
        t_metrics.add_row(
            "Исходы (TP / SL / Timeout)",
            f"[green]{stats['tp_count']}[/green] / [red]{stats['sl_count']}[/red] / [yellow]{stats['timeout_count']}[/yellow]"
        )
        t_metrics.add_row("Среднее время в сделке", f"{stats['avg_hold_hours']:.1f} ч")
        t_metrics.add_row("Лучшая сделка", f"[green]{stats['best_trade']:+.2f}%[/green]")
        t_metrics.add_row("Худшая сделка", f"[red]{stats['worst_trade']:+.2f}%[/red]")
        if filter_manager is not None and filter_manager.has_filters():
            t_metrics.add_row("Индикаторы-фильтры", f"[yellow]{filter_manager.describe()}[/yellow]")

        console.print(t_metrics)
        console.print()

        # Разбивка по месяцам
        if trades:
            df_trades = pd.DataFrame([
                {
                    "month": t.entry_time.strftime("%Y-%m"),
                    "pnl": t.net_pnl_pct,
                    "is_win": t.exit_reason == "tp",
                    "is_loss": t.exit_reason == "sl",
                }
                for t in trades
            ])
            monthly = df_trades.groupby("month").agg(
                trades=("pnl", "count"),
                wins=("is_win", "sum"),
                losses=("is_loss", "sum"),
                total_pnl=("pnl", "sum"),
            ).reset_index()

            t_month = Table(title="📅 РАЗБИВКА ПО МЕСЯЦАМ", header_style="bold yellow")
            t_month.add_column("Месяц", justify="center")
            t_month.add_column("Сделок", justify="right")
            t_month.add_column("TP", justify="right", style="green")
            t_month.add_column("SL", justify="right", style="red")
            t_month.add_column("Win Rate", justify="right")
            t_month.add_column("PnL за месяц", justify="right")

            cum_m_pnl = 0.0
            for _, row in monthly.iterrows():
                wr_m = (row["wins"] / row["trades"] * 100.0) if row["trades"] > 0 else 0.0
                m_pnl = row["total_pnl"]
                cum_m_pnl += m_pnl
                col = "green" if m_pnl >= 0 else "red"
                t_month.add_row(
                    str(row["month"]),
                    str(int(row["trades"])),
                    str(int(row["wins"])),
                    str(int(row["losses"])),
                    f"{wr_m:.1f}%",
                    f"[{col}]{m_pnl:+.2f}%[/{col}]",
                )
            console.print(t_month)
            console.print()

        # Таблица последних сделок (до 15)
        if trades:
            t_trades = Table(title="📋 ПОСЛЕДНИЕ СДЕЛКИ (МАКСИМУМ 15)", header_style="bold cyan")
            t_trades.add_column("#", justify="right", width=4)
            t_trades.add_column("Тип", justify="center", width=6)
            t_trades.add_column("Вход (дата)", justify="center", width=16)
            t_trades.add_column("Цена входа", justify="right", width=11)
            t_trades.add_column("Выход (дата)", justify="center", width=16)
            t_trades.add_column("Цена выхода", justify="right", width=11)
            t_trades.add_column("Исход", justify="center", width=9)
            t_trades.add_column("PnL %", justify="right", width=9)
            t_trades.add_column("Часов", justify="right", width=7)

            for t in trades[-15:]:
                r_style = "bold green" if t.exit_reason == "tp" else ("bold red" if t.exit_reason == "sl" else "yellow")
                side_style = "bold cyan" if t.side == "long" else "bold magenta"
                p_style = "green" if t.net_pnl_pct >= 0 else "red"
                t_trades.add_row(
                    str(t.trade_id),
                    f"[{side_style}]{t.side.upper()}[/{side_style}]",
                    t.entry_time.strftime("%d.%m.%y %H:%M"),
                    f"{t.entry_price:.4f}",
                    t.exit_time.strftime("%d.%m.%y %H:%M"),
                    f"{t.exit_price:.4f}",
                    f"[{r_style}]{t.exit_reason.upper()}[/{r_style}]",
                    f"[{p_style}]{t.net_pnl_pct:+.2f}%[/{p_style}]",
                    f"{t.hold_hours:.1f}",
                )
            console.print(t_trades)
            console.print()

    else:
        # Fallback вывод в стандартный терминал
        print("\n" + "=" * 60)
        print(f"ТЕСТ СТРАТЕГИИ «МАНИПУЛЯЦИЯ НА ЧАСЕ» | {symbol}")
        print(f"Таймфрейм: {timeframe} | Период: {days} дней | Свечей: {candles_count}")
        print(f"Импульс >= {impulse_pct}% | Вход: {entry_fib} | Тейк: {tp_fib} | Стоп: {sl_fib}")
        print("=" * 60)
        print(f"Всего сделок:        {stats['n_trades']}")
        print(f"Win Rate:            {stats['win_rate']:.1f}%")
        print(f"Total PnL:           {stats['total_pnl']:+.2f}%")
        print(f"Avg PnL:             {stats['avg_pnl']:+.3f}%")
        print(f"Profit Factor:       {stats['profit_factor']:.2f}")
        print(f"Max Drawdown:        {stats['max_drawdown']:.2f}%")
        print(f"TP / SL / Timeout:   {stats['tp_count']} / {stats['sl_count']} / {stats['timeout_count']}")
        print(f"Среднее время:       {stats['avg_hold_hours']:.1f} ч")
        print("=" * 60 + "\n")


# ─── Экспорт в CSV ────────────────────────────────────────────────────────────

def export_to_csv(trades: list[TradeRecord], path: str) -> None:
    records = []
    for t in trades:
        records.append({
            "trade_id": t.trade_id,
            "side": t.side,
            "impulse_start": t.impulse_start_time.isoformat(),
            "impulse_end": t.impulse_end_time.isoformat(),
            "impulse_high": t.impulse_high,
            "impulse_low": t.impulse_low,
            "impulse_pct": t.impulse_pct,
            "entry_fib": t.entry_fib,
            "tp_fib": t.tp_fib,
            "sl_fib": t.sl_fib,
            "entry_time": t.entry_time.isoformat(),
            "entry_price": t.entry_price,
            "exit_time": t.exit_time.isoformat(),
            "exit_price": t.exit_price,
            "exit_reason": t.exit_reason,
            "gross_pnl_pct": round(t.gross_pnl_pct, 4),
            "net_pnl_pct": round(t.net_pnl_pct, 4),
            "hold_candles": t.hold_candles,
            "hold_hours": round(t.hold_hours, 2),
            "has_dca": t.has_dca,
        })
    df = pd.DataFrame(records)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8")
    if USE_RICH and console:
        console.print(f"[green]✓ История сделок успешно сохранена в файл: {p.resolve()}[/green]")
    else:
        print(f"История сделок сохранена: {p.resolve()}")


# ─── Интерактивный мастер запуска ─────────────────────────────────────────────

def run_interactive_wizard() -> dict:
    """Пошаговый диалог в терминале для комфортного выбора параметров."""
    print("\n" + "═" * 65)
    print("  🎯 МАСТЕР БЭКТЕСТА: СТРАТЕГИЯ «МАНИПУЛЯЦИЯ НА ЧАСЕ»")
    print("═" * 65)

    # 1. Монета
    popular_coins = ["HYPEUSDT", "UNIUSDT", "NEARUSDT", "CAKEUSDT", "XRPUSDT", "SUIUSDT", "DOGEUSDT", "BTCUSDT", "ETHUSDT"]
    print("\n1. Введите тикер монеты:")
    for idx, c in enumerate(popular_coins, 1):
        print(f"   [{idx}] {c}", end="  " if idx % 3 != 0 else "\n")
    if len(popular_coins) % 3 != 0:
        print()
    print("   [0] Ввести свой тикер вручную")

    coin_choice = input("Выберите номер или введите тикер [по умолчанию 1 - HYPEUSDT]: ").strip()
    if not coin_choice or coin_choice == "1":
        symbol = "HYPEUSDT"
    elif coin_choice.isdigit() and 1 <= int(coin_choice) <= len(popular_coins):
        symbol = popular_coins[int(coin_choice) - 1]
    elif coin_choice == "0":
        custom = input("Введите тикер (напр. SOLUSDT): ").strip().upper()
        symbol = custom if custom else "HYPEUSDT"
    else:
        symbol = coin_choice.upper()

    # 2. Таймфрейм
    tf_options = ["5m", "15m", "30m", "1h", "4h", "1d"]
    print("\n2. Выберите таймфрейм свечей:")
    for idx, tf in enumerate(tf_options, 1):
        tag = " (по умолчанию)" if tf == "1h" else ""
        print(f"   [{idx}] {tf}{tag}")
    tf_input = input("Выберите номер [1-6] или введите таймфрейм [по умолчанию 4 - 1h]: ").strip().lower()
    if tf_input.isdigit() and 1 <= int(tf_input) <= len(tf_options):
        timeframe = tf_options[int(tf_input) - 1]
    elif tf_input in tf_options:
        timeframe = tf_input
    else:
        timeframe = "1h"

    # 3. Период теста
    period_options = [
        (90, "90 дней (~3 месяца)"),
        (180, "180 дней (~полгода, по умолчанию)"),
        (365, "365 дней (1 год)"),
        (548, "548 дней (1.5 года)"),
        (730, "730 дней (2 года)"),
    ]
    print("\n3. Выберите период теста:")
    for idx, (d, label) in enumerate(period_options, 1):
        print(f"   [{idx}] {label}")
    print("   [6] Ввести свое количество дней")

    p_input = input("Выберите номер [1-6] [по умолчанию 2 - 180 дней]: ").strip()
    if not p_input or p_input == "2":
        days = 180
    elif p_input == "1":
        days = 90
    elif p_input == "3":
        days = 365
    elif p_input == "4":
        days = 548
    elif p_input == "5":
        days = 730
    elif p_input == "6":
        custom_days = input("Введите количество дней (напр. 120): ").strip()
        days = int(custom_days) if custom_days.isdigit() else 180
    elif p_input.isdigit():
        days = int(p_input)
    else:
        days = 180

    # 4. Диапазон % импульса
    print("\n4. Диапазон % импульса (роста/падения):")
    imp_input = input("   Минимальный % импульса (от 0.5%) [по умолчанию 0.5]: ").strip()
    try:
        impulse_pct = float(imp_input) if imp_input else 0.5
        if impulse_pct < 0.5:
            print("   ⚠️ Порог не может быть меньше 0.5%, установлено 0.5%")
            impulse_pct = 0.5
    except ValueError:
        impulse_pct = 0.5

    max_imp_input = input("   Максимальный % импульса (напр. 2.0, 5.0 или Enter для без ограничений): ").strip()
    max_impulse_pct: float | None = None
    if max_imp_input:
        try:
            val = float(max_imp_input)
            if val > impulse_pct:
                max_impulse_pct = val
            else:
                print(f"   ⚠️ Максимальный % ({val}%) должен быть больше минимального ({impulse_pct}%), ограничение снято")
        except ValueError:
            max_impulse_pct = None

    # 5. Уровень входа
    print("\n5. Уровень входа Fib (напр. 0.618, 0.500, 1.618 и любые другие):")
    entry_input = input("Введите Fib входа [по умолчанию 0.618]: ").strip()
    try:
        entry_fib = float(entry_input) if entry_input else 0.618
    except ValueError:
        entry_fib = 0.618

    # 6. Уровень выхода (Take Profit)
    print("\n6. Уровень выхода / Take Profit Fib (напр. 0.382, 0.500 и любые другие):")
    tp_input = input("Введите Fib выхода/тейка [по умолчанию 0.382]: ").strip()
    try:
        tp_fib = float(tp_input) if tp_input else 0.382
    except ValueError:
        tp_fib = 0.382

    # 7. Уровень Стоп-Лосса (Stop Loss)
    print("\n7. Уровень Стоп-Лосса Fib (напр. 0.860, 1.000, 2.000, 2.618 или 'none'):")
    sl_input = input("Введите Fib стопа [по умолчанию 0.860]: ").strip().lower()
    if sl_input == "none" or sl_input == "нет":
        sl_fib = None
    else:
        try:
            sl_fib = float(sl_input) if sl_input else 0.860
        except ValueError:
            sl_fib = 0.860

    # 8. Направление
    print("\n8. Направление торговли:")
    print("   [1] LONG (по умолчанию)")
    print("   [2] SHORT")
    print("   [3] BOTH (Long + Short)")
    side_input = input("Выберите [1-3, по умолчанию 1]: ").strip()
    if side_input == "2":
        side = "short"
    elif side_input == "3":
        side = "both"
    else:
        side = "long"

    # 9. Шкала Фибоначчи
    print("\n9. Шкала уровней Фибоначчи:")
    print("   [1] Логарифмическая (Log Fib, как в index.php - рекомендуется)")
    print("   [2] Линейная (Linear Fib)")
    scale_input = input("Выберите [1-2, по умолчанию 1]: ").strip()
    scale = "linear" if scale_input == "2" else "log"

    # 10. Добор DCA (опционально)
    print("\n10. Использовать усреднение / добор DCA?")
    print("   [1] Одиночный вход (без усреднения, по умолчанию)")
    print("   [2] Сетка DCA (1x на первом уровне + 2x на доборе)")
    dca_choice = input("Выберите [1-2, по умолчанию 1]: ").strip()
    dca_entry = None
    dca_tp = None
    if dca_choice == "2":
        default_dca_entry = 0.618 if entry_fib == 0.500 else (2.000 if entry_fib == 1.618 else round(entry_fib + 0.118, 3))
        d_inp = input(f"Уровень второго входа (добора) [по умолчанию {default_dca_entry}]: ").strip()
        try:
            dca_entry = float(d_inp) if d_inp else default_dca_entry
        except ValueError:
            dca_entry = default_dca_entry
        dtp_inp = input("Уровень тейка при активации добора [по умолчанию 0.500]: ").strip()
        try:
            dca_tp = float(dtp_inp) if dtp_inp else 0.500
        except ValueError:
            dca_tp = 0.500

    # 11. Допуск погрешности касания уровней Фибо (tolerance)
    print("\n11. Допуск погрешности касания уровней Фибо в % цены (спред / микро-недоход):")
    print("   [0.0] Строгое математическое касание (по умолчанию)")
    print("   [0.1] Допуск 0.1% (сглаживает микро-недоходы до уровней 0.500 / входа)")
    tol_inp = input("Введите допуск в % [по умолчанию 0.0]: ").strip()
    try:
        tolerance_pct = float(tol_inp) if tol_inp else 0.0
    except ValueError:
        tolerance_pct = 0.0

    # 12. Подключение индикаторов-фильтров
    print("\n12. Подключить индикаторы-фильтры для точек входа?")
    print("   [0] Без индикаторов (только Price Action + Fib, по умолчанию)")
    print("   [1] RSI (вход только при перепроданности RSI < 35)")
    print("   [2] CCI (вход «Золотой вход» [-100, 0] из index.php)")
    print("   [3] EMA (тренд-фильтр: вход LONG только выше EMA 200)")
    print("   [4] MACD (вход только при растущем моментуме: Histogram > 0)")
    print("   [5] Stochastic RSI (вход только при StochRSI < 20)")
    print("   [6] Полосы Боллинджера (касание или прокол нижней полосы BB)")
    print("   [7] SuperTrend (вход строго по бычьему тренду SuperTrend)")
    print("   [8] ATR (фильтр минимальной волатильности, напр. > 1.0% или > sma)")
    print("   [9] Объем (вход только при повышенном объеме > SMA)")
    print("   [10] Выбрать несколько индикаторов одновременно")
    ind_choice = input("Выберите [0-10, по умолчанию 0]: ").strip()
    rsi_filter = None
    cci_filter = None
    ema_filter = None
    macd_filter = None
    stoch_filter = None
    bb_filter = None
    st_filter = None
    atr_filter = None
    vol_filter = None

    if ind_choice == "1":
        r_inp = input("Условие RSI (напр. '< 35' или '< 30') [по умолчанию < 35]: ").strip()
        rsi_filter = r_inp if r_inp else "< 35"
    elif ind_choice == "2":
        c_inp = input("Условие CCI (golden = [-100, 0], или напр. '< -100') [по умолчанию golden]: ").strip()
        cci_filter = c_inp if c_inp else "golden"
    elif ind_choice == "3":
        e_inp = input("Период EMA тренд-фильтра [по умолчанию 200]: ").strip()
        ema_filter = e_inp if e_inp else "200"
    elif ind_choice == "4":
        m_inp = input("Условие MACD (bullish / cross) [по умолчанию bullish]: ").strip()
        macd_filter = m_inp if m_inp else "bullish"
    elif ind_choice == "5":
        s_inp = input("Условие Stoch RSI (напр. '< 20' или 'cross') [по умолчанию < 20]: ").strip()
        stoch_filter = s_inp if s_inp else "< 20"
    elif ind_choice == "6":
        b_inp = input("Условие Bollinger (touch_lower / '< 0.2' / inside) [по умолчанию touch_lower]: ").strip()
        bb_filter = b_inp if b_inp else "touch_lower"
    elif ind_choice == "7":
        st_inp = input("Условие SuperTrend (trend / bullish) [по умолчанию trend]: ").strip()
        st_filter = st_inp if st_inp else "trend"
    elif ind_choice == "8":
        a_inp = input("Условие ATR (напр. '> 0.5%', '> 1.0%', '> sma') [по умолчанию > 0.5%]: ").strip()
        atr_filter = a_inp if a_inp else "> 0.5%"
    elif ind_choice == "9":
        v_inp = input("Условие объема (напр. '> sma', '> 1.5x', 'spike') [по умолчанию > sma]: ").strip()
        vol_filter = v_inp if v_inp else "> sma"
    elif ind_choice == "10":
        print("   Включить RSI? (введите условие, напр. '< 35', или Enter чтобы пропустить):")
        r_inp = input("   > ").strip()
        if r_inp: rsi_filter = r_inp
        print("   Включить CCI? (введите golden или условие, или Enter чтобы пропустить):")
        c_inp = input("   > ").strip()
        if c_inp: cci_filter = c_inp
        print("   Включить EMA? (введите период, напр. 200, или Enter чтобы пропустить):")
        e_inp = input("   > ").strip()
        if e_inp: ema_filter = e_inp
        print("   Включить MACD? (введите bullish, или Enter чтобы пропустить):")
        m_inp = input("   > ").strip()
        if m_inp: macd_filter = m_inp
        print("   Включить Stoch RSI? (введите '< 20', или Enter чтобы пропустить):")
        s_inp = input("   > ").strip()
        if s_inp: stoch_filter = s_inp
        print("   Включить Полосы Боллинджера? (введите touch_lower или '< 0.2', или Enter):")
        b_inp = input("   > ").strip()
        if b_inp: bb_filter = b_inp
        print("   Включить SuperTrend? (введите trend, или Enter чтобы пропустить):")
        st_inp = input("   > ").strip()
        if st_inp: st_filter = st_inp
        print("   Включить ATR? (введите '> 0.5%', или Enter чтобы пропустить):")
        a_inp = input("   > ").strip()
        if a_inp: atr_filter = a_inp
        print("   Включить объем? (введите '> sma' или '> 1.5x', или Enter чтобы пропустить):")
        v_inp = input("   > ").strip()
        if v_inp: vol_filter = v_inp

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "days": days,
        "impulse": impulse_pct,
        "max_impulse": max_impulse_pct,
        "entry": entry_fib,
        "tp": tp_fib,
        "sl": sl_fib,
        "side": side,
        "scale": scale,
        "dca_entry": dca_entry,
        "dca_mult": 2.0,
        "dca_tp": dca_tp,
        "rsi": rsi_filter,
        "cci": cci_filter,
        "ema": ema_filter,
        "macd": macd_filter,
        "stoch_rsi": stoch_filter,
        "bollinger": bb_filter,
        "supertrend": st_filter,
        "atr": atr_filter,
        "volume": vol_filter,
        "timeout": 720,
        "fee": 0.04,
        "tolerance": tolerance_pct,
        "save_csv": None,
    }


# ─── Главная функция CLI ──────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Бэктест стратегии «Манипуляция на часе» (из index.php)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("symbol", nargs="?", default=None, help="Тикер монеты, напр. HYPEUSDT")
    parser.add_argument("-tf", "--timeframe", default="1h",
                        choices=["5m", "15m", "30m", "1h", "4h", "1d"],
                        help="Таймфрейм свечей")
    parser.add_argument("-d", "--days", type=int, default=180,
                        help="Глубина истории в днях (90, 180, 365, 548, 730 и др.)")
    parser.add_argument("-imp", "--impulse", "--min-impulse", dest="min_impulse", type=float, default=0.5,
                        help="Минимальный % импульса (от 0.5%%)")
    parser.add_argument("-max-imp", "--max-impulse", dest="max_impulse", type=float, default=None,
                        help="Максимальный % импульса (ограничение сверху, напр. 2.0 или 5.0)")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="Допуск погрешности касания уровней Фибо в %% цены (напр. 0.1 для 0.1%%, по умолчанию 0.0)")
    parser.add_argument("--entry", type=float, default=0.618,
                        help="Уровень входа Фибо (0.618, 0.500, 1.618 и др.)")
    parser.add_argument("--tp", type=float, default=0.382,
                        help="Уровень выхода / Take Profit Фибо (0.382, 0.500 и др.)")
    parser.add_argument("--sl", type=str, default="0.860",
                        help="Уровень Стоп-Лосса Фибо (0.860, 1.000, 2.000, 2.618 или none)")
    parser.add_argument("--side", choices=["long", "short", "both"], default="long",
                        help="Направление торговли: long, short, both")
    parser.add_argument("--scale", choices=["log", "linear"], default="log",
                        help="Шкала Фибоначчи: log (как в index.php) или linear")
    parser.add_argument("--timeout", type=int, default=720,
                        help="Таймаут удержания позиции в свечах")
    parser.add_argument("--fee", type=float, default=0.04,
                        help="Комиссия за цикл (вход+выход) в %%")
    parser.add_argument("--dca-entry", type=float, default=None,
                        help="Опциональный уровень второго входа (DCA)")
    parser.add_argument("--dca-mult", type=float, default=2.0,
                        help="Множитель объема для второго входа DCA (2x)")
    parser.add_argument("--dca-tp", type=float, default=None,
                        help="Тейк при активации добора DCA")
    parser.add_argument("--rsi", type=str, default=None,
                        help="Фильтр RSI, напр. '< 35', '< 40', '> 50'")
    parser.add_argument("--cci", type=str, default=None,
                        help="Фильтр CCI, напр. 'golden' ([-100, 0] из index.php), '< -100'")
    parser.add_argument("--macd", type=str, default=None,
                        help="Фильтр MACD, напр. 'bullish', 'cross'")
    parser.add_argument("--stoch-rsi", type=str, default=None,
                        help="Фильтр Stoch RSI, напр. '< 20', 'cross'")
    parser.add_argument("--ema", type=str, default=None,
                        help="Тренд-фильтр EMA: период (напр. '200' или '50') или 'trend'")
    parser.add_argument("--bb", "--bollinger", dest="bollinger", type=str, default=None,
                        help="Фильтр Bollinger Bands, напр. 'touch_lower', '< 0.2', 'inside'")
    parser.add_argument("--supertrend", type=str, default=None,
                        help="Фильтр SuperTrend, напр. 'trend' (зеленый для Long, красный для Short)")
    parser.add_argument("--atr", type=str, default=None,
                        help="Фильтр волатильности ATR, напр. '> 0.5%', '> 1.0%', '> sma'")
    parser.add_argument("--vol", "--volume", dest="volume", type=str, default=None,
                        help="Фильтр объема, напр. '> sma', '> 1.5x', 'spike'")
    parser.add_argument("--allow-overlap", action="store_true",
                        help="Разрешать одновременные пересекающиеся сделки по разным импульсам")
    parser.add_argument("--save-csv", type=str, default=None,
                        help="Путь к файлу для сохранения сделок в формате CSV")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Запустить интерактивный пошаговый мастер")
    parser.add_argument("--cache-dir", default="data/cache",
                        help="Каталог для кеша котировок")

    # Если передан флаг --interactive или скрипт вызван вообще без аргументов
    raw_args = argv if argv is not None else sys.argv[1:]
    is_interactive = ("-i" in raw_args or "--interactive" in raw_args) or (len(raw_args) == 0 and sys.stdin.isatty())

    if is_interactive:
        cfg = run_interactive_wizard()
        symbol = cfg["symbol"]
        timeframe = cfg["timeframe"]
        days = cfg["days"]
        impulse_pct = cfg["impulse"]
        max_impulse_pct = cfg["max_impulse"]
        tolerance_pct = cfg.get("tolerance", 0.0)
        entry_fib = cfg["entry"]
        tp_fib = cfg["tp"]
        sl_fib = cfg["sl"]
        side = cfg["side"]
        scale = cfg["scale"]
        dca_entry_fib = cfg["dca_entry"]
        dca_mult = cfg["dca_mult"]
        dca_tp_fib = cfg["dca_tp"]
        rsi_filter = cfg["rsi"]
        cci_filter = cfg["cci"]
        ema_filter = cfg["ema"]
        macd_filter = cfg["macd"]
        stoch_filter = cfg["stoch_rsi"]
        bb_filter = cfg["bollinger"]
        st_filter = cfg["supertrend"]
        atr_filter = cfg["atr"]
        vol_filter = cfg["volume"]
        timeout_candles = cfg["timeout"]
        fee_pct = cfg["fee"]
        allow_overlap = cfg.get("allow_overlap", False)
        save_csv = cfg["save_csv"]
        cache_dir = "data/cache"
    else:
        args = parser.parse_args(argv)
        symbol = args.symbol if args.symbol else "HYPEUSDT"
        timeframe = args.timeframe
        days = args.days
        impulse_pct = max(0.5, args.min_impulse)
        max_impulse_pct = args.max_impulse
        tolerance_pct = max(0.0, args.tolerance)
        entry_fib = args.entry
        tp_fib = args.tp
        sl_str = str(args.sl).strip().lower()
        sl_fib = None if sl_str in ("none", "null", "no", "0") else float(sl_str)
        side = args.side
        scale = args.scale
        dca_entry_fib = args.dca_entry
        dca_mult = args.dca_mult
        dca_tp_fib = args.dca_tp
        rsi_filter = args.rsi
        cci_filter = args.cci
        ema_filter = args.ema
        macd_filter = args.macd
        stoch_filter = args.stoch_rsi
        bb_filter = args.bollinger
        st_filter = args.supertrend
        atr_filter = args.atr
        vol_filter = args.volume
        timeout_candles = args.timeout
        fee_pct = args.fee
        allow_overlap = args.allow_overlap
        save_csv = args.save_csv
        cache_dir = args.cache_dir

    imp_range_desc = f"от {impulse_pct}% до {max_impulse_pct}%" if max_impulse_pct is not None else f">= {impulse_pct}%"
    print(f"\n[INFO] Загрузка данных Bybit Linear: {symbol}, TF={timeframe}, дней={days}...", file=sys.stderr)
    try:
        df = fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            days=days,
            cache_dir=cache_dir,
            use_cache=True,
        )
    except Exception as e:
        print(f"[ERROR] Не удалось загрузить свечи для {symbol}: {e}", file=sys.stderr)
        return 1

    # Подготовка менеджера индикаторов-фильтров
    filter_mgr = FilterManager()
    if rsi_filter:
        filter_mgr.add_rsi(period=14, condition=rsi_filter)
    if cci_filter:
        filter_mgr.add_cci(period=14, condition=cci_filter)
    if ema_filter:
        period = int(ema_filter) if str(ema_filter).isdigit() else 200
        cond = "trend" if str(ema_filter).isdigit() else ema_filter
        filter_mgr.add_ema(period=period, condition=cond)
    if macd_filter:
        filter_mgr.add_macd(condition=macd_filter)
    if stoch_filter:
        filter_mgr.add_stoch_rsi(condition=stoch_filter)
    if bb_filter:
        filter_mgr.add_bollinger(condition=bb_filter)
    if st_filter:
        filter_mgr.add_supertrend(condition=st_filter)
    if atr_filter:
        filter_mgr.add_atr(condition=atr_filter)
    if vol_filter:
        filter_mgr.add_volume(condition=vol_filter)

    if filter_mgr.has_filters():
        filter_mgr.prepare(df)
        print(f"[INFO] Активные индикаторы-фильтры: {filter_mgr.describe()}", file=sys.stderr)

    print(f"[INFO] Загружено {len(df)} свечей. Поиск импульсов (диапазон {imp_range_desc})...", file=sys.stderr)
    impulses = detect_impulses(
        df,
        min_pct=impulse_pct,
        max_pct=max_impulse_pct,
        side=side,
        scale=scale,
        tolerance_pct=tolerance_pct,
    )
    print(f"[INFO] Найдено подтвержденных импульсов: {len(impulses)}", file=sys.stderr)

    trades = run_backtest(
        df=df,
        impulses=impulses,
        entry_fib=entry_fib,
        tp_fib=tp_fib,
        sl_fib=sl_fib,
        timeout_candles=timeout_candles,
        scale=scale,
        fee_pct=fee_pct,
        non_overlapping=not allow_overlap,
        dca_entry_fib=dca_entry_fib,
        dca_mult=dca_mult,
        dca_tp_fib=dca_tp_fib,
        filter_manager=filter_mgr if filter_mgr.has_filters() else None,
        tolerance_pct=tolerance_pct,
    )
    print(f"[INFO] Совершено сделок: {len(trades)}", file=sys.stderr)

    display_report(
        trades=trades,
        symbol=symbol,
        timeframe=timeframe,
        days=days,
        impulse_pct=impulse_pct,
        max_impulse_pct=max_impulse_pct,
        entry_fib=entry_fib,
        tp_fib=tp_fib,
        sl_fib=sl_fib,
        side=side,
        scale=scale,
        candles_count=len(df),
        dca_entry_fib=dca_entry_fib,
        filter_manager=filter_mgr if filter_mgr.has_filters() else None,
        tolerance_pct=tolerance_pct,
    )

    if save_csv:
        export_to_csv(trades, save_csv)

    return 0


if __name__ == "__main__":
    sys.exit(main())

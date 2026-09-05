#!/usr/bin/env python3
"""
Скрипт комплексного матричного тестирования стратегии на 90 днях (UNIUSDT 1h):
1. Базовые уровни Fib (Long и Short раздельно, SL 0.860 и 1.000, входы/тейки 0.5->0.382/0.236, 0.618->0.382, 0.786->0.500)
2. Зона Манипуляции (Входы 1.414, 1.618, 2.000; SL 2.400, 2.600; TP 0.618, 0.500)
3. Индикаторы поодиночке (RSI, CCI, EMA, MACD, Stoch RSI, BB, SuperTrend, ATR, Volume)
4. Синергия пар индикаторов (Dual Filters)
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from volatility_calc.data_fetcher import fetch_ohlcv
from indicators.filter_manager import FilterManager
from scripts.backtest_strategy_interactive import (
    detect_impulses,
    run_backtest,
    compute_statistics,
)


def run_test_suite(symbol: str = "UNIUSDT", days: int = 90, min_impulse: float = 1.5):
    print("================================================================================")
    print(f"  КОМПЛЕКСНЫЙ БЭКТЕСТ: {symbol} (1h), ПЕРИОД {days} ДНЕЙ")
    print(f"  Шкала: Логарифмическая (Log Fib) | Мин. импульс: >={min_impulse}%")
    print("================================================================================\n")

    df = fetch_ohlcv(symbol, timeframe="1h", days=days, use_cache=True)
    n_candles = len(df)
    print(f"Загружено {n_candles} свечей ({df['timestamp'].iloc[0]} — {df['timestamp'].iloc[-1]})\n")

    # Детектируем импульсы
    long_impulses = detect_impulses(df, min_pct=min_impulse, side="long", scale="log")
    short_impulses = detect_impulses(df, min_pct=min_impulse, side="short", scale="log")
    all_impulses = detect_impulses(df, min_pct=min_impulse, side="both", scale="log")

    print(f"Найдено импульсов: Всего {len(all_impulses)} (LONG: {len(long_impulses)}, SHORT: {len(short_impulses)})\n")

    # ─────────────────────────────────────────────────────────────────────────────
    # 1. МАТРИЦА БАЗОВЫХ УРОВНЕЙ (LONG и SHORT)
    # ─────────────────────────────────────────────────────────────────────────────
    print("=" * 80)
    print("  БЛОК 1: МАТРИЦА БАЗОВЫХ УРОВНЕЙ FIBONACCI")
    print("=" * 80)

    base_combos = [
        # (entry, tp, sl, name)
        (0.500, 0.382, 0.860, "0.500 -> 0.382 (SL 0.860)"),
        (0.500, 0.382, 1.000, "0.500 -> 0.382 (SL 1.000)"),
        (0.500, 0.236, 0.860, "0.500 -> 0.236 (SL 0.860)"),
        (0.500, 0.236, 1.000, "0.500 -> 0.236 (SL 1.000)"),
        (0.618, 0.382, 0.860, "0.618 -> 0.382 (SL 0.860)"),
        (0.618, 0.382, 1.000, "0.618 -> 0.382 (SL 1.000)"),
        (0.786, 0.500, 0.860, "0.786 -> 0.500 (SL 0.860)"),
        (0.786, 0.500, 1.000, "0.786 -> 0.500 (SL 1.000)"),
    ]

    for direction, imps in [("LONG", long_impulses), ("SHORT", short_impulses)]:
        print(f"\n>>> НАПРАВЛЕНИЕ: {direction} (Импульсов: {len(imps)}) <<<")
        results = []
        for entry, tp, sl, name in base_combos:
            trades = run_backtest(df, imps, entry_fib=entry, tp_fib=tp, sl_fib=sl, scale="log")
            stats = compute_statistics(trades)
            pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"
            results.append({
                "Связка": name,
                "Сделок": stats["n_trades"],
                "TP": stats["tp_count"],
                "SL": stats["sl_count"],
                "WinRate": f"{stats['win_rate']:.1f}%",
                "Total PnL": f"{stats['total_pnl']:+.2f}%",
                "Avg PnL": f"{stats['avg_pnl']:+.2f}%",
                "PF": pf_str,
                "MaxDD": f"{stats['max_drawdown']:.2f}%",
                "Время (ч)": f"{stats['avg_hold_hours']:.1f}",
            })
        res_df = pd.DataFrame(results)
        print(res_df.to_string(index=False))

    # ─────────────────────────────────────────────────────────────────────────────
    # 2. МАТРИЦА ЗОНЫ МАНИПУЛЯЦИИ (ВХОДЫ НИЖЕ 1.0)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  БЛОК 2: ЗОНА МАНИПУЛЯЦИИ (Входы 1.414, 1.618, 2.000)")
    print("=" * 80)

    manip_combos = []
    for entry in [1.414, 1.618, 2.000]:
        for tp in [0.618, 0.500]:
            for sl in [2.400, 2.600]:
                manip_combos.append((entry, tp, sl, f"Вход {entry:.3f} | TP {tp:.3f} | SL {sl:.3f}"))

    for direction, imps in [("LONG (Ловля шпилек на проливах)", long_impulses), ("SHORT (Ловля выносов хаев)", short_impulses)]:
        print(f"\n>>> МАНИПУЛЯЦИЯ: {direction} <<<")
        results = []
        for entry, tp, sl, name in manip_combos:
            trades = run_backtest(df, imps, entry_fib=entry, tp_fib=tp, sl_fib=sl, scale="log")
            stats = compute_statistics(trades)
            pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"
            results.append({
                "Конфигурация": name,
                "Входов": stats["n_trades"],
                "TP": stats["tp_count"],
                "SL": stats["sl_count"],
                "WinRate": f"{stats['win_rate']:.1f}%" if stats["n_trades"] > 0 else "0.0%",
                "Total PnL": f"{stats['total_pnl']:+.2f}%",
                "Avg PnL": f"{stats['avg_pnl']:+.2f}%",
                "PF": pf_str,
                "MaxDD": f"{stats['max_drawdown']:.2f}%",
            })
        res_df = pd.DataFrame(results)
        print(res_df.to_string(index=False))

    # ─────────────────────────────────────────────────────────────────────────────
    # 3. ТЕСТ ОДИНОЧНЫХ ИНДИКАТОРОВ (ДЛЯ LONG НАПРАВЛЕНИЯ)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  БЛОК 3: ТЕСТИРОВАНИЕ ИНДИКАТОРОВ ПООДИНОЧКЕ (LONG)")
    print("  Базовая связка: Вход 0.500, Тейк 0.382, Стоп 1.000 (высокая частота сделок)")
    print("=" * 80)

    # Базовый тест LONG без индикаторов
    base_trades_long = run_backtest(df, long_impulses, entry_fib=0.500, tp_fib=0.382, sl_fib=1.000, scale="log")
    base_stats_l = compute_statistics(base_trades_long)
    base_n_l = base_stats_l["n_trades"]
    base_wr_l = base_stats_l["win_rate"]
    base_pnl_l = base_stats_l["total_pnl"]

    print(f"\nБазовый LONG (0.500->0.382, SL 1.000): Сделок={base_n_l}, WR={base_wr_l:.1f}%, PnL={base_pnl_l:+.2f}%\n")

    single_filters = [
        ("Без индикаторов", None),
        ("RSI (< 40)", lambda m: m.add_rsi(period=14, condition="< 40")),
        ("RSI (< 35)", lambda m: m.add_rsi(period=14, condition="< 35")),
        ("CCI (Golden [-100, 0])", lambda m: m.add_cci(period=14, condition="golden")),
        ("CCI (< -100)", lambda m: m.add_cci(period=14, condition="< -100")),
        ("EMA 200 (Выше EMA)", lambda m: m.add_ema(period=200, condition="trend")),
        ("EMA 50 (Выше EMA)", lambda m: m.add_ema(period=50, condition="trend")),
        ("MACD (Bullish Histogram > 0)", lambda m: m.add_macd(condition="bullish")),
        ("Stoch RSI (< 20)", lambda m: m.add_stoch_rsi(condition="< 20")),
        ("Bollinger (Касание нижней)", lambda m: m.add_bollinger(condition="touch_lower")),
        ("SuperTrend (Bullish Trend)", lambda m: m.add_supertrend(condition="trend")),
        ("ATR (> SMA)", lambda m: m.add_atr(condition="> sma")),
        ("Volume (> SMA)", lambda m: m.add_volume(condition="> sma")),
        ("Volume (> 1.5x)", lambda m: m.add_volume(condition="> 1.5x")),
    ]

    ind_results = []
    for name, setup_fn in single_filters:
        fm = FilterManager()
        if setup_fn is not None:
            setup_fn(fm)
            fm.prepare(df)
        trades = run_backtest(df, long_impulses, entry_fib=0.500, tp_fib=0.382, sl_fib=1.000, scale="log", filter_manager=fm if fm.has_filters() else None)
        stats = compute_statistics(trades)
        n = stats["n_trades"]
        filtered_pct = ((base_n_l - n) / base_n_l * 100.0) if base_n_l > 0 else 0.0
        pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"
        ind_results.append({
            "Индикатор-фильтр": name,
            "Сделок": n,
            "Отсев %": f"-{filtered_pct:.1f}%",
            "WinRate": f"{stats['win_rate']:.1f}%",
            "Дельта WR": f"{stats['win_rate'] - base_wr_l:+.1f}%",
            "Total PnL": f"{stats['total_pnl']:+.2f}%",
            "Avg PnL": f"{stats['avg_pnl']:+.2f}%",
            "PF": pf_str,
            "MaxDD": f"{stats['max_drawdown']:.2f}%",
        })
    print(pd.DataFrame(ind_results).to_string(index=False))

    # ─────────────────────────────────────────────────────────────────────────────
    # 4. СИНЕРГИЯ ПАР ИНДИКАТОРОВ (DUAL FILTERS ДЛЯ LONG)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  БЛОК 4: СИНЕРГИЯ ПАР ИНДИКАТОРОВ (DUAL FILTERS ДЛЯ LONG)")
    print("=" * 80)

    pairs = [
        ("CCI (Golden) + SuperTrend", lambda m: (m.add_cci(14, condition="golden"), m.add_supertrend(condition="trend"))),
        ("CCI (Golden) + Volume (> SMA)", lambda m: (m.add_cci(14, condition="golden"), m.add_volume(condition="> sma"))),
        ("CCI (Golden) + EMA 200", lambda m: (m.add_cci(14, condition="golden"), m.add_ema(200, condition="trend"))),
        ("SuperTrend + Volume (> SMA)", lambda m: (m.add_supertrend(condition="trend"), m.add_volume(condition="> sma"))),
        ("SuperTrend + EMA 200", lambda m: (m.add_supertrend(condition="trend"), m.add_ema(200, condition="trend"))),
        ("RSI (< 40) + SuperTrend", lambda m: (m.add_rsi(14, condition="< 40"), m.add_supertrend(condition="trend"))),
        ("RSI (< 40) + Volume (> SMA)", lambda m: (m.add_rsi(14, condition="< 40"), m.add_volume(condition="> sma"))),
        ("Bollinger + CCI (Golden)", lambda m: (m.add_bollinger(condition="touch_lower"), m.add_cci(14, condition="golden"))),
        ("Bollinger + SuperTrend", lambda m: (m.add_bollinger(condition="touch_lower"), m.add_supertrend(condition="trend"))),
        ("MACD + SuperTrend", lambda m: (m.add_macd(condition="bullish"), m.add_supertrend(condition="trend"))),
        ("MACD + Volume (> SMA)", lambda m: (m.add_macd(condition="bullish"), m.add_volume(condition="> sma"))),
        ("Stoch RSI + ATR (> sma)", lambda m: (m.add_stoch_rsi(condition="< 20"), m.add_atr(condition="> sma"))),
        ("SuperTrend + ATR (> sma)", lambda m: (m.add_supertrend(condition="trend"), m.add_atr(condition="> sma"))),
        ("EMA 200 + Volume (> SMA)", lambda m: (m.add_ema(200, condition="trend"), m.add_volume(condition="> sma"))),
    ]

    pair_results = []
    for name, setup_fn in pairs:
        fm = FilterManager()
        setup_fn(fm)
        fm.prepare(df)
        trades = run_backtest(df, long_impulses, entry_fib=0.500, tp_fib=0.382, sl_fib=1.000, scale="log", filter_manager=fm)
        stats = compute_statistics(trades)
        n = stats["n_trades"]
        filtered_pct = ((base_n_l - n) / base_n_l * 100.0) if base_n_l > 0 else 0.0
        pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"
        pair_results.append({
            "Пара индикаторов": name,
            "Сделок": n,
            "Отсев %": f"-{filtered_pct:.1f}%",
            "WinRate": f"{stats['win_rate']:.1f}%",
            "Дельта WR": f"{stats['win_rate'] - base_wr_l:+.1f}%",
            "Total PnL": f"{stats['total_pnl']:+.2f}%",
            "Avg PnL": f"{stats['avg_pnl']:+.2f}%",
            "PF": pf_str,
            "MaxDD": f"{stats['max_drawdown']:.2f}%",
        })
    print(pd.DataFrame(pair_results).to_string(index=False))
    print("\n" + "=" * 80)
    print("  ТЕСТИРОВАНИЕ ЗАВЕРШЕНО УСПЕШНО")
    print("=" * 80)


if __name__ == "__main__":
    run_test_suite("UNIUSDT", days=90, min_impulse=1.5)

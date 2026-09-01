#!/usr/bin/env python3
"""
Скрипт для тестирования:
1. Вариаций CCI (CCI 14, CCI 50, и их пар)
2. Диапазонов импульсов от 1.0% до 5.0% с шагом 0.5%
на UNIUSDT 1h (90 дней, Log Fib).
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


def run_cci_and_buckets():
    print("=" * 85)
    print("  ИССЛЕДОВАНИЕ 1: СРАВНЕНИЕ CCI 14, CCI 50 И ИХ ПАР")
    print("  Монета: UNIUSDT (1h), 90 дней, Log Fib")
    print("=" * 85)

    df = fetch_ohlcv("UNIUSDT", timeframe="1h", days=90, use_cache=True)
    long_impulses = detect_impulses(df, min_pct=1.5, side="long", scale="log")
    print(f"Загружено {len(df)} свечей. Найдено Long-импульсов (>=1.5%): {len(long_impulses)}\n")

    # Базовая конфигурация LONG: 0.500 -> 0.382 (SL 1.000)
    base_trades = run_backtest(df, long_impulses, entry_fib=0.500, tp_fib=0.382, sl_fib=1.000, scale="log")
    base_stats = compute_statistics(base_trades)
    base_n = base_stats["n_trades"]
    base_wr = base_stats["win_rate"]

    cci_tests = [
        ("Без фильтров (База)", None),
        ("CCI 14 Golden [-100, 0]", lambda m: m.add_cci(period=14, condition="golden")),
        ("CCI 14 (< 0)", lambda m: m.add_cci(period=14, condition="< 0")),
        ("CCI 14 (< -100)", lambda m: m.add_cci(period=14, condition="< -100")),
        ("CCI 50 Golden [-100, 0]", lambda m: m.add_cci(period=50, condition="golden")),
        ("CCI 50 (< 0)", lambda m: m.add_cci(period=50, condition="< 0")),
        ("CCI 50 (> 0)", lambda m: m.add_cci(period=50, condition="> 0")),
        ("CCI 50 (< -100)", lambda m: m.add_cci(period=50, condition="< -100")),
        # Пары
        ("CCI 14 Golden + CCI 50 Golden", lambda m: (m.add_cci(14, condition="golden"), m.add_cci(50, condition="golden"))),
        ("CCI 14 Golden + CCI 50 (< 0)", lambda m: (m.add_cci(14, condition="golden"), m.add_cci(50, condition="< 0"))),
        ("CCI 14 Golden + CCI 50 (> 0)", lambda m: (m.add_cci(14, condition="golden"), m.add_cci(50, condition="> 0"))),
        ("CCI 14 (< 0) + CCI 50 (< 0)", lambda m: (m.add_cci(14, condition="< 0"), m.add_cci(50, condition="< 0"))),
        ("CCI 14 (< -100) + CCI 50 (< -100)", lambda m: (m.add_cci(14, condition="< -100"), m.add_cci(50, condition="< -100"))),
    ]

    results_cci = []
    for name, setup_fn in cci_tests:
        fm = FilterManager()
        if setup_fn is not None:
            setup_fn(fm)
            fm.prepare(df)
        trades = run_backtest(df, long_impulses, entry_fib=0.500, tp_fib=0.382, sl_fib=1.000, scale="log", filter_manager=fm if fm.has_filters() else None)
        stats = compute_statistics(trades)
        n = stats["n_trades"]
        filtered_pct = ((base_n - n) / base_n * 100.0) if base_n > 0 else 0.0
        pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"
        results_cci.append({
            "Фильтр CCI": name,
            "Сделок": n,
            "Отсев %": f"-{filtered_pct:.1f}%",
            "WinRate": f"{stats['win_rate']:.1f}%",
            "Дельта WR": f"{stats['win_rate'] - base_wr:+.1f}%",
            "Total PnL": f"{stats['total_pnl']:+.2f}%",
            "Avg PnL": f"{stats['avg_pnl']:+.2f}%",
            "PF": pf_str,
            "MaxDD": f"{stats['max_drawdown']:.2f}%",
        })

    print(pd.DataFrame(results_cci).to_string(index=False))

    # ─────────────────────────────────────────────────────────────────────────
    # ИССЛЕДОВАНИЕ 2: ТЕСТ КОРЗИН ИМПУЛЬСОВ (ОТ 1% ДО 5% С ШАГОМ 0.5%)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 85)
    print("  ИССЛЕДОВАНИЕ 2: ТЕСТ КОРЗИН РАЗМАХА ИМПУЛЬСА (ОТ 1.0% ДО 5.0% С ШАГОМ 0.5%)")
    print("  Сетап: LONG, Вход 0.500, Тейк 0.382, Стоп 1.000 (Log Fib)")
    print("=" * 85)

    buckets = [
        (1.0, 1.5, "1.0% — 1.5%"),
        (1.5, 2.0, "1.5% — 2.0%"),
        (2.0, 2.5, "2.0% — 2.5%"),
        (2.5, 3.0, "2.5% — 3.0%"),
        (3.0, 3.5, "3.0% — 3.5%"),
        (3.5, 4.0, "3.5% — 4.0%"),
        (4.0, 4.5, "4.0% — 4.5%"),
        (4.5, 5.0, "4.5% — 5.0%"),
        (5.0, 999.0, ">= 5.0% (Крупные)"),
        (1.0, 5.0, "1.0% — 5.0% (Все малые/средние)"),
        (1.5, 999.0, ">= 1.5% (Стандарт index.php)"),
    ]

    results_buckets = []
    for min_p, max_p, label in buckets:
        max_val = max_p if max_p < 500 else None
        imps = detect_impulses(df, min_pct=min_p, max_pct=max_val, side="long", scale="log")
        trades = run_backtest(df, imps, entry_fib=0.500, tp_fib=0.382, sl_fib=1.000, scale="log")
        stats = compute_statistics(trades)
        pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"
        results_buckets.append({
            "Размах импульса": label,
            "Импульсов": len(imps),
            "Сделок": stats["n_trades"],
            "TP": stats["tp_count"],
            "SL": stats["sl_count"],
            "WinRate": f"{stats['win_rate']:.1f}%" if stats["n_trades"] > 0 else "0.0%",
            "Total PnL": f"{stats['total_pnl']:+.2f}%",
            "Avg PnL": f"{stats['avg_pnl']:+.2f}%",
            "PF": pf_str,
            "MaxDD": f"{stats['max_drawdown']:.2f}%",
            "Ср. время (ч)": f"{stats['avg_hold_hours']:.1f}",
        })

    print(pd.DataFrame(results_buckets).to_string(index=False))

    # Также проверим корзины для более прибыльной связки: 0.500 -> 0.236 (SL 0.860)
    print("\n" + "=" * 85)
    print("  ИССЛЕДОВАНИЕ 2.2: КОРЗИНЫ ДЛЯ ШИРОКОГО ТЕЙКА (0.500 -> 0.236, SL 0.860)")
    print("=" * 85)
    results_buckets_wide = []
    for min_p, max_p, label in buckets:
        max_val = max_p if max_p < 500 else None
        imps = detect_impulses(df, min_pct=min_p, max_pct=max_val, side="long", scale="log")
        trades = run_backtest(df, imps, entry_fib=0.500, tp_fib=0.236, sl_fib=0.860, scale="log")
        stats = compute_statistics(trades)
        pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"
        results_buckets_wide.append({
            "Размах импульса": label,
            "Импульсов": len(imps),
            "Сделок": stats["n_trades"],
            "TP": stats["tp_count"],
            "SL": stats["sl_count"],
            "WinRate": f"{stats['win_rate']:.1f}%" if stats["n_trades"] > 0 else "0.0%",
            "Total PnL": f"{stats['total_pnl']:+.2f}%",
            "Avg PnL": f"{stats['avg_pnl']:+.2f}%",
            "PF": pf_str,
            "MaxDD": f"{stats['max_drawdown']:.2f}%",
        })
    print(pd.DataFrame(results_buckets_wide).to_string(index=False))


if __name__ == "__main__":
    run_cci_and_buckets()

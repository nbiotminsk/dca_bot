#!/usr/bin/env python3
"""
Скрипт бэктеста стратегии Манипуляции (пробой уровня 1.000):
  - Вход 1:  1.618 Fib
  - Добор 2: 2.000 Fib
  - Стоп:    2.400 Fib (для обоих ордеров)
  - Тейк 1:  0.500 Fib
  - Корзина: 1.000 Fib

Запуск:
    python3 scripts/backtest_manipulation_grid.py --symbol UNIUSDT --days 180
    python3 scripts/backtest_manipulation_grid.py --all --days 180
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from volatility_calc.data_fetcher import fetch_ohlcv
from scripts.backtest_strategy_interactive import detect_impulses
from scripts.strategy_engine import simulate_manipulation_grid, summarize

TOP_COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "UNIUSDT",
    "LINKUSDT", "DOGEUSDT", "AVAXUSDT", "NEARUSDT", "SUIUSDT"
]

def run_test(sym: str, days: int, e1: float, e2: float, sl: float, tp1: float, basket: float, risk: float, side: str = "long"):
    df = fetch_ohlcv(sym, timeframe="1h", days=days, use_cache=True)
    imps = detect_impulses(df, min_pct=3.0, side=side, scale="log", allow_internal=True)
    trades = simulate_manipulation_grid(
        df, imps,
        entry_fib_1=e1,
        entry_fib_2=e2,
        sl_fib=sl,
        tp_fib_1=tp1,
        basket_tp=basket,
        risk_per_order=risk,
    )
    s = summarize(trades)
    s["symbol"] = sym
    s["trades"] = trades
    return s

def main():
    parser = argparse.ArgumentParser(description="Бэктест стратегии Манипуляции (1.618 -> 2.0 -> SL 2.4)")
    parser.add_argument("--symbol", type=str, default="UNIUSDT", help="Торговая пара (например, UNIUSDT)")
    parser.add_argument("--days", type=int, default=180, help="Количество дней бэктеста")
    parser.add_argument("--all", action="store_true", help="Прогнать по ТОП-10 монетам")
    parser.add_argument("--side", type=str, default="long", choices=["long", "short", "both"], help="Направление торговли (по умолчанию long)")
    parser.add_argument("--e1", type=float, default=1.618, help="Уровень первого входа (по умолчанию 1.618)")
    parser.add_argument("--e2", type=float, default=2.000, help="Уровень добора (по умолчанию 2.000)")
    parser.add_argument("--sl", type=float, default=2.400, help="Уровень Стоп-Лосса (по умолчанию 2.400)")
    parser.add_argument("--tp1", type=float, default=1.000, help="Тейк одиночного входа (по умолчанию 1.000)")
    parser.add_argument("--basket", type=float, default=1.000, help="Тейк корзины при доборе (по умолчанию 1.000)")
    parser.add_argument("--risk", type=float, default=10.0, help="Риск на ордер в $ (по умолчанию $10)")
    args = parser.parse_args()

    symbols = TOP_COINS if args.all else [args.symbol]

    print("=" * 80)
    print(f"🎯 БЭКТЕСТ СТРАТЕГИИ МАНИПУЛЯЦИИ (Вход {args.e1} | Добор {args.e2} | Стоп {args.sl})")
    print(f"Период: {args.days} дней | Риск: ${args.risk:.1f}/ордер | TP1: {args.tp1} | Корзина: {args.basket}")
    print("=" * 80)

    results = []
    tot_pnl = 0.0

    print(f"{'Монета':<10} | {'Сделок':>7} | {'Побед':>6} | {'Стопов':>7} | {'Win Rate':>8} | {'PnL ($)':>10} | {'В мес ($)':>10}")
    print("-" * 80)

    for sym in symbols:
        try:
            s = run_test(sym, args.days, args.e1, args.e2, args.sl, args.tp1, args.basket, args.risk, side=args.side)
            results.append(s)
            tot_pnl += s["pnl"]
            print(f"{sym:<10} | {s['n']:>7d} | {s['wins']:>6d} | {s['sl_count']:>7d} | {s['wr']:>7.1f}% | {s['pnl']:>+9.2f}$ | {s['pnl']/(args.days/30):>+9.2f}$")
        except Exception as e:
            print(f"{sym:<10} | Ошибка: {e}")

    print("-" * 80)
    print(f"ИТОГО ПО ВСЕМ МОНЕТАМ: PnL = {tot_pnl:>+9.2f}$")
    print("=" * 80)

if __name__ == "__main__":
    main()

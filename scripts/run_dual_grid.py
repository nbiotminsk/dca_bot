#!/usr/bin/env python3
"""
Итоговый бэктест двухордерной Fibonacci-сетки (через strategy_engine).

Прогоняет 3 конфигурации для 12 монет (90 дней, 1h, импульс >= 3%):
  A. Раздельные тейки : 0.500→0.236 | 0.618→0.382 | стоп 1.000
  B. Корзинный выход  : 0.500+0.618 → оба на 0.382 | стоп 1.000
  C. One-and-Done     : 0.500→0.236 | 0.618→0.382 | стоп 1.000  (базовый режим, эквивалент A)

Депозит: $1 000 | Риск на каждый ордер: $10 | Всего риск на сделку: $20
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from scripts.backtest_strategy_interactive import detect_impulses
from scripts.strategy_engine import GridConfig, simulate_grid, summarize
from scripts.constants import COINS_12, TEST_PERIOD_MONTHS

# ──────────────────────────────────────────────────────────────────────────────
# Конфигурации стратегий
# ──────────────────────────────────────────────────────────────────────────────
CONFIGS: list[tuple[str, GridConfig]] = [
    (
        "A: 0.500→0.236 / 0.618→0.382 (раздельно)",
        GridConfig(entry_fib_1=0.500, tp_fib_1=0.236,
                   entry_fib_2=0.618, tp_fib_2=0.382,
                   sl_fib=1.000, basket_tp=None),
    ),
    (
        "B: корзина 0.500+0.618 → 0.382 (basket)",
        GridConfig(entry_fib_1=0.500, tp_fib_1=0.236,
                   entry_fib_2=0.618, tp_fib_2=0.382,
                   sl_fib=1.000, basket_tp=0.382),
    ),
]

RISK_PER_ORDER = 10.0
MIN_IMPULSE_PCT = 3.0
TIMEFRAME = "1h"
DAYS = 90


def print_sep(char="=", w=120):
    print(char * w)


def main():
    print_sep()
    print("  БЭКТЕСТ ДВУХОРДЕРНОЙ СЕТКИ (strategy_engine.py)")
    print(f"  Таймфрейм: {TIMEFRAME} | Импульс >= {MIN_IMPULSE_PCT}% | "
          f"Риск: ${RISK_PER_ORDER}/ордер (${RISK_PER_ORDER * 2}/сделку) | "
          f"Период: {DAYS} дней")
    print_sep()

    # ── Загрузка данных ──────────────────────────────────────────────────────
    print("\nЗагрузка данных...")
    data, impulses = {}, {}
    for name, symbol in COINS_12:
        try:
            df = fetch_ohlcv(symbol, timeframe=TIMEFRAME, days=DAYS, use_cache=True)
            imps = detect_impulses(df, min_pct=MIN_IMPULSE_PCT, side="both", scale="log")
            data[name] = df
            impulses[name] = imps
            print(f"  {name:<6} ✓  ({len(df)} свечей, {len(imps)} импульсов)")
        except Exception as exc:
            print(f"  {name:<6} ✗  Ошибка: {exc}")
    print()

    # ── Прогон конфигураций ──────────────────────────────────────────────────
    results: dict[str, dict[str, dict]] = {}   # cfg_name → coin_name → summary

    for cfg_name, cfg in CONFIGS:
        cfg.risk_per_order = RISK_PER_ORDER
        results[cfg_name] = {}
        for name in data:
            trades = simulate_grid(data[name], impulses[name], cfg)
            results[cfg_name][name] = summarize(trades)

    # ── Таблица по монетам ───────────────────────────────────────────────────
    for cfg_name, _ in CONFIGS:
        print_sep()
        print(f"  {cfg_name}")
        print_sep()
        hdr = (f"{'Монета':<8} | {'Сделок':<7} | {'Win/Loss':<10} | "
               f"{'WR%':<7} | {'Оба входа':<10} | {'Только 0.500':<13} | "
               f"{'Прибыль $':<12} | {'$/мес':<9}")
        print(hdr)
        print("-" * len(hdr))

        coin_results = results[cfg_name]
        tot = {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0, "both": 0, "only_o1": 0}

        for name, _ in COINS_12:
            if name not in coin_results:
                continue
            r = coin_results[name]
            wl = f"{r['wins']}/{r['losses']}"
            per_m = r["pnl"] / TEST_PERIOD_MONTHS
            print(f"{name:<8} | {r['n']:<7} | {wl:<10} | "
                  f"{r['wr']:>5.1f}%  | {r['both']:>8}   | {r['only_o1']:>11}   | "
                  f"{r['pnl']:>+10.2f} $  | {per_m:>+7.1f} $/м")
            tot["n"]       += r["n"]
            tot["wins"]    += r["wins"]
            tot["losses"]  += r["losses"]
            tot["pnl"]     += r["pnl"]
            tot["both"]    += r["both"]
            tot["only_o1"] += r["only_o1"]

        print("-" * len(hdr))
        wr_tot  = (tot["wins"] / tot["n"] * 100) if tot["n"] else 0.0
        per_tot = tot["pnl"] / TEST_PERIOD_MONTHS
        wl_tot  = f"{tot['wins']}/{tot['losses']}"
        print(f"{'ИТОГО':<8} | {tot['n']:<7} | {wl_tot:<10} | "
              f"{wr_tot:>5.1f}%  | {tot['both']:>8}   | {tot['only_o1']:>11}   | "
              f"{tot['pnl']:>+10.2f} $  | {per_tot:>+7.1f} $/м")
        print()

    # ── Сравнительная таблица конфигураций ───────────────────────────────────
    print_sep()
    print("  ИТОГОВОЕ СРАВНЕНИЕ КОНФИГУРАЦИЙ (12 монет)")
    print_sep()
    hdr2 = (f"{'Конфигурация':<48} | {'Сделок':<7} | "
            f"{'WR%':<7} | {'Прибыль $':<12} | {'$/мес':<9}")
    print(hdr2)
    print("-" * len(hdr2))

    for cfg_name, _ in CONFIGS:
        coin_results = results[cfg_name]
        n   = sum(r["n"]    for r in coin_results.values())
        w   = sum(r["wins"] for r in coin_results.values())
        pnl = sum(r["pnl"]  for r in coin_results.values())
        wr  = (w / n * 100) if n else 0.0
        print(f"{cfg_name:<48} | {n:<7} | {wr:>5.1f}%  | "
              f"{pnl:>+10.2f} $  | {pnl / TEST_PERIOD_MONTHS:>+7.1f} $/м")

    print_sep()


if __name__ == "__main__":
    main()

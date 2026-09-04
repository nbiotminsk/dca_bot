#!/usr/bin/env python3
"""
Сравнительный бэктест 3 стратегий на 12 монетах за 90 дней (1h, импульс >= 3%):
1. Стратегия 1: Вход 0.500 -> Тейк 0.236 | Стоп 1.000
2. Стратегия 2: Вход 0.618 -> Тейк 0.382 | Стоп 0.860
3. Стратегия 3: Вход 0.618 -> Тейк 0.382 | Стоп 1.000

Депозит: $1,000 | Риск на сделку: $20.00 | Комиссии Bybit Maker 0.02% / Taker 0.055%
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import numpy as np
from volatility_calc.data_fetcher import fetch_ohlcv
from scripts.backtest_strategy_interactive import (
    detect_impulses,
    run_backtest,
    compute_statistics,
    calc_fib,
)

COINS = [
    ("CAKE", "CAKEUSDT"),
    ("ICP", "ICPUSDT"),
    ("AVAX", "AVAXUSDT"),
    ("TRUMP", "TRUMPUSDT"),
    ("LINK", "LINKUSDT"),
    ("SUI", "SUIUSDT"),
    ("SOL", "SOLUSDT"),
    ("MNT", "MNTUSDT"),
    ("NEAR", "NEARUSDT"),
    ("BNB", "BNBUSDT"),
    ("XRP", "XRPUSDT"),
    ("GRAM", "GRAMUSDT"),
]

SETUPS = [
    {"name": "0.500->0.236 (SL 1.000)", "entry": 0.500, "tp": 0.236, "sl": 1.000},
    {"name": "0.618->0.382 (SL 0.860)", "entry": 0.618, "tp": 0.382, "sl": 0.860},
    {"name": "0.618->0.382 (SL 1.000)", "entry": 0.618, "tp": 0.382, "sl": 1.000},
]

def calc_dollar_metrics(trades, entry_fib, tp_fib, sl_fib, risk_usd=20.0):
    pnl_usd_list = []
    
    for t in trades:
        is_long = (t.side == "long")
        entry = t.entry_price
        sl = calc_fib(t.impulse_high, t.impulse_low, sl_fib, is_long=is_long, scale="log")
        tp = calc_fib(t.impulse_high, t.impulse_low, tp_fib, is_long=is_long, scale="log")
        
        if is_long:
            loss_per_coin = (entry - sl) + (entry * 0.0002) + (sl * 0.00055)
            gain_per_coin = (tp - entry) - (entry * 0.0002) - (tp * 0.0002)
        else:
            loss_per_coin = (sl - entry) + (entry * 0.0002) + (sl * 0.00055)
            gain_per_coin = (entry - tp) - (entry * 0.0002) - (tp * 0.0002)
            
        qty = risk_usd / loss_per_coin if loss_per_coin > 0 else 0.0
        
        if t.exit_reason == "tp":
            pnl = qty * gain_per_coin
        elif t.exit_reason == "sl":
            pnl = -risk_usd
        else:
            pnl = (t.net_pnl_pct / 100.0) * (qty * entry)
            
        pnl_usd_list.append(pnl)
        
    total_usd = sum(pnl_usd_list)
    wins = [p for p in pnl_usd_list if p > 0]
    losses = [p for p in pnl_usd_list if p < 0]
    avg_win_usd = np.mean(wins) if len(wins) > 0 else 0.0
    avg_loss_usd = np.mean(losses) if len(losses) > 0 else 0.0
    
    return {
        "total_usd": total_usd,
        "avg_win_usd": avg_win_usd,
        "avg_loss_usd": avg_loss_usd,
    }


def main():
    print("=" * 120)
    print("  СРАВНИТЕЛЬНЫЙ БЭКТЕСТ СТРАТЕГИЙ FIBONACCI НА 12 МОНЕТАХ ЗА 90 ДНЕЙ")
    print("  Таймфрейм: 1h | Импульс >= 3.0% | Шкала: Log Fib | Депозит: $1,000 | Риск: $20/сделка")
    print("=" * 120)

    # Load all data
    data_map = {}
    imps_map = {}
    for name, symbol in COINS:
        try:
            df = fetch_ohlcv(symbol, timeframe="1h", days=90, use_cache=True)
            data_map[name] = df
            imps_map[name] = detect_impulses(df, min_pct=3.0, side="both", scale="log")
        except Exception as e:
            print(f"Ошибка загрузки {symbol}: {e}")

    # Matrix: coin -> setup_idx -> metrics
    summary = {s["name"]: {"trades": 0, "tp": 0, "sl": 0, "usd": 0.0, "coin_res": {}} for s in SETUPS}

    for setup in SETUPS:
        s_name = setup["name"]
        entry = setup["entry"]
        tp = setup["tp"]
        sl = setup["sl"]

        for name, symbol in COINS:
            if name not in data_map:
                continue
            df = data_map[name]
            imps = imps_map[name]

            trades = run_backtest(df, imps, entry_fib=entry, tp_fib=tp, sl_fib=sl, scale="log")
            stats = compute_statistics(trades)
            usd_res = calc_dollar_metrics(trades, entry, tp, sl, risk_usd=20.0)

            summary[s_name]["trades"] += stats["n_trades"]
            summary[s_name]["tp"] += stats["tp_count"]
            summary[s_name]["sl"] += stats["sl_count"]
            summary[s_name]["usd"] += usd_res["total_usd"]

            summary[s_name]["coin_res"][name] = {
                "trades": stats["n_trades"],
                "tp": stats["tp_count"],
                "sl": stats["sl_count"],
                "wr": stats["win_rate"],
                "pf": stats["profit_factor"],
                "pnl_pct": stats["total_pnl"],
                "usd": usd_res["total_usd"],
                "avg_win": usd_res["avg_win_usd"],
            }

    # Print Side-by-Side Comparison per coin
    print("\n" + "=" * 120)
    print("  ТАБЛИЦА 1: ЧИСТАЯ ПРИБЫЛЬ В ДОЛЛАРАХ ($) ПО МОНЕТАМ (LONG + SHORT)")
    print("=" * 120)
    header1 = f"{'Монета':<8} | {'1. 0.500->0.236 (SL 1.0)':<24} | {'2. 0.618->0.382 (SL 0.86)':<25} | {'3. 0.618->0.382 (SL 1.0)':<25} | {'Лучший сетап':<12}"
    print(header1)
    print("-" * len(header1))

    for name, _ in COINS:
        r1 = summary[SETUPS[0]["name"]]["coin_res"].get(name, {})
        r2 = summary[SETUPS[1]["name"]]["coin_res"].get(name, {})
        r3 = summary[SETUPS[2]["name"]]["coin_res"].get(name, {})

        usd1 = r1.get("usd", 0.0)
        wr1 = r1.get("wr", 0.0)
        t1 = r1.get("trades", 0)

        usd2 = r2.get("usd", 0.0)
        wr2 = r2.get("wr", 0.0)
        t2 = r2.get("trades", 0)

        usd3 = r3.get("usd", 0.0)
        wr3 = r3.get("wr", 0.0)
        t3 = r3.get("trades", 0)

        best_val = max(usd1, usd2, usd3)
        if best_val == usd1:
            best_lbl = "Сетап 1 (0.500)"
        elif best_val == usd2:
            best_lbl = "Сетап 2 (0.618/0.86)"
        else:
            best_lbl = "Сетап 3 (0.618/1.0)"

        col1 = f"{usd1:>+8.2f} $ ({wr1:>4.1f}% | {t1:>2}сд)"
        col2 = f"{usd2:>+8.2f} $ ({wr2:>4.1f}% | {t2:>2}сд)"
        col3 = f"{usd3:>+8.2f} $ ({wr3:>4.1f}% | {t3:>2}сд)"

        print(f"{name:<8} | {col1:<24} | {col2:<25} | {col3:<25} | {best_lbl:<12}")

    print("-" * len(header1))
    tot_usd1 = summary[SETUPS[0]["name"]]["usd"]
    tot_usd2 = summary[SETUPS[1]["name"]]["usd"]
    tot_usd3 = summary[SETUPS[2]["name"]]["usd"]
    col_t1 = f"{tot_usd1:>+8.2f} $"
    col_t2 = f"{tot_usd2:>+8.2f} $"
    col_t3 = f"{tot_usd3:>+8.2f} $"
    print(f"{'ИТОГО ($)':<8} | {col_t1:<24} | {col_t2:<25} | {col_t3:<25} |")

    # Print Summary of 3 Setups
    print("\n" + "=" * 120)
    print("  ТАБЛИЦА 2: ИТОГОВОЕ СРАВНЕНИЕ 3-Х СТРАТЕГИЙ (ПОРТФЕЛЬ ИЗ 12 МОНЕТ ЗА 90 ДНЕЙ)")
    print("=" * 120)
    header2 = f"{'Стратегия':<28} | {'Сделок':<8} | {'Win Rate':<9} | {'Тейки / Стопы':<14} | {'Прибыль ($)':<14} | {'В месяц ($)':<12} | {'Ср. Тейк ($)':<12}"
    print(header2)
    print("-" * len(header2))

    for setup in SETUPS:
        s_name = setup["name"]
        s = summary[s_name]
        tot_t = s["trades"]
        tot_tp = s["tp"]
        tot_sl = s["sl"]
        wr = (tot_tp / tot_t * 100.0) if tot_t > 0 else 0.0
        tot_usd = s["usd"]
        per_month = tot_usd / 3.0
        # typical average win
        avg_w = 20.0 * (0.264 / 0.500) if "0.500" in s_name else (20.0 * (0.236 / 0.242) if "0.860" in s_name else 20.0 * (0.236 / 0.382))

        print(f"{s_name:<28} | {tot_t:<8} | {wr:>6.1f}%   | {tot_tp:>4} / {tot_sl:<7} | {tot_usd:>+10.2f} $   | {per_month:>+8.1f} $/м  | {avg_w:>+8.2f} $")

    print("-" * len(header2))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Бэктест стратегии на 12 монетах за 90 дней:
- Таймфрейм: 1h
- Импульс: >= 3.0%
- Вход: 0.500 Fib
- Тейк: 0.236 Fib
- Стоп: 1.000 Fib
- Шкала: Log Fib
- Монеты: CAKE, ICP, AVAX, TRUMP, LINK, SUI, SOL, MNT, NEAR, BNB, XRP, GRAM
- Расчет в реальных деньгах: Депозит $1,000, риск $20 на сделку.
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

def calc_dollar_metrics(trades, risk_usd=20.0, fee_pct=0.04):
    pnl_usd_list = []
    
    for t in trades:
        is_long = (t.side == "long")
        entry = t.entry_price
        sl = calc_fib(t.impulse_high, t.impulse_low, 1.000, is_long=is_long, scale="log")
        tp = calc_fib(t.impulse_high, t.impulse_low, 0.236, is_long=is_long, scale="log")
        
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
        "trades_usd": pnl_usd_list
    }


def main():
    print("=" * 110)
    print("  БЭКТЕСТ СТРАТЕГИИ: ВХОД 0.500 -> ТЕЙК 0.236 (СТОП 1.000) | ИМПУЛЬС >= 3.0%")
    print("  Период: 90 Дней | Таймфрейм: 1h | Шкала: Log Fib | Депозит: $1,000 | Риск на сделку: $20.00")
    print("=" * 110)
    
    results = []
    
    for name, symbol in COINS:
        try:
            df = fetch_ohlcv(symbol, timeframe="1h", days=90, use_cache=True)
        except Exception as e:
            print(f"Ошибка загрузки {symbol}: {e}")
            continue
            
        # 1. LONG
        l_imps = detect_impulses(df, min_pct=3.0, side="long", scale="log")
        l_trades = run_backtest(df, l_imps, entry_fib=0.500, tp_fib=0.236, sl_fib=1.000, scale="log")
        l_stats = compute_statistics(l_trades)
        l_usd = calc_dollar_metrics(l_trades, risk_usd=20.0)
        
        # 2. SHORT
        s_imps = detect_impulses(df, min_pct=3.0, side="short", scale="log")
        s_trades = run_backtest(df, s_imps, entry_fib=0.500, tp_fib=0.236, sl_fib=1.000, scale="log")
        s_stats = compute_statistics(s_trades)
        s_usd = calc_dollar_metrics(s_trades, risk_usd=20.0)
        
        # 3. BOTH
        all_imps = detect_impulses(df, min_pct=3.0, side="both", scale="log")
        all_trades = run_backtest(df, all_imps, entry_fib=0.500, tp_fib=0.236, sl_fib=1.000, scale="log")
        all_stats = compute_statistics(all_trades)
        all_usd = calc_dollar_metrics(all_trades, risk_usd=20.0)
        
        results.append({
            "name": name,
            "symbol": symbol,
            "candles": len(df),
            # Long
            "l_trades": l_stats["n_trades"],
            "l_wr": l_stats["win_rate"],
            "l_pf": l_stats["profit_factor"],
            "l_pnl_pct": l_stats["total_pnl"],
            "l_usd": l_usd["total_usd"],
            "l_avg_win": l_usd["avg_win_usd"],
            # Short
            "s_trades": s_stats["n_trades"],
            "s_wr": s_stats["win_rate"],
            "s_pf": s_stats["profit_factor"],
            "s_pnl_pct": s_stats["total_pnl"],
            "s_usd": s_usd["total_usd"],
            "s_avg_win": s_usd["avg_win_usd"],
            # Both
            "all_trades": all_stats["n_trades"],
            "all_wr": all_stats["win_rate"],
            "all_pf": all_stats["profit_factor"],
            "all_pnl_pct": all_stats["total_pnl"],
            "all_usd": all_usd["total_usd"],
            "all_tp": all_stats["tp_count"],
            "all_sl": all_stats["sl_count"],
            "all_avg_win": all_usd["avg_win_usd"],
        })

    # Output Tables
    print("\n" + "=" * 110)
    print("  РЕЗУЛЬТАТЫ: ОБА НАПРАВЛЕНИЯ (LONG + SHORT) — СОРТИРОВКА ПО ДОХОДНОСТИ ($)")
    print("=" * 110)
    
    results.sort(key=lambda x: x["all_usd"], reverse=True)
    
    header = f"{'Монета':<8} | {'Сделок':<7} | {'Win/Loss':<10} | {'Win Rate':<9} | {'Profit Factor':<13} | {'PnL цены':<10} | {'Чистая прибыль ($)':<18} | {'В месяц ($)':<11}"
    print(header)
    print("-" * len(header))
    
    tot_trades = 0
    tot_tp = 0
    tot_sl = 0
    tot_pnl_pct = 0.0
    tot_usd = 0.0
    
    for r in results:
        tot_trades += r["all_trades"]
        tot_tp += r["all_tp"]
        tot_sl += r["all_sl"]
        tot_pnl_pct += r["all_pnl_pct"]
        tot_usd += r["all_usd"]
        
        per_month = r["all_usd"] / 3.0
        wl_str = f"{r['all_tp']}/{r['all_sl']}"
        pf_str = f"{r['all_pf']:.2f}" if r['all_pf'] is not None and not np.isnan(r['all_pf']) else "—"
        
        print(f"{r['name']:<8} | {r['all_trades']:<7} | {wl_str:<10} | {r['all_wr']:>6.1f}%   | {pf_str:>8}      | {r['all_pnl_pct']:>+8.1f}%  | {r['all_usd']:>+12.2f} $      | {per_month:>+8.1f} $/м")
        
    print("-" * len(header))
    avg_wr = (tot_tp / tot_trades * 100.0) if tot_trades > 0 else 0.0
    total_per_month = tot_usd / 3.0
    print(f"{'ИТОГО':<8} | {tot_trades:<7} | {tot_tp}/{tot_sl:<7} | {avg_wr:>6.1f}%   | {'—':>8}      | {tot_pnl_pct:>+8.1f}%  | {tot_usd:>+12.2f} $      | {total_per_month:>+8.1f} $/м")

    # Output LONG only table
    print("\n" + "=" * 110)
    print("  РЕЗУЛЬТАТЫ: ТОЛЬКО LONG")
    print("=" * 110)
    results.sort(key=lambda x: x["l_usd"], reverse=True)
    header_l = f"{'Монета':<8} | {'Сделок':<7} | {'Win Rate':<9} | {'Profit Factor':<13} | {'PnL цены':<10} | {'Прибыль ($)':<14} | {'Ср. Тейк ($)':<12}"
    print(header_l)
    print("-" * len(header_l))
    tot_l_trades = 0
    tot_l_usd = 0.0
    for r in results:
        tot_l_trades += r["l_trades"]
        tot_l_usd += r["l_usd"]
        pf_str = f"{r['l_pf']:.2f}" if r['l_pf'] is not None and not np.isnan(r['l_pf']) else "—"
        print(f"{r['name']:<8} | {r['l_trades']:<7} | {r['l_wr']:>6.1f}%   | {pf_str:>8}      | {r['l_pnl_pct']:>+8.1f}%  | {r['l_usd']:>+10.2f} $   | {r['l_avg_win']:>+8.2f} $")
    print("-" * len(header_l))
    print(f"{'ИТОГО LONG':<8} | {tot_l_trades:<7} | {'—':<9} | {'—':>8}      | {'—':>10}  | {tot_l_usd:>+10.2f} $   |")

    # Output SHORT only table
    print("\n" + "=" * 110)
    print("  РЕЗУЛЬТАТЫ: ТОЛЬКО SHORT")
    print("=" * 110)
    results.sort(key=lambda x: x["s_usd"], reverse=True)
    header_s = f"{'Монета':<8} | {'Сделок':<7} | {'Win Rate':<9} | {'Profit Factor':<13} | {'PnL цены':<10} | {'Прибыль ($)':<14} | {'Ср. Тейк ($)':<12}"
    print(header_s)
    print("-" * len(header_s))
    tot_s_trades = 0
    tot_s_usd = 0.0
    for r in results:
        tot_s_trades += r["s_trades"]
        tot_s_usd += r["s_usd"]
        pf_str = f"{r['s_pf']:.2f}" if r['s_pf'] is not None and not np.isnan(r['s_pf']) else "—"
        print(f"{r['name']:<8} | {r['s_trades']:<7} | {r['s_wr']:>6.1f}%   | {pf_str:>8}      | {r['s_pnl_pct']:>+8.1f}%  | {r['s_usd']:>+10.2f} $   | {r['s_avg_win']:>+8.2f} $")
    print("-" * len(header_s))
    print(f"{'ИТОГО SHORT':<8} | {tot_s_trades:<7} | {'—':<9} | {'—':>8}      | {'—':>10}  | {tot_s_usd:>+10.2f} $   |")


if __name__ == "__main__":
    main()

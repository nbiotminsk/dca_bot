#!/usr/bin/env python3
"""
Скрипт бэктеста эталонной стратегии «Корзинный выход на 0.382 при двойном входе»
- Если захвачен только 0.500: тейк 0.236, ордер 0.618 отменяется.
- Если захвачены оба (0.500 и 0.618): ОБА ордера выходят на 0.382!
- Стопы: 1.000 Fib (База импульса)
- Депозит: $1,000, риск на сетку $20 ($10 на ордер).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from scripts.backtest_strategy_interactive import detect_impulses, calc_fib

COINS = [
    ("TRUMP", "TRUMPUSDT"),
    ("LINK", "LINKUSDT"),
    ("SOL", "SOLUSDT"),
    ("ICP", "ICPUSDT"),
    ("XRP", "XRPUSDT"),
    ("SUI", "SUIUSDT"),
    ("GRAM", "GRAMUSDT"),
    ("BNB", "BNBUSDT"),
    ("AVAX", "AVAXUSDT"),
    ("CAKE", "CAKEUSDT"),
    ("NEAR", "NEARUSDT"),
    ("MNT", "MNTUSDT"),
]

def simulate_basket_exit(df, impulses, risk_per_order=10.0, fee_maker=0.0002, fee_taker=0.00055):
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    trades = []
    last_exit_idx = -1

    for imp in impulses:
        if imp.end_idx <= last_exit_idx:
            continue

        is_long = imp.is_long
        p_500 = calc_fib(imp.high, imp.low, 0.500, is_long=is_long, scale="log")
        p_618 = calc_fib(imp.high, imp.low, 0.618, is_long=is_long, scale="log")
        p_tp236 = calc_fib(imp.high, imp.low, 0.236, is_long=is_long, scale="log")
        p_tp382 = calc_fib(imp.high, imp.low, 0.382, is_long=is_long, scale="log")
        p_sl100 = calc_fib(imp.high, imp.low, 1.000, is_long=is_long, scale="log")

        if is_long:
            loss_500 = (p_500 - p_sl100) + (p_500 * fee_maker) + (p_sl100 * fee_taker)
            gain_500_at_236 = (p_tp236 - p_500) - (p_500 * fee_maker) - (p_tp236 * fee_maker)
            gain_500_at_382 = (p_tp382 - p_500) - (p_500 * fee_maker) - (p_tp382 * fee_maker)
            loss_618 = (p_618 - p_sl100) + (p_618 * fee_maker) + (p_sl100 * fee_taker)
            gain_618_at_382 = (p_tp382 - p_618) - (p_618 * fee_maker) - (p_tp382 * fee_maker)
        else:
            loss_500 = (p_sl100 - p_500) + (p_500 * fee_maker) + (p_sl100 * fee_taker)
            gain_500_at_236 = (p_500 - p_tp236) - (p_500 * fee_maker) - (p_tp236 * fee_maker)
            gain_500_at_382 = (p_500 - p_tp382) - (p_500 * fee_maker) - (p_tp382 * fee_maker)
            loss_618 = (p_sl100 - p_618) + (p_618 * fee_maker) + (p_sl100 * fee_taker)
            gain_618_at_382 = (p_618 - p_tp382) - (p_618 * fee_maker) - (p_tp382 * fee_maker)

        qty_500 = risk_per_order / loss_500 if loss_500 > 0 else 0.0
        qty_618 = risk_per_order / loss_618 if loss_618 > 0 else 0.0

        o1_filled = False
        o2_filled = False
        o2_active = True

        end_search = min(imp.end_idx + 720, n)
        event_exit_idx = -1
        total_pnl = 0.0
        outcome = ""

        for k in range(imp.end_idx + 1, end_search):
            h_k = highs[k]
            l_k = lows[k]

            if not o1_filled and not o2_filled:
                if is_long and h_k > imp.high:
                    break
                if not is_long and l_k < imp.low:
                    break

            if not o1_filled:
                if is_long and l_k <= p_500:
                    o1_filled = True
                elif not is_long and h_k >= p_500:
                    o1_filled = True

            if o2_active and not o2_filled:
                if is_long and l_k <= p_618:
                    o2_filled = True
                elif not is_long and h_k >= p_618:
                    o2_filled = True

            if not o1_filled and not o2_filled:
                continue

            # Ситуация 1: Заполнен только 0.500
            if o1_filled and not o2_filled:
                tp_hit = (h_k >= p_tp236) if is_long else (l_k <= p_tp236)
                sl_hit = (l_k <= p_sl100) if is_long else (h_k >= p_sl100)

                if sl_hit:
                    total_pnl = -risk_per_order
                    outcome = "SL 0.500"
                    event_exit_idx = k
                    break
                elif tp_hit:
                    total_pnl = qty_500 * gain_500_at_236
                    outcome = "TP 0.500->0.236"
                    event_exit_idx = k
                    break

            # Ситуация 2: Заполнены оба ордера -> выход на 0.382
            if o1_filled and o2_filled:
                tp382_hit = (h_k >= p_tp382) if is_long else (l_k <= p_tp382)
                sl_hit = (l_k <= p_sl100) if is_long else (h_k >= p_sl100)

                if sl_hit and tp382_hit:
                    total_pnl = -2.0 * risk_per_order
                    outcome = "Both SL"
                    event_exit_idx = k
                    break
                elif sl_hit:
                    total_pnl = -2.0 * risk_per_order
                    outcome = "Both SL"
                    event_exit_idx = k
                    break
                elif tp382_hit:
                    pnl_o1 = qty_500 * gain_500_at_382
                    pnl_o2 = qty_618 * gain_618_at_382
                    total_pnl = pnl_o1 + pnl_o2
                    outcome = "Both TP 0.382"
                    event_exit_idx = k
                    break

        if o1_filled or o2_filled:
            trades.append({
                "pnl": total_pnl,
                "win": (total_pnl > 0),
                "outcome": outcome,
            })
            if event_exit_idx > 0:
                last_exit_idx = event_exit_idx

    return trades

if __name__ == "__main__":
    tot_pnl = 0.0
    for name, sym in COINS:
        df = fetch_ohlcv(sym, timeframe="1h", days=90, use_cache=True)
        imps = detect_impulses(df, min_pct=3.0, side="both", scale="log")
        trades = simulate_basket_exit(df, imps, risk_per_order=10.0)
        wins = sum(1 for t in trades if t["win"])
        pnl = sum(t["pnl"] for t in trades)
        tot_pnl += pnl
        wr = (wins / len(trades) * 100.0) if trades else 0.0
        print(f"{name:<8} | Сделок: {len(trades):<3} | WR: {wr:>5.1f}% | Прибыль: {pnl:>+8.2f} $")
    print(f"ИТОГО: {tot_pnl:>+8.2f} $")

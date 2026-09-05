#!/usr/bin/env python3
"""
Тестирование стратегии двух ордеров на одной фибе (12 монет, 90 дней, 1h, импульс >= 3%):
- Ордер 1: Вход 0.500 -> Тейк 0.236 | Стоп 1.000
- Ордер 2: Вход 0.618 -> Тейк 0.382 | Стоп 1.000

Правила взаимодействия:
- Если сработал только Ордер 1 (0.500) и закрылся по Тейку 0.236 до того, как цена дошла до 0.618 -> Ордер 2 отменяется (One-and-Done).
- Если цена дошла до 0.618 -> активируются ОБА ордера.
- У каждого ордера свой фиксированный риск: $10 на Ордер 1 и $10 на Ордер 2 (суммарный риск на сделку при заполнении обоих = $20).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from scripts.backtest_strategy_interactive import detect_impulses, calc_fib

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

def simulate_dual_orders(df, impulses, risk_per_order=10.0, fee_maker=0.0002, fee_taker=0.00055):
    highs = df["high"].values
    lows = df["low"].values
    df["close"].values
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

        # Расчет лотов под риск $10 на каждый ордер
        if is_long:
            loss_500 = (p_500 - p_sl100) + (p_500 * fee_maker) + (p_sl100 * fee_taker)
            gain_500 = (p_tp236 - p_500) - (p_500 * fee_maker) - (p_tp236 * fee_maker)
            loss_618 = (p_618 - p_sl100) + (p_618 * fee_maker) + (p_sl100 * fee_taker)
            gain_618 = (p_tp382 - p_618) - (p_618 * fee_maker) - (p_tp382 * fee_maker)
        else:
            loss_500 = (p_sl100 - p_500) + (p_500 * fee_maker) + (p_sl100 * fee_taker)
            gain_500 = (p_500 - p_tp236) - (p_500 * fee_maker) - (p_tp236 * fee_maker)
            loss_618 = (p_sl100 - p_618) + (p_618 * fee_maker) + (p_sl100 * fee_taker)
            gain_618 = (p_618 - p_tp382) - (p_618 * fee_maker) - (p_tp382 * fee_maker)

        qty_500 = risk_per_order / loss_500 if loss_500 > 0 else 0.0
        qty_618 = risk_per_order / loss_618 if loss_618 > 0 else 0.0

        # State machine
        o1_filled = False
        o1_closed = False
        o1_pnl = 0.0

        o2_active = True
        o2_filled = False
        o2_closed = False
        o2_pnl = 0.0

        end_search = min(imp.end_idx + 720, n)
        event_exit_idx = -1

        for k in range(imp.end_idx + 1, end_search):
            h_k = highs[k]
            l_k = lows[k]

            # Если еще никто не вошел, проверяем отмену при обновлении экстремума
            if not o1_filled and not o2_filled:
                if is_long and h_k > imp.high:
                    break
                if not is_long and l_k < imp.low:
                    break

            # 1. Проверка заполнения Ордера 1 (0.500)
            if not o1_filled:
                if is_long and l_k <= p_500:
                    o1_filled = True
                elif not is_long and h_k >= p_500:
                    o1_filled = True

            # 2. Проверка заполнения Ордера 2 (0.618)
            if o2_active and not o2_filled:
                if is_long and l_k <= p_618:
                    o2_filled = True
                elif not is_long and h_k >= p_618:
                    o2_filled = True

            # Если оба еще не вошли, идем к следующей свече
            if not o1_filled and not o2_filled:
                continue

            # 3. Сопровождение Ордера 1
            if o1_filled and not o1_closed:
                # Тейк 0.236
                tp_hit = (h_k >= p_tp236) if is_long else (l_k <= p_tp236)
                sl_hit = (l_k <= p_sl100) if is_long else (h_k >= p_sl100)

                if sl_hit and tp_hit:
                    o1_closed = True
                    o1_pnl = -risk_per_order
                elif sl_hit:
                    o1_closed = True
                    o1_pnl = -risk_per_order
                elif tp_hit:
                    o1_closed = True
                    o1_pnl = qty_500 * gain_500
                    # ПРАВИЛО 1: Если Ордер 2 еще не был заполнен, он отменяется!
                    if not o2_filled:
                        o2_active = False
                        event_exit_idx = k
                        break

            # 4. Сопровождение Ордера 2
            if o2_filled and not o2_closed:
                tp_hit = (h_k >= p_tp382) if is_long else (l_k <= p_tp382)
                sl_hit = (l_k <= p_sl100) if is_long else (h_k >= p_sl100)

                if sl_hit and tp_hit:
                    o2_closed = True
                    o2_pnl = -risk_per_order
                elif sl_hit:
                    o2_closed = True
                    o2_pnl = -risk_per_order
                elif tp_hit:
                    o2_closed = True
                    o2_pnl = qty_618 * gain_618

            # Проверяем, завершены ли все активные ордера
            all_done = False
            if o1_filled and not o2_filled and not o2_active:
                all_done = o1_closed
            elif o1_filled and o2_filled:
                all_done = (o1_closed and o2_closed)
            elif not o1_filled and o2_filled:
                all_done = o2_closed

            if all_done:
                event_exit_idx = k
                break

        # Если хотя бы один ордер был исполнен
        if o1_filled or o2_filled:
            tot_pnl = o1_pnl + o2_pnl
            both_entered = (o1_filled and o2_filled)
            only_500 = (o1_filled and not o2_filled)

            win = (tot_pnl > 0)
            trades.append({
                "pnl": tot_pnl,
                "win": win,
                "o1_pnl": o1_pnl,
                "o2_pnl": o2_pnl,
                "both_entered": both_entered,
                "only_500": only_500,
            })
            if event_exit_idx > 0:
                last_exit_idx = event_exit_idx

    return trades

def main():
    print("=" * 115)
    print("  ТЕСТ ГИБРИДНОЙ СТРАТЕГИИ ДВУХ ВХОДОВ НА ОДНОЙ ФИБЕ (12 МОНЕТ ЗА 90 ДНЕЙ)")
    print("  Ордер 1: Вход 0.500 -> Тейк 0.236 (Риск $10) | Ордер 2: Вход 0.618 -> Тейк 0.382 (Риск $10) | Стопы 1.000")
    print("=" * 115)

    summary = []
    tot_trades = 0
    tot_wins = 0
    tot_losses = 0
    tot_usd = 0.0
    tot_both = 0
    tot_only500 = 0

    for name, symbol in COINS:
        try:
            df = fetch_ohlcv(symbol, timeframe="1h", days=90, use_cache=True)
            imps = detect_impulses(df, min_pct=3.0, side="both", scale="log")
            trades = simulate_dual_orders(df, imps, risk_per_order=10.0)
        except Exception as e:
            print(f"Ошибка {name}: {e}")
            continue

        n_t = len(trades)
        wins = sum(1 for t in trades if t["win"])
        losses = n_t - wins
        pnl = sum(t["pnl"] for t in trades)
        wr = (wins / n_t * 100.0) if n_t > 0 else 0.0
        both = sum(1 for t in trades if t["both_entered"])
        only5 = sum(1 for t in trades if t["only_500"])

        tot_trades += n_t
        tot_wins += wins
        tot_losses += losses
        tot_usd += pnl
        tot_both += both
        tot_only500 += only5

        summary.append({
            "name": name,
            "trades": n_t,
            "wins": wins,
            "losses": losses,
            "wr": wr,
            "pnl": pnl,
            "both": both,
            "only500": only5,
        })

    summary.sort(key=lambda x: x["pnl"], reverse=True)

    header = f"{'Монета':<8} | {'Сделок':<7} | {'Win/Loss':<10} | {'Win Rate':<9} | {'Оба входа':<10} | {'Только 0.500':<12} | {'Чистая прибыль ($)':<18} | {'В месяц ($)':<11}"
    print(header)
    print("-" * len(header))

    for r in summary:
        wl_str = f"{r['wins']}/{r['losses']}"
        per_m = r['pnl'] / 3.0
        print(f"{r['name']:<8} | {r['trades']:<7} | {wl_str:<10} | {r['wr']:>6.1f}%   | {r['both']:>8}   | {r['only500']:>10}   | {r['pnl']:>+12.2f} $      | {per_m:>+8.1f} $/м")

    print("-" * len(header))
    avg_wr = (tot_wins / tot_trades * 100.0) if tot_trades > 0 else 0.0
    tot_m = tot_usd / 3.0
    print(f"{'ИТОГО':<8} | {tot_trades:<7} | {tot_wins}/{tot_losses:<7} | {avg_wr:>6.1f}%   | {tot_both:>8}   | {tot_only500:>10}   | {tot_usd:>+12.2f} $      | {tot_m:>+8.1f} $/м")

if __name__ == "__main__":
    main()

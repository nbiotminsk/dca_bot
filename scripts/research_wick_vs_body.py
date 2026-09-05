#!/usr/bin/env python3
"""
Исследование: Влияние характера второй/пробойной свечи импульса (Телом vs Фитилем / Зеленая vs Красная).
Период: 90 дней
Монеты: ARBUSDT, NEARUSDT, ZECUSDT
"""
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from indicators.atr import calculate_atr
from scripts.backtest_strategy_interactive import calc_fib, detect_impulses, Impulse
from scripts.backtest_filters_research import simulate_triple_grid, TradeOutcome


@dataclass
class ImpulseDetails:
    imp: Impulse
    atr_pct: float
    # Вторая свеча (i + 1)
    c2_is_green: bool
    c2_closed_above_h1: bool
    c2_wick_pct: float
    # Свеча пика (end_idx)
    peak_is_green: bool
    peak_wick_pct: float
    # Тип обновления: "BODY" (закрытие выше h1 + зеленая) vs "WICK" (закрытие <= h1 или красная)
    breakout_type: str  # "BODY" or "WICK"


def analyze_coin(symbol: str, days: int = 90, atr_mult: float = 2.5, timeout_hours: int = 24):
    df = fetch_ohlcv(symbol, timeframe="1h", days=days)
    atr_df = calculate_atr(df["high"], df["low"], df["close"], period=14)
    df["atr_pct"] = atr_df["atr_pct"]

    # Детектируем импульсы
    impulses = detect_impulses(df, min_pct=2.0, side="long", scale="log")

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    results = []

    for imp in impulses:
        atr_val = float(df["atr_pct"].iloc[imp.end_idx]) if not pd.isna(df["atr_pct"].iloc[imp.end_idx]) else 2.0

        # Симуляция тройной сетки с ATR и таймаутом
        outcome = simulate_triple_grid(
            df,
            imp,
            atr_pct=atr_val,
            atr_mult=atr_mult,
            timeout_hours=timeout_hours,
            total_risk=2.0,
        )

        if outcome is None or not outcome.entered:
            continue

        # Анализируем вторую свечу (i + 1)
        i = imp.start_idx
        h1 = highs[i]
        c2_idx = min(len(df) - 1, i + 1)
        o2, h2, l2, c2 = opens[c2_idx], highs[c2_idx], lows[c2_idx], closes[c2_idx]
        
        c2_green = (c2 >= o2)
        c2_closed_above = (c2 > h1)
        rng2 = h2 - l2
        wick2_pct = ((h2 - max(o2, c2)) / rng2 * 100.0) if rng2 > 0 else 0.0

        # Свеча пика (end_idx)
        p_idx = imp.end_idx
        op, hp, lp, cp = opens[p_idx], highs[p_idx], lows[p_idx], closes[p_idx]
        peak_green = (cp >= op)
        rng_p = hp - lp
        peak_wick_pct = ((hp - max(op, cp)) / rng_p * 100.0) if rng_p > 0 else 0.0

        # Классификация:
        # BODY: вторая свеча закрылась выше High первой свечи И она зеленая (тело закрепилось выше)
        # WICK: вторая свеча обновила High первой свечи только фитилем (закрылась <= h1 ИЛИ красная)
        if c2_closed_above and c2_green:
            b_type = "BODY"
        else:
            b_type = "WICK"

        results.append({
            "symbol": symbol,
            "start_time": imp.start_time,
            "peak_time": imp.end_time,
            "imp_pct": imp.pct,
            "bars": imp.end_idx - imp.start_idx + 1,
            "b_type": b_type,
            "c2_green": c2_green,
            "c2_closed_above": c2_closed_above,
            "c2_wick_pct": wick2_pct,
            "peak_green": peak_green,
            "peak_wick_pct": peak_wick_pct,
            "win": outcome.win,
            "outcome": outcome.outcome,
            "pnl": outcome.pnl,
        })

    return pd.DataFrame(results)


def main():
    coins = ["ARBUSDT", "NEARUSDT", "ZECUSDT"]
    all_dfs = []

    for c in coins:
        df_res = analyze_coin(c, days=90, atr_mult=2.5, timeout_hours=24)
        all_dfs.append(df_res)

    total_df = pd.concat(all_dfs, ignore_index=True)

    print("=" * 80)
    print("ИССЛЕДОВАНИЕ: ОБНОВЛЕНИЕ ХАЯ ТЕЛОМ vs ТОЛЬКО ФИТИЛЕМ (90 ДНЕЙ, 1h, ATR 2.5x, Timeout 24h)")
    print("=" * 80)

    for name, df_sub in [("ARBUSDT", all_dfs[0]), ("NEARUSDT", all_dfs[1]), ("ZECUSDT", all_dfs[2]), ("ВСЕ 3 МОНЕТЫ ВМЕСТЕ", total_df)]:
        print(f"\n>>> {name} <<<")
        
        # 1. По типу обновления: BODY vs WICK
        for b_t in ["BODY", "WICK"]:
            sub = df_sub[df_sub["b_type"] == b_t]
            n = len(sub)
            if n == 0:
                print(f"  {b_t:5s}: 0 сделок")
                continue
            wins = len(sub[sub["win"] == True])
            losses = len(sub[sub["outcome"] == "SL"])
            wr = wins / n * 100.0
            pnl = sub["pnl"].sum()
            gross_win = sub[sub["pnl"] > 0]["pnl"].sum()
            gross_loss = abs(sub[sub["pnl"] < 0]["pnl"].sum())
            pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
            avg_pnl = pnl / n

            label = "ТЕЛЕМ (закрылась выше H1 + зеленая)" if b_t == "BODY" else "ФИТИЛЕМ (закрылась ниже H1 или красная)"
            print(f"  [{b_t}] {label}:")
            print(f"      Сделок: {n} | Побед: {wins} | Стопов: {losses} | WR: {wr:.1f}%")
            print(f"      PnL: ${pnl:+.2f} | PF: {pf:.2f} | Ср. PnL: ${avg_pnl:+.2f}/сделка")

        # 2. Детальное разбиение: 
        #   - Вторая свеча Зеленая vs Красная
        print("\n  Детализация по цвету второй свечи:")
        for c_color, is_g in [("Зеленая (Green)", True), ("Красная (Red)", False)]:
            sub = df_sub[df_sub["c2_green"] == is_g]
            n = len(sub)
            if n == 0:
                continue
            wins = len(sub[sub["win"] == True])
            losses = len(sub[sub["outcome"] == "SL"])
            wr = wins / n * 100.0
            pnl = sub["pnl"].sum()
            gross_win = sub[sub["pnl"] > 0]["pnl"].sum()
            gross_loss = abs(sub[sub["pnl"] < 0]["pnl"].sum())
            pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
            print(f"    * Вторая {c_color:15s}: Сделок: {n:3d} | WR: {wr:5.1f}% | Стопов: {losses:2d} | PnL: ${pnl:+6.2f} | PF: {pf:.2f}")

        #   - Свеча пика: Фитиль > 50% vs Фитиль <= 50%
        print("\n  Детализация по пинбару на вершине (Фитиль свечи пика):")
        for w_label, cond in [("Пинбар / Сброс (Фитиль > 50%)", df_sub["peak_wick_pct"] > 50), ("Уверенный пик (Фитиль <= 50%)", df_sub["peak_wick_pct"] <= 50)]:
            sub = df_sub[cond]
            n = len(sub)
            if n == 0:
                continue
            wins = len(sub[sub["win"] == True])
            losses = len(sub[sub["outcome"] == "SL"])
            wr = wins / n * 100.0
            pnl = sub["pnl"].sum()
            gross_win = sub[sub["pnl"] > 0]["pnl"].sum()
            gross_loss = abs(sub[sub["pnl"] < 0]["pnl"].sum())
            pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
            print(f"    * {w_label:32s}: Сделок: {n:3d} | WR: {wr:5.1f}% | Стопов: {losses:2d} | PnL: ${pnl:+6.2f} | PF: {pf:.2f}")


if __name__ == "__main__":
    main()

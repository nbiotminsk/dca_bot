#!/usr/bin/env python3
"""
Исследовательский бэктестер стратегии Тройной сетки Фибоначчи:
Сравнение Базовой стратегии vs ATR-фильтра vs Тайм-аута свежести (24-48ч)
Период: 90 дней
Монеты: NEARUSDT, ZECUSDT, ARBUSDT
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from indicators.atr import calculate_atr
from scripts.backtest_strategy_interactive import calc_fib, detect_impulses, Impulse
from scripts.constants import FEE_MAKER


@dataclass
class TradeOutcome:
    impulse_idx: int
    start_time: pd.Timestamp
    peak_time: pd.Timestamp
    impulse_pct: float
    atr_pct: float
    entered: bool
    win: bool
    outcome: str  # "TP1", "TP2", "TP3", "SL", "EXPIRED", "NO_FILL"
    o1_filled: bool
    o2_filled: bool
    o3_filled: bool
    pnl: float
    hold_hours: int
    bars_to_fill: int


def simulate_triple_grid(
    df: pd.DataFrame,
    imp: Impulse,
    atr_pct: float,
    atr_mult: Optional[float] = None,
    timeout_hours: Optional[int] = None,
    total_risk: float = 2.0,
    entry_buffer_pct: float = 0.07,
    tp_buffer_pct: float = 0.10,
    scale: Literal["log", "linear"] = "log",
    max_hold_hours: int = 120,
) -> Optional[TradeOutcome]:
    """
    Симулирует сделку по тройной сетке для одного импульса.
    Если импульс отсеян ATR-фильтром -> возвращает None.
    Если таймаут сработал до налива Ордера 1 -> outcome='EXPIRED', pnl=0.0.
    """
    # 1. Проверка ATR-фильтра
    if atr_mult is not None and atr_mult > 0:
        required_pct = max(2.0, atr_mult * atr_pct)
        if imp.pct < required_pct:
            return None  # Отсеян фильтром волатильности

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    df["timestamp"].values
    n = len(df)

    is_long = imp.is_long
    if not is_long:
        return None  # Long only

    cur_high = imp.high
    start_price = imp.low

    # Расчет уровней Фибоначчи
    def get_levels(h: float, low: float):
        p_0500 = calc_fib(h, low, 0.500, is_long=True, scale=scale)
        p_0618 = calc_fib(h, low, 0.618, is_long=True, scale=scale)
        p_0786 = calc_fib(h, low, 0.786, is_long=True, scale=scale)
        p_0236 = calc_fib(h, low, 0.236, is_long=True, scale=scale)
        p_0382 = calc_fib(h, low, 0.382, is_long=True, scale=scale)
        p_1000 = low

        e1 = p_0500 * (1.0 + entry_buffer_pct / 100.0)
        e2 = p_0618 * (1.0 + entry_buffer_pct / 100.0)
        e3 = p_0786 * (1.0 + entry_buffer_pct / 100.0)

        tp1 = p_0236 * (1.0 - tp_buffer_pct / 100.0)
        tp2 = p_0382 * (1.0 - tp_buffer_pct / 100.0)
        tp3 = p_0500 * (1.0 - tp_buffer_pct / 100.0)
        sl = p_1000

        return e1, e2, e3, tp1, tp2, tp3, sl

    e1, e2, e3, tp1, tp2, tp3, sl = get_levels(cur_high, start_price)

    # Расчет размеров ордеров на основе риска
    r_each = total_risk / 3.0
    q1 = r_each / max(1e-6, (e1 - sl))
    q2 = r_each / max(1e-6, (e2 - sl))
    q3 = r_each / max(1e-6, (e3 - sl))

    o1_filled = False
    o2_filled = False
    o3_filled = False
    entry_bar = -1

    search_end = min(n, imp.end_idx + 1 + max_hold_hours)

    for k in range(imp.end_idx + 1, search_end):
        bars_since_peak = k - imp.end_idx
        h_k = highs[k]
        l_k = lows[k]
        closes[k]

        # Если еще не вошли:
        if not o1_filled:
            # Трейлинг: если хай обновлен до входа -> сетка подтягивается
            if h_k > cur_high:
                cur_high = h_k
                e1, e2, e3, tp1, tp2, tp3, sl = get_levels(cur_high, start_price)
                q1 = r_each / max(1e-6, (e1 - sl))
                q2 = r_each / max(1e-6, (e2 - sl))
                q3 = r_each / max(1e-6, (e3 - sl))

            # Проверка тайм-аута свежести:
            if timeout_hours is not None and bars_since_peak > timeout_hours:
                return TradeOutcome(
                    impulse_idx=imp.end_idx,
                    start_time=imp.start_time,
                    peak_time=imp.end_time,
                    impulse_pct=imp.pct,
                    atr_pct=atr_pct,
                    entered=False,
                    win=False,
                    outcome="EXPIRED",
                    o1_filled=False,
                    o2_filled=False,
                    o3_filled=False,
                    pnl=0.0,
                    hold_hours=bars_since_peak,
                    bars_to_fill=bars_since_peak,
                )

            # Проверка налива Ордера 1
            if l_k <= e1:
                o1_filled = True
                entry_bar = k

                # Проверяем, не налился ли сразу же Ордер 2 на этой же свече
                if l_k <= e2:
                    o2_filled = True
                if l_k <= e3:
                    o3_filled = True

                # Проверяем мгновенный стоп
                if l_k <= sl:
                    loss = -total_risk
                    return TradeOutcome(
                        impulse_idx=imp.end_idx,
                        start_time=imp.start_time,
                        peak_time=imp.end_time,
                        impulse_pct=imp.pct,
                        atr_pct=atr_pct,
                        entered=True,
                        win=False,
                        outcome="SL",
                        o1_filled=True,
                        o2_filled=o2_filled,
                        o3_filled=o3_filled,
                        pnl=loss,
                        hold_hours=1,
                        bars_to_fill=bars_since_peak,
                    )
            continue

        # Мы уже в позиции:
        # 1. Проверяем доборы
        if not o2_filled and l_k <= e2:
            o2_filled = True
        if o2_filled and not o3_filled and l_k <= e3:
            o3_filled = True

        # 2. Проверяем Стоп-Лосс (1.000)
        if l_k <= sl:
            loss = 0.0
            if o1_filled:
                loss -= r_each
            if o2_filled:
                loss -= r_each
            if o3_filled:
                loss -= r_each
            return TradeOutcome(
                impulse_idx=imp.end_idx,
                start_time=imp.start_time,
                peak_time=imp.end_time,
                impulse_pct=imp.pct,
                atr_pct=atr_pct,
                entered=True,
                win=False,
                outcome="SL",
                o1_filled=o1_filled,
                o2_filled=o2_filled,
                o3_filled=o3_filled,
                pnl=loss,
                hold_hours=k - entry_bar,
                bars_to_fill=entry_bar - imp.end_idx,
            )

        # 3. Проверяем Тейк-Профит в зависимости от налитых ордеров:
        if o3_filled:
            # Все три ордера выходят на 0.500 (tp3)
            if h_k >= tp3:
                gain1 = q1 * ((tp3 - e1) - e1 * FEE_MAKER - tp3 * FEE_MAKER)
                gain2 = q2 * ((tp3 - e2) - e2 * FEE_MAKER - tp3 * FEE_MAKER)
                gain3 = q3 * ((tp3 - e3) - e3 * FEE_MAKER - tp3 * FEE_MAKER)
                tot_pnl = gain1 + gain2 + gain3
                return TradeOutcome(
                    impulse_idx=imp.end_idx,
                    start_time=imp.start_time,
                    peak_time=imp.end_time,
                    impulse_pct=imp.pct,
                    atr_pct=atr_pct,
                    entered=True,
                    win=True,
                    outcome="TP3",
                    o1_filled=True,
                    o2_filled=True,
                    o3_filled=True,
                    pnl=tot_pnl,
                    hold_hours=k - entry_bar,
                    bars_to_fill=entry_bar - imp.end_idx,
                )
        elif o2_filled:
            # Ордера 1 и 2 выходят на 0.382 (tp2), Ордер 3 отменяется
            if h_k >= tp2:
                gain1 = q1 * ((tp2 - e1) - e1 * FEE_MAKER - tp2 * FEE_MAKER)
                gain2 = q2 * ((tp2 - e2) - e2 * FEE_MAKER - tp2 * FEE_MAKER)
                tot_pnl = gain1 + gain2
                return TradeOutcome(
                    impulse_idx=imp.end_idx,
                    start_time=imp.start_time,
                    peak_time=imp.end_time,
                    impulse_pct=imp.pct,
                    atr_pct=atr_pct,
                    entered=True,
                    win=True,
                    outcome="TP2",
                    o1_filled=True,
                    o2_filled=True,
                    o3_filled=False,
                    pnl=tot_pnl,
                    hold_hours=k - entry_bar,
                    bars_to_fill=entry_bar - imp.end_idx,
                )
        else:
            # Только Ордер 1 выходит на 0.236 (tp1), Ордера 2 и 3 отменяются
            if h_k >= tp1:
                gain1 = q1 * ((tp1 - e1) - e1 * FEE_MAKER - tp1 * FEE_MAKER)
                tot_pnl = gain1
                return TradeOutcome(
                    impulse_idx=imp.end_idx,
                    start_time=imp.start_time,
                    peak_time=imp.end_time,
                    impulse_pct=imp.pct,
                    atr_pct=atr_pct,
                    entered=True,
                    win=True,
                    outcome="TP1",
                    o1_filled=True,
                    o2_filled=False,
                    o3_filled=False,
                    pnl=tot_pnl,
                    hold_hours=k - entry_bar,
                    bars_to_fill=entry_bar - imp.end_idx,
                )

    # Если вышли по лимиту удержания (max_hold_hours)
    if o1_filled:
        last_c = closes[search_end - 1]
        pnl = 0.0
        if o1_filled:
            pnl += q1 * (last_c - e1)
        if o2_filled:
            pnl += q2 * (last_c - e2)
        if o3_filled:
            pnl += q3 * (last_c - e3)
        return TradeOutcome(
            impulse_idx=imp.end_idx,
            start_time=imp.start_time,
            peak_time=imp.end_time,
            impulse_pct=imp.pct,
            atr_pct=atr_pct,
            entered=True,
            win=pnl > 0,
            outcome="TIMEOUT_EXIT",
            o1_filled=o1_filled,
            o2_filled=o2_filled,
            o3_filled=o3_filled,
            pnl=pnl,
            hold_hours=search_end - 1 - entry_bar,
            bars_to_fill=entry_bar - imp.end_idx,
        )

    return None


def run_backtest_for_symbol(
    df: pd.DataFrame,
    atr_mult: Optional[float] = None,
    timeout_hours: Optional[int] = None,
    min_pct: float = 2.0,
) -> dict:
    """Прогон бэктеста по всем импульсам датафрейма с защитой от наложения сделок."""
    atr_df = calculate_atr(df["high"], df["low"], df["close"], period=14)
    atr_pct_series = atr_df["atr_pct"]

    impulses = detect_impulses(df, min_pct=min_pct, side="long", scale="log", allow_internal=True)

    trades: list[TradeOutcome] = []
    last_free_bar = 0

    for imp in impulses:
        if imp.end_idx < last_free_bar:
            continue

        atr_val = float(atr_pct_series.iloc[imp.end_idx]) if imp.end_idx < len(atr_pct_series) else 2.0
        res = simulate_triple_grid(
            df=df,
            imp=imp,
            atr_pct=atr_val,
            atr_mult=atr_mult,
            timeout_hours=timeout_hours,
        )

        if res is not None and res.entered:
            trades.append(res)
            last_free_bar = imp.end_idx + res.bars_to_fill + res.hold_hours

    n_trades = len(trades)
    if n_trades == 0:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "wr": 0.0,
            "pnl": 0.0,
            "stops": 0,
            "tp1": 0,
            "tp2": 0,
            "tp3": 0,
            "pf": 0.0,
            "avg_hold": 0.0,
        }

    wins = sum(1 for t in trades if t.win)
    losses = sum(1 for t in trades if not t.win)
    stops = sum(1 for t in trades if t.outcome == "SL")
    tp1 = sum(1 for t in trades if t.outcome == "TP1")
    tp2 = sum(1 for t in trades if t.outcome == "TP2")
    tp3 = sum(1 for t in trades if t.outcome == "TP3")
    tot_pnl = sum(t.pnl for t in trades)
    wr = (wins / n_trades) * 100.0

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 999.0
    avg_hold = sum(t.hold_hours for t in trades) / n_trades

    return {
        "trades": n_trades,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": tot_pnl,
        "stops": stops,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "pf": pf,
        "avg_hold": avg_hold,
    }


def main():
    parser = argparse.ArgumentParser(description="Исследование ATR и Timeout фильтров (90 дней)")
    parser.add_argument("--days", type=int, default=90, help="Дней бэктеста")
    args = parser.parse_args()

    symbols = ["NEARUSDT", "ZECUSDT", "ARBUSDT"]
    dfs = {}
    for sym in symbols:
        dfs[sym] = fetch_ohlcv(sym, timeframe="1h", days=args.days, use_cache=True)

    print("=" * 95)
    print(f"📊 ИССЛЕДОВАНИЕ ФИЛЬТРОВ ТРОЙНОЙ СЕТКИ НА ИСТОРИИ ({args.days} ДНЕЙ / 1H)")
    print("=" * 95)

    # 1. БАЗОВАЯ СТРАТЕГИЯ (min_pct = 2.0%, без таймаута, без ATR-фильтра)
    print("\n[ 1. БАЗОВАЯ СТРАТЕГИЯ: Без фильтров (min_pct=2.0%, No Timeout) ]")
    print(f"{'Монета':<10} | {'Сделок':>7} | {'Побед':>6} | {'Стопов':>7} | {'Win Rate':>8} | {'PnL ($)':>9} | {'PF':>6} | {'TP1/TP2/TP3':>11}")
    print("-" * 95)
    baseline_res = {}
    for sym in symbols:
        r = run_backtest_for_symbol(dfs[sym], atr_mult=None, timeout_hours=None)
        baseline_res[sym] = r
        tp_str = f"{r['tp1']}/{r['tp2']}/{r['tp3']}"
        print(f"{sym:<10} | {r['trades']:>7d} | {r['wins']:>6d} | {r['stops']:>7d} | {r['wr']:>7.1f}% | {r['pnl']:>+8.2f}$ | {r['pf']:>6.2f} | {tp_str:>11}")

    # 2. ИССЛЕДОВАНИЕ ТАЙМ-АУТА (Timeout: 24h, 30h, 36h, 42h, 48h)
    print("\n" + "=" * 95)
    print("[ 2. ТЕСТ ТАЙМ-АУТА СВЕЖЕСТИ (24h - 48h) при базовом min_pct=2.0% ]")
    print("=" * 95)
    timeouts = [24, 30, 36, 42, 48]
    for sym in symbols:
        print(f"\n--- {sym} ---")
        print(f"{'Режим':<14} | {'Сделок':>7} | {'Побед':>6} | {'Стопов':>7} | {'Win Rate':>8} | {'PnL ($)':>9} | {'PF':>6} | {'Δ Стопов':>9}")
        print("-" * 80)
        base = baseline_res[sym]
        print(f"{'Базовый (нет)':<14} | {base['trades']:>7d} | {base['wins']:>6d} | {base['stops']:>7d} | {base['wr']:>7.1f}% | {base['pnl']:>+8.2f}$ | {base['pf']:>6.2f} | {'база':>9}")
        for t in timeouts:
            r = run_backtest_for_symbol(dfs[sym], atr_mult=None, timeout_hours=t)
            diff_sl = r['stops'] - base['stops']
            diff_sl_str = f"{diff_sl:+d}"
            print(f"{f'Таймаут {t}ч':<14} | {r['trades']:>7d} | {r['wins']:>6d} | {r['stops']:>7d} | {r['wr']:>7.1f}% | {r['pnl']:>+8.2f}$ | {r['pf']:>6.2f} | {diff_sl_str:>9}")

    # 3. ИССЛЕДОВАНИЕ ATR-ФИЛЬТРА (k = 1.5, 2.0, 2.5, 3.0, 3.5)
    print("\n" + "=" * 95)
    print("[ 3. ТЕСТ ATR-ФИЛЬТРА ВОЛАТИЛЬНОСТИ (k * ATR) без таймаута ]")
    print("=" * 95)
    atr_mults = [1.5, 2.0, 2.5, 3.0, 3.5]
    for sym in symbols:
        print(f"\n--- {sym} ---")
        print(f"{'Режим':<14} | {'Сделок':>7} | {'Побед':>6} | {'Стопов':>7} | {'Win Rate':>8} | {'PnL ($)':>9} | {'PF':>6} | {'Δ Стопов':>9}")
        print("-" * 80)
        base = baseline_res[sym]
        print(f"{'Базовый (нет)':<14} | {base['trades']:>7d} | {base['wins']:>6d} | {base['stops']:>7d} | {base['wr']:>7.1f}% | {base['pnl']:>+8.2f}$ | {base['pf']:>6.2f} | {'база':>9}")
        for k in atr_mults:
            r = run_backtest_for_symbol(dfs[sym], atr_mult=k, timeout_hours=None)
            diff_sl = r['stops'] - base['stops']
            diff_sl_str = f"{diff_sl:+d}"
            print(f"{f'ATR k={k:.1f}':<14} | {r['trades']:>7d} | {r['wins']:>6d} | {r['stops']:>7d} | {r['wr']:>7.1f}% | {r['pnl']:>+8.2f}$ | {r['pf']:>6.2f} | {diff_sl_str:>9}")

    # 4. СИНЕРГИЯ: ATR + ТАЙМ-АУТ
    print("\n" + "=" * 95)
    print("[ 4. СИНЕРГИЯ: Оптимальный ATR (k=2.5) + Таймаут (24ч и 36ч) vs База ]")
    print("=" * 95)
    print(f"{'Монета':<10} | {'Конфиг':<18} | {'Сделок':>7} | {'Побед':>6} | {'Стопов':>7} | {'Win Rate':>8} | {'PnL ($)':>9} | {'PF':>6}")
    print("-" * 95)
    for sym in symbols:
        base = baseline_res[sym]
        print(f"{sym:<10} | {'Базовый (нет)':<18} | {base['trades']:>7d} | {base['wins']:>6d} | {base['stops']:>7d} | {base['wr']:>7.1f}% | {base['pnl']:>+8.2f}$ | {base['pf']:>6.2f}")
        for t in [24, 36]:
            r = run_backtest_for_symbol(dfs[sym], atr_mult=2.5, timeout_hours=t)
            print(f"{sym:<10} | {f'ATR 2.5 + {t}h':<18} | {r['trades']:>7d} | {r['wins']:>6d} | {r['stops']:>7d} | {r['wr']:>7.1f}% | {r['pnl']:>+8.2f}$ | {r['pf']:>6.2f}")
        print("-" * 95)


if __name__ == "__main__":
    main()

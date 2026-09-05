#!/usr/bin/env python3
"""
Комплексный исследовательский бэктест стратегий Фибоначчи на пуле монет (1h, 180 дней):
1. Тест 3 базовых стратегий + расширенная линейка (Манипуляция, DCA).
2. Одиночные уровни vs Добор корзиной.
3. Короткие скальп-движения и динамический безубыток (0.500 -> 0.382 -> 0.236).
4. Вероятностный анализ отскоков от 1.618 к 0.618/0.500 и глубина просадки.
5. Глобальный поиск работающих чисел (Grid Search & Optimization).
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from scripts.backtest_strategy_interactive import detect_impulses, calc_fib, Impulse
from scripts.constants import FEE_MAKER, FEE_TAKER

# ─── Список исследуемых монет ──────────────────────────────────────────────────
COINS_UNIVERSE: list[tuple[str, str]] = [
    ("BNB", "BNBUSDT"),
    ("XRP", "XRPUSDT"),
    ("SOL", "SOLUSDT"),
    ("HYPE", "HYPEUSDT"),
    ("ZEC", "ZECUSDT"),
    ("DOGE", "DOGEUSDT"),
    ("LINK", "LINKUSDT"),
    ("XLM", "XLMUSDT"),
    ("CC", "CCUSDT"),
    ("LTC", "LTCUSDT"),
    ("UNI", "UNIUSDT"),
    ("GRAM", "GRAMUSDT"),
    ("HBAR", "HBARUSDT"),
    ("SUI", "SUIUSDT"),
    ("NEAR", "NEARUSDT"),
    ("ASTER", "ASTERUSDT"),
    ("MNT", "MNTUSDT"),
    ("ONDO", "ONDOUSDT"),
    ("ENA", "ENAUSDT"),
    ("DOT", "DOTUSDT"),
    ("PEPE", "1000PEPEUSDT"),
    ("WLD", "WLDUSDT"),
    ("ICP", "ICPUSDT"),
    ("ARB", "ARBUSDT"),
    ("ALGO", "ALGOUSDT"),
    ("OP", "OPUSDT"),
    ("ATOM", "ATOMUSDT"),
    ("RENDER", "RENDERUSDT"),
    ("CAKE", "CAKEUSDT"),
    ("FIL", "FILUSDT"),
    ("TRUMP", "TRUMPUSDT"),
]

# ─── Базовые структуры ────────────────────────────────────────────────────────
@dataclass
class SingleTrade:
    entry_price: float
    exit_price: float
    exit_reason: str  # "tp", "sl", "be", "timeout"
    pnl_usd: float
    pnl_pct: float
    hold_bars: int
    entry_fib: float
    tp_fib: float
    sl_fib: float


# ─── Симуляция одиночного ордера ──────────────────────────────────────────────
def sim_single_order(
    df: pd.DataFrame,
    imp: Impulse,
    entry_fib: float,
    tp_fib: float,
    sl_fib: float,
    risk_usd: float = 20.0,
    timeout_candles: int = 720,
    fee_maker: float = FEE_MAKER,
    fee_taker: float = FEE_TAKER,
) -> Optional[SingleTrade]:
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    n = len(df)
    is_long = imp.is_long

    p_entry = calc_fib(imp.high, imp.low, entry_fib, is_long=is_long, scale="log")
    p_tp = calc_fib(imp.high, imp.low, tp_fib, is_long=is_long, scale="log")
    p_sl = calc_fib(imp.high, imp.low, sl_fib, is_long=is_long, scale="log")

    if is_long:
        dist_sl = (p_entry - p_sl) + p_entry * fee_maker + p_sl * fee_taker
        gain_tp = (p_tp - p_entry) - p_entry * fee_maker - p_tp * fee_maker
    else:
        dist_sl = (p_sl - p_entry) + p_entry * fee_maker + p_sl * fee_taker
        gain_tp = (p_entry - p_tp) - p_entry * fee_maker - p_tp * fee_maker

    if dist_sl <= 0 or gain_tp <= 0:
        return None

    qty = risk_usd / dist_sl

    # Поиск входа
    entry_k = None
    end_search = min(imp.end_idx + timeout_candles, n)
    for k in range(imp.end_idx + 1, end_search):
        if entry_fib < 1.0:
            if is_long and highs[k] > imp.high:
                break
            if not is_long and lows[k] < imp.low:
                break

        if (is_long and lows[k] <= p_entry) or (not is_long and highs[k] >= p_entry):
            entry_k = k
            break

    if entry_k is None:
        return None

    # Сопровождение
    max_hold = min(entry_k + timeout_candles, n)
    for m in range(entry_k + 1, max_hold):
        h = highs[m]
        low_m = lows[m]
        sl_hit = (low_m <= p_sl) if is_long else (h >= p_sl)
        tp_hit = (h >= p_tp) if is_long else (low_m <= p_tp)

        if sl_hit and tp_hit:
            return SingleTrade(p_entry, p_sl, "sl", -risk_usd, -dist_sl / p_entry * 100.0, m - entry_k, entry_fib, tp_fib, sl_fib)
        elif sl_hit:
            return SingleTrade(p_entry, p_sl, "sl", -risk_usd, -dist_sl / p_entry * 100.0, m - entry_k, entry_fib, tp_fib, sl_fib)
        elif tp_hit:
            return SingleTrade(p_entry, p_tp, "tp", qty * gain_tp, gain_tp / p_entry * 100.0, m - entry_k, entry_fib, tp_fib, sl_fib)

    # Таймаут
    c = closes[max_hold - 1]
    if is_long:
        unr = (c - p_entry) - p_entry * fee_maker - c * fee_taker
    else:
        unr = (p_entry - c) - p_entry * fee_maker - c * fee_taker
    return SingleTrade(p_entry, c, "timeout", qty * unr, unr / p_entry * 100.0, max_hold - 1 - entry_k, entry_fib, tp_fib, sl_fib)


# ─── Симуляция трейлинга в безубыток ───────────────────────────────────────────
def sim_trailing_breakeven(
    df: pd.DataFrame,
    imp: Impulse,
    entry_fib: float = 0.500,
    trigger_fib: float = 0.382,
    target_fib: float = 0.236,
    initial_sl_fib: float = 1.000,
    risk_usd: float = 20.0,
    timeout_candles: int = 720,
    fee_maker: float = FEE_MAKER,
    fee_taker: float = FEE_TAKER,
) -> Optional[dict]:
    highs = df["high"].values
    lows = df["low"].values
    df["close"].values
    n = len(df)
    is_long = imp.is_long

    p_e = calc_fib(imp.high, imp.low, entry_fib, is_long=is_long, scale="log")
    p_trig = calc_fib(imp.high, imp.low, trigger_fib, is_long=is_long, scale="log")
    p_targ = calc_fib(imp.high, imp.low, target_fib, is_long=is_long, scale="log")
    p_sl_init = calc_fib(imp.high, imp.low, initial_sl_fib, is_long=is_long, scale="log")

    if is_long:
        dist_sl = (p_e - p_sl_init) + p_e * fee_maker + p_sl_init * fee_taker
        gain_trig = (p_trig - p_e) - p_e * fee_maker - p_trig * fee_maker
        gain_targ = (p_targ - p_e) - p_e * fee_maker - p_targ * fee_maker
        p_be = p_e * (1.0 + fee_maker + fee_taker)
    else:
        dist_sl = (p_sl_init - p_e) + p_e * fee_maker + p_sl_init * fee_taker
        gain_trig = (p_e - p_trig) - p_e * fee_maker - p_trig * fee_maker
        gain_targ = (p_e - p_targ) - p_e * fee_maker - p_targ * fee_maker
        p_be = p_e * (1.0 - fee_maker - fee_taker)

    if dist_sl <= 0 or gain_trig <= 0:
        return None

    qty = risk_usd / dist_sl

    entry_k = None
    end_search = min(imp.end_idx + timeout_candles, n)
    for k in range(imp.end_idx + 1, end_search):
        if is_long and highs[k] > imp.high:
            break
        if not is_long and lows[k] < imp.low:
            break
        if (is_long and lows[k] <= p_e) or (not is_long and highs[k] >= p_e):
            entry_k = k
            break

    if entry_k is None:
        return None

    max_hold = min(entry_k + timeout_candles, n)
    trigger_hit = False

    res_a = None
    res_b = None
    res_c = None

    for m in range(entry_k + 1, max_hold):
        h = highs[m]
        low_m = lows[m]

        sl_init_hit = (low_m <= p_sl_init) if is_long else (h >= p_sl_init)
        trig_hit = (h >= p_trig) if is_long else (low_m <= p_trig)
        targ_hit = (h >= p_targ) if is_long else (low_m <= p_targ)

        # Сценарий А (Фикс на 0.382)
        if res_a is None:
            if sl_init_hit and trig_hit:
                res_a = ("sl", -risk_usd)
            elif sl_init_hit:
                res_a = ("sl", -risk_usd)
            elif trig_hit:
                res_a = ("tp", qty * gain_trig)

        # Сценарий В (Жадный со статическим стопом до 0.236)
        if res_c is None:
            if sl_init_hit and targ_hit:
                res_c = ("sl", -risk_usd)
            elif sl_init_hit:
                res_c = ("sl", -risk_usd)
            elif targ_hit:
                res_c = ("tp", qty * gain_targ)

        # Сценарий Б (Перенос в безубыток при достижении trigger)
        if not trigger_hit:
            if sl_init_hit and trig_hit:
                res_b = ("sl_init", -risk_usd)
                trigger_hit = True
            elif sl_init_hit:
                res_b = ("sl_init", -risk_usd)
                break
            elif trig_hit:
                trigger_hit = True
                if targ_hit:
                    res_b = ("tp_target", qty * gain_targ)
                    break
        else:
            be_hit = (low_m <= p_be) if is_long else (h >= p_be)
            if be_hit and targ_hit:
                res_b = ("be_exit", 0.0)
                break
            elif be_hit:
                res_b = ("be_exit", 0.0)
                break
            elif targ_hit:
                res_b = ("tp_target", qty * gain_targ)
                break

    if res_a is None:
        res_a = ("timeout", 0.0)
    if res_b is None:
        res_b = ("timeout", 0.0)
    if res_c is None:
        res_c = ("timeout", 0.0)

    return {
        "scenario_a": res_a,
        "scenario_b": res_b,
        "scenario_c": res_c,
        "reached_0382": trigger_hit,
    }


# ─── Вероятностный переход уровней (Reachability) ─────────────────────────────
def analyze_level_reachability(
    df: pd.DataFrame,
    impulses: list[Impulse],
    levels: list[float] = [0.236, 0.382, 0.500, 0.618, 0.707, 0.786, 1.000, 1.272, 1.414, 1.618, 2.000],
    timeout_candles: int = 720,
) -> dict:
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    counts = {lvl: 0 for lvl in levels}
    total_imps = len(impulses)

    for imp in impulses:
        is_long = imp.is_long
        end_search = min(imp.end_idx + timeout_candles, n)
        prices = {lvl: calc_fib(imp.high, imp.low, lvl, is_long=is_long, scale="log") for lvl in levels}

        if is_long:
            max_retracement = min(lows[imp.end_idx + 1:end_search]) if end_search > imp.end_idx + 1 else imp.high
            for lvl in levels:
                if max_retracement <= prices[lvl]:
                    counts[lvl] += 1
        else:
            max_retracement = max(highs[imp.end_idx + 1:end_search]) if end_search > imp.end_idx + 1 else imp.low
            for lvl in levels:
                if max_retracement >= prices[lvl]:
                    counts[lvl] += 1

    pcts = {lvl: (counts[lvl] / total_imps * 100.0) if total_imps > 0 else 0.0 for lvl in levels}
    return {"total": total_imps, "counts": counts, "pcts": pcts}


# ─── Анализ отскока от 1.618 (Манипуляция) ────────────────────────────────────
def analyze_reversals_from_1618(
    df: pd.DataFrame,
    impulses: list[Impulse],
    retrace_targets: list[float] = [1.272, 1.000, 0.786, 0.618, 0.500, 0.382],
    adverse_levels: list[float] = [1.786, 2.000, 2.272, 2.400, 2.618, 3.000],
    timeout_candles: int = 720,
) -> dict:
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    reached_1618_count = 0
    retrace_counts = {lvl: 0 for lvl in retrace_targets}
    adverse_counts = {lvl: 0 for lvl in adverse_levels}

    for imp in impulses:
        is_long = imp.is_long
        p_1618 = calc_fib(imp.high, imp.low, 1.618, is_long=is_long, scale="log")
        end_search = min(imp.end_idx + timeout_candles, n)

        touch_1618_k = None
        for k in range(imp.end_idx + 1, end_search):
            if (is_long and lows[k] <= p_1618) or (not is_long and highs[k] >= p_1618):
                touch_1618_k = k
                break

        if touch_1618_k is None:
            continue

        reached_1618_count += 1
        max_after = min(touch_1618_k + timeout_candles, n)
        sub_highs = highs[touch_1618_k + 1:max_after]
        sub_lows = lows[touch_1618_k + 1:max_after]

        if len(sub_highs) == 0:
            continue

        # 1. Проверка возврата назад
        for target in retrace_targets:
            p_t = calc_fib(imp.high, imp.low, target, is_long=is_long, scale="log")
            if is_long:
                if max(sub_highs) >= p_t:
                    retrace_counts[target] += 1
            else:
                if min(sub_lows) <= p_t:
                    retrace_counts[target] += 1

        # 2. Проверка неблагоприятного движения глубже
        for adv in adverse_levels:
            p_adv = calc_fib(imp.high, imp.low, adv, is_long=is_long, scale="log")
            if is_long:
                if min(sub_lows) <= p_adv:
                    adverse_counts[adv] += 1
            else:
                if max(sub_highs) >= p_adv:
                    adverse_counts[adv] += 1

    return {
        "reached_1618": reached_1618_count,
        "retrace_counts": retrace_counts,
        "retrace_pcts": {k: (v / reached_1618_count * 100.0) if reached_1618_count > 0 else 0.0 for k, v in retrace_counts.items()},
        "adverse_counts": adverse_counts,
        "adverse_pcts": {k: (v / reached_1618_count * 100.0) if reached_1618_count > 0 else 0.0 for k, v in adverse_counts.items()},
    }


# ─── Симуляция сетки DCA с корзинным выходом ──────────────────────────────────
def sim_dca_grid(
    df: pd.DataFrame,
    imp: Impulse,
    e1_fib: float,
    e2_fib: float,
    basket_tp_fib: float,
    sl_fib: float,
    risk_per_order: float = 10.0,
    mult_2: float = 2.0,
    timeout_candles: int = 720,
    fee_maker: float = FEE_MAKER,
    fee_taker: float = FEE_TAKER,
) -> Optional[dict]:
    highs = df["high"].values
    lows = df["low"].values
    df["close"].values
    n = len(df)
    is_long = imp.is_long

    p_e1 = calc_fib(imp.high, imp.low, e1_fib, is_long=is_long, scale="log")
    p_e2 = calc_fib(imp.high, imp.low, e2_fib, is_long=is_long, scale="log")
    p_sl = calc_fib(imp.high, imp.low, sl_fib, is_long=is_long, scale="log")
    p_tp = calc_fib(imp.high, imp.low, basket_tp_fib, is_long=is_long, scale="log")

    if is_long:
        dist1 = (p_e1 - p_sl) + p_e1 * fee_maker + p_sl * fee_taker
        dist2 = (p_e2 - p_sl) + p_e2 * fee_maker + p_sl * fee_taker
        gain1 = (p_tp - p_e1) - p_e1 * fee_maker - p_tp * fee_maker
        gain2 = (p_tp - p_e2) - p_e2 * fee_maker - p_tp * fee_maker
    else:
        dist1 = (p_sl - p_e1) + p_e1 * fee_maker + p_sl * fee_taker
        dist2 = (p_sl - p_e2) + p_e2 * fee_maker + p_sl * fee_taker
        gain1 = (p_e1 - p_tp) - p_e1 * fee_maker - p_tp * fee_maker
        gain2 = (p_e2 - p_tp) - p_e2 * fee_maker - p_tp * fee_maker

    if dist1 <= 0 or dist2 <= 0:
        return None

    qty1 = risk_per_order / dist1
    qty2 = (risk_per_order * mult_2) / dist2

    entry1_k = None
    end_search = min(imp.end_idx + timeout_candles, n)
    for k in range(imp.end_idx + 1, end_search):
        if e1_fib < 1.0:
            if is_long and highs[k] > imp.high:
                break
            if not is_long and lows[k] < imp.low:
                break
        if (is_long and lows[k] <= p_e1) or (not is_long and highs[k] >= p_e1):
            entry1_k = k
            break

    if entry1_k is None:
        return None

    o2_filled = False
    max_hold = min(entry1_k + timeout_candles, n)

    for m in range(entry1_k + 1, max_hold):
        h = highs[m]
        low_m = lows[m]

        if not o2_filled:
            if (is_long and low_m <= p_e2) or (not is_long and h >= p_e2):
                o2_filled = True

        sl_hit = (low_m <= p_sl) if is_long else (h >= p_sl)
        tp_hit = (h >= p_tp) if is_long else (low_m <= p_tp)

        if sl_hit and tp_hit:
            total_loss = -(risk_per_order + (risk_per_order * mult_2 if o2_filled else 0.0))
            return {"outcome": "sl", "pnl": total_loss, "both": o2_filled}
        elif sl_hit:
            total_loss = -(risk_per_order + (risk_per_order * mult_2 if o2_filled else 0.0))
            return {"outcome": "sl", "pnl": total_loss, "both": o2_filled}
        elif tp_hit:
            total_win = qty1 * gain1 + (qty2 * gain2 if o2_filled else 0.0)
            return {"outcome": "tp", "pnl": total_win, "both": o2_filled}

    return {"outcome": "timeout", "pnl": 0.0, "both": o2_filled}


# ─── Основной исполнитель исследования ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fibonacci Strategy Comprehensive Research")
    parser.add_argument("--days", type=int, default=180, help="Days of history (default 180)")
    parser.add_argument("--min-impulse", type=float, default=1.5, help="Min impulse pct (default 1.5)")
    args = parser.parse_args()

    print("=" * 110)
    print(f"  КОМПЛЕКСНЫЙ ИССЛЕДОВАТЕЛЬСКИЙ ТЕСТ FIBONACCI: 31 МОНЕТА (1H, {args.days} ДНЕЙ)")
    print(f"  Мин. импульс: >={args.min_impulse}% | Шкала: Log Fib | Maker: {FEE_MAKER*100:.2f}%, Taker: {FEE_TAKER*100:.3f}%")
    print("=" * 110)

    # 1. Загрузка данных
    data_map: dict[str, pd.DataFrame] = {}
    impulses_map: dict[str, list[Impulse]] = {}

    print("\n[1/5] Загрузка и подготовка данных...")
    for name, symbol in COINS_UNIVERSE:
        try:
            df = fetch_ohlcv(symbol, timeframe="1h", days=args.days, use_cache=True)
            if len(df) < 50 and name == "ASTER":
                df = fetch_ohlcv("ASTRUSDT", timeframe="1h", days=args.days, use_cache=True)

            data_map[name] = df
            imps = detect_impulses(df, min_pct=args.min_impulse, side="both", scale="log")
            impulses_map[name] = imps
            days_span = (df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400.0
            print(f"  ✓ {name:<6s} ({symbol:<12s}): {len(df):>5d} свечей ({days_span:>5.1f} дн) | Импульсов: {len(imps):>4d}")
        except Exception as e:
            print(f"  ✗ {name:<6s} ({symbol:<12s}): ОШИБКА ({e})")

    total_coins = len(data_map)
    total_all_imps = sum(len(imps) for imps in impulses_map.values())
    print(f"\nУспешно загружено монет: {total_coins}/{len(COINS_UNIVERSE)} | Всего найдено импульсов: {total_all_imps}\n")

    # ─────────────────────────────────────────────────────────────────────────────
    # БЛОК 1: ТЕСТ 3 БАЗОВЫХ СТРАТЕГИЙ + РАСШИРЕННАЯ ЛИНЕЙКА
    # ─────────────────────────────────────────────────────────────────────────────
    print("=" * 110)
    print("  БЛОК 1: СРАВНИТЕЛЬНЫЙ ТЕСТ СТРАТЕГИЙ ПО ПОРТФЕЛЮ МОНЕТ ($20 РИСК/СДЕЛКА)")
    print("=" * 110)

    setups_block1 = [
        {"name": "1. 0.500 -> TP 0.236 (SL 1.000)", "entry": 0.500, "tp": 0.236, "sl": 1.000},
        {"name": "2. 0.618 -> TP 0.382 (SL 0.860)", "entry": 0.618, "tp": 0.382, "sl": 0.860},
        {"name": "3. 0.618 -> TP 0.382 (SL 1.000)", "entry": 0.618, "tp": 0.382, "sl": 1.000},
        {"name": "4. 0.500 -> TP 0.382 (SL 0.860)", "entry": 0.500, "tp": 0.382, "sl": 0.860},
        {"name": "5. 0.618 -> TP 0.500 (SL 0.860)", "entry": 0.618, "tp": 0.500, "sl": 0.860},
        {"name": "6. 0.786 -> TP 0.500 (SL 1.000)", "entry": 0.786, "tp": 0.500, "sl": 1.000},
        {"name": "7. Манип. 1.618 -> TP 0.500 (SL 2.000)", "entry": 1.618, "tp": 0.500, "sl": 2.000},
        {"name": "8. Манип. 1.618 -> TP 0.500 (SL 2.400)", "entry": 1.618, "tp": 0.500, "sl": 2.400},
    ]

    block1_results = {}
    for s in setups_block1:
        s_name = s["name"]
        tot_trades = 0
        tot_tp = 0
        tot_sl = 0
        tot_usd = 0.0
        coin_pnl = {}

        for name, df in data_map.items():
            imps = impulses_map[name]
            c_trades = 0
            c_usd = 0.0
            for imp in imps:
                tr = sim_single_order(df, imp, s["entry"], s["tp"], s["sl"], risk_usd=20.0)
                if tr is not None:
                    tot_trades += 1
                    c_trades += 1
                    if tr.exit_reason == "tp":
                        tot_tp += 1
                    elif tr.exit_reason == "sl":
                        tot_sl += 1
                    tot_usd += tr.pnl_usd
                    c_usd += tr.pnl_usd
            coin_pnl[name] = {"trades": c_trades, "usd": c_usd}

        wr = (tot_tp / tot_trades * 100.0) if tot_trades > 0 else 0.0
        block1_results[s_name] = {
            "trades": tot_trades,
            "tp": tot_tp,
            "sl": tot_sl,
            "wr": wr,
            "usd": tot_usd,
            "coin_pnl": coin_pnl,
        }

    b1_header = f"{'Стратегия':<38} | {'Сделок':<8} | {'Win Rate':<9} | {'Тейки / Стопы':<14} | {'Чистая прибыль ($)':<18} | {'В месяц ($)':<12}"
    print(b1_header)
    print("-" * len(b1_header))
    for s_name, res in block1_results.items():
        per_month = res["usd"] / (args.days / 30.0)
        print(f"{s_name:<38} | {res['trades']:<8} | {res['wr']:>6.1f}%   | {res['tp']:>4} / {res['sl']:<7} | {res['usd']:>+14.2f} $   | {per_month:>+8.1f} $/м")

    # ─────────────────────────────────────────────────────────────────────────────
    # БЛОК 2: ОДИНОЧНЫЕ УРОВНИ VS ДОБОР КОРЗИНОЙ (DCA)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print("  БЛОК 2: ОДИНОЧНЫЕ УРОВНИ VS ДОБОР КОРЗИНОЙ (СЕТКИ DCA)")
    print("=" * 110)

    dca_setups = [
        {"name": "Сетка 0.500 (1x) + 0.618 (2x) -> Basket TP 0.382 (SL 1.000)", "e1": 0.500, "e2": 0.618, "tp": 0.382, "sl": 1.000},
        {"name": "Сетка 0.500 (1x) + 0.618 (2x) -> Basket TP 0.500 (SL 1.000)", "e1": 0.500, "e2": 0.618, "tp": 0.500, "sl": 1.000},
        {"name": "Сетка 0.618 (1x) + 0.786 (2x) -> Basket TP 0.382 (SL 1.000)", "e1": 0.618, "e2": 0.786, "tp": 0.382, "sl": 1.000},
        {"name": "Сетка 0.618 (1x) + 0.786 (2x) -> Basket TP 0.500 (SL 1.000)", "e1": 0.618, "e2": 0.786, "tp": 0.500, "sl": 1.000},
        {"name": "Манип. Сетка 1.618 (1x) + 2.000 (2x) -> TP 1.000 (SL 2.400)", "e1": 1.618, "e2": 2.000, "tp": 1.000, "sl": 2.400},
        {"name": "Манип. Сетка 1.618 (1x) + 2.000 (2x) -> TP 0.500 (SL 2.400)", "e1": 1.618, "e2": 2.000, "tp": 0.500, "sl": 2.400},
    ]

    block2_results = {}
    for ds in dca_setups:
        tot_grids = 0
        tot_tp = 0
        tot_sl = 0
        tot_both = 0
        tot_usd = 0.0

        for name, df in data_map.items():
            imps = impulses_map[name]
            for imp in imps:
                res = sim_dca_grid(df, imp, ds["e1"], ds["e2"], ds["tp"], ds["sl"], risk_per_order=10.0, mult_2=2.0)
                if res is not None:
                    tot_grids += 1
                    if res["both"]:
                        tot_both += 1
                    if res["outcome"] == "tp":
                        tot_tp += 1
                    elif res["outcome"] == "sl":
                        tot_sl += 1
                    tot_usd += res["pnl"]

        wr = (tot_tp / tot_grids * 100.0) if tot_grids > 0 else 0.0
        both_pct = (tot_both / tot_grids * 100.0) if tot_grids > 0 else 0.0
        block2_results[ds["name"]] = {
            "trades": tot_grids,
            "tp": tot_tp,
            "sl": tot_sl,
            "wr": wr,
            "both_filled": tot_both,
            "both_pct": both_pct,
            "usd": tot_usd,
        }

    b2_header = f"{'Сетка DCA (Корзина)':<50} | {'Сеток':<7} | {'WinRate':<8} | {'Добор 2-го ордера':<18} | {'Прибыль ($)':<14}"
    print(b2_header)
    print("-" * len(b2_header))
    for s_name, res in block2_results.items():
        both_str = f"{res['both_filled']} ({res['both_pct']:.1f}%)"
        print(f"{s_name:<50} | {res['trades']:<7} | {res['wr']:>6.1f}% | {both_str:<18} | {res['usd']:>+12.2f} $")

    # ─────────────────────────────────────────────────────────────────────────────
    # БЛОК 3: КОРОТКИЕ ДВИЖЕНИЯ И ТРЕЙЛИНГ В БЕЗУБЫТОК (0.500 -> 0.382 -> 0.236)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print("  БЛОК 3: КОРОТКИЕ ДВИЖЕНИЯ (СКАЛЬП) И БЕЗУБЫТОК ПРИ ДОСТИЖЕНИИ 0.382")
    print("=" * 110)
    print("  Вопрос: 'Что будет если поставить стоп на 0.5 уровень когда придем к 0.382, придет ли в итоге к 0.236?'")

    tot_entries = 0
    reached_0382_count = 0
    scen_a_tp = 0
    scen_a_sl = 0
    scen_a_usd = 0.0

    scen_b_tp = 0
    scen_b_be = 0
    scen_b_sl = 0
    scen_b_usd = 0.0

    scen_c_tp = 0
    scen_c_sl = 0
    scen_c_usd = 0.0

    for name, df in data_map.items():
        imps = impulses_map[name]
        for imp in imps:
            tb = sim_trailing_breakeven(df, imp, entry_fib=0.500, trigger_fib=0.382, target_fib=0.236, initial_sl_fib=1.000, risk_usd=20.0)
            if tb is not None:
                tot_entries += 1
                if tb["reached_0382"]:
                    reached_0382_count += 1

                res_a = tb["scenario_a"]
                scen_a_usd += res_a[1]
                if res_a[0] == "tp":
                    scen_a_tp += 1
                elif res_a[0] == "sl":
                    scen_a_sl += 1

                res_b = tb["scenario_b"]
                scen_b_usd += res_b[1]
                if res_b[0] == "tp_target":
                    scen_b_tp += 1
                elif res_b[0] == "be_exit":
                    scen_b_be += 1
                elif res_b[0] == "sl_init":
                    scen_b_sl += 1

                res_c = tb["scenario_c"]
                scen_c_usd += res_c[1]
                if res_c[0] == "tp":
                    scen_c_tp += 1
                elif res_c[0] == "sl":
                    scen_c_sl += 1

    pct_0382 = (reached_0382_count / tot_entries * 100.0) if tot_entries > 0 else 0.0
    conv_to_0236 = (scen_b_tp / reached_0382_count * 100.0) if reached_0382_count > 0 else 0.0
    conv_to_be = (scen_b_be / reached_0382_count * 100.0) if reached_0382_count > 0 else 0.0

    print(f"\nВсего входов на уровне 0.500: {tot_entries}")
    print(f"Касаний уровня 0.382 после входа: {reached_0382_count} ({pct_0382:.1f}%)")
    print(f"  ├── Из них дошли до 0.236: {scen_b_tp} ({conv_to_0236:.1f}% от дошедших до 0.382)")
    print(f"  └── Из них выбило в безубыток на 0.500: {scen_b_be} ({conv_to_be:.1f}% от дошедших до 0.382)\n")

    print(f"{'Подход':<50} | {'Win Rate':<9} | {'Результаты (TP / BE / SL)':<25} | {'Чистая прибыль ($)':<16}")
    print("-" * 110)
    wr_a = (scen_a_tp / tot_entries * 100.0) if tot_entries > 0 else 0.0
    wr_b = (scen_b_tp / tot_entries * 100.0) if tot_entries > 0 else 0.0
    wr_c = (scen_c_tp / tot_entries * 100.0) if tot_entries > 0 else 0.0

    print(f"{'А. Фикс на 0.382 (короткое скальп-движение)':<50} | {wr_a:>6.1f}%   | {f'{scen_a_tp} TP / {scen_a_sl} SL':<25} | {scen_a_usd:>+12.2f} $")
    print(f"{'Б. Трейлинг: при 0.382 стоп в Б/У, цель 0.236':<50} | {wr_b:>6.1f}%   | {f'{scen_b_tp} TP / {scen_b_be} BE / {scen_b_sl} SL':<25} | {scen_b_usd:>+12.2f} $")
    print(f"{'В. Жадный: цель 0.236, стоп фиксирован на 1.000':<50} | {wr_c:>6.1f}%   | {f'{scen_c_tp} TP / {scen_c_sl} SL':<25} | {scen_c_usd:>+12.2f} $")

    # ─────────────────────────────────────────────────────────────────────────────
    # БЛОК 4: ВЕРОЯТНОСТНЫЙ АНАЛИЗ ОТСКОКОВ ОТ 1.618 К 0.618 / 0.500
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print("  БЛОК 4: ВЕРОЯТНОСТНЫЙ АНАЛИЗ ПЕРЕХОДОВ И ЗОНЫ МАНИПУЛЯЦИИ (1.618)")
    print("=" * 110)

    total_agg_reach = {lvl: 0 for lvl in [0.236, 0.382, 0.500, 0.618, 0.707, 0.786, 1.000, 1.272, 1.414, 1.618, 2.000]}
    tot_all_imp_count = 0
    for name, df in data_map.items():
        imps = impulses_map[name]
        r = analyze_level_reachability(df, imps, list(total_agg_reach.keys()))
        tot_all_imp_count += r["total"]
        for lvl, c in r["counts"].items():
            total_agg_reach[lvl] += c

    print("\n1. Вероятность достижения уровней коррекции после формирования импульса (всего импульсов: %d):" % tot_all_imp_count)
    print("-" * 70)
    print(f"{'Уровень Fib':<15} | {'Количество касаний':<20} | {'Вероятность (%)':<15}")
    print("-" * 70)
    for lvl, c in total_agg_reach.items():
        p = (c / tot_all_imp_count * 100.0) if tot_all_imp_count > 0 else 0.0
        print(f"{lvl:<15.3f} | {c:<20d} | {p:>6.1f}%")

    print("\n2. Анализ реакции после касания уровня 1.618 (Манипуляция):")
    tot_1618_hits = 0
    tot_retrace_counts = {lvl: 0 for lvl in [1.272, 1.000, 0.786, 0.618, 0.500, 0.382]}
    tot_adverse_counts = {lvl: 0 for lvl in [1.786, 2.000, 2.272, 2.400, 2.618, 3.000]}

    for name, df in data_map.items():
        imps = impulses_map[name]
        rev = analyze_reversals_from_1618(df, imps)
        tot_1618_hits += rev["reached_1618"]
        for k, v in rev["retrace_counts"].items():
            tot_retrace_counts[k] += v
        for k, v in rev["adverse_counts"].items():
            tot_adverse_counts[k] += v

    print(f"Всего зафиксировано касаний уровня 1.618: {tot_1618_hits}")
    print("\nЧастота отскока назад (к уровням возврата):")
    print("-" * 65)
    print(f"{'Целевой уровень возврата':<28} | {'Отскоков':<15} | {'Частота (%)':<15}")
    print("-" * 65)
    for lvl, c in tot_retrace_counts.items():
        p = (c / tot_1618_hits * 100.0) if tot_1618_hits > 0 else 0.0
        star = " 👑" if lvl in (0.618, 0.500) else ""
        print(f"Возврат к {lvl:<18.3f} | {c:<15d} | {p:>6.1f}%{star}")

    print("\nГлубина неблагоприятного ухода (просадка / пробой дальше):")
    print("-" * 65)
    print(f"{'Уровень углубления (MAE)':<28} | {'Событий':<15} | {'Частота (%)':<15}")
    print("-" * 65)
    for lvl, c in tot_adverse_counts.items():
        p = (c / tot_1618_hits * 100.0) if tot_1618_hits > 0 else 0.0
        print(f"Уход глубже {lvl:<16.3f} | {c:<15d} | {p:>6.1f}%")

    # ─────────────────────────────────────────────────────────────────────────────
    # БЛОК 5: ГЛОБАЛЬНЫЙ ПОИСК РАБОТАЮЩИХ ЧИСЕЛ (GRID SEARCH)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    print("  БЛОК 5: ГЛОБАЛЬНЫЙ ПОИСК ОПТИМАЛЬНЫХ ЧИСЕЛ (GRID SEARCH & OPTIMIZATION)")
    print("=" * 110)

    candidate_entries = [0.382, 0.500, 0.618, 0.707, 0.786, 1.000, 1.414, 1.618]
    candidate_tps = [0.000, 0.236, 0.382, 0.500, 0.618, 0.786, 1.000]
    candidate_sls = [0.707, 0.860, 1.000, 1.130, 1.272, 1.618, 2.000, 2.400, 2.618]

    valid_combos = []
    for e in candidate_entries:
        for tp in candidate_tps:
            if tp >= e:
                continue
            for sl in candidate_sls:
                if sl <= e:
                    continue
                valid_combos.append((e, tp, sl))

    print(f"Всего валидных связок параметров для перебора: {len(valid_combos)}")
    print("Выполняется расчет матрицы на всем пуле из 31 монеты...")

    grid_results = []
    t0 = time.time()

    for idx, (e, tp, sl) in enumerate(valid_combos):
        tot_trades = 0
        tot_tp = 0
        tot_sl = 0
        tot_usd = 0.0

        for name, df in data_map.items():
            imps = impulses_map[name]
            for imp in imps:
                tr = sim_single_order(df, imp, e, tp, sl, risk_usd=20.0)
                if tr is not None:
                    tot_trades += 1
                    if tr.exit_reason == "tp":
                        tot_tp += 1
                    elif tr.exit_reason == "sl":
                        tot_sl += 1
                    tot_usd += tr.pnl_usd

        if tot_trades >= 50:
            wr = (tot_tp / tot_trades * 100.0) if tot_trades > 0 else 0.0
            exp_usd = tot_usd / tot_trades if tot_trades > 0 else 0.0

            grid_results.append({
                "entry": e,
                "tp": tp,
                "sl": sl,
                "trades": tot_trades,
                "tp_count": tot_tp,
                "sl_count": tot_sl,
                "wr": wr,
                "usd": tot_usd,
                "exp_usd": exp_usd,
            })

    elapsed = time.time() - t0
    print(f"Расчет матрицы завершен за {elapsed:.1f} сек. Найдено {len(grid_results)} статистически значимых связок.")

    grid_results.sort(key=lambda x: x["usd"], reverse=True)

    print("\n🏆 ТОП-15 УНИВЕРСАЛЬНЫХ СВЯЗОК ПО СОВОКУПНОЙ ПРИБЫЛИ ($):")
    print("-" * 110)
    g_header = f"{'№':<3} | {'Вход':<7} | {'Тейк':<7} | {'Стоп':<7} | {'Сделок':<8} | {'WinRate':<9} | {'TP / SL':<12} | {'Чистая прибыль ($)':<18} | {'Мат. ожидание':<14}"
    print(g_header)
    print("-" * 110)
    for rank, gr in enumerate(grid_results[:15], 1):
        tp_sl_str = f"{gr['tp_count']} / {gr['sl_count']}"
        print(f"{rank:<3} | {gr['entry']:<7.3f} | {gr['tp']:<7.3f} | {gr['sl']:<7.3f} | {gr['trades']:<8} | {gr['wr']:>6.1f}%   | {tp_sl_str:<12} | {gr['usd']:>+14.2f} $   | {gr['exp_usd']:>+6.2f} $/сд")

    # ─────────────────────────────────────────────────────────────────────────────
    # СОХРАНЕНИЕ ПОДРОБНОГО ОТЧЕТА В MARKDOWN
    # ─────────────────────────────────────────────────────────────────────────────
    report_path = PROJECT_ROOT / "results" / "fib_research_6m_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 📊 Исчерпывающий аналитический отчет: Тестирование стратегий Fibonacci (1H, 180 дней)\n\n")
        f.write("* **Период исследования:** 180 дней (6 месяцев, часовой таймфрейм 1H).\n")
        f.write(f"* **Пул монет:** {total_coins} монет Bybit Linear Futures.\n")
        f.write(f"* **Всего обнаружено импульсов:** {total_all_imps}.\n")
        f.write("* **Биржевые комиссии:** Maker 0.02%, Taker 0.055%.\n")
        f.write("* **Заложенный риск:** $20.00 на сетап (масштабирование объема).\n\n")
        f.write("---\n\n")

        # Блок 1
        f.write("## 1. Сравнительный анализ базовых и расширенных стратегий\n\n")
        f.write("| Стратегия | Всего сделок | Win Rate | TP / SL | Чистая прибыль ($) | В месяц ($) |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: |\n")
        for s_name, res in block1_results.items():
            per_m = res["usd"] / (args.days / 30.0)
            f.write(f"| **{s_name}** | {res['trades']} | **{res['wr']:.1f}%** | {res['tp']} / {res['sl']} | **{res['usd']:+.2f} $** | {per_m:+.1f} $/мес |\n")
        f.write("\n---\n\n")

        # Блок 2
        f.write("## 2. Одиночные входы против Сеток DCA (Корзинный выход)\n\n")
        f.write("| Сетка DCA | Сеток | Win Rate | Добор 2-го ордера | Чистая прибыль ($) |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: |\n")
        for s_name, res in block2_results.items():
            both_str = f"{res['both_filled']} ({res['both_pct']:.1f}%)"
            f.write(f"| **{s_name}** | {res['trades']} | **{res['wr']:.1f}%** | {both_str} | **{res['usd']:+.2f} $** |\n")
        f.write("\n---\n\n")

        # Блок 3
        f.write("## 3. Короткие движения (Скальп) и динамический безубыток (0.500 -> 0.382 -> 0.236)\n\n")
        f.write("### Ключевая статистика переходов:\n")
        f.write(f"* **Всего входов на уровне 0.500:** {tot_entries}\n")
        f.write(f"* **Касаний уровня 0.382 после входа:** {reached_0382_count} ({pct_0382:.1f}%)\n")
        f.write(f"* **Из дошедших до 0.382 продолжили движение к 0.236:** {scen_b_tp} (**{conv_to_0236:.1f}%**)\n")
        f.write(f"* **Из дошедших до 0.382 развернулись и выбили стоп на 0.500 (Б/У):** {scen_b_be} (**{conv_to_be:.1f}%**)\n\n")
        f.write("| Вариант управления позицией | Win Rate | Результаты | Чистая прибыль ($) |\n")
        f.write("| :--- | :---: | :---: | :---: |\n")
        f.write(f"| **А. Фиксация на 0.382 (Скальп)** | **{wr_a:.1f}%** | {scen_a_tp} TP / {scen_a_sl} SL | **{scen_a_usd:+.2f} $** |\n")
        f.write(f"| **Б. Трейлинг: при 0.382 стоп в Б/У, цель 0.236** | **{wr_b:.1f}%** | {scen_b_tp} TP / {scen_b_be} BE / {scen_b_sl} SL | **{scen_b_usd:+.2f} $** |\n")
        f.write(f"| **В. Жадный: цель 0.236, стоп на 1.000** | **{wr_c:.1f}%** | {scen_c_tp} TP / {scen_c_sl} SL | **{scen_c_usd:+.2f} $** |\n\n")
        f.write("---\n\n")

        # Блок 4
        f.write("## 4. Вероятностная матрица и поведение зоны 1.618 (Манипуляция)\n\n")
        f.write("### 4.1 Вероятность отката назад после касания 1.618:\n\n")
        f.write(f"*Всего касаний 1.618: {tot_1618_hits}*\n\n")
        f.write("| Возврат к уровню | Количество отскоков | Вероятность (%) |\n")
        f.write("| :--- | :---: | :---: |\n")
        for lvl, c in tot_retrace_counts.items():
            p = (c / tot_1618_hits * 100.0) if tot_1618_hits > 0 else 0.0
            f.write(f"| **Возврат к {lvl:.3f}** | {c} | **{p:.1f}%** |\n")
        f.write("\n### 4.2 Глубина просадки (уход ниже 1.618):\n\n")
        f.write("| Уровень углубления (MAE) | Количество событий | Частота ухода (%) |\n")
        f.write("| :--- | :---: | :---: |\n")
        for lvl, c in tot_adverse_counts.items():
            p = (c / tot_1618_hits * 100.0) if tot_1618_hits > 0 else 0.0
            f.write(f"| **Пробой ниже {lvl:.3f}** | {c} | **{p:.1f}%** |\n")
        f.write("\n---\n\n")

        # Блок 5
        f.write("## 5. ТОП-15 работающих чисел (Grid Search по всей вселенной параметров)\n\n")
        f.write("| № | Вход | Тейк | Стоп | Сделок | Win Rate | TP / SL | Чистая прибыль ($) | Ожидание ($/сд) |\n")
        f.write("| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |\n")
        for rank, gr in enumerate(grid_results[:15], 1):
            f.write(f"| {rank} | **{gr['entry']:.3f}** | **{gr['tp']:.3f}** | **{gr['sl']:.3f}** | {gr['trades']} | **{gr['wr']:.1f}%** | {gr['tp_count']} / {gr['sl_count']} | **{gr['usd']:+.2f} $** | **{gr['exp_usd']:+.2f} $** |\n")

    print(f"\nОтчет успешно записан в: {report_path}")


if __name__ == "__main__":
    main()

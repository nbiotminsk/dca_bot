from __future__ import annotations
"""Unit-тесты для математики Фибоначчи и симулятора стратегии «Манипуляция на часе»."""
import numpy as np
import pandas as pd
import pytest


def calc_fib_log(high: float, low: float, level: float, is_long: bool = True) -> float:
    """Логарифмический уровень Фибоначчи строго как в index.php."""
    if high <= 0 or low <= 0:
        return 0.0
    lh = np.log(high)
    ll = np.log(low)
    if is_long:
        return float(np.exp(lh - level * (lh - ll)))
    else:
        return float(np.exp(ll + level * (lh - ll)))


def calc_fib_linear(high: float, low: float, level: float, is_long: bool = True) -> float:
    """Линейный уровень Фибоначчи."""
    if is_long:
        return float(high - level * (high - low))
    else:
        return float(low + level * (high - low))


class TestFibMath:
    def test_fib_long_log_key_levels(self):
        high = 100.0
        low = 50.0
        # 0.0 -> high
        assert np.isclose(calc_fib_log(high, low, 0.0, is_long=True), 100.0)
        # 1.0 -> low
        assert np.isclose(calc_fib_log(high, low, 1.0, is_long=True), 50.0)
        # 0.5 -> sqrt(100 * 50) = 70.710678...
        assert np.isclose(calc_fib_log(high, low, 0.5, is_long=True), np.sqrt(5000))
        # 1.618 -> below low (manipulation)
        m1618 = calc_fib_log(high, low, 1.618, is_long=True)
        assert m1618 < low
        # 2.0 -> 25.0
        assert np.isclose(calc_fib_log(high, low, 2.0, is_long=True), 25.0)

    def test_fib_short_log_key_levels(self):
        high = 100.0
        low = 50.0
        # 0.0 -> low
        assert np.isclose(calc_fib_log(high, low, 0.0, is_long=False), 50.0)
        # 1.0 -> high
        assert np.isclose(calc_fib_log(high, low, 1.0, is_long=False), 100.0)
        # 0.5 -> 70.710678...
        assert np.isclose(calc_fib_log(high, low, 0.5, is_long=False), np.sqrt(5000))
        # 1.618 -> above high (short manipulation)
        m1618_short = calc_fib_log(high, low, 1.618, is_long=False)
        assert m1618_short > high

    def test_fib_linear(self):
        high = 100.0
        low = 50.0
        assert np.isclose(calc_fib_linear(high, low, 0.0, is_long=True), 100.0)
        assert np.isclose(calc_fib_linear(high, low, 0.5, is_long=True), 75.0)
        assert np.isclose(calc_fib_linear(high, low, 1.0, is_long=True), 50.0)
        assert np.isclose(calc_fib_linear(high, low, 1.618, is_long=True), 100 - 1.618 * 50)

from dataclasses import dataclass


@dataclass
class Impulse:
    start_idx: int
    end_idx: int
    high: float
    low: float
    pct: float
    is_long: bool


def detect_impulses_test(
    df: pd.DataFrame,
    min_pct: float = 1.5,
    side: str = "long",
    scale: str = "log"
) -> list[Impulse]:
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    impulses = []

    calc_fn = calc_fib_log if scale == "log" else calc_fib_linear

    if side in ("long", "both"):
        i = 0
        while i < n - 2:
            l_s = lows[i]
            h_s = highs[i]
            cur_h = h_s
            is_imp = False
            broken = False
            end_idx = i

            j = i + 1
            while j < n:
                l_j = lows[j]
                h_j = highs[j]

                if not is_imp:
                    if l_j < l_s:
                        broken = True
                        break
                    fib_05 = calc_fn(h_s, l_s, 0.500, is_long=True)
                    if l_j <= fib_05:
                        broken = True
                        break
                    if h_j > h_s:
                        is_imp = True
                        cur_h = h_j
                        end_idx = j
                else:
                    fib_05 = calc_fn(cur_h, l_s, 0.500, is_long=True)
                    if l_j <= fib_05:
                        break
                    if h_j > cur_h:
                        cur_h = h_j
                        end_idx = j
                j += 1

            if is_imp and not broken:
                pct = (cur_h - l_s) / l_s * 100.0
                if pct >= min_pct:
                    impulses.append(Impulse(i, end_idx, cur_h, l_s, pct, True))
                    i = end_idx + 1
                    continue
            i += 1

    return impulses


class TestImpulseDetector:
    def test_detects_clean_long_impulse(self):
        # 0: base candle (low=100, high=102)
        # 1: higher high (low=101, high=105)
        # 2: higher high (low=103, high=110)
        # 3: pullback touching 0.5 (low=104, high=109)
        # 4: subsequent candles
        candles = [
            {"high": 102.0, "low": 100.0, "close": 101.5},
            {"high": 105.0, "low": 101.0, "close": 104.5},
            {"high": 110.0, "low": 103.0, "close": 109.0},
            {"high": 109.0, "low": 104.0, "close": 105.0}, # fib 0.5 of 110/100 is ~104.88
            {"high": 106.0, "low": 103.0, "close": 104.0},
        ]
        df = pd.DataFrame(candles)
        imps = detect_impulses_test(df, min_pct=1.0, side="long")
        assert len(imps) >= 1
        assert imps[0].high == 110.0
        assert imps[0].low == 100.0
        assert np.isclose(imps[0].pct, 10.0)

@dataclass
class TradeRecord:
    side: str
    impulse_start: int
    impulse_end: int
    impulse_high: float
    impulse_low: float
    impulse_pct: float
    entry_fib: float
    tp_fib: float
    sl_fib: float | None
    entry_idx: int
    entry_price: float
    exit_idx: int
    exit_price: float
    exit_reason: str
    gross_pnl_pct: float
    net_pnl_pct: float
    hold_candles: int


def simulate_single_trade(
    df: pd.DataFrame,
    imp: Impulse,
    entry_fib: float = 0.618,
    tp_fib: float = 0.382,
    sl_fib: float | None = 0.860,
    timeout_candles: int = 168,
    scale: str = "log",
    fee_pct: float = 0.04
) -> TradeRecord | None:
    calc_fn = calc_fib_log if scale == "log" else calc_fib_linear
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    n = len(df)

    p_entry = calc_fn(imp.high, imp.low, entry_fib, is_long=imp.is_long)
    p_tp = calc_fn(imp.high, imp.low, tp_fib, is_long=imp.is_long)
    p_sl = calc_fn(imp.high, imp.low, sl_fib, is_long=imp.is_long) if sl_fib is not None else None

    # Поиск точки входа
    entry_idx = None
    start_search = imp.end_idx + 1
    max_search = min(start_search + timeout_candles, n)

    for k in range(start_search, max_search):
        if imp.is_long:
            # Если цена до касания входа обновила хай или коснулась TP -> отмена
            if highs[k] >= p_tp or highs[k] > imp.high:
                if lows[k] > p_entry:
                    return None
            if lows[k] <= p_entry:
                entry_idx = k
                break
        else: # short
            if lows[k] <= p_tp or lows[k] < imp.low:
                if highs[k] < p_entry:
                    return None
            if highs[k] >= p_entry:
                entry_idx = k
                break

    if entry_idx is None:
        return None

    # Позиция открыта, сопровождаем
    exit_idx = None
    exit_price = None
    exit_reason = "timeout"
    max_hold = min(entry_idx + timeout_candles, n)

    for m in range(entry_idx + 1, max_hold):
        if imp.is_long:
            sl_hit = (p_sl is not None and lows[m] <= p_sl)
            tp_hit = (highs[m] >= p_tp)
            if sl_hit and tp_hit:
                exit_idx = m
                exit_price = p_sl
                exit_reason = "sl"
                break
            elif sl_hit:
                exit_idx = m
                exit_price = p_sl
                exit_reason = "sl"
                break
            elif tp_hit:
                exit_idx = m
                exit_price = p_tp
                exit_reason = "tp"
                break
        else: # short
            sl_hit = (p_sl is not None and highs[m] >= p_sl)
            tp_hit = (lows[m] <= p_tp)
            if sl_hit and tp_hit:
                exit_idx = m
                exit_price = p_sl
                exit_reason = "sl"
                break
            elif sl_hit:
                exit_idx = m
                exit_price = p_sl
                exit_reason = "sl"
                break
            elif tp_hit:
                exit_idx = m
                exit_price = p_tp
                exit_reason = "tp"
                break

    if exit_idx is None:
        exit_idx = max_hold - 1
        exit_price = closes[exit_idx]
        exit_reason = "timeout"

    hold_candles = exit_idx - entry_idx
    if imp.is_long:
        gross_pnl = (exit_price - p_entry) / p_entry * 100.0
    else:
        gross_pnl = (p_entry - exit_price) / p_entry * 100.0
    net_pnl = gross_pnl - fee_pct

    return TradeRecord(
        side="long" if imp.is_long else "short",
        impulse_start=imp.start_idx,
        impulse_end=imp.end_idx,
        impulse_high=imp.high,
        impulse_low=imp.low,
        impulse_pct=imp.pct,
        entry_fib=entry_fib,
        tp_fib=tp_fib,
        sl_fib=sl_fib,
        entry_idx=entry_idx,
        entry_price=p_entry,
        exit_idx=exit_idx,
        exit_price=exit_price,
        exit_reason=exit_reason,
        gross_pnl_pct=gross_pnl,
        net_pnl_pct=net_pnl,
        hold_candles=hold_candles,
    )


class TestSimulator:
    def test_simulate_trade_tp(self):
        # Импульс 100 -> 110 (завершается на свече 2)
        # 3: откат к 0.618 (~103.7)
        # 4: отскок к 0.382 (~106.1) -> TP!
        candles = [
            {"high": 102.0, "low": 100.0, "close": 101.5},
            {"high": 106.0, "low": 101.0, "close": 105.5},
            {"high": 110.0, "low": 104.0, "close": 109.0}, # end_idx = 2
            {"high": 105.0, "low": 103.0, "close": 103.5}, # entry 0.618 touched
            {"high": 107.0, "low": 103.2, "close": 106.5}, # tp 0.382 touched
        ]
        df = pd.DataFrame(candles)
        imp = Impulse(start_idx=0, end_idx=2, high=110.0, low=100.0, pct=10.0, is_long=True)
        trade = simulate_single_trade(df, imp, entry_fib=0.618, tp_fib=0.382, sl_fib=0.860)
        assert trade is not None
        assert trade.exit_reason == "tp"
        assert trade.gross_pnl_pct > 0

    def test_simulate_trade_sl(self):
        # Импульс 100 -> 110
        # 3: откат к 0.618 (~103.7) -> entry
        # 4: дальнейшее падение ниже 0.860 (~101.3) -> SL!
        candles = [
            {"high": 102.0, "low": 100.0, "close": 101.5},
            {"high": 106.0, "low": 101.0, "close": 105.5},
            {"high": 110.0, "low": 104.0, "close": 109.0},
            {"high": 105.0, "low": 103.0, "close": 103.5}, # entry
            {"high": 103.5, "low": 100.5, "close": 101.0}, # sl hit
        ]
        df = pd.DataFrame(candles)
        imp = Impulse(start_idx=0, end_idx=2, high=110.0, low=100.0, pct=10.0, is_long=True)
        trade = simulate_single_trade(df, imp, entry_fib=0.618, tp_fib=0.382, sl_fib=0.860)
        assert trade is not None
        assert trade.exit_reason == "sl"
        assert trade.gross_pnl_pct < 0

    def test_simulate_trade_cancelled_if_tp_hit_before_entry(self):
        # Импульс 100 -> 110
        # 3: цена поднимается выше high (111) не доходя до 0.618
        candles = [
            {"high": 102.0, "low": 100.0, "close": 101.5},
            {"high": 106.0, "low": 101.0, "close": 105.5},
            {"high": 110.0, "low": 104.0, "close": 109.0},
            {"high": 112.0, "low": 106.0, "close": 111.0}, # breaks higher
        ]
        df = pd.DataFrame(candles)
        imp = Impulse(start_idx=0, end_idx=2, high=110.0, low=100.0, pct=10.0, is_long=True)
        trade = simulate_single_trade(df, imp, entry_fib=0.618, tp_fib=0.382, sl_fib=0.860)
        assert trade is None

    def test_detect_impulses_with_max_pct_filter(self):
        from scripts.backtest_strategy_interactive import detect_impulses
        # 100 -> 110 (+10% impulse)
        candles = [
            {"timestamp": pd.Timestamp("2026-01-01 00:00"), "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 01:00"), "open": 101.5, "high": 106.0, "low": 101.0, "close": 105.5, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 02:00"), "open": 105.5, "high": 110.0, "low": 104.0, "close": 109.0, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 03:00"), "open": 109.0, "high": 108.0, "low": 103.0, "close": 104.0, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 04:00"), "open": 104.0, "high": 105.0, "low": 102.0, "close": 103.0, "volume": 100},
        ]
        df = pd.DataFrame(candles)

        # 1. Диапазон 0.5% - 5.0%: импульс 10% НЕ должен пройти
        imps_narrow = detect_impulses(df, min_pct=0.5, max_pct=5.0, side="long")
        assert len(imps_narrow) == 0

        # 2. Диапазон 5.0% - 15.0%: импульс 10% ДОЛЖЕН пройти
        imps_wide = detect_impulses(df, min_pct=5.0, max_pct=15.0, side="long")
        assert len(imps_wide) == 1
        assert imps_wide[0].high == 110.0
        assert imps_wide[0].low == 100.0

    def test_detect_impulses_with_tolerance(self):
        from scripts.backtest_strategy_interactive import detect_impulses
        # 100 -> 110 (0.500 fib log = sqrt(100*110) = 104.88)
        # 3: low 104.92 (на 0.04 выше 0.500)
        # 4: high 115.0 (новый хай)
        # 5: откат до 103.0 (касание 0.500 для 115)
        candles = [
            {"timestamp": pd.Timestamp("2026-01-01 00:00"), "open": 100.0, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 01:00"), "open": 101.5, "high": 110.0, "low": 101.0, "close": 108.0, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 02:00"), "open": 108.0, "high": 109.0, "low": 104.92, "close": 107.0, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 03:00"), "open": 107.0, "high": 115.0, "low": 106.0, "close": 114.0, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 04:00"), "open": 114.0, "high": 114.0, "low": 103.0, "close": 105.0, "volume": 100},
        ]
        df = pd.DataFrame(candles)

        # 1. Без допуска (0.0%): 104.92 > 104.88, пик 110 не зафиксирован, волна выросла до 115
        imps_strict = detect_impulses(df, min_pct=1.0, side="long", tolerance_pct=0.0)
        assert len(imps_strict) == 1
        assert imps_strict[0].high == 115.0

        # 2. С допуском 0.1%: 104.92 <= 104.88 * 1.001 = 104.985 -> пик 110 зафиксирован!
        imps_tol = detect_impulses(df, min_pct=1.0, side="long", tolerance_pct=0.1)
        assert len(imps_tol) >= 1
        assert imps_tol[0].high == 110.0

    def test_detect_impulses_green_candle_confirmation(self):
        from scripts.backtest_strategy_interactive import detect_impulses
        # 1. Вторая свеча зеленая, но закрылась ниже High первой (пробой фитилем):
        # Candle 0: High 105.0, Low 100.0, Open 100.0, Close 104.0
        # Candle 1: High 110.0, Low 103.0, Open 103.5, Close 104.5 (Зеленая! Но Close 104.5 < High_0 105.0)
        # Candle 2: откат к 104.0 (0.500 fib ~ 104.88)
        candles_green = [
            {"timestamp": pd.Timestamp("2026-01-01 00:00"), "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 01:00"), "open": 103.5, "high": 110.0, "low": 103.0, "close": 104.5, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 02:00"), "open": 104.5, "high": 105.0, "low": 104.0, "close": 104.2, "volume": 100},
        ]
        imps_green = detect_impulses(pd.DataFrame(candles_green), min_pct=2.0, side="long")
        assert len(imps_green) == 1
        assert imps_green[0].high == 110.0

        # 2. Вторая свеча красная (пробой фитилем со сбросом, как ARB):
        # Candle 1: High 110.0, Low 103.0, Open 105.5, Close 103.5 (Красная!)
        # Candle 2: падение ниже 0.500 первой свечи (102.5) -> импульс не должен подтвердиться
        candles_red = [
            {"timestamp": pd.Timestamp("2026-01-01 00:00"), "open": 100.0, "high": 105.0, "low": 100.0, "close": 104.0, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 01:00"), "open": 105.5, "high": 110.0, "low": 103.0, "close": 103.5, "volume": 100},
            {"timestamp": pd.Timestamp("2026-01-01 02:00"), "open": 103.5, "high": 104.0, "low": 101.0, "close": 101.5, "volume": 100},
        ]
        imps_red = detect_impulses(pd.DataFrame(candles_red), min_pct=2.0, side="long")
        assert len(imps_red) == 0



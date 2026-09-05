"""
Универсальный движок симуляции двухордерной Fibonacci-сетки.

Стратегия:
  - Ордер 1: вход на entry_fib_1 → тейк tp_fib_1 (или basket_tp) | стоп sl_fib
  - Ордер 2: вход на entry_fib_2 → тейк tp_fib_2 (или basket_tp) | стоп sl_fib
  - Правило One-and-Done: если Ордер 1 закрылся по тейку до того, как Ордер 2 вошёл → Ордер 2 отменяется.
  - Режим basket_tp: если заданы оба входа, оба выходят на одном уровне basket_tp.

Использование:
    from scripts.strategy_engine import GridConfig, simulate_grid

    cfg = GridConfig()                               # Раздельные тейки (0.500→0.236, 0.618→0.382)
    cfg_basket = GridConfig(basket_tp=0.382)         # Корзинный выход на 0.382
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_strategy_interactive import calc_fib
from scripts.constants import FEE_MAKER, FEE_TAKER, MAX_HOLD_BARS


@dataclass
class GridConfig:
    """Параметры сетки ордеров."""
    # Режим торговли: "dual" (два ордера), "solo_1" (только ордер 1), "solo_2" (только ордер 2)
    mode: Literal["dual", "solo_1", "solo_2"] = "dual"
    # Схема тейков:
    #   "classic" -> 0.500 -> TP 0.236, 0.618 -> TP 0.382
    #   "fast"    -> 0.500 -> TP 0.382, 0.618 -> TP 0.500
    #   "custom"  -> пользовательские tp_fib_1 / tp_fib_2
    tp_scheme: Literal["classic", "fast", "custom"] = "classic"
    # Уровни входов
    entry_fib_1: Optional[float] = 0.500
    entry_fib_2: Optional[float] = 0.618
    # Уровни стопа
    sl_fib: float = 1.000
    # Тейки при раздельном закрытии
    tp_fib_1: Optional[float] = None   # тейк Ордера 1 (если None, задается по tp_scheme)
    tp_fib_2: Optional[float] = None   # тейк Ордера 2 (если None, задается по tp_scheme)
    # Корзинный выход: если задан, оба ордера выходят на этом уровне
    basket_tp: Optional[float] = None
    # Риск и комиссии
    risk_per_order: float = 10.0
    fee_maker: float = FEE_MAKER
    fee_taker: float = FEE_TAKER
    # Максимальное ожидание в барах
    max_hold_bars: int = MAX_HOLD_BARS
    # Модуль Reclaim (подбор ложного пробоя / свипа ликвидности)
    enable_sweep_reclaim: bool = True
    sweep_max_pct: float = 3.5
    reclaim_risk: float = 10.0
    reclaim_max_hold_bars: int = 48
    # Модуль Качества Импульса (Anti-Dump Filter: RSI + Верхний фитиль)
    enable_quality_filter: bool = False
    rsi_min: float = 50.0
    rsi_max: float = 82.0
    max_wick_pct: float = 60.0

    def __post_init__(self):
        if self.entry_fib_1 is None and self.entry_fib_2 is not None:
            self.mode = "solo_2"
        elif self.entry_fib_2 is None and self.entry_fib_1 is not None:
            self.mode = "solo_1"

        if self.tp_scheme == "fast":
            if self.tp_fib_1 is None:
                self.tp_fib_1 = 0.382
            if self.tp_fib_2 is None:
                self.tp_fib_2 = 0.500
        elif self.tp_scheme == "classic":
            if self.tp_fib_1 is None:
                self.tp_fib_1 = 0.236
            if self.tp_fib_2 is None:
                self.tp_fib_2 = 0.382


@dataclass
class TradeResult:
    """Результат одной сделки."""
    pnl: float
    win: bool
    o1_pnl: float
    o2_pnl: float
    both_entered: bool
    only_o1: bool
    outcome: str   # "TP1", "TP2", "Basket", "SL1", "SL2", "SL_both", "timeout"
    exit_idx: int = -1
    only_o2: bool = False
    entry_idx: int = -1
    side: str = "long"
    entry_price: float = 0.0
    exit_price: float = 0.0
    hold_bars: int = 0
    entry_time: Optional[str] = None
    exit_time: Optional[str] = None


def _calc_order_metrics(
    imp_high: float,
    imp_low: float,
    entry_fib: float,
    tp_fib: float,
    sl_fib: float,
    is_long: bool,
    risk: float,
    fee_maker: float,
    fee_taker: float,
) -> tuple[float, float, float, float]:
    """
    Вычисляет: (цена входа, цена тейка, цена стопа, кол-во монет).
    Возвращает (p_entry, p_tp, p_sl, qty).
    """
    p_entry = calc_fib(imp_high, imp_low, entry_fib, is_long=is_long, scale="log")
    p_tp    = calc_fib(imp_high, imp_low, tp_fib,    is_long=is_long, scale="log")
    p_sl    = calc_fib(imp_high, imp_low, sl_fib,    is_long=is_long, scale="log")

    if is_long:
        loss = (p_entry - p_sl) + p_entry * fee_maker + p_sl * fee_taker
        gain = (p_tp - p_entry) - p_entry * fee_maker - p_tp * fee_maker
    else:
        loss = (p_sl - p_entry) + p_entry * fee_maker + p_sl * fee_taker
        gain = (p_entry - p_tp) - p_entry * fee_maker - p_tp * fee_maker

    qty = risk / loss if loss > 0 else 0.0
    return p_entry, p_tp, p_sl, qty, gain


def _check_sweep_reclaim(
    df,
    imp,
    exit_i: int,
    cfg: GridConfig,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    opens: np.ndarray,
    hist_line: Optional[np.ndarray],
) -> Optional[TradeResult]:
    """
    Модуль подбора после сбора ликвидности (Sweep Reclaim).
    Если сделка закрылась по стопу, ищем свип (прокол основания до sweep_max_pct),
    подтверждённый затуханием/дивергенцией MACD и возвратом цены (Reclaim).
    При входе ставит стоп за кончик шпильки и тейк на уровень 0.500 Fib.
    """
    n = len(closes)
    w_end = min(exit_i + 30, n)
    if exit_i >= w_end or exit_i >= n - 1:
        return None

    is_long = imp.is_long
    base_lvl = imp.low if is_long else imp.high

    # Целевой тейк - 0.500 Fib исходного импульса
    p_target = calc_fib(imp.high, imp.low, 0.500, is_long=is_long, scale="log")

    if is_long:
        sub_lows = lows[exit_i:w_end]
        sweep_val = float(sub_lows.min())
        sweep_rel_idx = int(sub_lows.argmin())
        sweep_idx = exit_i + sweep_rel_idx

        if sweep_val >= base_lvl:
            return None
        sweep_pct = (base_lvl - sweep_val) / base_lvl * 100.0
        if sweep_pct > cfg.sweep_max_pct or sweep_pct <= 0.05:
            return None

        # Проверка моментума / дивергенции MACD
        if hist_line is not None and sweep_idx < len(hist_line):
            prev_k = max(0, sweep_idx - 1)
            if hist_line[sweep_idx] < hist_line[prev_k] and hist_line[exit_i] < -0.02:
                return None

        # Поиск свечи возврата (Reclaim)
        reclaim_idx = -1
        for k in range(sweep_idx, min(sweep_idx + 15, n)):
            c_k = closes[k]
            o_k = opens[k]
            h_k = highs[k]
            l_k = lows[k]
            if c_k >= base_lvl or (c_k > o_k and (c_k - l_k) > (h_k - c_k)):
                reclaim_idx = k
                break

        if reclaim_idx == -1 or reclaim_idx >= n - 1:
            return None

        p_entry = closes[reclaim_idx]
        p_sl = sweep_val * 0.998  # стоп за кончик шпильки с буфером 0.2%
        if p_entry <= p_sl or p_entry >= p_target:
            return None

        loss_dist = p_entry - p_sl
        qty = cfg.reclaim_risk / loss_dist

        # Симуляция удержания Reclaim-сделки
        hold_end = min(reclaim_idx + cfg.reclaim_max_hold_bars, n)
        for m in range(reclaim_idx + 1, hold_end):
            if lows[m] <= p_sl:
                return TradeResult(
                    pnl=-cfg.reclaim_risk,
                    win=False,
                    o1_pnl=-cfg.reclaim_risk,
                    o2_pnl=0.0,
                    both_entered=False,
                    only_o1=True,
                    outcome="Sweep_SL",
                    exit_idx=m,
                )
            if highs[m] >= p_target:
                gain = (p_target - p_entry) - p_entry * cfg.fee_maker - p_target * cfg.fee_taker
                pnl = qty * gain
                return TradeResult(
                    pnl=pnl,
                    win=True,
                    o1_pnl=pnl,
                    o2_pnl=0.0,
                    both_entered=False,
                    only_o1=True,
                    outcome="Sweep_TP",
                    exit_idx=m,
                )
    else:
        # Для SHORT
        sub_highs = highs[exit_i:w_end]
        sweep_val = float(sub_highs.max())
        sweep_rel_idx = int(sub_highs.argmax())
        sweep_idx = exit_i + sweep_rel_idx

        if sweep_val <= base_lvl:
            return None
        sweep_pct = (sweep_val - base_lvl) / base_lvl * 100.0
        if sweep_pct > cfg.sweep_max_pct or sweep_pct <= 0.05:
            return None

        if hist_line is not None and sweep_idx < len(hist_line):
            prev_k = max(0, sweep_idx - 1)
            if hist_line[sweep_idx] > hist_line[prev_k] and hist_line[exit_i] > 0.02:
                return None

        reclaim_idx = -1
        for k in range(sweep_idx, min(sweep_idx + 15, n)):
            c_k = closes[k]
            o_k = opens[k]
            h_k = highs[k]
            l_k = lows[k]
            if c_k <= base_lvl or (c_k < o_k and (h_k - c_k) > (c_k - l_k)):
                reclaim_idx = k
                break

        if reclaim_idx == -1 or reclaim_idx >= n - 1:
            return None

        p_entry = closes[reclaim_idx]
        p_sl = sweep_val * 1.002
        if p_entry >= p_sl or p_entry <= p_target:
            return None

        loss_dist = p_sl - p_entry
        qty = cfg.reclaim_risk / loss_dist

        hold_end = min(reclaim_idx + cfg.reclaim_max_hold_bars, n)
        for m in range(reclaim_idx + 1, hold_end):
            if highs[m] >= p_sl:
                return TradeResult(
                    pnl=-cfg.reclaim_risk,
                    win=False,
                    o1_pnl=-cfg.reclaim_risk,
                    o2_pnl=0.0,
                    both_entered=False,
                    only_o1=True,
                    outcome="Sweep_SL",
                    exit_idx=m,
                )
            if lows[m] <= p_target:
                gain = (p_entry - p_target) - p_entry * cfg.fee_maker - p_target * cfg.fee_taker
                pnl = qty * gain
                return TradeResult(
                    pnl=pnl,
                    win=True,
                    o1_pnl=pnl,
                    o2_pnl=0.0,
                    both_entered=False,
                    only_o1=True,
                    outcome="Sweep_TP",
                    exit_idx=m,
                )

    return None


def simulate_grid(df, impulses, config: GridConfig, *args, **kwargs) -> list[TradeResult]:
    """
    Прогоняет симуляцию сетки ордеров по импульсам.

    Режимы:
      - mode="solo_1": только Ордер 1 (entry_fib_1 -> tp_fib_1, SL sl_fib)
      - mode="solo_2": только Ордер 2 (entry_fib_2 -> tp_fib_2, SL sl_fib)
      - mode="dual":
          * Вход Ордера 1 на entry_fib_1
          * Если TP1 взят до касания entry_fib_2 -> выход с TP1_only, Ордер 2 отменяется (One-and-Done)
          * Если цена коснулась entry_fib_2 при незакрытом TP1 -> вход Ордера 2
          * При двойном входе: выход по basket_tp (если задан) или по отдельным TP1 / TP2
    """
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    opens  = df["open"].values if "open" in df else closes
    n      = len(df)
    cfg    = config

    # Предрасчёт MACD гистограммы для модуля Reclaim
    if cfg.enable_sweep_reclaim and hasattr(df, "columns") and "close" in df.columns and len(df) >= 26:
        fast_m = df["close"].ewm(span=12, adjust=False).mean()
        slow_m = df["close"].ewm(span=26, adjust=False).mean()
        macd_l = fast_m - slow_m
        sig_l  = macd_l.ewm(span=9, adjust=False).mean()
        hist_line = (macd_l - sig_l).values
    else:
        hist_line = None

    # Предрасчёт RSI для модуля качества импульса
    if cfg.enable_quality_filter and hasattr(df, "columns") and "close" in df.columns and len(df) >= 14:
        from indicators.rsi import calculate_rsi
        rsi_line = calculate_rsi(df["close"], period=14).values
    else:
        rsi_line = None

    enable_o1 = (cfg.mode in ("dual", "solo_1")) and (cfg.entry_fib_1 is not None)
    enable_o2 = (cfg.mode in ("dual", "solo_2")) and (cfg.entry_fib_2 is not None)

    trades: list[TradeResult] = []
    last_exit_idx = -1

    for imp in impulses:
        if imp.end_idx <= last_exit_idx:
            continue

        # Фильтр модуля качества (RSI + Фитиль)
        if cfg.enable_quality_filter:
            end_i = imp.end_idx
            if rsi_line is not None and end_i < len(rsi_line):
                r_val = float(rsi_line[end_i])
                if r_val < cfg.rsi_min or r_val > cfg.rsi_max:
                    continue
            if end_i < n:
                h_bar = highs[end_i]
                l_bar = lows[end_i]
                o_bar = opens[end_i]
                c_bar = closes[end_i]
                rng_bar = h_bar - l_bar
                if rng_bar > 0:
                    if imp.is_long:
                        wick_pct = (h_bar - max(o_bar, c_bar)) / rng_bar * 100.0
                    else:
                        wick_pct = (min(o_bar, c_bar) - l_bar) / rng_bar * 100.0
                    if wick_pct > cfg.max_wick_pct:
                        continue

        is_long = imp.is_long

        # --- Предрасчёт уровней ---
        p_sl = calc_fib(imp.high, imp.low, cfg.sl_fib, is_long=is_long, scale="log")

        if enable_o1:
            solo_tp1 = cfg.tp_fib_1 if cfg.tp_fib_1 is not None else 0.236
            p_e1, p_tp1, _, qty1, gain1 = _calc_order_metrics(
                imp.high, imp.low, cfg.entry_fib_1, solo_tp1, cfg.sl_fib,
                is_long, cfg.risk_per_order, cfg.fee_maker, cfg.fee_taker
            )
        else:
            p_e1 = p_tp1 = qty1 = gain1 = 0.0

        if enable_o2:
            tp2_lvl = cfg.tp_fib_2 if cfg.tp_fib_2 is not None else 0.382
            p_e2, p_tp2, _, qty2, gain2 = _calc_order_metrics(
                imp.high, imp.low, cfg.entry_fib_2, tp2_lvl, cfg.sl_fib,
                is_long, cfg.risk_per_order, cfg.fee_maker, cfg.fee_taker
            )
        else:
            p_e2 = p_tp2 = qty2 = gain2 = 0.0

        # Уровни тейка в корзинном режиме (только если оба активны)
        if cfg.basket_tp is not None and enable_o1 and enable_o2:
            p_basket = calc_fib(imp.high, imp.low, cfg.basket_tp, is_long=is_long, scale="log")
            if is_long:
                gain1_basket = (p_basket - p_e1) - p_e1 * cfg.fee_maker - p_basket * cfg.fee_maker
                gain2_basket = (p_basket - p_e2) - p_e2 * cfg.fee_maker - p_basket * cfg.fee_maker
            else:
                gain1_basket = (p_e1 - p_basket) - p_e1 * cfg.fee_maker - p_basket * cfg.fee_maker
                gain2_basket = (p_e2 - p_basket) - p_e2 * cfg.fee_maker - p_basket * cfg.fee_maker
        else:
            p_basket = None
            gain1_basket = gain2_basket = 0.0

        # --- State machine ---
        o1_filled = o1_closed = False
        o2_filled = o2_closed = False
        o2_active = enable_o2
        o1_pnl = o2_pnl = 0.0
        outcome = ""
        event_exit_idx = -1
        o1_entry_bar = -1
        o2_entry_bar = -1

        end_search = min(imp.end_idx + cfg.max_hold_bars, n)

        for k in range(imp.end_idx + 1, end_search):
            h_k = highs[k]
            l_k = lows[k]
            c_k = closes[k]
            o_k = opens[k]

            # Отмена сетки при обновлении экстремума (никто ещё не вошёл)
            if not o1_filled and not o2_filled:
                if is_long and h_k > imp.high:
                    break
                if not is_long and l_k < imp.low:
                    break

            sl_hit = (l_k <= p_sl) if is_long else (h_k >= p_sl)

            # --- Сценарий 1: solo_1 (только Ордер 1) ---
            if cfg.mode == "solo_1" or (enable_o1 and not enable_o2):
                if not o1_filled:
                    hit = (l_k <= p_e1) if is_long else (h_k >= p_e1)
                    if hit:
                        o1_filled = True
                        o1_entry_bar = k
                if not o1_filled:
                    continue

                if k > o1_entry_bar:
                    tp_hit = (h_k >= p_tp1) if is_long else (l_k <= p_tp1)
                else:
                    tp_hit = (c_k >= p_tp1) if is_long else (c_k <= p_tp1)

                if sl_hit:
                    o1_closed = True
                    o1_pnl = -cfg.risk_per_order
                    outcome = "SL1"
                    event_exit_idx = k
                    break
                elif tp_hit:
                    o1_closed = True
                    o1_pnl = qty1 * gain1
                    outcome = "TP1"
                    event_exit_idx = k
                    break

            # --- Сценарий 2: solo_2 (только Ордер 2) ---
            elif cfg.mode == "solo_2" or (enable_o2 and not enable_o1):
                if not o2_filled:
                    hit = (l_k <= p_e2) if is_long else (h_k >= p_e2)
                    if hit:
                        o2_filled = True
                        o2_entry_bar = k
                if not o2_filled:
                    continue

                if k > o2_entry_bar:
                    tp_hit = (h_k >= p_tp2) if is_long else (l_k <= p_tp2)
                else:
                    tp_hit = (c_k >= p_tp2) if is_long else (c_k <= p_tp2)

                if sl_hit:
                    o2_closed = True
                    o2_pnl = -cfg.risk_per_order
                    outcome = "SL2"
                    event_exit_idx = k
                    break
                elif tp_hit:
                    o2_closed = True
                    o2_pnl = qty2 * gain2
                    outcome = "TP2"
                    event_exit_idx = k
                    break

            # --- Сценарий 3: dual (последовательный вход 0.500 -> 0.618 при незакрытом TP) ---
            else:
                # 1. Если еще никто не вошел: проверяем вход Ордера 1
                if not o1_filled:
                    hit_1 = (l_k <= p_e1) if is_long else (h_k >= p_e1)
                    if hit_1:
                        o1_filled = True
                        o1_entry_bar = k

                        # Проверяем, закрылся ли Ордер 1 по TP на той же свече
                        tp1_closed_on_entry = (c_k >= p_tp1) if is_long else (c_k <= p_tp1)
                        if tp1_closed_on_entry:
                            o1_closed = True
                            o1_pnl = qty1 * gain1
                            o2_active = False
                            outcome = "TP1_only"
                            event_exit_idx = k
                            break

                        # Если TP не закрыт, проверяем, достала ли свеча до 0.618
                        hit_2 = (l_k <= p_e2) if is_long else (h_k >= p_e2)
                        if hit_2 and o2_active:
                            o2_filled = True
                            o2_entry_bar = k

                if not o1_filled:
                    continue

                # 2. Если Ордер 1 в рынке, а Ордер 2 еще НЕ вошел
                if o1_filled and not o1_closed and o2_active and not o2_filled:
                    hit_e2 = (l_k <= p_e2) if is_long else (h_k >= p_e2)
                    if k > o1_entry_bar:
                        tp1_hit = (h_k >= p_tp1) if is_long else (l_k <= p_tp1)
                    else:
                        tp1_hit = (c_k >= p_tp1) if is_long else (c_k <= p_tp1)

                    if sl_hit:
                        o1_closed = True
                        o1_pnl = -cfg.risk_per_order
                        o2_filled = True
                        o2_closed = True
                        o2_pnl = -cfg.risk_per_order
                        outcome = "SL_both"
                        event_exit_idx = k
                        break
                    elif tp1_hit and not hit_e2:
                        o1_closed = True
                        o1_pnl = qty1 * gain1
                        o2_active = False
                        outcome = "TP1_only"
                        event_exit_idx = k
                        break
                    elif hit_e2 and not tp1_hit:
                        o2_filled = True
                        o2_entry_bar = k
                    elif hit_e2 and tp1_hit:
                        tp_first = (c_k < o_k) if is_long else (c_k > o_k)
                        if tp_first:
                            o1_closed = True
                            o1_pnl = qty1 * gain1
                            o2_active = False
                            outcome = "TP1_only"
                            event_exit_idx = k
                            break
                        else:
                            o2_filled = True
                            o2_entry_bar = k

                # 3. Сопровождение, когда оба ордера (или оставшийся ордер 1) в рынке
                if o1_filled and not o1_closed:
                    if cfg.basket_tp is not None and o2_filled:
                        basket_bar = max(o1_entry_bar, o2_entry_bar)
                        if k > basket_bar:
                            tp_hit = (h_k >= p_basket) if is_long else (l_k <= p_basket)
                        else:
                            tp_hit = (c_k >= p_basket) if is_long else (c_k <= p_basket)

                        if sl_hit:
                            o1_closed = True
                            o1_pnl = -cfg.risk_per_order
                        elif tp_hit:
                            o1_closed = True
                            o1_pnl = qty1 * gain1_basket
                    else:
                        if k > o1_entry_bar:
                            tp_hit = (h_k >= p_tp1) if is_long else (l_k <= p_tp1)
                        else:
                            tp_hit = (c_k >= p_tp1) if is_long else (c_k <= p_tp1)

                        if sl_hit:
                            o1_closed = True
                            o1_pnl = -cfg.risk_per_order
                        elif tp_hit:
                            o1_closed = True
                            o1_pnl = qty1 * gain1
                            if not o2_filled:
                                o2_active = False
                                outcome = "TP1_only"
                                event_exit_idx = k
                                break

                if o2_filled and not o2_closed:
                    if cfg.basket_tp is not None:
                        basket_bar = max(o1_entry_bar, o2_entry_bar)
                        if k > basket_bar:
                            tp_hit = (h_k >= p_basket) if is_long else (l_k <= p_basket)
                        else:
                            tp_hit = (c_k >= p_basket) if is_long else (c_k <= p_basket)

                        if sl_hit:
                            o2_closed = True
                            o2_pnl = -cfg.risk_per_order
                        elif tp_hit:
                            o2_closed = True
                            o2_pnl = qty2 * gain2_basket
                    else:
                        if k > o2_entry_bar:
                            tp_hit = (h_k >= p_tp2) if is_long else (l_k <= p_tp2)
                        else:
                            tp_hit = (c_k >= p_tp2) if is_long else (c_k <= p_tp2)

                        if sl_hit:
                            o2_closed = True
                            o2_pnl = -cfg.risk_per_order
                        elif tp_hit:
                            o2_closed = True
                            o2_pnl = qty2 * gain2

                # Проверяем, все ли активные ордера закрыты
                if o1_filled and not o2_active:
                    all_done = o1_closed
                elif o1_filled and o2_filled:
                    all_done = o1_closed and o2_closed
                elif not o1_filled and o2_filled:
                    all_done = o2_closed
                else:
                    all_done = False

                if all_done:
                    event_exit_idx = k
                    if not outcome:
                        if o1_pnl < 0 or o2_pnl < 0:
                            outcome = "SL_both" if (o1_filled and o2_filled) else ("SL1" if o1_filled else "SL2")
                        elif cfg.basket_tp is not None and o1_filled and o2_filled:
                            outcome = "Basket"
                        else:
                            outcome = "TP1+TP2" if (o1_filled and o2_filled) else ("TP1" if o1_filled else "TP2")
                    break

        if o1_filled or o2_filled:
            first_entry_bar = o1_entry_bar if o1_filled else o2_entry_bar
            if o1_filled and o2_filled:
                first_entry_bar = min(o1_entry_bar, o2_entry_bar)

            if o1_filled and o2_filled:
                tot_q = qty1 + qty2
                avg_entry = (p_e1 * qty1 + p_e2 * qty2) / tot_q if tot_q > 0 else p_e1
            elif o1_filled:
                avg_entry = p_e1
            else:
                avg_entry = p_e2

            if "SL" in (outcome or ""):
                exit_price = p_sl
            elif "Basket" in (outcome or ""):
                exit_price = p_basket if p_basket is not None else avg_entry
            elif outcome in ("TP1_only", "TP1"):
                exit_price = p_tp1
            elif outcome == "TP2":
                exit_price = p_tp2
            elif outcome == "TP1+TP2":
                tot_q = qty1 + qty2
                exit_price = (p_tp1 * qty1 + p_tp2 * qty2) / tot_q if tot_q > 0 else p_tp1
            elif 0 <= event_exit_idx < n:
                exit_price = float(closes[event_exit_idx])
            else:
                exit_price = avg_entry

            hold_bars = (event_exit_idx - first_entry_bar) if (event_exit_idx >= 0 and first_entry_bar >= 0) else 0
            e_time = str(df["timestamp"].iloc[first_entry_bar]) if (hasattr(df, "columns") and "timestamp" in df.columns and 0 <= first_entry_bar < n) else None
            x_time = str(df["timestamp"].iloc[event_exit_idx]) if (hasattr(df, "columns") and "timestamp" in df.columns and 0 <= event_exit_idx < n) else None

            tot_pnl = o1_pnl + o2_pnl
            trades.append(TradeResult(
                pnl=tot_pnl,
                win=(tot_pnl > 0),
                o1_pnl=o1_pnl,
                o2_pnl=o2_pnl,
                both_entered=(o1_filled and o2_filled),
                only_o1=(o1_filled and not o2_filled),
                outcome=outcome or "timeout",
                exit_idx=event_exit_idx,
                only_o2=(o2_filled and not o1_filled),
                entry_idx=first_entry_bar,
                side="long" if is_long else "short",
                entry_price=avg_entry,
                exit_price=exit_price,
                hold_bars=hold_bars,
                entry_time=e_time,
                exit_time=x_time,
            ))
            if event_exit_idx >= 0:
                last_exit_idx = event_exit_idx

            # === Модуль Reclaim (подбор ложного пробоя / свипа ликвидности) ===
            if cfg.enable_sweep_reclaim and "SL" in (outcome or "") and event_exit_idx >= 0:
                rec_trade = _check_sweep_reclaim(
                    df, imp, event_exit_idx, cfg, closes, highs, lows, opens, hist_line
                )
                if rec_trade is not None:
                    trades.append(rec_trade)
                    if rec_trade.exit_idx >= 0:
                        last_exit_idx = max(last_exit_idx, rec_trade.exit_idx)

    return trades


def simulate_manipulation_grid(
    df,
    impulses,
    entry_fib_1: float = 1.618,
    entry_fib_2: float = 2.000,
    sl_fib: float = 2.400,
    tp_fib_1: float = 0.500,
    basket_tp: float = 1.000,
    risk_per_order: float = 10.0,
    fee_maker: float = FEE_MAKER,
    fee_taker: float = FEE_TAKER,
    max_hold_bars: int = 120,
) -> list[TradeResult]:
    """
    Симуляция торговли при пробое экстремума (стратегия Манипуляции):
      - Вход 1: уровень 1.618 Fib
      - Добор 2: уровень 2.000 Fib
      - Стоп-Лосс: уровень 2.400 Fib (для обоих ордеров)
      - Тейк одиночного входа: tp_fib_1 (0.500 Fib)
      - Тейк корзины (при доборе): basket_tp (1.000 Fib)
    """
    highs = df["high"].values
    lows = df["low"].values
    df["close"].values
    n = len(df)

    trades: list[TradeResult] = []
    last_exit = -1

    for imp in impulses:
        if imp.end_idx <= last_exit:
            continue
        is_long = imp.is_long

        p_e1 = calc_fib(imp.high, imp.low, entry_fib_1, is_long=is_long, scale="log")
        p_e2 = calc_fib(imp.high, imp.low, entry_fib_2, is_long=is_long, scale="log")
        p_sl = calc_fib(imp.high, imp.low, sl_fib, is_long=is_long, scale="log")
        p_tp1 = calc_fib(imp.high, imp.low, tp_fib_1, is_long=is_long, scale="log")
        p_basket = calc_fib(imp.high, imp.low, basket_tp, is_long=is_long, scale="log")

        dist1 = abs(p_e1 - p_sl)
        qty1 = risk_per_order / dist1 if dist1 > 0 else 0.0
        dist2 = abs(p_e2 - p_sl)
        qty2 = risk_per_order / dist2 if dist2 > 0 else 0.0

        o1_filled = False
        o2_filled = False
        o1_pnl = o2_pnl = 0.0
        outcome = ""
        exit_k = -1

        for k in range(imp.end_idx + 1, min(imp.end_idx + max_hold_bars, n)):
            h_k = highs[k]
            l_k = lows[k]

            if not o1_filled:
                if is_long and h_k > imp.high * 1.05:
                    break
                if not is_long and l_k < imp.low * 0.95:
                    break
                if (is_long and l_k <= p_e1) or (not is_long and h_k >= p_e1):
                    o1_filled = True

            if o1_filled and not o2_filled:
                if (is_long and l_k <= p_e2) or (not is_long and h_k >= p_e2):
                    o2_filled = True

            if not o1_filled:
                continue

            sl_hit = (is_long and l_k <= p_sl) or (not is_long and h_k >= p_sl)

            if o2_filled:
                tp_hit = (is_long and h_k >= p_basket) or (not is_long and l_k <= p_basket)
                if sl_hit:
                    o1_pnl = o2_pnl = -risk_per_order
                    outcome = "Manip_SL_both"
                    exit_k = k
                    break
                elif tp_hit:
                    g1 = abs(p_basket - p_e1) - p_e1 * fee_maker - p_basket * fee_taker
                    g2 = abs(p_basket - p_e2) - p_e2 * fee_maker - p_basket * fee_taker
                    o1_pnl = qty1 * g1
                    o2_pnl = qty2 * g2
                    outcome = "Manip_Basket_TP"
                    exit_k = k
                    break
            else:
                tp_hit = (is_long and h_k >= p_tp1) or (not is_long and l_k <= p_tp1)
                if sl_hit:
                    o1_pnl = -risk_per_order
                    outcome = "Manip_SL1"
                    exit_k = k
                    break
                elif tp_hit:
                    g1 = abs(p_tp1 - p_e1) - p_e1 * fee_maker - p_tp1 * fee_taker
                    o1_pnl = qty1 * g1
                    outcome = "Manip_TP1"
                    exit_k = k
                    break

        if o1_filled and outcome:
            tot = o1_pnl + o2_pnl
            trades.append(TradeResult(
                pnl=tot,
                win=(tot > 0),
                o1_pnl=o1_pnl,
                o2_pnl=o2_pnl,
                both_entered=o2_filled,
                only_o1=(o1_filled and not o2_filled),
                outcome=outcome,
                exit_idx=exit_k,
            ))
            if exit_k >= 0:
                last_exit = exit_k

    return trades


def trades_to_df(trades: list[TradeResult], df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Преобразует список сделок в pandas.DataFrame с детальными столбцами:
    индексы и даты входа/выхода, сторона (long/short), цены, результат,
    PnL в $, PnL по каждому ордеру, кумулятивный PnL и просадка.
    """
    if not trades:
        return pd.DataFrame(columns=[
            "entry_idx", "exit_idx", "entry_time", "exit_time", "side",
            "outcome", "win", "entry_price", "exit_price",
            "pnl", "o1_pnl", "o2_pnl", "both_entered", "only_o1", "only_o2",
            "hold_bars", "cum_pnl", "drawdown"
        ])

    records = []
    has_timestamps = df is not None and hasattr(df, "columns") and "timestamp" in df.columns

    for t in trades:
        e_time = t.entry_time
        x_time = t.exit_time
        if has_timestamps:
            if e_time is None and 0 <= t.entry_idx < len(df):
                e_time = str(df["timestamp"].iloc[t.entry_idx])
            if x_time is None and 0 <= t.exit_idx < len(df):
                x_time = str(df["timestamp"].iloc[t.exit_idx])

        records.append({
            "entry_idx": t.entry_idx,
            "exit_idx": t.exit_idx,
            "entry_time": e_time,
            "exit_time": x_time,
            "side": t.side,
            "outcome": t.outcome,
            "win": bool(t.win),
            "entry_price": round(t.entry_price, 6),
            "exit_price": round(t.exit_price, 6),
            "pnl": round(t.pnl, 4),
            "o1_pnl": round(t.o1_pnl, 4),
            "o2_pnl": round(t.o2_pnl, 4),
            "both_entered": t.both_entered,
            "only_o1": t.only_o1,
            "only_o2": getattr(t, "only_o2", False),
            "hold_bars": t.hold_bars,
        })

    res_df = pd.DataFrame(records)
    # Расчет кумулятивного PnL и просадки средствами pandas и numpy
    res_df["cum_pnl"] = res_df["pnl"].cumsum()
    cum_peak = np.maximum.accumulate(np.maximum(0.0, res_df["cum_pnl"].values))
    res_df["drawdown"] = np.round(cum_peak - res_df["cum_pnl"].values, 4)

    return res_df


def summarize_df(trades: list[TradeResult] | pd.DataFrame) -> pd.DataFrame:
    """
    Формирует сводную таблицу метрик стратегии в виде pandas.DataFrame.
    """
    trades_df = trades if isinstance(trades, pd.DataFrame) else trades_to_df(trades)
    n = len(trades_df)
    if n == 0:
        return pd.DataFrame([{"Метрика": "Сделок", "Значение": 0}])

    wins = int(trades_df["win"].sum())
    losses = n - wins
    wr = (wins / n * 100.0) if n > 0 else 0.0
    tot_pnl = float(trades_df["pnl"].sum())
    avg_pnl = float(trades_df["pnl"].mean()) if n > 0 else 0.0
    max_dd = float(trades_df["drawdown"].max()) if "drawdown" in trades_df else 0.0
    profit_factor = (
        float(trades_df.loc[trades_df["pnl"] > 0, "pnl"].sum()) /
        abs(float(trades_df.loc[trades_df["pnl"] < 0, "pnl"].sum()))
        if (trades_df["pnl"] < 0).any() else float("inf")
    )

    metrics = [
        {"Метрика": "Всего сделок", "Значение": n},
        {"Метрика": "Прибыльных (Wins)", "Значение": wins},
        {"Метрика": "Убыточных (Losses)", "Значение": losses},
        {"Метрика": "Винрейт (Win Rate, %)", "Значение": f"{wr:.2f}%"},
        {"Метрика": "Итоговый PnL ($)", "Значение": f"{tot_pnl:+.2f}"},
        {"Метрика": "Средний PnL ($)", "Значение": f"{avg_pnl:+.2f}"},
        {"Метрика": "Макс. просадка ($)", "Значение": f"{max_dd:.2f}"},
        {"Метрика": "Profit Factor", "Значение": f"{profit_factor:.2f}"},
        {"Метрика": "Оба ордера вошли", "Значение": int(trades_df["both_entered"].sum())},
        {"Метрика": "Только Ордер 1 (0.500)", "Значение": int(trades_df["only_o1"].sum())},
        {"Метрика": "Только Ордер 2 (0.618)", "Значение": int(trades_df["only_o2"].sum())},
    ]
    return pd.DataFrame(metrics)


def summarize(trades: list[TradeResult] | pd.DataFrame) -> dict:
    """Быстрая сводка по списку сделок с поддержкой pandas и numpy."""
    if isinstance(trades, pd.DataFrame):
        trades_df = trades
    else:
        trades_df = trades_to_df(trades)

    n = len(trades_df)
    if n == 0:
        return {
            "n": 0, "wins": 0, "losses": 0, "sl_count": 0,
            "wr": 0.0, "pnl": 0.0, "both": 0, "only_o1": 0, "only_o2": 0,
            "max_drawdown": 0.0, "per_month": 0.0,
        }

    wins = int(trades_df["win"].sum())
    sl_count = int(trades_df["outcome"].str.contains("SL", na=False).sum())
    pnl = float(trades_df["pnl"].sum())
    both = int(trades_df["both_entered"].sum())
    only1 = int(trades_df["only_o1"].sum())
    only2 = int(trades_df["only_o2"].sum())
    max_dd = float(trades_df["drawdown"].max()) if "drawdown" in trades_df else 0.0
    wr = (wins / n * 100.0) if n > 0 else 0.0

    return {
        "n": n, "wins": wins, "losses": n - wins,
        "sl_count": sl_count,
        "wr": wr, "pnl": pnl,
        "both": both, "only_o1": only1, "only_o2": only2,
        "max_drawdown": max_dd,
        "per_month": pnl / 3.0,
    }

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_strategy_interactive import calc_fib
from scripts.constants import FEE_MAKER, FEE_TAKER, MAX_HOLD_BARS


@dataclass
class GridConfig:
    """Параметры двухордерной сетки."""
    # Уровни входов
    entry_fib_1: float = 0.500
    entry_fib_2: float = 0.618
    # Уровни стопа
    sl_fib: float = 1.000
    # Тейки при раздельном закрытии
    tp_fib_1: float = 0.236   # тейк Ордера 1 (используется если basket_tp is None)
    tp_fib_2: float = 0.382   # тейк Ордера 2 (используется если basket_tp is None)
    # Корзинный выход: если задан, оба ордера выходят на этом уровне
    basket_tp: Optional[float] = None
    # Риск и комиссии
    risk_per_order: float = 10.0
    fee_maker: float = FEE_MAKER
    fee_taker: float = FEE_TAKER
    # Максимальное ожидание в барах
    max_hold_bars: int = MAX_HOLD_BARS


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


def simulate_grid(df, impulses, config: GridConfig) -> list[TradeResult]:
    """
    Прогоняет симуляцию двухордерной сетки по всем импульсам.

    Режимы:
      - basket_tp is None → Ордер 1 → tp_fib_1, Ордер 2 → tp_fib_2
      - basket_tp задан   → Ордер 1 → basket_tp, Ордер 2 → basket_tp (при двойном входе)
                            Ордер 1 → tp_fib_1 (при одиночном входе)
    """
    highs  = df["high"].values
    lows   = df["low"].values
    n      = len(df)
    cfg    = config

    # Определяем тейк для Ордера 1 в корзинном режиме (одиночный вход)
    solo_tp1 = cfg.tp_fib_1

    trades: list[TradeResult] = []
    last_exit_idx = -1

    for imp in impulses:
        if imp.end_idx <= last_exit_idx:
            continue

        is_long = imp.is_long

        # --- Предрасчёт уровней ---
        p_e1, p_tp1, p_sl, qty1, gain1 = _calc_order_metrics(
            imp.high, imp.low, cfg.entry_fib_1, solo_tp1, cfg.sl_fib,
            is_long, cfg.risk_per_order, cfg.fee_maker, cfg.fee_taker
        )
        p_e2, p_tp2, _,    qty2, gain2 = _calc_order_metrics(
            imp.high, imp.low, cfg.entry_fib_2, cfg.tp_fib_2, cfg.sl_fib,
            is_long, cfg.risk_per_order, cfg.fee_maker, cfg.fee_taker
        )

        # Уровни тейка в корзинном режиме
        if cfg.basket_tp is not None:
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
        o2_active = True
        o1_pnl = o2_pnl = 0.0
        outcome = ""
        event_exit_idx = -1

        end_search = min(imp.end_idx + cfg.max_hold_bars, n)

        for k in range(imp.end_idx + 1, end_search):
            h_k = highs[k]
            l_k = lows[k]

            # Отмена сетки при обновлении экстремума (никто ещё не вошёл)
            if not o1_filled and not o2_filled:
                if is_long and h_k > imp.high:
                    break
                if not is_long and l_k < imp.low:
                    break

            # Заполнение Ордера 1
            if not o1_filled:
                hit = (l_k <= p_e1) if is_long else (h_k >= p_e1)
                if hit:
                    o1_filled = True

            # Заполнение Ордера 2
            if o2_active and not o2_filled:
                hit = (l_k <= p_e2) if is_long else (h_k >= p_e2)
                if hit:
                    o2_filled = True

            if not o1_filled and not o2_filled:
                continue

            sl_hit = (l_k <= p_sl) if is_long else (h_k >= p_sl)

            # === Сопровождение Ордера 1 ===
            if o1_filled and not o1_closed:
                if cfg.basket_tp is not None and o2_filled:
                    # Корзинный режим с обоими ордерами
                    tp_hit = (h_k >= p_basket) if is_long else (l_k <= p_basket)
                    if sl_hit:
                        o1_closed = True
                        o1_pnl = -cfg.risk_per_order
                    elif tp_hit:
                        o1_closed = True
                        o1_pnl = qty1 * gain1_basket
                else:
                    # Раздельный тейк Ордера 1 (одиночный вход ИЛИ раздельный режим)
                    tp_hit = (h_k >= p_tp1) if is_long else (l_k <= p_tp1)
                    if sl_hit:
                        o1_closed = True
                        o1_pnl = -cfg.risk_per_order
                    elif tp_hit:
                        o1_closed = True
                        o1_pnl = qty1 * gain1
                        # One-and-Done: Ордер 2 ещё не вошёл → отменяем его
                        if not o2_filled:
                            o2_active = False
                            outcome = "TP1_only"
                            event_exit_idx = k
                            break

            # === Сопровождение Ордера 2 ===
            if o2_filled and not o2_closed:
                if cfg.basket_tp is not None:
                    tp_hit = (h_k >= p_basket) if is_long else (l_k <= p_basket)
                    if sl_hit:
                        o2_closed = True
                        o2_pnl = -cfg.risk_per_order
                    elif tp_hit:
                        o2_closed = True
                        o2_pnl = qty2 * gain2_basket
                else:
                    tp_hit = (h_k >= p_tp2) if is_long else (l_k <= p_tp2)
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
                # Определяем исход для статистики
                if not outcome:
                    if o1_pnl < 0 or o2_pnl < 0:
                        outcome = "SL_both" if (o1_filled and o2_filled) else ("SL1" if o1_filled else "SL2")
                    elif cfg.basket_tp is not None and o1_filled and o2_filled:
                        outcome = "Basket"
                    else:
                        outcome = "TP1+TP2" if (o1_filled and o2_filled) else ("TP1" if o1_filled else "TP2")
                break

        if o1_filled or o2_filled:
            tot_pnl = o1_pnl + o2_pnl
            trades.append(TradeResult(
                pnl=tot_pnl,
                win=(tot_pnl > 0),
                o1_pnl=o1_pnl,
                o2_pnl=o2_pnl,
                both_entered=(o1_filled and o2_filled),
                only_o1=(o1_filled and not o2_filled),
                outcome=outcome or "timeout",
            ))
            if event_exit_idx >= 0:
                last_exit_idx = event_exit_idx

    return trades


def summarize(trades: list[TradeResult]) -> dict:
    """Быстрая сводка по списку сделок."""
    n = len(trades)
    wins   = sum(1 for t in trades if t.win)
    pnl    = sum(t.pnl for t in trades)
    both   = sum(1 for t in trades if t.both_entered)
    only1  = sum(1 for t in trades if t.only_o1)
    wr     = (wins / n * 100.0) if n > 0 else 0.0
    return {
        "n": n, "wins": wins, "losses": n - wins,
        "wr": wr, "pnl": pnl,
        "both": both, "only_o1": only1,
        "per_month": pnl / 3.0,
    }

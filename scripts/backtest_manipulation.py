"""Бэктест стратегии «Манипуляция на часе» — только LONG.

Правила:
  - Импульс: ≥2 свечи, каждая обновляет HIGH предыдущей, тени учитываются.
  - Коррекция не должна достигать 0.5 Fib от импульса, иначе импульс недействителен.
  - Fibonacci: 0 = HIGH импульса, 1 = начало импульса.

  Обычный сценарий (вход на коррекции):
    entry 0.618 → TP 0.500, SL пробой 1.0
    entry 0.500 → TP 0.382, SL пробой 1.0
    entry 0.382 → TP 0.236, SL пробой 1.0

  Сценарий манипуляции (пробой 1.0):
    entry 1.618 → TP 0.500, SL пробой 2.0

Запуск:
    python scripts/backtest_manipulation.py HYPEUSDT --days 180
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv

# ─── Fibonacci ────────────────────────────────────────────────────────────────

NORMAL_ENTRIES: list[tuple[float, float]] = [
    (0.618, 0.500),
    (0.500, 0.236),
]
MANIP_ENTRY_FIB = 1.618
MANIP_TP_FIB    = 0.618
MANIP_SL_FIB    = 2.0
NORMAL_SL_FIB   = 1.0


def fib_price(high: float, low: float, level: float) -> float:
    """0 = HIGH импульса, 1 = начало импульса (LOW)."""
    return high - level * (high - low)


# ─── Детектор импульса ────────────────────────────────────────────────────────

@dataclass
class Impulse:
    start_idx: int
    end_idx: int
    impulse_high: float
    impulse_low: float


def detect_impulses(df: pd.DataFrame, min_pct: float = 1.5) -> list[Impulse]:
    """Детектор восходящего импульса (100% синхронизирован с скринером и индикатором).

    Правила:
    - Начало: свеча i (Low = l_s, High = h_s).
    - Любая закрытая свеча после i обновляет h_s без предварительного отката до 0.5 -> импульс подтверждён.
    - Тянем пик за новыми HIGH до первого касания 0.5 Fib.
    """
    highs = df["high"].values
    lows  = df["low"].values
    n = len(df)
    impulses: list[Impulse] = []

    i = 0
    while i < n - 2:
        l_s = lows[i]
        h_s = highs[i]
        cur_h = h_s
        is_impulse = False
        broken = False
        end_idx = i

        j = i + 1
        while j < n:
            l_j = lows[j]
            h_j = highs[j]

            if not is_impulse:
                if l_j < l_s:
                    broken = True
                    break
                fib_05_first = fib_price(h_s, l_s, 0.5)
                if l_j <= fib_05_first:
                    broken = True
                    break
                if h_j > h_s:
                    is_impulse = True
                    cur_h = h_j
                    end_idx = j
            else:
                fib_05_cur = fib_price(cur_h, l_s, 0.5)
                if l_j <= fib_05_cur:
                    # Касание 0.5 -> импульс зафиксирован!
                    break
                else:
                    if h_j > cur_h:
                        cur_h = h_j
                        end_idx = j
            j += 1

        if is_impulse and not broken:
            pct = (cur_h - l_s) / l_s * 100.0
            if pct >= min_pct:
                impulses.append(Impulse(i, end_idx, cur_h, l_s))
                i = end_idx + 1
                continue

        i += 1

    return impulses


# ─── Сделка ───────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    # scenario: "single"=only 0.5 entry (TP 0.382),
    #           "dca"=0.5+0.618 entries (TP 0.5),
    #           "manipulation"=1.618 entry (TP 0.5)
    scenario:     Literal["single", "dca", "manipulation"]
    entry_fib:    float        # first entry fib level
    entry_price:  float        # avg entry price (weighted)
    tp_price:     float
    sl_price:     float
    exit_price:   float
    exit_reason:  Literal["tp", "sl", "timeout"]
    pnl_pct:      float        # % of total deployed capital
    hold_candles: int
    has_dca:      bool = False  # True if 0.618 was also filled


# _simulate_trade removed; logic is inline in run_backtest


# ─── Бэктест ─────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, timeout_candles: int = 168, manip_timeout: int | None = None) -> list[Trade]:
    """DCA-стратегия на коррекции после импульса.

    Обычный сценарий:
      - Вход 1 (1 лот) на уровне 0.500.
      - Если цена продолжает падать до 0.618 → Вход 2 (2 лота) на 0.618.
      - TP при DCA (оба входа): 0.500   → PnL считается по совокупной позиции.
      - TP без DCA (только 0.500):  0.382.
      - SL: пробой уровня 1.0 для всей позиции.

    Манипуляция (пробой 1.0):
      - Вход 1 лот на 1.618, TP 0.500, SL 2.0.
    """
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    n = len(df)

    impulses = detect_impulses(df)
    trades: list[Trade] = []
    m_timeout = manip_timeout if manip_timeout is not None else timeout_candles

    for imp in impulses:
        h = imp.impulse_high
        imp_low = imp.impulse_low

        p382 = fib_price(h, imp_low, 0.382)
        p500 = fib_price(h, imp_low, 0.500)
        p618 = fib_price(h, imp_low, 0.618)
        sl   = fib_price(h, imp_low, NORMAL_SL_FIB)   # = imp_low (уровень 1.0)

        # ── Обычный сценарий: вход на 0.500 ──────────────────────────────────
        # Ищем первое касание 0.500 после завершения импульса
        entry1_candle = None
        for k in range(imp.end_idx + 1, min(imp.end_idx + timeout_candles, n)):
            if lows[k] <= p500:
                if lows[k] <= sl:
                    break  # SL и вход в одной свече — пропускаем
                entry1_candle = k
                break

        if entry1_candle is not None:
            has_dca = False
            exit_price: float | None = None
            exit_reason: str = "timeout"
            hold = 0

            for k in range(entry1_candle + 1, min(entry1_candle + timeout_candles, n)):
                hold = k - entry1_candle

                # SL: пробой 1.0
                if lows[k] <= sl:
                    exit_price = sl
                    exit_reason = "sl"
                    break

                # DCA-вход на 0.618 (если ещё не было)
                if not has_dca and lows[k] <= p618:
                    has_dca = True
                    # В этой же свече может быть и TP (если HIGH >= p500)
                    if highs[k] >= p500:
                        exit_price = p500
                        exit_reason = "tp"
                        break
                    continue

                # TP: зависит от наличия DCA
                if has_dca:
                    if highs[k] >= p500:
                        exit_price = p500
                        exit_reason = "tp"
                        break
                else:
                    if highs[k] >= p382:
                        exit_price = p382
                        exit_reason = "tp"
                        break
            else:
                exit_price = closes[min(entry1_candle + timeout_candles, n - 1)]
                exit_reason = "timeout"
                hold = min(timeout_candles, n - 1 - entry1_candle)

            # PnL: взвешенный по позиции
            # DCA: 1 лот @ p500 + 2 лота @ p618, итого 3 лота
            # Single: 1 лот @ p500
            if has_dca:
                total_pnl_abs = 1.0 * (exit_price - p500) + 2.0 * (exit_price - p618)
                total_cost    = 1.0 * p500 + 2.0 * p618
                avg_entry     = total_cost / 3.0
            else:
                total_pnl_abs = exit_price - p500
                total_cost    = p500
                avg_entry     = p500

            pnl_pct = total_pnl_abs / total_cost * 100.0
            tp_price = p500 if has_dca else p382

            trades.append(Trade(
                scenario="dca" if has_dca else "single",
                entry_fib=0.500,
                entry_price=avg_entry,
                tp_price=tp_price,
                sl_price=sl,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl_pct=pnl_pct,
                hold_candles=hold,
                has_dca=has_dca,
            ))

        # ── Манипуляция: пробой 1.0 → DCA 1лот@1.618 + 2лота@2.0 ──────────
        breach_candle = None
        for k in range(imp.end_idx + 1, min(imp.end_idx + m_timeout, n)):
            if lows[k] <= sl:
                breach_candle = k
                break

        if breach_candle is None:
            continue

        m_ep1 = fib_price(h, imp_low, MANIP_ENTRY_FIB)   # 1.618 — первый вход
        m_ep2 = fib_price(h, imp_low, MANIP_SL_FIB)       # 2.0   — второй вход (DCA)
        m_tp  = fib_price(h, imp_low, MANIP_TP_FIB)       # 0.500 — цель для обоих

        # Ищем первое касание 1.618
        entry1_candle = None
        for k in range(breach_candle, min(breach_candle + m_timeout, n)):
            if lows[k] <= m_ep1:
                if lows[k] <= m_ep2:
                    break  # сразу пробило 2.0 — пропускаем
                entry1_candle = k
                break

        if entry1_candle is None:
            continue

        has_dca_m = False
        exit_price = closes[min(entry1_candle + timeout_candles, n - 1)]
        exit_reason = "timeout"
        hold = 0

        for k in range(entry1_candle + 1, min(entry1_candle + m_timeout + 1, n)):
            hold = k - entry1_candle

            # SL только для одиночного входа: пробой 2.0
            if not has_dca_m and lows[k] <= m_ep2:
                # Это DCA-вход, а не SL
                has_dca_m = True
                if highs[k] >= m_tp:   # в той же свече уже TP?
                    exit_price = m_tp
                    exit_reason = "tp"
                    break
                continue

            if highs[k] >= m_tp:
                exit_price = m_tp
                exit_reason = "tp"
                break

            # SL только если DCA не было и цена пошла ниже 2.0
            # (после has_dca=True — ждём TP или таймаут)
        else:
            hold = min(m_timeout, n - 1 - entry1_candle)

        # PnL
        if has_dca_m:
            total_pnl_abs = 1.0 * (exit_price - m_ep1) + 2.0 * (exit_price - m_ep2)
            total_cost    = 1.0 * m_ep1 + 2.0 * m_ep2
            avg_entry     = total_cost / 3.0
        else:
            total_pnl_abs = exit_price - m_ep1
            total_cost    = m_ep1
            avg_entry     = m_ep1

        pnl_pct = total_pnl_abs / total_cost * 100.0

        trades.append(Trade(
            scenario="manipulation",
            entry_fib=MANIP_ENTRY_FIB,
            entry_price=avg_entry,
            tp_price=m_tp,
            sl_price=m_ep2,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl_pct=pnl_pct,
            hold_candles=hold,
            has_dca=has_dca_m,
        ))

    return trades


# ─── Отчёт ────────────────────────────────────────────────────────────────────

def print_report(trades: list[Trade], symbol: str, days: int) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        use_rich = True
    except ImportError:
        console = None
        use_rich = False

    def _summary(subset: list[Trade], label: str) -> None:
        if not subset:
            print(f"\n{label}: нет сделок")
            return
        pnls   = np.array([t.pnl_pct for t in subset])
        wins   = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        wr     = len(wins) / len(pnls) * 100
        total  = float(pnls.sum())
        avg    = float(pnls.mean())
        cumsum = np.cumsum(pnls)
        dd     = float(np.max(np.maximum.accumulate(cumsum) - cumsum))
        pf     = (float(wins.sum()) / abs(float(losses.sum()))) if len(losses) else float("inf")
        tp_c   = sum(1 for t in subset if t.exit_reason == "tp")
        sl_c   = sum(1 for t in subset if t.exit_reason == "sl")
        to_c   = sum(1 for t in subset if t.exit_reason == "timeout")
        avg_h  = float(np.mean([t.hold_candles for t in subset]))

        rows = [
            ("Сделок",            str(len(subset))),
            ("Win Rate",          f"{wr:.1f}%"),
            ("Total PnL",         f"{total:+.2f}%"),
            ("Avg PnL",           f"{avg:+.3f}%"),
            ("Max Drawdown",      f"{dd:.2f}%"),
            ("Profit Factor",     f"{pf:.2f}"),
            ("TP / SL / Timeout", f"{tp_c} / {sl_c} / {to_c}"),
            ("Avg Hold (ч)",      f"{avg_h:.1f}"),
        ]

        if use_rich:
            tbl = Table(title=label, header_style="bold cyan")
            tbl.add_column("Метрика", style="dim")
            tbl.add_column("Значение", justify="right")
            for r in rows:
                tbl.add_row(*r)
            console.print(tbl)
        else:
            print(f"\n=== {label} ===")
            for k, v in rows:
                print(f"  {k}: {v}")

    header = f"МАНИПУЛЯЦИЯ НА ЧАСЕ  |  {symbol}  |  1H  |  {days} дней"
    if use_rich:
        console.rule(f"[bold]{header}[/bold]")
    else:
        print(f"\n{'='*60}\n{header}\n{'='*60}")

    single = [t for t in trades if t.scenario == "single"]
    dca    = [t for t in trades if t.scenario == "dca"]
    manip  = [t for t in trades if t.scenario == "manipulation"]

    _summary(trades, "ВСЕ СДЕЛКИ")
    _summary(single, "ТОЛЬКО 0.500 (TP → 0.382)")
    _summary(dca,    "DCA: 0.500 + 0.618×2 (TP → 0.500)")
    m_single = [t for t in manip if not t.has_dca]
    m_dca    = [t for t in manip if t.has_dca]
    _summary(manip,    "МАНИПУЛЯЦИЯ (всего)")
    _summary(m_single, "  Только 1.618 (TP → 0.500, SL 2.0)")
    _summary(m_dca,    "  DCA: 1.618 + 2.0×2 (TP → 0.500)")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Бэктест «Манипуляция на часе» LONG")
    parser.add_argument("symbol", nargs="?", default="HYPEUSDT")
    parser.add_argument("--days",    type=int, default=180)
    parser.add_argument("--timeout", type=int, default=168,
                        help="Макс. свечей удержания позиции (обычный)")
    parser.add_argument("--manip-timeout", type=int, default=336,
                        help="Макс. свечей удержания для манипуляции (по умолч. 336=14д)")
    parser.add_argument("--cache-dir", default="data/cache")
    args = parser.parse_args(argv)

    print(f"[INFO] Загружаю {args.symbol} 1H, {args.days} дней...", file=sys.stderr)
    df = fetch_ohlcv(
        args.symbol,
        timeframe="1h",
        days=args.days,
        cache_dir=args.cache_dir,
        use_cache=True,
    )
    print(f"[INFO] Получено {len(df)} свечей", file=sys.stderr)

    trades = run_backtest(df, timeout_candles=args.timeout,
                          manip_timeout=args.manip_timeout)
    print(f"[INFO] Найдено сделок: {len(trades)}", file=sys.stderr)

    print_report(trades, args.symbol, args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())

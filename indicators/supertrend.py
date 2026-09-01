"""Трендовый индикатор SuperTrend на базе ATR (точный аналог TradingView / Pine Script)."""
from __future__ import annotations

import pandas as pd
import numpy as np
from indicators.base import BaseIndicator


def calculate_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """Расчет классического SuperTrend (TradingView Pine Script)."""
    n = len(close)
    if n == 0:
        return pd.DataFrame({"supertrend": [], "direction": []})

    # True Range
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # ATR (Wilder's RMA)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    hl2 = (high + low) / 2.0
    basic_up = (hl2 - multiplier * atr).values
    basic_dn = (hl2 + multiplier * atr).values
    c = close.values

    up = np.zeros(n)
    dn = np.zeros(n)
    trend = np.ones(n)

    up[0] = basic_up[0]
    dn[0] = basic_dn[0]
    trend[0] = 1

    for i in range(1, n):
        # UP Band
        if c[i - 1] > up[i - 1]:
            up[i] = max(basic_up[i], up[i - 1])
        else:
            up[i] = basic_up[i]

        # DOWN Band
        if c[i - 1] < dn[i - 1]:
            dn[i] = min(basic_dn[i], dn[i - 1])
        else:
            dn[i] = basic_dn[i]

        # Trend direction (1 = Bullish / Green, -1 = Bearish / Red)
        if trend[i - 1] == -1 and c[i] > dn[i - 1]:
            trend[i] = 1
        elif trend[i - 1] == 1 and c[i] < up[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]

    st = np.where(trend == 1, up, dn)

    return pd.DataFrame({
        "supertrend": st,
        "direction": trend,
    }, index=close.index)


class SuperTrendIndicator(BaseIndicator):
    name = "supertrend"

    def __init__(self, period: int = 10, multiplier: float = 3.0):
        self.period = period
        self.multiplier = multiplier
        self.df_st: pd.DataFrame | None = None

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        self.df_st = calculate_supertrend(
            df["high"], df["low"], df["close"],
            period=self.period, multiplier=self.multiplier
        )
        return self.df_st

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.df_st is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.df_st):
            return False

        dir_val = int(self.df_st["direction"].iloc[candle_idx])
        cond = (condition or "trend").strip().lower()

        # Режим "trend" / "bullish":
        # Для Long: направление 1 (бычий зеленый тренд)
        # Для Short: направление -1 (медвежий красный тренд)
        if cond in ("trend", "bullish", "with_trend", "зеленый", "по_тренду"):
            return dir_val == 1 if side == "long" else dir_val == -1

        if cond in ("counter", "counter_trend", "против_тренда"):
            return dir_val == -1 if side == "long" else dir_val == 1

        return dir_val == 1 if side == "long" else dir_val == -1

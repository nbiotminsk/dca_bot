"""Трендовый индикатор EMA (Exponential Moving Average) для торговли по тренду."""
from __future__ import annotations

import pandas as pd
from indicators.base import BaseIndicator


def calculate_ema(close: pd.Series, period: int = 200) -> pd.Series:
    """Расчет EMA с заданным периодом."""
    return close.ewm(span=period, adjust=False).mean()


class EMAIndicator(BaseIndicator):
    name = "ema"

    def __init__(self, period: int = 200):
        self.period = period
        self.series: pd.Series | None = None

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        self.series = calculate_ema(df["close"], period=self.period)
        return self.series

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.series is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.series):
            return False

        price = float(df["close"].iloc[candle_idx])
        ema_val = float(self.series.iloc[candle_idx])

        cond = (condition or "trend").strip().lower()

        # По умолчанию "trend":
        # Long: цена ВЫШЕ EMA (восходящий макро-тренд)
        # Short: цена НИЖЕ EMA (нисходящий макро-тренд)
        if cond in ("trend", "with_trend", "по_тренду", "above", "below"):
            if side == "long":
                return price >= ema_val
            else:
                return price <= ema_val

        # Контртренд (counter_trend)
        if cond in ("counter", "counter_trend", "против_тренда"):
            if side == "long":
                return price <= ema_val
            else:
                return price >= ema_val

        return price >= ema_val if side == "long" else price <= ema_val

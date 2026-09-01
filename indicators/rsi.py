"""Индикатор RSI (Relative Strength Index) с фильтрацией перепроданности/перекупленности."""
from __future__ import annotations

import pandas as pd
import numpy as np
from indicators.base import BaseIndicator


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Расчет RSI с использованием сглаживания Wilder's RMA (как в index.php)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder's RMA экспоненциальное сглаживание: alpha = 1 / period
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # Заполнение крайних значений
    rsi = rsi.fillna(50.0)
    return rsi


class RSIIndicator(BaseIndicator):
    name = "rsi"

    def __init__(self, period: int = 14):
        self.period = period
        self.series: pd.Series | None = None

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        self.series = calculate_rsi(df["close"], period=self.period)
        return self.series

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.series is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.series):
            return False

        val = float(self.series.iloc[candle_idx])

        # Если условие не задано, по умолчанию:
        # Long: перепроданность (< 40)
        # Short: перекупленность (> 60)
        if not condition:
            return val <= 40.0 if side == "long" else val >= 60.0

        cond = condition.strip().lower()

        # Разбор условий вида "< 35", "<= 30", "> 50", "[30, 50]"
        if cond.startswith("<="):
            limit = float(cond[2:].strip())
            return val <= limit if side == "long" else val >= (100.0 - limit)
        elif cond.startswith("<"):
            limit = float(cond[1:].strip())
            return val < limit if side == "long" else val > (100.0 - limit)
        elif cond.startswith(">="):
            limit = float(cond[2:].strip())
            return val >= limit if side == "long" else val <= (100.0 - limit)
        elif cond.startswith(">"):
            limit = float(cond[1:].strip())
            return val > limit if side == "long" else val < (100.0 - limit)
        elif ":" in cond or "," in cond:
            # Диапазон вида "30:50" или "[30, 50]"
            clean = cond.strip("[]() ")
            parts = clean.replace(":", ",").split(",")
            low_bound = float(parts[0].strip())
            high_bound = float(parts[1].strip())
            return low_bound <= val <= high_bound

        try:
            limit = float(cond)
            return val <= limit if side == "long" else val >= (100.0 - limit)
        except ValueError:
            return True

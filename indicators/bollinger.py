"""Индикатор Полосы Боллинджера (Bollinger Bands) для поиска экстремумов и отскоков."""
from __future__ import annotations

import pandas as pd
import numpy as np
from indicators.base import BaseIndicator


def calculate_bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """Расчет средней линии, верхней и нижней полосы Боллинджера, а также %B."""
    basis = close.rolling(period).mean()
    dev = close.rolling(period).std(ddof=0)
    upper = basis + std_dev * dev
    lower = basis - std_dev * dev

    width = upper - lower
    percent_b = ((close - lower) / width.replace(0.0, np.nan)).fillna(0.5)

    return pd.DataFrame({
        "basis": basis,
        "upper": upper,
        "lower": lower,
        "percent_b": percent_b,
    })


class BollingerBandsIndicator(BaseIndicator):
    name = "bollinger"

    def __init__(self, period: int = 20, std_dev: float = 2.0):
        self.period = period
        self.std_dev = std_dev
        self.df_bb: pd.DataFrame | None = None

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        self.df_bb = calculate_bollinger_bands(df["close"], period=self.period, std_dev=self.std_dev)
        return self.df_bb

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.df_bb is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.df_bb):
            return False

        low_val = float(df["low"].iloc[candle_idx])
        high_val = float(df["high"].iloc[candle_idx])
        close_val = float(df["close"].iloc[candle_idx])
        b_lower = float(self.df_bb["lower"].iloc[candle_idx])
        b_upper = float(self.df_bb["upper"].iloc[candle_idx])
        percent_b = float(self.df_bb["percent_b"].iloc[candle_idx])

        cond = (condition or "touch_lower").strip().lower()

        # Режим 1: Касание или выход за нижнюю полосу для Long / верхнюю для Short
        if cond in ("touch", "touch_lower", "lower", "касание", "пробой"):
            if side == "long":
                return low_val <= b_lower or close_val <= b_lower
            else:
                return high_val >= b_upper or close_val >= b_upper

        # Режим 2: Порог по %B (например "< 0.2" или "< 0.1")
        if cond.startswith("<="):
            limit = float(cond[2:].strip())
            return percent_b <= limit if side == "long" else percent_b >= (1.0 - limit)
        elif cond.startswith("<"):
            limit = float(cond[1:].strip())
            return percent_b < limit if side == "long" else percent_b > (1.0 - limit)
        elif cond.startswith(">="):
            limit = float(cond[2:].strip())
            return percent_b >= limit if side == "long" else percent_b <= (1.0 - limit)
        elif cond.startswith(">"):
            limit = float(cond[1:].strip())
            return percent_b > limit if side == "long" else percent_b < (1.0 - limit)

        # Режим 3: Внутри полос (inside)
        if cond in ("inside", "внутри"):
            return b_lower <= close_val <= b_upper

        return low_val <= b_lower if side == "long" else high_val >= b_upper

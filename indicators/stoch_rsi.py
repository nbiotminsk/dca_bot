"""Индикатор Stochastic RSI (%K и %D) с фильтрацией экстремальных зон."""
from __future__ import annotations

import pandas as pd
import numpy as np
from indicators.base import BaseIndicator
from indicators.rsi import calculate_rsi


def calculate_stoch_rsi(
    close: pd.Series,
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_period: int = 3,
    d_period: int = 3,
) -> pd.DataFrame:
    """Расчет Stochastic RSI (%K и %D)."""
    rsi = calculate_rsi(close, period=rsi_period)
    min_rsi = rsi.rolling(stoch_period).min()
    max_rsi = rsi.rolling(stoch_period).max()

    diff = max_rsi - min_rsi
    stoch = ((rsi - min_rsi) / diff.replace(0.0, np.nan)) * 100.0
    stoch = stoch.fillna(50.0)

    k_line = stoch.rolling(k_period).mean().fillna(50.0)
    d_line = k_line.rolling(d_period).mean().fillna(50.0)

    return pd.DataFrame({"k": k_line, "d": d_line, "stoch": stoch})


class StochRSIIndicator(BaseIndicator):
    name = "stoch_rsi"

    def __init__(
        self,
        rsi_period: int = 14,
        stoch_period: int = 14,
        k_period: int = 3,
        d_period: int = 3,
    ):
        self.rsi_period = rsi_period
        self.stoch_period = stoch_period
        self.k_period = k_period
        self.d_period = d_period
        self.df_stoch: pd.DataFrame | None = None

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        self.df_stoch = calculate_stoch_rsi(
            df["close"],
            rsi_period=self.rsi_period,
            stoch_period=self.stoch_period,
            k_period=self.k_period,
            d_period=self.d_period,
        )
        return self.df_stoch

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.df_stoch is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.df_stoch):
            return False

        k_val = float(self.df_stoch["k"].iloc[candle_idx])
        d_val = float(self.df_stoch["d"].iloc[candle_idx])

        cond = (condition or "< 20").strip().lower()

        if cond in ("oversold", "перепроданность"):
            return k_val <= 20.0 if side == "long" else k_val >= 80.0

        if cond in ("overbought", "перекупленность"):
            return k_val >= 80.0 if side == "long" else k_val <= 20.0

        if cond in ("cross", "crossover", "cross_up"):
            if candle_idx == 0:
                return False
            prev_k = float(self.df_stoch["k"].iloc[candle_idx - 1])
            prev_d = float(self.df_stoch["d"].iloc[candle_idx - 1])
            if side == "long":
                return prev_k <= prev_d and k_val > d_val
            else:
                return prev_k >= prev_d and k_val < d_val

        if cond.startswith("<="):
            limit = float(cond[2:].strip())
            return k_val <= limit if side == "long" else k_val >= (100.0 - limit)
        elif cond.startswith("<"):
            limit = float(cond[1:].strip())
            return k_val < limit if side == "long" else k_val > (100.0 - limit)
        elif cond.startswith(">="):
            limit = float(cond[2:].strip())
            return k_val >= limit if side == "long" else k_val <= (100.0 - limit)
        elif cond.startswith(">"):
            limit = float(cond[1:].strip())
            return k_val > limit if side == "long" else k_val < (100.0 - limit)

        try:
            limit = float(cond)
            return k_val <= limit if side == "long" else k_val >= (100.0 - limit)
        except ValueError:
            return True

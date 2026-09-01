"""Индикатор MACD (Moving Average Convergence Divergence) с фильтрацией импульса."""
from __future__ import annotations

import pandas as pd
from indicators.base import BaseIndicator


def calculate_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Расчет линии MACD, сигнальной линии и гистограммы."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "hist": hist,
    })


class MACDIndicator(BaseIndicator):
    name = "macd"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.df_macd: pd.DataFrame | None = None

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        self.df_macd = calculate_macd(df["close"], fast=self.fast, slow=self.slow, signal=self.signal)
        return self.df_macd

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.df_macd is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.df_macd):
            return False

        hist = float(self.df_macd["hist"].iloc[candle_idx])
        macd = float(self.df_macd["macd"].iloc[candle_idx])
        sig = float(self.df_macd["signal"].iloc[candle_idx])

        cond = (condition or "bullish").strip().lower()

        # По умолчанию "bullish": гистограмма > 0 или MACD > Signal для Long
        if cond in ("bullish", "positive", "бычий", "> 0", ">0"):
            if side == "long":
                return hist > 0.0 or macd > sig
            else:
                return hist < 0.0 or macd < sig

        if cond in ("bearish", "negative", "медвежий", "< 0", "<0"):
            if side == "long":
                return hist < 0.0
            else:
                return hist > 0.0

        if cond in ("cross", "crossover", "пересечение"):
            if candle_idx == 0:
                return False
            prev_macd = float(self.df_macd["macd"].iloc[candle_idx - 1])
            prev_sig = float(self.df_macd["signal"].iloc[candle_idx - 1])
            if side == "long":
                return prev_macd <= prev_sig and macd > sig
            else:
                return prev_macd >= prev_sig and macd < sig

        return hist > 0.0 if side == "long" else hist < 0.0

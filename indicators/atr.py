"""Индикатор ATR (Average True Range) для оценки волатильности рынка."""
from __future__ import annotations

import pandas as pd
from indicators.base import BaseIndicator


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.DataFrame:
    """Расчет истинного диапазона (TR), ATR и относительного ATR в % от цены."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's RMA
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    atr_pct = (atr / close) * 100.0
    atr_sma = atr.rolling(period).mean()

    return pd.DataFrame({
        "atr": atr,
        "atr_pct": atr_pct,
        "atr_sma": atr_sma,
    })


class ATRIndicator(BaseIndicator):
    name = "atr"

    def __init__(self, period: int = 14):
        self.period = period
        self.df_atr: pd.DataFrame | None = None

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        self.df_atr = calculate_atr(df["high"], df["low"], df["close"], period=self.period)
        return self.df_atr

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.df_atr is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.df_atr):
            return False

        atr_val = float(self.df_atr["atr"].iloc[candle_idx])
        atr_pct = float(self.df_atr["atr_pct"].iloc[candle_idx])
        atr_sma = float(self.df_atr["atr_sma"].iloc[candle_idx])

        cond = (condition or "> 0.5%").strip().lower()

        # Режим 1: Волатильность выше средней (> sma)
        if cond in ("> sma", ">sma", "rising", "растущая"):
            return atr_val >= atr_sma

        # Режим 2: Порог по % от цены (напр. "> 1.0%" или "> 0.5%")
        if "%" in cond:
            val_str = cond.replace("%", "").strip()
            if val_str.startswith(">="):
                return atr_pct >= float(val_str[2:].strip())
            elif val_str.startswith(">"):
                return atr_pct > float(val_str[1:].strip())
            elif val_str.startswith("<="):
                return atr_pct <= float(val_str[2:].strip())
            elif val_str.startswith("<"):
                return atr_pct < float(val_str[1:].strip())
            else:
                try:
                    return atr_pct >= float(val_str)
                except ValueError:
                    return True

        if cond.startswith(">="):
            return atr_pct >= float(cond[2:].strip())
        elif cond.startswith(">"):
            return atr_pct > float(cond[1:].strip())

        return atr_pct >= 0.5

"""Индикатор CCI (Commodity Channel Index) с поддержкой логики «Золотого входа» из index.php."""
from __future__ import annotations

import pandas as pd
import numpy as np
from indicators.base import BaseIndicator


def calculate_cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Расчет CCI по формуле Дональда Ламберта (строго как в index.php)."""
    hlc3 = (high + low + close) / 3.0
    sma = hlc3.rolling(period).mean()

    # Среднее абсолютное отклонение (Mean Absolute Deviation)
    mad = hlc3.rolling(period).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    )
    mad = mad.replace(0.0, np.nan)
    cci = (hlc3 - sma) / (0.015 * mad)
    return cci.fillna(0.0)


class CCIIndicator(BaseIndicator):
    name = "cci"

    def __init__(self, period: int = 14):
        self.period = period
        self.series: pd.Series | None = None

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        self.series = calculate_cci(df["high"], df["low"], df["close"], period=self.period)
        return self.series

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.series is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.series):
            return False

        val = float(self.series.iloc[candle_idx])

        # Режимы из index.php:
        # "golden": диапазон [-100, 0] для Long ("Золотой вход" винрейт ~90%), [0, 100] для Short
        # "oversold": < -100 для Long, > 100 для Short
        if not condition or condition.strip().lower() in ("golden", "gold", "золотой"):
            if side == "long":
                return -100.0 <= val <= 0.0
            else:
                return 0.0 <= val <= 100.0

        cond = condition.strip().lower()
        if cond in ("oversold", "перепроданность"):
            return val < -100.0 if side == "long" else val > 100.0

        if cond.startswith("<="):
            limit = float(cond[2:].strip())
            return val <= limit if side == "long" else val >= -limit
        elif cond.startswith("<"):
            limit = float(cond[1:].strip())
            return val < limit if side == "long" else val > -limit
        elif cond.startswith(">="):
            limit = float(cond[2:].strip())
            return val >= limit if side == "long" else val <= -limit
        elif cond.startswith(">"):
            limit = float(cond[1:].strip())
            return val > limit if side == "long" else val < -limit
        elif ":" in cond or "," in cond:
            clean = cond.strip("[]() ")
            parts = clean.replace(":", ",").split(",")
            low_bound = float(parts[0].strip())
            high_bound = float(parts[1].strip())
            return low_bound <= val <= high_bound

        try:
            limit = float(cond)
            return val <= limit if side == "long" else val >= -limit
        except ValueError:
            return True

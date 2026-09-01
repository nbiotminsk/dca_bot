"""Индикатор объема (Volume) для подтверждения входов торговой активностью."""
from __future__ import annotations

import pandas as pd
import numpy as np
from indicators.base import BaseIndicator


def calculate_volume(volume: pd.Series, period: int = 20) -> pd.DataFrame:
    """Расчет скользящей средней объема и коэффициента аномалии объема."""
    vol_sma = volume.rolling(period).mean()
    vol_ratio = (volume / vol_sma.replace(0.0, np.nan)).fillna(1.0)

    return pd.DataFrame({
        "volume": volume,
        "volume_sma": vol_sma,
        "volume_ratio": vol_ratio,
    })


class VolumeIndicator(BaseIndicator):
    name = "volume"

    def __init__(self, period: int = 20):
        self.period = period
        self.df_vol: pd.DataFrame | None = None

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        self.df_vol = calculate_volume(df["volume"], period=self.period)
        return self.df_vol

    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        if self.df_vol is None:
            self.calculate(df)

        if candle_idx < 0 or candle_idx >= len(self.df_vol):
            return False

        vol = float(self.df_vol["volume"].iloc[candle_idx])
        vol_sma = float(self.df_vol["volume_sma"].iloc[candle_idx])
        ratio = float(self.df_vol["volume_ratio"].iloc[candle_idx])

        cond = (condition or "> sma").strip().lower()

        # Режим 1: Объем выше среднего (> sma / > 1.0x)
        if cond in ("> sma", ">sma", "above_average", "выше_среднего"):
            return vol >= vol_sma

        # Режим 2: Всплеск объема (spike / > 1.5x / > 2.0x)
        if cond in ("spike", "всплеск"):
            return ratio >= 1.5

        if "x" in cond:
            val_str = cond.replace("x", "").strip()
            if val_str.startswith(">="):
                return ratio >= float(val_str[2:].strip())
            elif val_str.startswith(">"):
                return ratio > float(val_str[1:].strip())
            elif val_str.startswith("<="):
                return ratio <= float(val_str[2:].strip())
            elif val_str.startswith("<"):
                return ratio < float(val_str[1:].strip())
            else:
                try:
                    return ratio >= float(val_str)
                except ValueError:
                    return True

        if cond.startswith(">="):
            return ratio >= float(cond[2:].strip())
        elif cond.startswith(">"):
            return ratio > float(cond[1:].strip())

        return vol >= vol_sma

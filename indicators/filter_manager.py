"""Менеджер индикаторов-фильтров для стратегии."""
from __future__ import annotations

import pandas as pd
from indicators.base import BaseIndicator
from indicators.rsi import RSIIndicator
from indicators.cci import CCIIndicator
from indicators.macd import MACDIndicator
from indicators.stoch_rsi import StochRSIIndicator
from indicators.ema import EMAIndicator
from indicators.bollinger import BollingerBandsIndicator
from indicators.supertrend import SuperTrendIndicator
from indicators.atr import ATRIndicator
from indicators.volume import VolumeIndicator


class FilterManager:
    """Управляет набором индикаторов-фильтров для точек входа."""

    def __init__(self):
        self.filters: list[tuple[BaseIndicator, str | None, str]] = []

    def add_rsi(self, period: int = 14, condition: str = "< 40") -> FilterManager:
        self.filters.append((RSIIndicator(period=period), condition, f"RSI({period}) {condition}"))
        return self

    def add_cci(self, period: int = 14, condition: str = "golden") -> FilterManager:
        label = "CCI(14) [-100, 0] (Золотой вход)" if condition in ("golden", "gold") else f"CCI({period}) {condition}"
        self.filters.append((CCIIndicator(period=period), condition, label))
        return self

    def add_macd(self, fast: int = 12, slow: int = 26, signal: int = 9, condition: str = "bullish") -> FilterManager:
        self.filters.append((MACDIndicator(fast=fast, slow=slow, signal=signal), condition, f"MACD({fast},{slow},{signal}) {condition}"))
        return self

    def add_stoch_rsi(self, rsi_period: int = 14, stoch_period: int = 14, k: int = 3, d: int = 3, condition: str = "< 20") -> FilterManager:
        self.filters.append((StochRSIIndicator(rsi_period=rsi_period, stoch_period=stoch_period, k_period=k, d_period=d), condition, f"StochRSI {condition}"))
        return self

    def add_ema(self, period: int = 200, condition: str = "trend") -> FilterManager:
        label = f"Цена > EMA({period})" if condition == "trend" else f"EMA({period}) {condition}"
        self.filters.append((EMAIndicator(period=period), condition, label))
        return self

    def add_bollinger(self, period: int = 20, std_dev: float = 2.0, condition: str = "touch_lower") -> FilterManager:
        label = f"BB({period},{std_dev}) {condition}"
        self.filters.append((BollingerBandsIndicator(period=period, std_dev=std_dev), condition, label))
        return self

    def add_supertrend(self, period: int = 10, multiplier: float = 3.0, condition: str = "trend") -> FilterManager:
        label = f"SuperTrend({period},{multiplier}) {condition}"
        self.filters.append((SuperTrendIndicator(period=period, multiplier=multiplier), condition, label))
        return self

    def add_atr(self, period: int = 14, condition: str = "> 0.5%") -> FilterManager:
        label = f"ATR({period}) {condition}"
        self.filters.append((ATRIndicator(period=period), condition, label))
        return self

    def add_volume(self, period: int = 20, condition: str = "> sma") -> FilterManager:
        label = f"Объем {condition}"
        self.filters.append((VolumeIndicator(period=period), condition, label))
        return self

    def add_custom_indicator(self, indicator: BaseIndicator, condition: str | None = None, label: str | None = None) -> FilterManager:
        desc = label or f"{indicator.name} {condition or ''}".strip()
        self.filters.append((indicator, condition, desc))
        return self

    def prepare(self, df: pd.DataFrame) -> None:
        """Предрасчет всех индикаторов по датафрейму."""
        for indicator, _, _ in self.filters:
            indicator.calculate(df)

    def is_entry_allowed(self, candle_idx: int, side: str, df: pd.DataFrame) -> bool:
        """Проверяет, разрешен ли вход по всем активным индикаторам."""
        if not self.filters:
            return True

        for indicator, condition, _ in self.filters:
            if not indicator.is_valid(candle_idx, side, df, condition=condition):
                return False
        return True

    def has_filters(self) -> bool:
        return len(self.filters) > 0

    def describe(self) -> str:
        if not self.filters:
            return "Нет (чистый Price Action + Fib)"
        return " + ".join(label for _, _, label in self.filters)

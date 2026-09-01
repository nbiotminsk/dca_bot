"""Базовый класс для модульных индикаторов-фильтров."""
from __future__ import annotations

from abc import ABC, abstractmethod
import pandas as pd


class BaseIndicator(ABC):
    """Абстрактный класс индикатора."""
    name: str = "base"

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.Series | pd.DataFrame:
        """Векторизованный предрасчет индикатора на всем DataFrame."""
        pass

    @abstractmethod
    def is_valid(self, candle_idx: int, side: str, df: pd.DataFrame, condition: str | None = None) -> bool:
        """Проверка условия фильтра на свече входа candle_idx."""
        pass

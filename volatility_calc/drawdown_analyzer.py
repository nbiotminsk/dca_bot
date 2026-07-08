"""Multi-horizon расчёт просадки long/short по реальным экстремумам."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DEFAULT_HORIZONS = [24, 72, 168]
DEFAULT_THRESHOLDS = [5.0, 10.0, 15.0]


def compute_hurst_exponent(series: pd.Series, max_lag: int = 100) -> float:
    """Вычислить Hurst exponent для определения режима рынка.
    
    H < 0.5 — mean-reverting (DCA работает хорошо)
    H ≈ 0.5 — random walk (нейтрально)
    H > 0.5 — trending (DCA работает хуже)
    
    Метод: R/S анализ (Rescaled Range).
    """
    values = series.to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    n = len(values)
    
    if n < max_lag * 2:
        max_lag = n // 4
    
    if max_lag < 10:
        return 0.5
    
    rs_values = []
    lag_values = []
    
    for lag in range(10, max_lag + 1, 5):
        n_chunks = n // lag
        if n_chunks < 1:
            continue
        
        rs_chunk = []
        for i in range(n_chunks):
            chunk = values[i * lag:(i + 1) * lag]
            mean_chunk = np.mean(chunk)
            cumdev = np.cumsum(chunk - mean_chunk)
            R = np.max(cumdev) - np.min(cumdev)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_chunk.append(R / S)
        
        if rs_chunk:
            rs_values.append(np.mean(rs_chunk))
            lag_values.append(lag)
    
    if len(rs_values) < 2:
        return 0.5
    
    log_rs = np.log(rs_values)
    log_lag = np.log(lag_values)
    
    slope, _ = np.polyfit(log_lag, log_rs, 1)
    
    return float(slope)


@dataclass(frozen=True)
class SideStats:
    mean: float
    median: float
    std: float
    p90: float
    p95: float
    p99: float
    max: float
    cvar_95: float = 0.0
    cvar_99: float = 0.0

    @classmethod
    def from_array(cls, arr: np.ndarray) -> "SideStats":
        arr = np.asarray(arr, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            nan = float("nan")
            return cls(nan, nan, nan, nan, nan, nan, nan, nan, nan)
        
        p95 = float(np.percentile(arr, 95))
        p99 = float(np.percentile(arr, 99))
        
        cvar_95 = float(np.mean(arr[arr >= p95])) if np.any(arr >= p95) else p95
        cvar_99 = float(np.mean(arr[arr >= p99])) if np.any(arr >= p99) else p99
        
        return cls(
            mean=float(np.mean(arr)),
            median=float(np.median(arr)),
            std=float(np.std(arr, ddof=0)),
            p90=float(np.percentile(arr, 90)),
            p95=p95,
            p99=p99,
            max=float(np.max(arr)),
            cvar_95=cvar_95,
            cvar_99=cvar_99,
        )


@dataclass
class HorizonStats:
    horizon_h: int
    long: SideStats
    short: SideStats
    long_above_thresholds: dict[float, float] = field(default_factory=dict)
    short_above_thresholds: dict[float, float] = field(default_factory=dict)


@dataclass
class MultiHorizonStats:
    symbol: str
    timeframe: str
    days: int
    n_candles: int
    horizons: list[HorizonStats]
    hurst_exponent: float = 0.5

    def get(self, horizon_h: int) -> HorizonStats:
        for h in self.horizons:
            if h.horizon_h == horizon_h:
                return h
        raise KeyError(f"Горизонт {horizon_h} не найден")


def _rolling_extreme_after(series: pd.Series, window: int, func: str = "min") -> np.ndarray:
    """Экстремум (min/max) среди будущих `window` точек после текущей.
    
    Для точки i возвращает min/max среди точек [i+1, i+window].
    Использует pandas.rolling для O(n) производительности.
    """
    values = series.to_numpy(dtype=float)
    n = len(values)
    
    if n == 0:
        return np.array([], dtype=float)
    
    s = pd.Series(values)
    shifted = s.shift(-1)
    
    if func == "min":
        rolled = shifted.rolling(window=window, min_periods=1).min()
    elif func == "max":
        rolled = shifted.rolling(window=window, min_periods=1).max()
    else:
        raise ValueError(f"func должен быть 'min' или 'max', получено {func!r}")
    
    out = rolled.to_numpy(dtype=float)
    out[-1] = np.nan
    return out


def _fraction_above(arr: np.ndarray, threshold: float) -> float:
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.mean(arr > threshold) * 100)


def analyze_extremes(
    df: pd.DataFrame,
    horizons_hours: list[int] | None = None,
    thresholds: list[float] | None = None,
    symbol: str = "",
    timeframe: str = "1h",
    days: int = 90,
) -> MultiHorizonStats:
    """Рассчитать просадки long/short по реальным экстремумам для горизонтов.

    Все метрики хранятся как положительные величины (глубина просадки в %):
      Long  DD = (close - min_low_after)  / close * 100
      Short DD = (max_high_after - close) / close * 100
    """
    if horizons_hours is None:
        horizons_hours = list(DEFAULT_HORIZONS)
    if thresholds is None:
        thresholds = list(DEFAULT_THRESHOLDS)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    horizons_stats: list[HorizonStats] = []
    for H in horizons_hours:
        long_low = _rolling_extreme_after(low, H, func="min")
        short_high = _rolling_extreme_after(high, H, func="max")
        close_arr = close.to_numpy()

        # Drawdown magnitudes (всегда >= 0): для long — глубина падения от close,
        # для short — высота подъёма от close.
        with np.errstate(divide="ignore", invalid="ignore"):
            long_dd = (close_arr - long_low) / close_arr * 100.0
            short_dd = (short_high - close_arr) / close_arr * 100.0

        long_dd = np.abs(long_dd.astype(float))
        short_dd = np.abs(short_dd.astype(float))

        long_stats = SideStats.from_array(long_dd)
        short_stats = SideStats.from_array(short_dd)

        long_above = {t: _fraction_above(long_dd, t) for t in thresholds}
        short_above = {t: _fraction_above(short_dd, t) for t in thresholds}

        horizons_stats.append(HorizonStats(
            horizon_h=H,
            long=long_stats,
            short=short_stats,
            long_above_thresholds=long_above,
            short_above_thresholds=short_above,
        ))

    return MultiHorizonStats(
        symbol=symbol,
        timeframe=timeframe,
        days=days,
        n_candles=len(df),
        horizons=horizons_stats,
        hurst_exponent=compute_hurst_exponent(df["close"]),
    )
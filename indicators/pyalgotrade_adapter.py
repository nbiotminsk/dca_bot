"""
Адаптер для расчета технических индикаторов с использованием библиотеки PyAlgoTrade.
Преобразует pandas.Series / DataFrame в DataSeries PyAlgoTrade и возвращает результат в виде pandas.Series.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pyalgotrade.dataseries import SequenceDataSeries
from pyalgotrade.technical import rsi as pat_rsi
from pyalgotrade.technical import ma as pat_ma
from pyalgotrade.technical import macd as pat_macd
from pyalgotrade.technical import bollinger as pat_bollinger


def calculate_pyalgotrade_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Расчет RSI через PyAlgoTrade."""
    s = SequenceDataSeries()
    r = pat_rsi.RSI(s, period)
    for val in series.values:
        s.append(float(val) if pd.notna(val) else 0.0)

    res = [r[i] if r[i] is not None else np.nan for i in range(len(r))]
    return pd.Series(res, index=series.index, name=f"RSI_{period}_pat")


def calculate_pyalgotrade_ema(series: pd.Series, period: int = 14) -> pd.Series:
    """Расчет EMA через PyAlgoTrade."""
    s = SequenceDataSeries()
    e = pat_ma.EMA(s, period)
    for val in series.values:
        s.append(float(val) if pd.notna(val) else 0.0)

    res = [e[i] if e[i] is not None else np.nan for i in range(len(e))]
    return pd.Series(res, index=series.index, name=f"EMA_{period}_pat")


def calculate_pyalgotrade_sma(series: pd.Series, period: int = 14) -> pd.Series:
    """Расчет SMA через PyAlgoTrade."""
    s = SequenceDataSeries()
    m = pat_ma.SMA(s, period)
    for val in series.values:
        s.append(float(val) if pd.notna(val) else 0.0)

    res = [m[i] if m[i] is not None else np.nan for i in range(len(m))]
    return pd.Series(res, index=series.index, name=f"SMA_{period}_pat")


def calculate_pyalgotrade_macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Расчет MACD через PyAlgoTrade.
    Возвращает (macd_line, signal_line, histogram).
    """
    s = SequenceDataSeries()
    mc = pat_macd.MACD(s, fast, slow, signal)
    sig = mc.getSignal()
    for val in series.values:
        s.append(float(val) if pd.notna(val) else 0.0)

    n = len(series)
    macd_vals = [mc[i] if mc[i] is not None else np.nan for i in range(n)]
    sig_vals = [sig[i] if sig[i] is not None else np.nan for i in range(n)]

    s_macd = pd.Series(macd_vals, index=series.index, name="macd_line_pat")
    s_sig = pd.Series(sig_vals, index=series.index, name="macd_signal_pat")
    s_hist = s_macd - s_sig
    s_hist.name = "macd_hist_pat"

    return s_macd, s_sig, s_hist


def calculate_pyalgotrade_bollinger(
    series: pd.Series, period: int = 20, num_std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Расчет Bollinger Bands через PyAlgoTrade.
    Возвращает (upper_band, middle_band, lower_band).
    """
    s = SequenceDataSeries()
    bb = pat_bollinger.BollingerBands(s, period, num_std_dev)
    for val in series.values:
        s.append(float(val) if pd.notna(val) else 0.0)

    n = len(series)
    upper_vals = [bb.getUpperBand()[i] if bb.getUpperBand()[i] is not None else np.nan for i in range(n)]
    mid_vals = [bb.getMiddleBand()[i] if bb.getMiddleBand()[i] is not None else np.nan for i in range(n)]
    lower_vals = [bb.getLowerBand()[i] if bb.getLowerBand()[i] is not None else np.nan for i in range(n)]

    return (
        pd.Series(upper_vals, index=series.index, name="bb_upper_pat"),
        pd.Series(mid_vals, index=series.index, name="bb_mid_pat"),
        pd.Series(lower_vals, index=series.index, name="bb_lower_pat"),
    )

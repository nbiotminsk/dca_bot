"""Тесты для модульных индикаторов: RSI, CCI, MACD, Stoch RSI, EMA."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indicators.rsi import calculate_rsi, RSIIndicator
from indicators.cci import calculate_cci, CCIIndicator
from indicators.macd import calculate_macd, MACDIndicator
from indicators.stoch_rsi import calculate_stoch_rsi, StochRSIIndicator
from indicators.ema import calculate_ema, EMAIndicator
from indicators.filter_manager import FilterManager


@pytest.fixture
def sample_ohlcv():
    np.random.seed(42)
    n = 100
    close = 100.0 + np.cumsum(np.random.randn(n) * 1.5)
    high = close + np.abs(np.random.randn(n) * 0.8)
    low = close - np.abs(np.random.randn(n) * 0.8)
    open_ = close + np.random.randn(n) * 0.3
    volume = np.random.rand(n) * 1000 + 100
    dates = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "timestamp": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def test_rsi_bounds_and_filter(sample_ohlcv):
    rsi = calculate_rsi(sample_ohlcv["close"], period=14)
    assert len(rsi) == len(sample_ohlcv)
    # Значения должны лежать в [0, 100]
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()

    # Проверка фильтра
    ind = RSIIndicator(period=14)
    ind.calculate(sample_ohlcv)
    assert ind.is_valid(15, "long", sample_ohlcv, condition="< 100") is True
    assert ind.is_valid(15, "long", sample_ohlcv, condition="< 0") is False


def test_cci_golden_zone(sample_ohlcv):
    cci = calculate_cci(sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"], period=14)
    assert len(cci) == len(sample_ohlcv)

    ind = CCIIndicator(period=14)
    ind.calculate(sample_ohlcv)

    # Проверяем "золотой вход" [-100, 0]
    for idx in range(20, 30):
        val = cci.iloc[idx]
        is_in_golden = (-100.0 <= val <= 0.0)
        assert ind.is_valid(idx, "long", sample_ohlcv, condition="golden") == is_in_golden


def test_macd_calculation(sample_ohlcv):
    df_macd = calculate_macd(sample_ohlcv["close"], fast=12, slow=26, signal=9)
    assert "macd" in df_macd.columns
    assert "signal" in df_macd.columns
    assert "hist" in df_macd.columns
    assert len(df_macd) == len(sample_ohlcv)

    ind = MACDIndicator()
    ind.calculate(sample_ohlcv)
    # hist > 0 или macd > sig
    for idx in range(30, 40):
        expected = df_macd["hist"].iloc[idx] > 0 or df_macd["macd"].iloc[idx] > df_macd["signal"].iloc[idx]
        assert ind.is_valid(idx, "long", sample_ohlcv, condition="bullish") == expected


def test_stoch_rsi(sample_ohlcv):
    df_stoch = calculate_stoch_rsi(sample_ohlcv["close"])
    assert "k" in df_stoch.columns
    assert "d" in df_stoch.columns
    valid_k = df_stoch["k"].dropna()
    assert (valid_k >= 0).all() and (valid_k <= 100).all()

    ind = StochRSIIndicator()
    ind.calculate(sample_ohlcv)
    assert ind.is_valid(20, "long", sample_ohlcv, condition="< 101") is True


def test_ema_trend_filter(sample_ohlcv):
    ema200 = calculate_ema(sample_ohlcv["close"], period=20)
    ind = EMAIndicator(period=20)
    ind.calculate(sample_ohlcv)

    for idx in range(25, 35):
        price = sample_ohlcv["close"].iloc[idx]
        ema_val = ema200.iloc[idx]
        assert ind.is_valid(idx, "long", sample_ohlcv, condition="trend") == (price >= ema_val)
        assert ind.is_valid(idx, "short", sample_ohlcv, condition="trend") == (price <= ema_val)


def test_filter_manager(sample_ohlcv):
    fm = FilterManager()
    fm.add_rsi(period=14, condition="< 70")
    fm.add_ema(period=20, condition="trend")
    fm.add_bollinger(period=20, std_dev=2.0, condition="touch_lower")
    fm.add_supertrend(period=10, multiplier=3.0, condition="trend")
    assert fm.has_filters() is True
    assert "RSI" in fm.describe()
    assert "EMA" in fm.describe()
    assert "BB" in fm.describe()
    assert "SuperTrend" in fm.describe()

    fm.prepare(sample_ohlcv)
    # Проверка разрешения входа
    res = fm.is_entry_allowed(30, "long", sample_ohlcv)
    assert isinstance(res, bool)


def test_bollinger_bands(sample_ohlcv):
    from indicators.bollinger import calculate_bollinger_bands, BollingerBandsIndicator
    df_bb = calculate_bollinger_bands(sample_ohlcv["close"], period=20, std_dev=2.0)
    assert "basis" in df_bb.columns
    assert "upper" in df_bb.columns
    assert "lower" in df_bb.columns
    assert "percent_b" in df_bb.columns

    # Upper должно быть >= Lower
    valid = df_bb.dropna()
    assert (valid["upper"] >= valid["lower"]).all()

    ind = BollingerBandsIndicator(period=20, std_dev=2.0)
    ind.calculate(sample_ohlcv)

    # Проверка режима "touch_lower"
    for idx in range(25, 35):
        low = sample_ohlcv["low"].iloc[idx]
        b_low = df_bb["lower"].iloc[idx]
        expected = low <= b_low
        assert ind.is_valid(idx, "long", sample_ohlcv, condition="touch_lower") == expected


def test_supertrend(sample_ohlcv):
    from indicators.supertrend import calculate_supertrend, SuperTrendIndicator
    df_st = calculate_supertrend(sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"], period=10, multiplier=3.0)
    assert "supertrend" in df_st.columns
    assert "direction" in df_st.columns
    assert len(df_st) == len(sample_ohlcv)

    # Направление должно быть 1 (бычий) или -1 (медвежий)
    dirs = df_st["direction"].unique()
    for d in dirs:
        assert d in (1.0, -1.0, 1, -1)

    ind = SuperTrendIndicator(period=10, multiplier=3.0)
    ind.calculate(sample_ohlcv)

    for idx in range(15, 25):
        dir_val = df_st["direction"].iloc[idx]
        assert ind.is_valid(idx, "long", sample_ohlcv, condition="trend") == (dir_val == 1)
        assert ind.is_valid(idx, "short", sample_ohlcv, condition="trend") == (dir_val == -1)


def test_atr(sample_ohlcv):
    from indicators.atr import calculate_atr, ATRIndicator
    df_atr = calculate_atr(sample_ohlcv["high"], sample_ohlcv["low"], sample_ohlcv["close"], period=14)
    assert "atr" in df_atr.columns
    assert "atr_pct" in df_atr.columns
    assert "atr_sma" in df_atr.columns
    assert (df_atr["atr"].dropna() > 0).all()

    ind = ATRIndicator(period=14)
    ind.calculate(sample_ohlcv)
    assert ind.is_valid(20, "long", sample_ohlcv, condition="> 0.01%") is True
    assert ind.is_valid(20, "long", sample_ohlcv, condition="> 100%") is False


def test_volume(sample_ohlcv):
    from indicators.volume import calculate_volume, VolumeIndicator
    df_vol = calculate_volume(sample_ohlcv["volume"], period=20)
    assert "volume" in df_vol.columns
    assert "volume_sma" in df_vol.columns
    assert "volume_ratio" in df_vol.columns

    ind = VolumeIndicator(period=20)
    ind.calculate(sample_ohlcv)
    # Проверка режима > sma
    for idx in range(25, 35):
        v = sample_ohlcv["volume"].iloc[idx]
        v_sma = df_vol["volume_sma"].iloc[idx]
        assert ind.is_valid(idx, "long", sample_ohlcv, condition="> sma") == (v >= v_sma)
        assert ind.is_valid(idx, "long", sample_ohlcv, condition="> 0.01x") is True
        assert ind.is_valid(idx, "long", sample_ohlcv, condition="> 100x") is False


class TestPyAlgoTradeIndicators:
    """Тесты индикаторов с использованием библиотеки PyAlgoTrade."""

    def test_pyalgotrade_ema_matches_our_ema(self, sample_ohlcv):
        from indicators.pyalgotrade_adapter import calculate_pyalgotrade_ema
        from indicators.ema import calculate_ema

        pat_ema = calculate_pyalgotrade_ema(sample_ohlcv["close"], period=14).values
        our_ema = calculate_ema(sample_ohlcv["close"], period=14).values

        # После периода разогрева (первые 25 баров) значения совпадают с высокой точностью (<= 0.05%)
        valid_mask = ~np.isnan(pat_ema) & ~np.isnan(our_ema)
        valid_indices = np.where(valid_mask)[0]
        warm_idx = valid_indices[valid_indices >= 25]

        assert len(warm_idx) > 0
        np.testing.assert_allclose(pat_ema[warm_idx], our_ema[warm_idx], rtol=5e-4)

    def test_pyalgotrade_rsi_convergence(self, sample_ohlcv):
        from indicators.pyalgotrade_adapter import calculate_pyalgotrade_rsi
        from indicators.rsi import calculate_rsi

        pat_rsi = calculate_pyalgotrade_rsi(sample_ohlcv["close"], period=14).values
        our_rsi = calculate_rsi(sample_ohlcv["close"], period=14).values

        # Значения должны лежать в [0, 100]
        valid_pat = pat_rsi[~np.isnan(pat_rsi)]
        assert (valid_pat >= 0).all() and (valid_pat <= 100).all()

        # Корреляция между расчетом PyAlgoTrade и нашим RSI после разогрева (>=30) > 0.99
        valid_mask = ~np.isnan(pat_rsi) & ~np.isnan(our_rsi)
        valid_indices = np.where(valid_mask)[0]
        warm_30 = valid_indices[valid_indices >= 30]
        assert len(warm_30) > 0
        corr = np.corrcoef(pat_rsi[warm_30], our_rsi[warm_30])[0, 1]
        assert corr > 0.99

        # Среднее абсолютное отклонение после разогрева не превышает 2.0 пунктов
        warm_idx = valid_indices[valid_indices >= 40]
        assert len(warm_idx) > 0
        mean_diff = np.mean(np.abs(pat_rsi[warm_idx] - our_rsi[warm_idx]))
        assert mean_diff < 2.0

    def test_pyalgotrade_bollinger_bands(self, sample_ohlcv):
        from indicators.pyalgotrade_adapter import calculate_pyalgotrade_bollinger
        from indicators.bollinger import calculate_bollinger_bands

        pat_up, pat_mid, pat_low = calculate_pyalgotrade_bollinger(sample_ohlcv["close"], period=20, num_std_dev=2.0)
        our_bb = calculate_bollinger_bands(sample_ohlcv["close"], period=20, std_dev=2.0)

        # Проверка средней линии (SMA 20): pat_mid совпадает с our_bb["basis"]
        valid_mask = ~np.isnan(pat_mid.values) & ~np.isnan(our_bb["basis"].values)
        np.testing.assert_allclose(pat_mid.values[valid_mask], our_bb["basis"].values[valid_mask], rtol=1e-5)

        # Верхняя полоса выше нижней
        valid_bands = ~np.isnan(pat_up.values) & ~np.isnan(pat_low.values)
        assert (pat_up.values[valid_bands] >= pat_low.values[valid_bands]).all()

    def test_pyalgotrade_macd(self, sample_ohlcv):
        from indicators.pyalgotrade_adapter import calculate_pyalgotrade_macd

        macd_line, macd_sig, macd_hist = calculate_pyalgotrade_macd(sample_ohlcv["close"], fast=12, slow=26, signal=9)
        assert len(macd_line) == len(sample_ohlcv)
        assert len(macd_sig) == len(sample_ohlcv)
        assert len(macd_hist) == len(sample_ohlcv)

        # Гистограмма равна разности линии и сигнала
        valid_mask = ~np.isnan(macd_hist.values)
        diff = macd_line.values[valid_mask] - macd_sig.values[valid_mask]
        np.testing.assert_allclose(macd_hist.values[valid_mask], diff, rtol=1e-5)


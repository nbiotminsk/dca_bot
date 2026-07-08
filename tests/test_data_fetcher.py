import time
from unittest.mock import MagicMock

import pandas as pd
import pytest

from volatility_calc.data_fetcher import (
    parse_symbol,
    validate_ohlcv,
    fetch_ohlcv,
    SymbolNotFoundError,
    _timeframe_to_ms,
    OHLCV_COLUMNS,
)


def make_df(n=10, start_ts=1_700_000_000_000):
    rows = []
    for i in range(n):
        ts = start_ts + i * 3_600_000
        rows.append([ts, 100 + i, 101 + i, 99 + i, 100.5 + i, 10.0 + i])
    df = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def test_parse_symbol_usdt():
    assert parse_symbol("ETHUSDT") == "ETH/USDT:USDT"
    assert parse_symbol("ethusdt") == "ETH/USDT:USDT"
    assert parse_symbol("HYPEUSDT") == "HYPE/USDT:USDT"


def test_parse_symbol_passthrough_ccxt_form():
    assert parse_symbol("ETH/USDT:USDT") == "ETH/USDT:USDT"


def test_parse_symbol_invalid():
    with pytest.raises(SymbolNotFoundError):
        parse_symbol("XYZ")


def test_validate_ok():
    validate_ohlcv(make_df())


def test_validate_missing_columns():
    with pytest.raises(ValueError):
        validate_ohlcv(pd.DataFrame({"open": [1]}))


def test_validate_high_low():
    df = make_df()
    df.loc[0, "high"] = df.loc[0, "low"] - 1
    with pytest.raises(ValueError):
        validate_ohlcv(df)


def test_validate_duplicate_ts():
    df = make_df()
    df.loc[1, "timestamp"] = df.loc[0, "timestamp"]
    with pytest.raises(ValueError):
        validate_ohlcv(df)


def test_timeframe_to_ms():
    assert _timeframe_to_ms("1h") == 3_600_000
    assert _timeframe_to_ms("15m") == 900_000
    assert _timeframe_to_ms("1d") == 86_400_000


def test_fetch_ohlcv_uses_cache(tmp_path, monkeypatch):
    df = make_df(20)
    # Save in whichever format data_fetcher expects for this environment.
    from volatility_calc.data_fetcher import _cache_path, _save_cache
    cache_file = _cache_path("ETH/USDT:USDT", "1h", 90, str(tmp_path))
    _save_cache(df, cache_file)
    out = fetch_ohlcv("ETHUSDT", timeframe="1h", days=90,
                      cache_dir=str(tmp_path), use_cache=True,
                      exchange_factory=lambda: pytest.fail("нет сети"))
    assert len(out) == 20
    assert list(out.columns) == OHLCV_COLUMNS


def _fake_exchange(symbol_present=True, n_rows=24):
    ex = MagicMock()
    ex.markets = {symbol_present and "ETH/USDT:USDT" or "BTC/USDT:USDT": {}}
    rows = [[1_700_000_000_000 + i * 3_600_000, 100, 101, 99, 100.5, 10.0]
            for i in range(n_rows)]
    ex.fetch_ohlcv.return_value = rows
    return ex


def test_fetch_ohlcv_from_exchange(tmp_path):
    ex = _fake_exchange(n_rows=30)
    df = fetch_ohlcv("ETHUSDT", timeframe="1h", days=1,
                     cache_dir=str(tmp_path), use_cache=True,
                     exchange_factory=lambda: ex)
    assert len(df) >= 1
    assert ex.fetch_ohlcv.called
    from volatility_calc.data_fetcher import _cache_path
    assert _cache_path("ETH/USDT:USDT", "1h", 1, str(tmp_path)).exists()


def test_fetch_ohlcv_symbol_not_found(tmp_path):
    ex = _fake_exchange(symbol_present=False)
    with pytest.raises(SymbolNotFoundError) as exc_info:
        fetch_ohlcv("ETHUSDT", timeframe="1h", days=1,
                    cache_dir=str(tmp_path), use_cache=False,
                    exchange_factory=lambda: ex)
    assert "ETH/USDT:USDT" in str(exc_info.value)
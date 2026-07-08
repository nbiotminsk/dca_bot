"""Загрузка OHLCV с Bybit Linear Futures через ccxt, с кешем parquet."""
from __future__ import annotations

import difflib
import logging
import os
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class SymbolNotFoundError(ValueError):
    """Тикер не найден на бирже."""


def parse_symbol(raw: str) -> str:
    """`ETHUSDT` → `ETH/USDT:USDT` (Bybit linear futures)."""
    raw = raw.upper().strip()
    if ":" in raw:
        return raw
    for quote in ("USDT", "USD", "USDC"):
        if raw.endswith(quote):
            base = raw[: -len(quote)]
            if base:
                return f"{base}/{quote}:{quote}"
    raise SymbolNotFoundError(
        f"Не удалось разобрать тикер {raw!r}: ожидается формат вида ETHUSDT"
    )


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Проверка структуры OHLCV датафрейма."""
    if not isinstance(df, pd.DataFrame):
        raise TypeError("Ожидался DataFrame")
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Отсутствуют колонки OHLCV: {missing}")
    if len(df) == 0:
        raise ValueError("Пустой OHLCV датафрейм")
    num_cols = ["open", "high", "low", "close", "volume"]
    for col in num_cols:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise ValueError(f"Колонка {col!r} должна быть числовой")
    if df["high"].lt(df["low"]).any():
        raise ValueError("Найдены свечи, где high < low")
    if df["timestamp"].duplicated().any():
        raise ValueError("Дубликаты timestamp в OHLCV")
    return df


def _cache_path(symbol: str, timeframe: str, days: int, cache_dir: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    if _has_parquet_engine():
        return Path(cache_dir) / f"bybit_{safe}_{timeframe}_{days}.parquet"
    return Path(cache_dir) / f"bybit_{safe}_{timeframe}_{days}.csv"


def _has_parquet_engine() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except Exception:
        try:
            import fastparquet  # noqa: F401
            return True
        except Exception:
            return False


def _load_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        if path.suffix == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, parse_dates=["timestamp"])
        return validate_ohlcv(df)
    except Exception as e:
        logger.warning(f"Не удалось загрузить кеш {path}: {e}")
        return None


def _save_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)


def _create_exchange():
    import ccxt

    exchange = ccxt.bybit({"options": {"defaultType": "linear"}})
    exchange.load_markets()
    return exchange


def _suggest_symbols(symbol: str, markets: dict) -> list[str]:
    all_symbols = list(markets.keys())
    return difflib.get_close_matches(symbol, all_symbols, n=5, cutoff=0.4)


def _fetch_paginated(exchange, symbol_ccxt: str, timeframe: str,
                      since_ms: int, until_ms: int) -> list[list]:
    """Пагинация по `since` — Bybit отдаёт до 1000 свечей за запрос."""
    all_rows: list[list] = []
    cur = since_ms
    limit = 1000
    last_seen_ts: int | None = None
    while cur < until_ms:
        rows = exchange.fetch_ohlcv(symbol_ccxt, timeframe=timeframe,
                                    since=cur, limit=limit)
        if not rows:
            break
        all_rows.extend(rows)
        last_ts = rows[-1][0]
        if last_ts == last_seen_ts:
            # Прогресса нет — выходим, чтобы избежать бесконечного цикла.
            break
        last_seen_ts = last_ts
        cur = last_ts + 1
    return all_rows


def fetch_ohlcv(
    raw_symbol: str,
    timeframe: str = "1h",
    days: int = 90,
    cache_dir: str = "data/cache",
    use_cache: bool = True,
    *,
    exchange_factory=None,
) -> pd.DataFrame:
    """Загрузить OHLCV для линейных фьючерсов Bybit.

    Parameters
    ----------
    raw_symbol : str
        Тикер в любом формате (`ETHUSDT` или `ETH/USDT:USDT`).
    timeframe : str
        Таймфрейм, напр. `1h`.
    days : int
        Глубина истории в днях.
    cache_dir : str
        Каталог кеша.
    use_cache : bool
        Использовать кеш parquet.
    exchange_factory : callable, optional
        Фабрика exchange-объекта для тестов. Должна возвращать объект
        ccxt-формата с `load_markets()` и `fetch_ohlcv()`.
    """
    symbol = parse_symbol(raw_symbol)
    cache_file = _cache_path(symbol, timeframe, days, cache_dir)
    if use_cache:
        cached = _load_cache(cache_file)
        if cached is not None:
            return cached

    exchange = exchange_factory() if exchange_factory is not None else _create_exchange()
    if symbol not in exchange.markets:
        suggestions = _suggest_symbols(symbol, exchange.markets)
        hint = f" Похожие: {', '.join(suggestions)}" if suggestions else ""
        raise SymbolNotFoundError(f"Символ {symbol!r} не найден на Bybit.{hint}")

    import time

    now_ms = int(time.time() * 1000)
    tf_ms = _timeframe_to_ms(timeframe)
    since_ms = now_ms - days * 24 * 60 * 60 * 1000
    raw_rows = _fetch_paginated(exchange, symbol, timeframe, since_ms, now_ms)
    if not raw_rows:
        raise SymbolNotFoundError(f"По символу {symbol!r} нет свечей за период")

    df = pd.DataFrame(raw_rows, columns=OHLCV_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").reset_index(drop=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = validate_ohlcv(df)

    if use_cache:
        _save_cache(df, cache_file)
    return df


def _timeframe_to_ms(timeframe: str) -> int:
    """Парсинг `1h` → миллисекунды. Используется только для оценки since."""
    import re

    match = re.fullmatch(r"(\d+)([smhd])", timeframe)
    if not match:
        return 60 * 60 * 1000
    value, unit = int(match.group(1)), match.group(2)
    multipliers = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return value * multipliers[unit]
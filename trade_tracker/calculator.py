"""Per-trade метрики: avg_entry, PnL, MAE, tp_efficiency."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .models import Trade
from .utils import avg_entry_price, total_qty

logger = logging.getLogger(__name__)

ZERO = Decimal(0)
HUNDRED = Decimal(100)


@dataclass
class TradeMetrics:
    avg_entry: Decimal
    total_qty: Decimal
    notional_in: Decimal
    notional_out: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    pnl_pct: Decimal
    dca_used: int
    mae_pct: Decimal | None
    tp_efficiency: Decimal | None
    side: str


def compute_metrics(trade: Trade) -> TradeMetrics:
    avg_entry = avg_entry_price(trade.entries)
    total_qty_val = total_qty(trade.entries)
    notional_in = avg_entry * total_qty_val
    notional_out = trade.exit_price * total_qty_val
    raw_pnl = (trade.exit_price - avg_entry) * total_qty_val
    if trade.side == "short":
        raw_pnl = -raw_pnl
    gross_pnl = raw_pnl
    fees = trade.fees_paid or ZERO
    net_pnl = gross_pnl - fees
    pnl_pct = (gross_pnl / notional_in * HUNDRED) if notional_in != 0 else ZERO
    dca_used = len(trade.entries)

    tp_efficiency = None
    tp = trade.bot.tp
    if tp != 0:
        tp_target_move = avg_entry * tp
        if trade.side == "short":
            tp_target_move = -tp_target_move
        actual_move = (trade.exit_price - avg_entry)
        if trade.side == "short":
            actual_move = -actual_move
        if tp_target_move != 0:
            tp_efficiency = (Decimal(actual_move) / Decimal(tp_target_move)
                              * HUNDRED)

    return TradeMetrics(
        avg_entry=avg_entry,
        total_qty=total_qty_val,
        notional_in=notional_in,
        notional_out=notional_out,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        pnl_pct=pnl_pct,
        dca_used=dca_used,
        mae_pct=trade.mae_pct,
        tp_efficiency=tp_efficiency,
        side=trade.side,
    )


def fetch_mae_from_cache(trade: Trade, cache_dir: str = "data/cache") -> Decimal | None:
    """Попытаться извлечь MAE из кеша parquet (best-effort, без сети).

    Возвращает None, если пары/периода нет в кеше.
    """
    sym = trade.symbol.upper()
    m = re.fullmatch(r"(\w+)(USDT|USD|USDC)", sym)
    if not m:
        return None
    base, quote = m.group(1), m.group(2)
    ccxt_sym = f"{base}_{quote}_{quote}"
    cache_path = Path(cache_dir)
    matches = list(cache_path.glob(f"bybit_{ccxt_sym}_*.parquet"))
    if not matches:
        return None
    try:
        df = pd.read_parquet(matches[0])
    except Exception as e:
        logger.warning(f"Не удалось прочитать parquet {matches[0]}: {e}")
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    trade_date = pd.Timestamp(trade.date, tz="UTC")
    window = df[(df["timestamp"] >= trade_date) &
                (df["timestamp"] < trade_date + pd.Timedelta(days=7))]
    if window.empty:
        return None
    after = window[window["timestamp"] > trade_date]
    if after.empty:
        return None
    if trade.side == "long":
        extreme = after["low"].min()
        ref_close = window.iloc[0]["close"]
        dd = (extreme - ref_close) / ref_close * 100
    else:
        extreme = after["high"].max()
        ref_close = window.iloc[0]["close"]
        dd = (extreme - ref_close) / ref_close * 100
    return Decimal(str(dd))

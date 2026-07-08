"""Хранилище сделок: плоский CSV (22 колонки) + JSON-зеркало с полными entries."""
from __future__ import annotations

import csv
import io
import json
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .models import Trade, TradeEntry, BotSettingsSnapshot
from .utils import avg_entry_price, total_qty

CSV_COLUMNS = [
    "date", "symbol", "side", "entry_count", "avg_entry", "total_qty",
    "exit_price", "fees_paid", "mae_pct",
    "bot_long_orders", "bot_long_coverage", "bot_long_price_scale", "bot_long_volume_scale",
    "bot_short_orders", "bot_short_coverage", "bot_short_price_scale", "bot_short_volume_scale",
    "bot_tp", "bot_leverage", "bot_base_qty_long", "bot_base_qty_short",
    "notes",
]


def csv_columns() -> list[str]:
    """Список колонок CSV-формата (22 шт.)."""
    return list(CSV_COLUMNS)


class DuplicateTradeError(ValueError):
    pass


def trade_to_csv_row(trade: Trade) -> dict[str, str]:
    """Агрегированная строка CSV из сделки."""
    bot = trade.bot
    return {
        "date": trade.date.isoformat(),
        "symbol": trade.symbol,
        "side": trade.side,
        "entry_count": str(len(trade.entries)),
        "avg_entry": str(avg_entry_price(trade.entries)),
        "total_qty": str(total_qty(trade.entries)),
        "exit_price": str(trade.exit_price),
        "fees_paid": "" if trade.fees_paid is None else str(trade.fees_paid),
        "mae_pct": "" if trade.mae_pct is None else str(trade.mae_pct),
        "bot_long_orders": str(bot.long_orders),
        "bot_long_coverage": str(bot.long_coverage),
        "bot_long_price_scale": str(bot.long_price_scale),
        "bot_long_volume_scale": str(bot.long_volume_scale),
        "bot_short_orders": str(bot.short_orders),
        "bot_short_coverage": str(bot.short_coverage),
        "bot_short_price_scale": str(bot.short_price_scale),
        "bot_short_volume_scale": str(bot.short_volume_scale),
        "bot_tp": str(bot.tp),
        "bot_leverage": str(bot.leverage),
        "bot_base_qty_long": str(bot.long_base_qty),
        "bot_base_qty_short": str(bot.short_base_qty),
        "notes": trade.notes,
    }


def csv_row_to_trade(row: dict[str, str]) -> Trade:
    """Восстановить Trade из CSV-строки. entries синтезируются как одиночный вход."""
    def dec(v, allow_empty=False):
        if v is None or v == "":
            return None
        return Decimal(str(v))

    bot = BotSettingsSnapshot(
        long_orders=int(row["bot_long_orders"]),
        long_coverage=Decimal(str(row["bot_long_coverage"])),
        long_price_scale=Decimal(str(row["bot_long_price_scale"])),
        long_volume_scale=Decimal(str(row["bot_long_volume_scale"])),
        long_base_qty=Decimal(str(row["bot_base_qty_long"])),
        short_orders=int(row["bot_short_orders"]),
        short_coverage=Decimal(str(row["bot_short_coverage"])),
        short_price_scale=Decimal(str(row["bot_short_price_scale"])),
        short_volume_scale=Decimal(str(row["bot_short_volume_scale"])),
        short_base_qty=Decimal(str(row["bot_base_qty_short"])),
        tp=Decimal(str(row["bot_tp"])),
        leverage=int(row["bot_leverage"]),
    )
    avg_entry = Decimal(str(row["avg_entry"]))
    total_qty_val = Decimal(str(row["total_qty"]))
    entries = [TradeEntry(price=avg_entry, qty=total_qty_val)]

    fees = dec(row.get("fees_paid"))
    mae = dec(row.get("mae_pct"))
    return Trade(
        date=_date.fromisoformat(row["date"]),
        symbol=row["symbol"],
        side=row["side"],
        entries=entries,
        exit_price=Decimal(str(row["exit_price"])),
        fees_paid=fees,
        mae_pct=mae,
        notes=row.get("notes", ""),
        bot=bot,
    )


def write_csv(trades: Iterable[Trade], csv_path: str) -> None:
    trades = list(trades)
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade_to_csv_row(trade))


def write_json(trades: Iterable[Trade], json_path: str) -> None:
    trades = list(trades)
    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump([t.to_dict() for t in trades], fh, ensure_ascii=False, indent=2)


def load_trades_from_json(json_path: str) -> list[Trade]:
    if not Path(json_path).exists():
        return []
    with open(json_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [Trade.from_dict(d) for d in data]


def load_trades_from_csv(csv_path: str) -> list[Trade]:
    if not Path(csv_path).exists():
        return []
    with open(csv_path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    if reader.fieldnames != CSV_COLUMNS:
        raise ValueError(
            f"CSV колонки не соответствуют формату: "
            f"ожидалось {len(CSV_COLUMNS)} колонок, получено {reader.fieldnames}"
        )
    return [csv_row_to_trade(r) for r in rows]


def load_trades(csv_path: str, json_path: str, *, prefer: str = "json") -> list[Trade]:
    """Загрузить сделки; JSON — источник правды (с полными entries)."""
    if prefer == "json" and Path(json_path).exists():
        return load_trades_from_json(json_path)
    if prefer == "csv" and Path(csv_path).exists():
        return load_trades_from_csv(csv_path)
    if Path(json_path).exists():
        return load_trades_from_json(json_path)
    if Path(csv_path).exists():
        return load_trades_from_csv(csv_path)
    return []


def save_trade(trade: Trade, csv_path: str, json_path: str,
                *, allow_duplicate: bool = False) -> None:
    """Добавить сделку в хранилище. Защита от дублей по (date, symbol, side)."""
    existing = load_trades(csv_path, json_path)
    for t in existing:
        if t.key == trade.key:
            if allow_duplicate:
                continue
            raise DuplicateTradeError(
                f"Сделка уже существует: {trade.key}. "
                f"Используйте --allow-duplicate для подтверждения."
            )
    trades = existing + [trade]
    write_json(trades, json_path)
    write_csv(trades, csv_path)


def rebuild_json_from_csv(csv_path: str, json_path: str) -> None:
    """Пересоздать JSON из CSV (entries синтезируются как одиночный вход)."""
    trades = load_trades_from_csv(csv_path)
    write_json(trades, json_path)


def csv_template() -> str:
    """Вернуть строку шаблона CSV с заголовком и одной пустой строкой."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerow({col: "" for col in CSV_COLUMNS})
    return buf.getvalue()
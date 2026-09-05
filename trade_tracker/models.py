"""Доменные модели trade_tracker: Trade, TradeEntry, BotSettingsSnapshot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

import yaml


@dataclass(frozen=True)
class TradeEntry:
    price: Decimal
    qty: Decimal

    def to_dict(self) -> dict:
        return {"price": str(self.price), "qty": str(self.qty)}

    @classmethod
    def from_dict(cls, d: dict) -> "TradeEntry":
        return cls(price=Decimal(str(d["price"])), qty=Decimal(str(d["qty"])))


@dataclass(frozen=True)
class BotSettingsSnapshot:
    long_orders: int
    long_coverage: Decimal
    long_price_scale: Decimal
    long_volume_scale: Decimal
    long_base_qty: Decimal
    short_orders: int
    short_coverage: Decimal
    short_price_scale: Decimal
    short_volume_scale: Decimal
    short_base_qty: Decimal
    tp: Decimal
    leverage: int

    def to_dict(self) -> dict:
        return {
            "long_orders": self.long_orders,
            "long_coverage": str(self.long_coverage),
            "long_price_scale": str(self.long_price_scale),
            "long_volume_scale": str(self.long_volume_scale),
            "long_base_qty": str(self.long_base_qty),
            "short_orders": self.short_orders,
            "short_coverage": str(self.short_coverage),
            "short_price_scale": str(self.short_price_scale),
            "short_volume_scale": str(self.short_volume_scale),
            "short_base_qty": str(self.short_base_qty),
            "tp": str(self.tp),
            "leverage": self.leverage,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BotSettingsSnapshot":
        return cls(
            long_orders=int(d["long_orders"]),
            long_coverage=Decimal(str(d["long_coverage"])),
            long_price_scale=Decimal(str(d["long_price_scale"])),
            long_volume_scale=Decimal(str(d["long_volume_scale"])),
            long_base_qty=Decimal(str(d["long_base_qty"])),
            short_orders=int(d["short_orders"]),
            short_coverage=Decimal(str(d["short_coverage"])),
            short_price_scale=Decimal(str(d["short_price_scale"])),
            short_volume_scale=Decimal(str(d["short_volume_scale"])),
            short_base_qty=Decimal(str(d["short_base_qty"])),
            tp=Decimal(str(d["tp"])),
            leverage=int(d["leverage"]),
        )

    @classmethod
    def from_config(cls, config_path: str = "config/settings.yaml") -> "BotSettingsSnapshot":
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        cs = cfg["current_settings"]
        return cls(
            long_orders=int(cs["long"]["orders"]),
            long_coverage=Decimal(str(cs["long"]["price_coverage"])),
            long_price_scale=Decimal(str(cs["long"]["price_scale"])),
            long_volume_scale=Decimal(str(cs["long"]["volume_scale"])),
            long_base_qty=Decimal(str(cs["long"]["base_qty"])),
            short_orders=int(cs["short"]["orders"]),
            short_coverage=Decimal(str(cs["short"]["price_coverage"])),
            short_price_scale=Decimal(str(cs["short"]["price_scale"])),
            short_volume_scale=Decimal(str(cs["short"]["volume_scale"])),
            short_base_qty=Decimal(str(cs["short"]["base_qty"])),
            tp=Decimal(str(cs["tp"])),
            leverage=int(cfg.get("leverage", 2)),
        )


@dataclass
class Trade:
    date: date
    symbol: str
    side: Literal["long", "short"]
    entries: list[TradeEntry]
    exit_price: Decimal
    bot: BotSettingsSnapshot
    fees_paid: Decimal | None = None
    mae_pct: Decimal | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.side not in ("long", "short"):
            raise ValueError(f"side должен быть long/short, получено {self.side!r}")
        if not self.entries:
            raise ValueError("entries не может быть пустым")
        if isinstance(self.date, str):
            self.date = _parse_date(self.date)
        if isinstance(self.exit_price, (int, float, str)):
            self.exit_price = Decimal(str(self.exit_price))
        if isinstance(self.fees_paid, (int, float, str)) and self.fees_paid is not None:
            self.fees_paid = Decimal(str(self.fees_paid))
        if isinstance(self.mae_pct, (int, float, str)) and self.mae_pct is not None:
            self.mae_pct = Decimal(str(self.mae_pct))

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "symbol": self.symbol,
            "side": self.side,
            "entries": [e.to_dict() for e in self.entries],
            "exit_price": str(self.exit_price),
            "fees_paid": None if self.fees_paid is None else str(self.fees_paid),
            "mae_pct": None if self.mae_pct is None else str(self.mae_pct),
            "notes": self.notes,
            "bot": self.bot.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        return cls(
            date=_parse_date(d["date"]),
            symbol=d["symbol"],
            side=d["side"],
            entries=[TradeEntry.from_dict(e) for e in d["entries"]],
            exit_price=Decimal(str(d["exit_price"])),
            fees_paid=None if d.get("fees_paid") is None else Decimal(str(d["fees_paid"])),
            mae_pct=None if d.get("mae_pct") is None else Decimal(str(d["mae_pct"])),
            notes=d.get("notes", ""),
            bot=BotSettingsSnapshot.from_dict(d["bot"]),
        )

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.date.isoformat(), self.symbol.upper(), self.side)


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))

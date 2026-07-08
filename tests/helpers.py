"""Тестовые утилиты."""
from datetime import date
from decimal import Decimal

from trade_tracker.models import Trade, TradeEntry, BotSettingsSnapshot


def make_bot(**overrides) -> BotSettingsSnapshot:
    defaults = dict(
        long_orders=5,
        long_coverage=Decimal("0.18"),
        long_price_scale=Decimal("1.4"),
        long_volume_scale=Decimal("1.2"),
        long_base_qty=Decimal("0.04"),
        short_orders=3,
        short_coverage=Decimal("0.12"),
        short_price_scale=Decimal("1.3"),
        short_volume_scale=Decimal("1.1"),
        short_base_qty=Decimal("0.03"),
        tp=Decimal("0.008"),
        leverage=2,
    )
    defaults.update(overrides)
    return BotSettingsSnapshot(**defaults)


def make_trade(**overrides) -> Trade:
    bot = overrides.pop("bot", make_bot())
    defaults = dict(
        date=date(2026, 1, 15),
        symbol="ETHUSDT",
        side="long",
        entries=[TradeEntry(Decimal("2300"), Decimal("0.025")),
                  TradeEntry(Decimal("2200"), Decimal("0.050"))],
        exit_price=Decimal("2450"),
        fees_paid=Decimal("1.85"),
        mae_pct=Decimal("-6.20"),
        notes="",
        bot=bot,
    )
    defaults.update(overrides)
    return Trade(**defaults)
from datetime import date
from decimal import Decimal

import pytest

from trade_tracker.models import (
    Trade, TradeEntry, BotSettingsSnapshot,
)
from tests.helpers import make_bot


SAMPLE_CONFIG = "tests/fixtures/sample_settings.yaml"


def write_config(tmp_path):
    cfg = {
        "leverage": 2,
        "current_settings": {
            "long": {"orders": 5, "price_coverage": 0.18, "price_scale": 1.4,
                      "volume_scale": 1.2, "base_qty": 0.04},
            "short": {"orders": 3, "price_coverage": 0.12, "price_scale": 1.3,
                       "volume_scale": 1.1, "base_qty": 0.03},
            "tp": 0.008,
        },
    }
    import yaml

    p = tmp_path / "settings.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


def test_bot_snapshot_to_dict_roundtrip():
    bot = make_bot()
    d = bot.to_dict()
    assert d["long_orders"] == 5
    assert isinstance(d["long_coverage"], str)
    bot2 = BotSettingsSnapshot.from_dict(d)
    assert bot2 == bot


def test_bot_snapshot_from_config(tmp_path):
    path = write_config(tmp_path)
    bot = BotSettingsSnapshot.from_config(path)
    assert bot.long_orders == 5
    assert bot.long_coverage == Decimal("0.18")
    assert bot.tp == Decimal("0.008")
    assert bot.leverage == 2


def test_trade_requires_bot():
    with pytest.raises(TypeError):
        Trade(date=date(2026, 1, 1), symbol="ETHUSDT", side="long",
              entries=[TradeEntry(Decimal("100"), Decimal("0.1"))],
              exit_price=Decimal("110"))


def test_trade_invalid_side():
    with pytest.raises(ValueError):
        Trade(date=date(2026, 1, 1), symbol="ETHUSDT", side="up",
              entries=[TradeEntry(Decimal("100"), Decimal("0.1"))],
              exit_price=Decimal("110"), bot=make_bot())


def test_trade_empty_entries():
    with pytest.raises(ValueError):
        Trade(date=date(2026, 1, 1), symbol="ETHUSDT", side="long",
              entries=[], exit_price=Decimal("110"), bot=make_bot())


def test_trade_to_dict_roundtrip():
    t = Trade(
        date=date(2026, 1, 15), symbol="ETHUSDT", side="long",
        entries=[TradeEntry(Decimal("2300"), Decimal("0.025")),
                  TradeEntry(Decimal("2200"), Decimal("0.050"))],
        exit_price=Decimal("2450"), fees_paid=Decimal("1.85"),
        mae_pct=Decimal("-6.20"), notes="откат", bot=make_bot(),
    )
    d = t.to_dict()
    t2 = Trade.from_dict(d)
    assert t2.date == t.date
    assert t2.side == t.side
    assert t2.bot == t.bot
    assert len(t2.entries) == 2


def test_trade_key_unique():
    t = Trade(
        date=date(2026, 1, 15), symbol="ETHUSDT", side="long",
        entries=[TradeEntry(Decimal("100"), Decimal("0.1"))],
        exit_price=Decimal("110"), bot=make_bot(),
    )
    assert t.key == ("2026-01-15", "ETHUSDT", "long")

from decimal import Decimal

import pytest

from trade_tracker.comparator import (
    compare_epochs, compare_with_config, group_by_epoch,
)
from trade_tracker.aggregator import aggregate
from tests.helpers import make_bot, make_trade


def _trade_with_coverage(coverage, exit_price="2400"):
    bot = make_bot(long_coverage=Decimal(str(coverage)))
    return make_trade(bot=bot, exit_price=Decimal(exit_price))


def test_group_by_coverage():
    trades = [
        _trade_with_coverage("0.18", "2500"),
        _trade_with_coverage("0.18", "2300"),
        _trade_with_coverage("0.10", "2000"),
    ]
    groups = group_by_epoch(trades, "bot_long_coverage")
    assert Decimal("0.18") in groups
    assert Decimal("0.10") in groups
    assert groups[Decimal("0.18")].n == 2
    assert groups[Decimal("0.10")].n == 1


def test_compare_epochs_returns_deltas():
    trades = [
        _trade_with_coverage("0.10", "2500"),
        _trade_with_coverage("0.10", "2300"),
        _trade_with_coverage("0.18", "2000"),
        _trade_with_coverage("0.18", "2100"),
    ]
    cmp = compare_epochs(trades, "bot_long_coverage")
    assert len(cmp.epochs) == 2
    assert len(cmp.deltas) == 1
    delta = cmp.deltas[0]
    assert "avg_pnl_delta" in delta
    assert "win_rate_delta" in delta


def test_compare_with_config_match():
    bot = make_bot()
    trade = make_trade(bot=bot)
    report = compare_with_config([trade], current_settings=bot)
    assert report.matches_config is True
    assert report.diffs == []


def test_compare_with_config_mismatch():
    cfg = make_bot()
    diff_bot = make_bot(long_orders=7)
    trade = make_trade(bot=diff_bot)
    report = compare_with_config([trade], current_settings=cfg)
    assert report.matches_config is False
    fields = [d["field"] for d in report.diffs]
    assert "long_orders" in fields


def test_compare_with_config_vs_history():
    cfg = make_bot()
    trades = [make_trade(bot=cfg, exit_price=Decimal("2400")),
              make_trade(bot=cfg, exit_price=Decimal("2500"))]
    history = aggregate([make_trade(bot=cfg, exit_price=Decimal("2300"))])
    report = compare_with_config(trades, current_settings=cfg, historical_stats=history)
    assert report.vs_history is not None
    assert report.vs_history["trade_pnl_pct"] is not None


def test_compare_with_config_empty():
    report = compare_with_config([], current_settings=make_bot())
    assert report.matches_config is True


def test_unsupported_field_raises():
    with pytest.raises(ValueError):
        group_by_epoch([], "nonexistent_field")

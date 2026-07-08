from datetime import date
from decimal import Decimal

from trade_tracker.aggregator import aggregate
from trade_tracker.models import TradeEntry
from tests.helpers import make_bot, make_trade


def _win_trade(exit_price="2500"):
    return make_trade(exit_price=Decimal(exit_price))


def _loss_trade(exit_price="2000"):
    return make_trade(exit_price=Decimal(exit_price))


def test_empty_aggregate():
    stats = aggregate([])
    assert stats.n_trades == 0
    assert stats.total_pnl == Decimal(0)


def test_win_rate_half():
    trades = [_win_trade(), _loss_trade(), _win_trade(), _loss_trade()]
    stats = aggregate(trades)
    assert stats.n_trades == 4
    assert stats.n_wins == 2
    assert stats.n_losses == 2
    assert stats.win_rate == 50.0


def test_cumulative_pnl():
    trades = [_win_trade(), _win_trade()]
    stats = aggregate(trades)
    assert len(stats.cumulative_pnl) == 2
    assert stats.cumulative_pnl[1] == stats.total_pnl


def test_dca_distribution():
    t1 = make_trade(entries=[TradeEntry(Decimal("100"), Decimal("0.1"))])
    t2 = make_trade(entries=[TradeEntry(Decimal("100"), Decimal("0.1")),
                              TradeEntry(Decimal("98"), Decimal("0.1"))])
    stats = aggregate([t1, t2])
    assert stats.dca_distribution == {1: 1, 2: 1}


def test_mae_aggregates():
    t1 = make_trade(mae_pct=Decimal("-5.0"))
    t2 = make_trade(mae_pct=Decimal("-10.0"))
    t3 = make_trade(mae_pct=None)
    stats = aggregate([t1, t2, t3])
    assert stats.avg_mae == Decimal("-7.5")
    assert stats.max_mae == Decimal("-10.0")
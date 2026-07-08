from datetime import date
from decimal import Decimal

import pytest

from trade_tracker.calculator import compute_metrics
from trade_tracker.models import TradeEntry
from tests.helpers import make_bot, make_trade


def test_avg_entry_weighted():
    t = make_trade()  # entries 2300×0.025 + 2200×0.050
    m = compute_metrics(t)
    # weighted avg = (2300*0.025 + 2200*0.05) / 0.075 = 2233.333...
    assert m.avg_entry == pytest.approx(Decimal("2233.3333"), abs=Decimal("0.01"))
    assert m.total_qty == Decimal("0.075")


def test_long_pnl():
    # exit above avg → positive pnl
    t = make_trade(exit_price=Decimal("2400"))
    m = compute_metrics(t)
    expected_gross = (Decimal("2400") - Decimal("2233.333333333333333333333333")) * Decimal("0.075")
    assert m.gross_pnl == pytest.approx(expected_gross, abs=Decimal("0.01"))
    assert m.pnl_pct > 0


def test_short_pnl_sign_inverted():
    t = make_trade(side="short", exit_price=Decimal("2000"))
    m = compute_metrics(t)
    # short: pnl = -(exit - avg) * qty = -(2000 - 2233.33)*0.075 = +17.5
    assert m.gross_pnl > 0
    assert m.net_pnl < m.gross_pnl  # вычли fees


def test_net_pnl_with_fees():
    t = make_trade(exit_price=Decimal("2400"), fees_paid=Decimal("2.00"))
    m = compute_metrics(t)
    assert m.net_pnl == m.gross_pnl - Decimal("2.00")


def test_dca_used():
    t = make_trade(entries=[TradeEntry(Decimal("100"), Decimal("0.1")),
                            TradeEntry(Decimal("98"), Decimal("0.1")),
                            TradeEntry(Decimal("96"), Decimal("0.1"))])
    m = compute_metrics(t)
    assert m.dca_used == 3


def test_tp_efficiency_long():
    # bot.tp = 0.008 (0.8%), avg=2233.33 → target_move=17.87; actual=16.67 → ~93%
    t = make_trade(exit_price=Decimal("2250"))
    m = compute_metrics(t)
    assert m.tp_efficiency is not None
    assert 80 < float(m.tp_efficiency) < 100


def test_no_mae_returns_none():
    t = make_trade()
    t.mae_pct = None
    m = compute_metrics(t)
    assert m.mae_pct is None


def test_pnl_pct_zero_notional_safe():
    t = make_trade(entries=[TradeEntry(Decimal("100"), Decimal("0"))])
    m = compute_metrics(t)
    assert m.pnl_pct == Decimal(0)
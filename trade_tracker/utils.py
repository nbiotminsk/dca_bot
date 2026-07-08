"""Общие утилиты для расчётов по сделкам."""
from __future__ import annotations

from decimal import Decimal

from .models import TradeEntry

ZERO = Decimal(0)


def avg_entry_price(entries: list[TradeEntry]) -> Decimal:
    """Средневзвешенная цена входа."""
    total_qty = sum((e.qty for e in entries), ZERO)
    if total_qty == 0:
        return ZERO
    total_cost = sum((e.price * e.qty for e in entries), ZERO)
    return total_cost / total_qty


def total_qty(entries: list[TradeEntry]) -> Decimal:
    """Общее количество."""
    return sum((e.qty for e in entries), ZERO)

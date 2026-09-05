"""trade_tracker: журнал сделок и A/B анализ."""

from .models import Trade, TradeEntry, BotSettingsSnapshot
from .storage import save_trade, load_trades, csv_columns, write_csv, write_json
from .calculator import compute_metrics, TradeMetrics
from .aggregator import aggregate, PortfolioStats
from .comparator import group_by_epoch, compare_epochs, compare_with_config
from .report import (
    render_single_trade,
    render_trade_table,
    render_mae_coverage_check,
    render_settings_timeline,
    render_epoch_comparison,
)

__all__ = [
    "Trade",
    "TradeEntry",
    "BotSettingsSnapshot",
    "save_trade",
    "load_trades",
    "csv_columns",
    "write_csv",
    "write_json",
    "compute_metrics",
    "TradeMetrics",
    "aggregate",
    "PortfolioStats",
    "group_by_epoch",
    "compare_epochs",
    "compare_with_config",
    "render_single_trade",
    "render_trade_table",
    "render_mae_coverage_check",
    "render_settings_timeline",
    "render_epoch_comparison",
]

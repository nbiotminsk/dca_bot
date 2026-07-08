"""rich-таблицы для trade_tracker."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .aggregator import PortfolioStats
from .calculator import compute_metrics
from .comparator import EpochComparison
from .models import Trade


def _fmt(x, suffix=""):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.2f}{suffix}"
    return f"{x}{suffix}"


def render_single_trade(trade: Trade, *, return_str: bool = False) -> str | None:
    m = compute_metrics(trade)
    console = Console(record=True, force_terminal=False, width=80) if return_str else Console()
    table = Table(title=f"СДЕЛКА  {trade.symbol} {trade.side}  {trade.date}",
                  header_style="bold cyan", show_header=False)
    table.add_column("Параметр", style="dim")
    table.add_column("Значение", justify="right")
    table.add_row("Кол-во входов (DCA)", str(m.dca_used))
    table.add_row("Средняя цена входа", f"{m.avg_entry:.2f}")
    table.add_row("Выход", f"{trade.exit_price:.2f}")
    table.add_row("Total qty", f"{m.total_qty}")
    table.add_row("Notional in/out (USDT)", f"{m.notional_in:.2f} / {m.notional_out:.2f}")
    table.add_row("Gross PnL", f"{m.gross_pnl:+.2f} USDT")
    table.add_row("Net PnL", f"{m.net_pnl:+.2f} USDT")
    table.add_row("PnL %", f"{m.pnl_pct:+.2f}%")
    table.add_row("MAE %", _fmt(trade.mae_pct))
    table.add_row("TP efficiency", _fmt(m.tp_efficiency, "%"))
    table.add_row("Заметки", trade.notes or "—")
    console.print(table)
    if return_str:
        return console.export_text()
    return None


def render_trade_table(trades: list[Trade], aggregate_stats: PortfolioStats,
                       *, return_str: bool = False) -> str | None:
    console = Console(record=True, force_terminal=False, width=100) if return_str else Console(width=100)
    table = Table(title="ЖУРНАЛ СДЕЛОК", header_style="bold cyan")
    for col in ("Date", "Symbol", "Side", "DCA", "Avg", "Exit", "Gross PnL",
                "PnL %", "MAE %"):
        table.add_column(col)
    for t in trades:
        m = compute_metrics(t)
        table.add_row(
            t.date.isoformat(),
            t.symbol,
            t.side,
            str(m.dca_used),
            f"{m.avg_entry:.2f}",
            f"{t.exit_price:.2f}",
            f"{m.gross_pnl:+.2f}",
            f"{m.pnl_pct:+.2f}%",
            _fmt(t.mae_pct),
        )
    console.print(table)

    summary = Table(title="ИТОГО", header_style="bold cyan", show_header=False)
    summary.add_column("Метрика", style="dim")
    summary.add_column("Значение", justify="right")
    summary.add_row("Сделок", str(aggregate_stats.n_trades))
    summary.add_row("Win rate", f"{aggregate_stats.win_rate:.1f}%")
    summary.add_row("Avg PnL", f"{aggregate_stats.avg_pnl:+.2f}")
    summary.add_row("Median PnL", f"{aggregate_stats.median_pnl:+.2f}")
    summary.add_row("Total PnL", f"{aggregate_stats.total_pnl:+.2f}")
    summary.add_row("Avg MAE", _fmt(aggregate_stats.avg_mae))
    summary.add_row("Max MAE", _fmt(aggregate_stats.max_mae))
    console.print(summary)
    if return_str:
        return console.export_text()
    return None


def render_mae_coverage_check(trades: list[Trade], *, return_str: bool = False) -> str | None:
    console = Console(record=True, force_terminal=False, width=100) if return_str else Console(width=100)
    table = Table(title="ПРОВЕРКА COVERAGE vs MAE", header_style="bold cyan")
    for col in ("Date", "Symbol", "Side", "MAE %", "Bot coverage", "Хватает?"):
        table.add_column(col)
    for t in trades:
        mae = t.mae_pct
        if t.side == "long":
            coverage = t.bot.long_coverage
        else:
            coverage = t.bot.short_coverage
        ok = ("OK" if mae is None or coverage is None
              else "✗" if abs(mae) > float(coverage) * 100 else "OK")
        table.add_row(
            t.date.isoformat(), t.symbol, t.side, _fmt(mae), f"{float(coverage)*100:.1f}%", ok
        )
    console.print(table)
    if return_str:
        return console.export_text()
    return None


def render_settings_timeline(trades: list[Trade], *, return_str: bool = False) -> str | None:
    console = Console(record=True, force_terminal=False, width=100) if return_str else Console(width=100)
    table = Table(title="ХРОНОЛОГИЯ НАСТРОЕК", header_style="bold cyan")
    for col in ("Date", "Symbol", "Side",
                "L.orders", "L.cov", "L.ps", "L.vs",
                "S.orders", "S.cov", "S.ps", "S.vs",
                "TP", "Lev"):
        table.add_column(col)
    for t in trades:
        b = t.bot
        table.add_row(
            t.date.isoformat(), t.symbol, t.side,
            str(b.long_orders), f"{b.long_coverage}", f"{b.long_price_scale}",
            f"{b.long_volume_scale}",
            str(b.short_orders), f"{b.short_coverage}", f"{b.short_price_scale}",
            f"{b.short_volume_scale}", f"{b.tp}", f"{b.leverage}x",
        )
    console.print(table)
    if return_str:
        return console.export_text()
    return None


def render_epoch_comparison(cmp: EpochComparison, *, return_str: bool = False) -> str | None:
    console = Console(record=True, force_terminal=False, width=100) if return_str else Console(width=100)
    table = Table(title=f"A/B СРАВНЕНИЕ ПО {cmp.field}", header_style="bold cyan")
    for col in ("Эпоха", "N", "Win rate", "Avg PnL", "Avg MAE"):
        table.add_column(col)
    for e in cmp.epochs:
        table.add_row(str(e.key), str(e.n), f"{e.win_rate:.1f}%",
                       f"{e.avg_pnl:+.2f}", _fmt(e.avg_mae))
    console.print(table)

    if cmp.deltas:
        dtable = Table(title="ДЕЛЬТЫ ЭПОХ", header_style="bold cyan")
        for col in ("From", "To", "Δ Win rate", "Δ Avg PnL"):
            dtable.add_column(col)
        for d in cmp.deltas:
            dtable.add_row(str(d["from"]), str(d["to"]),
                            f"{d['win_rate_delta']:+.1f}pp",
                            f"{d['avg_pnl_delta']:+.2f}")
        console.print(dtable)
    if return_str:
        return console.export_text()
    return None
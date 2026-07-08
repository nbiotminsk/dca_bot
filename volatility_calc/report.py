"""rich-таблицы для волатильности и DCA-рекомендации."""
from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.table import Table

from .drawdown_analyzer import MultiHorizonStats
from .liquidation import LiquidationAssessment
from .dca_recommender import FullRecommendation, CurrentSettings


def _fmt_pct(x: float, suffix: str = "%") -> str:
    if x is None or (isinstance(x, float) and x != x):  # NaN
        return "—"
    return f"{x:+.2f}{suffix}" if suffix == "%" else f"{x:.2f}"


def _side_stats_table(stats, label: str) -> Table:
    table = Table(title=f"ПРОСАДКА {label} (%)", show_header=True, header_style="bold cyan")
    table.add_column("Метрика", style="dim")
    for h in stats.horizons:
        table.add_column(f"{h.horizon_h}h", justify="right")
    rows = ["mean", "median", "std", "p90", "p95", "p99", "max"]
    for name in rows:
        row = [name]
        for h in stats.horizons:
            side = h.long if label == "LONG" else h.short
            row.append(_fmt_pct(getattr(side, name)))
        table.add_row(*row)
    # threshold rows
    thresholds = list(stats.horizons[0].long_above_thresholds.keys()) if stats.horizons else []
    for t in thresholds:
        row = [f">{t}%"]
        for h in stats.horizons:
            above = h.long_above_thresholds if label == "LONG" else h.short_above_thresholds
            row.append(f"{above.get(t, 0):.1f}%")
        table.add_row(*row)
    return table


def _liq_table(liq: LiquidationAssessment) -> Table:
    table = Table(title=f"РИСК ЛИКВИДАЦИИ (плечо {liq.leverage}x)", header_style="bold cyan")
    table.add_column("Параметр", style="dim")
    table.add_column("Значение", justify="right")
    table.add_row("Расстояние до ликвидации", f"{liq.liq_distance_pct:.2f}%")
    table.add_row("p99 long dd (168h)", _fmt_pct(liq.p99_long_dd))
    table.add_row("p99 short dd (168h)", _fmt_pct(liq.p99_short_dd))
    table.add_row("Buffer до ликвидации", f"{liq.buffer_pct:.2f}%  [{liq.level.value}]")
    table.add_row("Макс. безопасное плечо",
                  f"{liq.max_safe_leverage}x (buffer = {liq.max_safe_leverage_buffer_pct:.1f}%)")
    return table


def _dca_table(rec: FullRecommendation,
               current: tuple[CurrentSettings, CurrentSettings]) -> Table:
    cur_long, cur_short = current
    table = Table(title="DCA-СЕТКА: ТЕКУЩАЯ vs РЕКОМЕНДАЦИЯ", header_style="bold cyan")
    table.add_column("Параметр", style="dim")
    table.add_column("СЕЙЧАС", justify="right")
    table.add_column("РЕКОМЕНДАЦИЯ", justify="right")

    table.add_row("[bold]LONG[/bold]", "", "")
    table.add_row("  Orders", str(cur_long.orders), str(rec.long.orders))
    table.add_row("  Price Coverage", f"{cur_long.coverage*100:.1f}%", f"{rec.long.coverage*100:.1f}%")
    table.add_row("  Price Scale", f"{cur_long.price_scale:.2f}", f"{rec.long.price_scale:.2f}")
    table.add_row("  Volume Scale", f"{cur_long.volume_scale:.2f}", f"{rec.long.volume_scale:.2f}")

    table.add_row("[bold]SHORT[/bold]", "", "")
    table.add_row("  Orders", str(cur_short.orders), str(rec.short.orders))
    table.add_row("  Price Coverage", f"{cur_short.coverage*100:.1f}%", f"{rec.short.coverage*100:.1f}%")
    table.add_row("  Price Scale", f"{cur_short.price_scale:.2f}", f"{rec.short.price_scale:.2f}")
    table.add_row("  Volume Scale", f"{cur_short.volume_scale:.2f}", f"{rec.short.volume_scale:.2f}")

    table.add_row("[bold]TP[/bold]", "", "")
    table.add_row("  Take Profit", "—", f"{rec.tp:.3f}%")
    return table


def _summary_table(rec: FullRecommendation,
                  current: tuple[CurrentSettings, CurrentSettings],
                  liq: LiquidationAssessment) -> Table:
    cur_long, cur_short = current
    table = Table(title="РЕЗЮМЕ ИЗМЕНЕНИЙ", header_style="bold cyan", show_header=False)
    table.add_column("Line")
    table.add_row(
        f"[LONG]  coverage {cur_long.coverage*100:.1f}% → {rec.long.coverage*100:.1f}%  "
        f"— p95 за 7д, запас ×1.2",
    )
    table.add_row(
        f"[SHORT] orders {cur_short.orders} → {rec.short.orders}  — рекомендация по short",
    )
    table.add_row(f"[TP]    {rec.tp:.3f}%  — медианный полож. ход ×1.2")
    table.add_row(
        f"[B]     buffer {liq.buffer_pct:.1f}% [{liq.level.value}]  "
        f"— макс. безопасное плечо {liq.max_safe_leverage}x",
    )
    return table


def render_volatility_report(
    stats: MultiHorizonStats,
    liq: LiquidationAssessment,
    rec: FullRecommendation,
    current: tuple[CurrentSettings, CurrentSettings],
    *,
    return_str: bool = False,
) -> str | None:
    """Отрисовать полный отчёт через rich. Возвращает строку, если return_str=True."""
    console = Console(record=True, force_terminal=False, width=80) if return_str else Console()
    console.rule(f"[bold]ВОЛАТИЛЬНОСТЬ И DCA-РЕКОМЕНДАЦИЯ  |  {stats.symbol}[/bold]")
    console.print(f"Bybit Linear Futures  |  {stats.timeframe}  |  "
                  f"{stats.days} дней ({stats.n_candles} свечей)")
    console.rule()
    console.print(_side_stats_table(stats, "LONG"))
    console.print()
    console.print(_side_stats_table(stats, "SHORT"))
    console.print()
    console.print(_liq_table(liq))
    console.print()
    console.print(_dca_table(rec, current))
    console.print()
    console.print(_summary_table(rec, current, liq))
    console.rule()

    for line in rec.rationale:
        console.print(f"[dim]• {line}[/dim]")

    if return_str:
        return console.export_text()
    return None
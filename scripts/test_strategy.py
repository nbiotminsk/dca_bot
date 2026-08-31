"""CLI: тест стратегии бота по конфигу.

Примеры:
    python scripts/test_strategy.py
    python scripts/test_strategy.py --config config/settings_hype.yaml --symbol HYPEUSDT
    python scripts/test_strategy.py ETHUSDT --days 180 --non-overlapping
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import yaml
from rich.console import Console
from rich.table import Table

from volatility_calc.backtest import simulate, summarize
from volatility_calc.data_fetcher import fetch_ohlcv
from volatility_calc.drawdown_analyzer import analyze_extremes, compute_hurst_exponent
from volatility_calc.liquidation import assess_liquidation_risk
from volatility_calc.portfolio_optimizer import compute_optimal_hedge_weights
from volatility_calc.regime import detect_regime


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _calc_side_metrics(pnls: list[float]) -> dict:
    if not pnls:
        return {
            "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
            "kelly": 0.0, "half_kelly": 0.0, "cvar_5": 0.0,
        }
    arr = np.array(pnls)
    mean_pnl = float(np.mean(arr))
    std_pnl = float(np.std(arr))
    sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0
    downside = arr[arr < 0]
    downside_std = float(np.std(downside)) if len(downside) else 0.0
    sortino = mean_pnl / downside_std if downside_std > 0 else 0.0
    cumsum = np.cumsum(arr)
    peak = np.maximum.accumulate(cumsum)
    max_dd = float(np.max(peak - cumsum)) if len(cumsum) else 0.0
    calmar = mean_pnl / max_dd if max_dd > 0 else 0.0
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    win_rate = len(wins) / len(arr)
    avg_win = float(np.mean(wins)) if len(wins) else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) else 0.0
    b = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    kelly = ((win_rate * b - (1 - win_rate)) / b) if b > 0 else 0.0
    cvar_5 = float(np.mean(arr[arr <= np.percentile(arr, 5)])) if len(arr) > 20 else 0.0
    return {
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "kelly": kelly,
        "half_kelly": kelly / 2,
        "cvar_5": cvar_5,
    }


def run_strategy_test(
    *,
    symbol: str,
    config_path: str,
    days: int | None = None,
    non_overlapping: bool = True,
    step: int = 1,
) -> int:
    console = Console()
    cfg = load_config(config_path)
    history_days = days if days is not None else int(cfg.get("history_days", 90))

    console.print(f"\n[bold cyan]ТЕСТ СТРАТЕГИИ {symbol}[/bold cyan]\n")
    console.print(f"[dim]Конфиг:[/dim] {config_path}")
    console.print(f"[dim]Таймфрейм:[/dim] {cfg['timeframe']}")
    console.print(f"[dim]История:[/dim] {history_days} дней")
    console.print(f"[dim]Плечо:[/dim] {cfg['leverage']}x")
    console.print(f"[dim]Non-overlap:[/dim] {non_overlapping}\n")

    console.print("[yellow]Загрузка данных...[/yellow]")
    try:
        df = fetch_ohlcv(
            symbol,
            timeframe=cfg["timeframe"],
            days=history_days,
            cache_dir=cfg.get("cache", {}).get("dir", "data/cache"),
            use_cache=True,
        )
    except Exception as e:
        console.print(f"[red]Ошибка загрузки: {e}[/red]")
        return 1
    console.print(f"[green]Загружено {len(df)} свечей[/green]\n")

    cs = cfg["current_settings"]
    settings_table = Table(show_header=False, box=None)
    settings_table.add_column("Параметр", style="dim")
    settings_table.add_column("Long", justify="right", style="cyan")
    settings_table.add_column("Short", justify="right", style="magenta")
    settings_table.add_row("Orders", str(cs["long"]["orders"]), str(cs["short"]["orders"]))
    settings_table.add_row(
        "Coverage",
        f"{cs['long']['price_coverage']*100:.1f}%",
        f"{cs['short']['price_coverage']*100:.1f}%",
    )
    settings_table.add_row(
        "Price Scale", f"{cs['long']['price_scale']:.2f}", f"{cs['short']['price_scale']:.2f}"
    )
    settings_table.add_row(
        "Volume Scale", f"{cs['long']['volume_scale']:.2f}", f"{cs['short']['volume_scale']:.2f}"
    )
    settings_table.add_row("Base Qty", str(cs["long"]["base_qty"]), str(cs["short"]["base_qty"]))
    settings_table.add_row("TP", f"{cs['tp']*100:.2f}%", f"{cs['tp']*100:.2f}%")
    console.print(settings_table)
    console.print()

    common = dict(
        leverage=cfg["leverage"],
        maintenance_margin_rate=cfg["maintenance_margin_rate"],
        horizon_h=cfg["recommendation_horizon"],
        step=step,
        non_overlapping=non_overlapping,
        tp_pct=cs["tp"],
    )

    console.print("[yellow]Backtest LONG...[/yellow]")
    long_results = simulate(
        df,
        n_orders=cs["long"]["orders"],
        price_scale=cs["long"]["price_scale"],
        volume_scale=cs["long"]["volume_scale"],
        base_qty=cs["long"]["base_qty"],
        side="long",
        **common,
    )
    long_summary = summarize(long_results)
    console.print(f"[green]Long: {long_summary.n_trades} сделок[/green]")

    console.print("[yellow]Backtest SHORT...[/yellow]")
    short_results = simulate(
        df,
        n_orders=cs["short"]["orders"],
        price_scale=cs["short"]["price_scale"],
        volume_scale=cs["short"]["volume_scale"],
        base_qty=cs["short"]["base_qty"],
        side="short",
        **common,
    )
    short_summary = summarize(short_results)
    console.print(f"[green]Short: {short_summary.n_trades} сделок[/green]\n")

    results_table = Table(title="BACKTEST", header_style="bold cyan")
    results_table.add_column("Метрика", style="dim")
    results_table.add_column("Long", justify="right", style="cyan")
    results_table.add_column("Short", justify="right", style="magenta")
    rows = [
        ("Сделок", str(long_summary.n_trades), str(short_summary.n_trades)),
        ("Win Rate", f"{long_summary.win_rate:.1f}%", f"{short_summary.win_rate:.1f}%"),
        ("Total PnL", f"{long_summary.total_pnl_pct:+.2f}%", f"{short_summary.total_pnl_pct:+.2f}%"),
        ("Avg PnL", f"{long_summary.avg_pnl_pct:+.3f}%", f"{short_summary.avg_pnl_pct:+.3f}%"),
        ("Median PnL", f"{long_summary.median_pnl_pct:+.3f}%", f"{short_summary.median_pnl_pct:+.3f}%"),
        ("Max DD", f"{long_summary.max_drawdown_pct:.2f}%", f"{short_summary.max_drawdown_pct:.2f}%"),
        ("Sharpe", f"{long_summary.sharpe_ratio:.2f}", f"{short_summary.sharpe_ratio:.2f}"),
        ("PF", f"{long_summary.profit_factor:.2f}", f"{short_summary.profit_factor:.2f}"),
        ("Ликвидаций", str(long_summary.n_liquidations), str(short_summary.n_liquidations)),
        ("Avg Hold h", f"{long_summary.avg_hold_hours:.1f}", f"{short_summary.avg_hold_hours:.1f}"),
        ("Avg Entries", f"{long_summary.avg_entries:.2f}", f"{short_summary.avg_entries:.2f}"),
    ]
    for r in rows:
        results_table.add_row(*r)
    console.print(results_table)
    console.print()

    stats = analyze_extremes(
        df,
        horizons_hours=cfg["horizons_hours"],
        symbol=symbol,
        timeframe=cfg["timeframe"],
        days=history_days,
    )
    hurst = compute_hurst_exponent(df["close"])
    console.print(f"Hurst: {hurst:.3f}")

    horizon = cfg["recommendation_horizon"]
    liq = assess_liquidation_risk(
        stats,
        leverage=cfg["leverage"],
        maintenance_margin_rate=cfg["maintenance_margin_rate"],
        horizon_h=horizon,
    )
    h_stats = stats.get(horizon)
    risk_table = Table(show_header=False, box=None, title="RISK")
    risk_table.add_column("Метрика", style="dim")
    risk_table.add_column("Значение", justify="right")
    risk_table.add_row(f"p95 Long DD ({horizon}h)", f"{h_stats.long.p95:.2f}%")
    risk_table.add_row(f"p95 Short DD ({horizon}h)", f"{h_stats.short.p95:.2f}%")
    risk_table.add_row("Liq distance", f"{liq.liq_distance_pct:.2f}%")
    risk_table.add_row("Buffer", f"{liq.buffer_pct:.2f}%")
    risk_table.add_row("Risk", liq.level.value)
    risk_table.add_row("Max safe lev", f"{liq.max_safe_leverage}x")
    console.print(risk_table)
    console.print()

    regime_stats = detect_regime(df, window=min(168, len(df) - 2))
    console.print(
        f"Regime: {regime_stats.current_regime.value} "
        f"(conf {regime_stats.confidence*100:.1f}%)\n"
    )

    long_m = _calc_side_metrics([r.pnl_pct for r in long_results])
    short_m = _calc_side_metrics([r.pnl_pct for r in short_results])
    metrics_table = Table(title="RISK-ADJUSTED", header_style="bold cyan")
    metrics_table.add_column("Метрика", style="dim")
    metrics_table.add_column("Long", justify="right", style="cyan")
    metrics_table.add_column("Short", justify="right", style="magenta")
    for key, label in [
        ("sharpe", "Sharpe"),
        ("sortino", "Sortino"),
        ("calmar", "Calmar"),
        ("half_kelly", "Half-Kelly"),
        ("cvar_5", "CVaR 5%"),
    ]:
        fmt = ".3f" if key != "cvar_5" else ".2f"
        suffix = "%" if key == "cvar_5" else ""
        metrics_table.add_row(
            label,
            f"{long_m[key]:{fmt}}{suffix}",
            f"{short_m[key]:{fmt}}{suffix}",
        )
    console.print(metrics_table)
    console.print()

    if long_results and short_results:
        hedge = compute_optimal_hedge_weights(
            np.array([r.pnl_pct for r in long_results]),
            np.array([r.pnl_pct for r in short_results]),
        )
        console.print(
            f"Hedge weights L/S: {hedge.long_weight*100:.1f}% / "
            f"{hedge.short_weight*100:.1f}% | Sharpe {hedge.sharpe_ratio:.3f}\n"
        )

    total_pnl = long_summary.total_pnl_pct + short_summary.total_pnl_pct
    total_liq = long_summary.n_liquidations + short_summary.n_liquidations
    score = 0
    if total_pnl > 0:
        score += 2
    if total_liq == 0:
        score += 2
    if long_m["sharpe"] > 0.5:
        score += 1
    if short_m["sharpe"] > 0.5:
        score += 1
    if liq.level.value == "SAFE":
        score += 2
    elif liq.level.value == "WARNING":
        score += 1

    grade = (
        "ОТЛИЧНО" if score >= 7 else
        "ХОРОШО" if score >= 5 else
        "УДОВЛ." if score >= 3 else
        "ПЛОХО"
    )
    console.print(f"[bold]Оценка: {grade} ({score}/8)[/bold]")
    console.print(f"Total PnL L+S: {total_pnl:+.2f}% | Liqs: {total_liq} | Risk: {liq.level.value}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Тест DCA-стратегии")
    parser.add_argument("symbol", nargs="?", default=None, help="Тикер, напр. ETHUSDT")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--symbol", dest="symbol_flag", default=None)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument(
        "--overlapping",
        action="store_true",
        help="Разрешить overlapping entries (по умолчанию non-overlap)",
    )
    args = parser.parse_args(argv)

    symbol = args.symbol_flag or args.symbol
    if symbol is None:
        # infer from config name
        cfg_name = Path(args.config).stem
        if "hype" in cfg_name.lower():
            symbol = "HYPEUSDT"
        elif "eth" in cfg_name.lower():
            symbol = "ETHUSDT"
        else:
            symbol = "ETHUSDT"

    return run_strategy_test(
        symbol=symbol,
        config_path=args.config,
        days=args.days,
        non_overlapping=not args.overlapping,
        step=args.step,
    )


if __name__ == "__main__":
    sys.exit(main())

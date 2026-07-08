"""Тест стратегии бота с текущими настройками из config/settings.yaml."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from volatility_calc.data_fetcher import fetch_ohlcv
from volatility_calc.backtest import simulate, summarize, coverage_to_ps
from volatility_calc.drawdown_analyzer import analyze_extremes, compute_hurst_exponent
from volatility_calc.liquidation import assess_liquidation_risk
from volatility_calc.dca_recommender import recommend_all, GridConfig, CurrentSettings
from volatility_calc.regime import detect_regime, adapt_parameters_to_regime
from volatility_calc.portfolio_optimizer import compute_optimal_hedge_weights
from trade_tracker.aggregator import aggregate


def load_config(path: str = "config/settings.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main():
    console = Console()
    
    console.print("\n[bold cyan]📊 ТЕСТ СТРАТЕГИИ БОТА[/bold cyan]\n")
    
    cfg = load_config()
    
    symbol = "ETHUSDT"
    console.print(f"[dim]Символ:[/dim] [bold]{symbol}[/bold]")
    console.print(f"[dim]Таймфрейм:[/dim] {cfg['timeframe']}")
    console.print(f"[dim]История:[/dim] {cfg['history_days']} дней")
    console.print(f"[dim]Плечо:[/dim] {cfg['leverage']}x\n")
    
    console.print("[yellow]Загрузка данных...[/yellow]")
    df = fetch_ohlcv(
        symbol,
        timeframe=cfg['timeframe'],
        days=cfg['history_days'],
        cache_dir=cfg['cache']['dir'],
        use_cache=True
    )
    console.print(f"[green]✓ Загружено {len(df)} свечей[/green]\n")
    
    console.print("[bold]Текущие настройки:[/bold]")
    cs = cfg['current_settings']
    
    settings_table = Table(show_header=False, box=None)
    settings_table.add_column("Параметр", style="dim")
    settings_table.add_column("Long", justify="right", style="cyan")
    settings_table.add_column("Short", justify="right", style="magenta")
    
    settings_table.add_row("Orders", str(cs['long']['orders']), str(cs['short']['orders']))
    settings_table.add_row("Coverage", f"{cs['long']['price_coverage']*100:.1f}%", f"{cs['short']['price_coverage']*100:.1f}%")
    settings_table.add_row("Price Scale", f"{cs['long']['price_scale']:.2f}", f"{cs['short']['price_scale']:.2f}")
    settings_table.add_row("Volume Scale", f"{cs['long']['volume_scale']:.2f}", f"{cs['short']['volume_scale']:.2f}")
    settings_table.add_row("Base Qty", str(cs['long']['base_qty']), str(cs['short']['base_qty']))
    settings_table.add_row("TP", f"{cs['tp']*100:.2f}%", f"{cs['tp']*100:.2f}%")
    
    console.print(settings_table)
    console.print()
    
    console.print("[yellow]Запуск backtest для LONG...[/yellow]")
    long_results = simulate(
        df,
        n_orders=cs['long']['orders'],
        price_scale=cs['long']['price_scale'],
        volume_scale=cs['long']['volume_scale'],
        tp_pct=cs['tp'],
        leverage=cfg['leverage'],
        maintenance_margin_rate=cfg['maintenance_margin_rate'],
        horizon_h=cfg['recommendation_horizon'],
        base_qty=cs['long']['base_qty'],
        step=1,
        side="long"
    )
    long_summary = summarize(long_results)
    console.print(f"[green]✓ Long: {long_summary.n_trades} сделок[/green]\n")
    
    console.print("[yellow]Запуск backtest для SHORT...[/yellow]")
    short_results = simulate(
        df,
        n_orders=cs['short']['orders'],
        price_scale=cs['short']['price_scale'],
        volume_scale=cs['short']['volume_scale'],
        tp_pct=cs['tp'],
        leverage=cfg['leverage'],
        maintenance_margin_rate=cfg['maintenance_margin_rate'],
        horizon_h=cfg['recommendation_horizon'],
        base_qty=cs['short']['base_qty'],
        step=1,
        side="short"
    )
    short_summary = summarize(short_results)
    console.print(f"[green]✓ Short: {short_summary.n_trades} сделок[/green]\n")
    
    console.print("[bold cyan]📈 РЕЗУЛЬТАТЫ BACKTEST[/bold cyan]\n")
    
    results_table = Table(show_header=True, header_style="bold cyan")
    results_table.add_column("Метрика", style="dim")
    results_table.add_column("Long", justify="right", style="cyan")
    results_table.add_column("Short", justify="right", style="magenta")
    
    results_table.add_row("Всего сделок", str(long_summary.n_trades), str(short_summary.n_trades))
    results_table.add_row("Win Rate", f"{long_summary.win_rate:.1f}%", f"{short_summary.win_rate:.1f}%")
    results_table.add_row("Total PnL", f"{long_summary.total_pnl_pct:+.2f}%", f"{short_summary.total_pnl_pct:+.2f}%")
    results_table.add_row("Avg PnL", f"{long_summary.avg_pnl_pct:+.3f}%", f"{short_summary.avg_pnl_pct:+.3f}%")
    results_table.add_row("Median PnL", f"{long_summary.median_pnl_pct:+.3f}%", f"{short_summary.median_pnl_pct:+.3f}%")
    results_table.add_row("Max PnL", f"{long_summary.max_pnl_pct:+.2f}%", f"{short_summary.max_pnl_pct:+.2f}%")
    results_table.add_row("Min PnL", f"{long_summary.min_pnl_pct:+.2f}%", f"{short_summary.min_pnl_pct:+.2f}%")
    results_table.add_row("Ликвидаций", str(long_summary.n_liquidations), str(short_summary.n_liquidations))
    results_table.add_row("Avg Hold (часов)", f"{long_summary.avg_hold_hours:.1f}", f"{short_summary.avg_hold_hours:.1f}")
    results_table.add_row("Avg Entries", f"{long_summary.avg_entries:.2f}", f"{short_summary.avg_entries:.2f}")
    
    console.print(results_table)
    console.print()
    
    console.print("[bold cyan]📊 РАСШИРЕННАЯ АНАЛИТИКА[/bold cyan]\n")
    
    console.print("[yellow]Анализ волатильности...[/yellow]")
    stats = analyze_extremes(
        df,
        horizons_hours=cfg['horizons_hours'],
        symbol=symbol,
        timeframe=cfg['timeframe'],
        days=cfg['history_days']
    )
    
    hurst = compute_hurst_exponent(df['close'])
    
    console.print(f"[green]✓ Hurst Exponent: {hurst:.3f}[/green]", end=" ")
    if hurst < 0.45:
        console.print("[green](Mean-reverting - DCA работает хорошо)[/green]")
    elif hurst > 0.55:
        console.print("[yellow](Trending - DCA работает хуже)[/yellow]")
    else:
        console.print("[dim](Random walk)[/dim]")
    console.print()
    
    liq = assess_liquidation_risk(
        stats,
        leverage=cfg['leverage'],
        maintenance_margin_rate=cfg['maintenance_margin_rate'],
        horizon_h=cfg['recommendation_horizon']
    )
    
    risk_table = Table(show_header=False, box=None)
    risk_table.add_column("Метрика", style="dim")
    risk_table.add_column("Значение", justify="right")
    
    risk_table.add_row("p95 Long DD (168h)", f"{stats.get(168).long.p95:.2f}%")
    risk_table.add_row("p95 Short DD (168h)", f"{stats.get(168).short.p95:.2f}%")
    risk_table.add_row("CVaR 95% Long", f"{stats.get(168).long.cvar_95:.2f}%")
    risk_table.add_row("CVaR 95% Short", f"{stats.get(168).short.cvar_95:.2f}%")
    risk_table.add_row("Расстояние до ликвидации", f"{liq.liq_distance_pct:.2f}%")
    risk_table.add_row("Buffer", f"{liq.buffer_pct:.2f}%")
    risk_table.add_row("Уровень риска", f"[bold]{liq.level.value}[/bold]")
    risk_table.add_row("Макс. безопасное плечо", f"{liq.max_safe_leverage}x")
    
    console.print(risk_table)
    console.print()
    
    console.print("[yellow]Анализ режима рынка...[/yellow]")
    regime_stats = detect_regime(df, window=168)
    
    regime_table = Table(show_header=False, box=None)
    regime_table.add_column("Режим", style="dim")
    regime_table.add_column("Вероятность", justify="right")
    
    regime_table.add_row(f"[bold]Текущий режим[/bold]", f"[bold]{regime_stats.current_regime.value}[/bold]")
    regime_table.add_row("Confidence", f"{regime_stats.confidence*100:.1f}%")
    
    for regime, prob in regime_stats.regime_probabilities.items():
        regime_table.add_row(f"  {regime.value}", f"{prob*100:.1f}%")
    
    console.print(regime_table)
    console.print()
    
    console.print("[yellow]Расчёт risk-adjusted метрик...[/yellow]")
    
    long_pnls = [r.pnl_pct for r in long_results]
    short_pnls = [r.pnl_pct for r in short_results]
    
    def calc_metrics(pnls, name):
        if not pnls:
            return {}
        
        arr = np.array(pnls)
        mean_pnl = np.mean(arr)
        std_pnl = np.std(arr)
        
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0
        
        downside = arr[arr < 0]
        downside_std = np.std(downside) if len(downside) > 0 else 0
        sortino = mean_pnl / downside_std if downside_std > 0 else 0
        
        cumsum = np.cumsum(arr)
        peak = np.maximum.accumulate(cumsum)
        drawdown = peak - cumsum
        max_dd = np.max(drawdown)
        calmar = mean_pnl / max_dd if max_dd > 0 else 0
        
        wins = arr[arr > 0]
        losses = arr[arr < 0]
        win_rate = len(wins) / len(arr) * 100
        avg_win = np.mean(wins) if len(wins) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0
        
        p = win_rate / 100
        q = 1 - p
        b = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        kelly = (p * b - q) / b if b > 0 else 0
        half_kelly = kelly / 2
        
        cvar_5 = np.mean(arr[arr <= np.percentile(arr, 5)]) if len(arr) > 20 else 0
        
        return {
            'sharpe': sharpe,
            'sortino': sortino,
            'calmar': calmar,
            'kelly': kelly,
            'half_kelly': half_kelly,
            'cvar_5': cvar_5,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
        }
    
    long_metrics = calc_metrics(long_pnls, "Long")
    short_metrics = calc_metrics(short_pnls, "Short")
    
    metrics_table = Table(show_header=True, header_style="bold cyan")
    metrics_table.add_column("Метрика", style="dim")
    metrics_table.add_column("Long", justify="right", style="cyan")
    metrics_table.add_column("Short", justify="right", style="magenta")
    
    metrics_table.add_row("Sharpe Ratio", f"{long_metrics['sharpe']:.3f}", f"{short_metrics['sharpe']:.3f}")
    metrics_table.add_row("Sortino Ratio", f"{long_metrics['sortino']:.3f}", f"{short_metrics['sortino']:.3f}")
    metrics_table.add_row("Calmar Ratio", f"{long_metrics['calmar']:.3f}", f"{short_metrics['calmar']:.3f}")
    metrics_table.add_row("Kelly Criterion", f"{long_metrics['kelly']:.3f}", f"{short_metrics['kelly']:.3f}")
    metrics_table.add_row("Half-Kelly (рекоменд.)", f"{long_metrics['half_kelly']:.3f}", f"{short_metrics['half_kelly']:.3f}")
    metrics_table.add_row("CVaR 5%", f"{long_metrics['cvar_5']:.2f}%", f"{short_metrics['cvar_5']:.2f}%")
    
    console.print(metrics_table)
    console.print()
    
    console.print("[yellow]Анализ hedge-эффективности...[/yellow]")
    
    hedge_alloc = compute_optimal_hedge_weights(
        np.array(long_pnls),
        np.array(short_pnls)
    )
    
    hedge_table = Table(show_header=False, box=None)
    hedge_table.add_column("Метрика", style="dim")
    hedge_table.add_column("Значение", justify="right")
    
    hedge_table.add_row("Оптимальный вес Long", f"{hedge_alloc.long_weight*100:.1f}%")
    hedge_table.add_row("Оптимальный вес Short", f"{hedge_alloc.short_weight*100:.1f}%")
    hedge_table.add_row("Expected Return", f"{hedge_alloc.expected_return:.3f}%")
    hedge_table.add_row("Expected Volatility", f"{hedge_alloc.expected_volatility:.3f}%")
    hedge_table.add_row("Sharpe Ratio (hedge)", f"{hedge_alloc.sharpe_ratio:.3f}")
    hedge_table.add_row("Nash Equilibrium", "✓" if hedge_alloc.nash_equilibrium else "✗")
    
    console.print(hedge_table)
    console.print()
    
    console.print("[bold cyan]💡 РЕКОМЕНДАЦИИ[/bold cyan]\n")
    
    recommendations = []
    
    if long_metrics['sharpe'] < 0.5:
        recommendations.append("⚠️  Long Sharpe < 0.5: низкая доходность с поправкой на риск")
    if short_metrics['sharpe'] < 0.5:
        recommendations.append("⚠️  Short Sharpe < 0.5: низкая доходность с поправкой на риск")
    
    if long_summary.n_liquidations > 0:
        recommendations.append(f"🚨 Long: {long_summary.n_liquidations} ликвидаций - увеличить coverage или снизить плечо")
    if short_summary.n_liquidations > 0:
        recommendations.append(f"🚨 Short: {short_summary.n_liquidations} ликвидаций - увеличить coverage или снизить плечо")
    
    if liq.level.value == "CRITICAL":
        recommendations.append("🚨 КРИТИЧЕСКИЙ риск ликвидации - немедленно снизить плечо!")
    elif liq.level.value == "WARNING":
        recommendations.append("⚠️  Высокий риск ликвидации - рассмотреть снижение плеча")
    
    if hurst > 0.6:
        recommendations.append("ℹ️  Рынок trending (H > 0.6) - DCA может быть менее эффективен")
    elif hurst < 0.4:
        recommendations.append("✓ Рынок mean-reverting (H < 0.4) - DCA работает хорошо")
    
    if long_metrics['half_kelly'] > 0:
        recommendations.append(f"💰 Kelly рекомендует {long_metrics['half_kelly']*100:.1f}% капитала на Long сделку")
    if short_metrics['half_kelly'] > 0:
        recommendations.append(f"💰 Kelly рекомендует {short_metrics['half_kelly']*100:.1f}% капитала на Short сделку")
    
    if regime_stats.current_regime.value == "trending_up":
        recommendations.append("📈 Режим: Trending Up - увеличить TP, снизить coverage")
    elif regime_stats.current_regime.value == "trending_down":
        recommendations.append("📉 Режим: Trending Down - увеличить coverage, снизить TP")
    elif regime_stats.current_regime.value == "high_volatility":
        recommendations.append("⚡ Режим: High Volatility - увеличить coverage значительно")
    elif regime_stats.current_regime.value == "mean_reverting":
        recommendations.append("✓ Режим: Mean Reverting - текущие параметры оптимальны")
    
    if not recommendations:
        recommendations.append("✓ Все метрики в норме - стратегия работает хорошо")
    
    for rec in recommendations:
        console.print(rec)
    
    console.print()
    
    console.print("[bold cyan]📋 ИТОГОВАЯ ОЦЕНКА[/bold cyan]\n")
    
    total_pnl = long_summary.total_pnl_pct + short_summary.total_pnl_pct
    total_liq = long_summary.n_liquidations + short_summary.n_liquidations
    
    score = 0
    if total_pnl > 0:
        score += 2
    if total_liq == 0:
        score += 2
    if long_metrics['sharpe'] > 0.5:
        score += 1
    if short_metrics['sharpe'] > 0.5:
        score += 1
    if liq.level.value == "SAFE":
        score += 2
    elif liq.level.value == "WARNING":
        score += 1
    
    max_score = 8
    
    if score >= 7:
        grade = "🟢 ОТЛИЧНО"
        color = "green"
    elif score >= 5:
        grade = "🟡 ХОРОШО"
        color = "yellow"
    elif score >= 3:
        grade = "🟠 УДОВЛЕТВОРИТЕЛЬНО"
        color = "yellow"
    else:
        grade = "🔴 ПЛОХО"
        color = "red"
    
    console.print(f"[bold {color}]Оценка стратегии: {grade} ({score}/{max_score})[/bold {color}]\n")
    
    console.print(f"[dim]Total PnL (Long + Short): {total_pnl:+.2f}%[/dim]")
    console.print(f"[dim]Total Liquidations: {total_liq}[/dim]")
    console.print(f"[dim]Risk Level: {liq.level.value}[/dim]\n")
    
    console.print("[dim]Для детального анализа используйте:[/dim]")
    console.print("[dim]  python scripts/calc_volatility.py ETHUSDT[/dim]")
    console.print("[dim]  python scripts/backtest_long.py ETHUSDT[/dim]\n")


if __name__ == "__main__":
    main()

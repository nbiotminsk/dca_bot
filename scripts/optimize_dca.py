"""CLI: мультипараметрическая оптимизация DCA-стратегии.

Использование:
    python scripts/optimize_dca.py HYPEUSDT --days 180
    python scripts/optimize_dca.py HYPEUSDT --days 180 --json results/optimize_180.json

Формула DCA Fitness Score (DFS):
    Score = Profitability x Consistency x RiskAdjusted x Safety x Significance x Efficiency x 100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from volatility_calc.optimizer import (
    ParamGrid,
    run_optimization,
    best_per_side,
    sensitivity_analysis,
)
from rich.console import Console
from rich.table import Table


def _parse_csv(arg: str) -> tuple:
    return tuple(float(x) for x in arg.split(","))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Мультипараметрическая оптимизация DCA"
    )
    parser.add_argument("symbol", help="Тикер, напр. HYPEUSDT")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--leverage", type=int, default=1)
    parser.add_argument("--horizon", type=int, default=168)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--fee", type=float, default=0.04,
                        help="Комиссия %% за вход+выход (по умолчанию 0.04)")
    parser.add_argument("--orders", default="3,4,5,6,7,8",
                        help="Список orders через запятую")
    parser.add_argument("--coverages", default="0.20,0.30,0.40,0.50,0.60",
                        help="Список coverage через запятую")
    parser.add_argument("--volume-scales", default="1.0,1.03,1.05,1.08,1.10,1.12,1.15,1.20",
                        help="Список volume_scale через запятую")
    parser.add_argument("--tp", default="0.8,0.9,1.0",
                        help="Список TP %% через запятую")
    parser.add_argument("--sides", default="long,short",
                        help="Стороны (long,short)")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--top", type=int, default=15,
                        help="Сколько топ-конфигураций показывать")
    parser.add_argument("--json", dest="json_path", help="Сохранить все результаты в JSON")
    args = parser.parse_args(argv)

    grid = ParamGrid(
        orders=tuple(int(x) for x in args.orders.split(",")),
        coverages=_parse_csv(args.coverages),
        volume_scales=_parse_csv(args.volume_scales),
        tp_pcts=_parse_csv(args.tp),
        leverage=args.leverage,
        horizon_h=args.horizon,
        step=args.step,
        fee_pct=args.fee / 100.0,
        sides=tuple(args.sides.split(",")),
    )

    print(
        f"[INFO] {grid.n_combinations()} комбинаций | "
        f"{args.symbol} | {args.days}д | lev {args.leverage}x",
        file=sys.stderr,
    )
    print(f"[INFO] Загружаю OHLCV {args.symbol}...", file=sys.stderr)
    df = fetch_ohlcv(args.symbol, timeframe="1h", days=args.days,
                     cache_dir=args.cache_dir, use_cache=not args.no_cache)
    print(f"[INFO] {len(df)} свечей. Запускаю оптимизацию...", file=sys.stderr)

    results = run_optimization(df, grid)
    print(f"[INFO] Готово. {len(results)} конфигураций оценено.", file=sys.stderr)

    console = Console(width=200, force_terminal=True)

    for side, table_name in [("long", "LONG"), ("short", "SHORT")]:
        if side not in results["side"].values:
            continue

        top = results[results["side"] == side].head(args.top)

        tbl = Table(
            title=f"ТОП-{args.top} {table_name} | DCA Fitness Score | {args.symbol} {args.days}д",
            header_style="bold cyan",
        )

        cols = [
            "score", "n_orders", "coverage", "price_scale", "volume_scale",
            "tp_pct", "n_trades", "win_rate", "total_pnl", "avg_pnl",
            "min_pnl", "n_liquidations", "max_dd", "sharpe", "profit_factor",
            "avg_hold", "avg_entries",
        ]
        for c in cols:
            tbl.add_column(c, justify="right")

        for _, row in top.iterrows():
            style = "[bold green]" if row["total_pnl"] > 0 and row["n_liquidations"] == 0 else ""
            tbl.add_row(*[style + str(row[c]) for c in cols])

        console.print(tbl)
        console.print()

    for param in ("n_orders", "coverage", "volume_scale", "tp_pct"):
        sens = sensitivity_analysis(results, param)
        tbl = Table(title=f"ЧУВСТВИТЕЛЬНОСТЬ: {param}", header_style="bold magenta")
        for c in sens.columns:
            tbl.add_column(c, justify="right")
        for param_val, row in sens.iterrows():
            tbl.add_row(str(param_val), *[str(v) for v in row])
        console.print(tbl)
        console.print()

    if args.json_path:
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        results.to_json(args.json_path, orient="records", indent=2, force_ascii=False)
        print(f"[INFO] JSON: {args.json_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
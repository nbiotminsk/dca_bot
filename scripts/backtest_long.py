"""CLI: backtest DCA — перебор coverage и TP на реальной истории."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from volatility_calc.backtest import grid_search, coverage_to_ps
from rich.console import Console
from rich.table import Table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest DCA для перебора coverage/TP")
    parser.add_argument("symbol", help="Тикер, напр. HYPEUSDT")
    parser.add_argument("--side", choices=["long", "short"], default="long",
                        help="Сторона: long или short")
    parser.add_argument("--days", type=int, default=45, help="Глубина истории (по умолчанию 45)")
    parser.add_argument("--n-orders", type=int, default=3)
    parser.add_argument("--volume-scale", default="1.0,1.05,1.10,1.15,1.20,1.30,1.50",
                        help="Список volume_scale через запятую (мартингейл)")
    parser.add_argument("--leverage", type=int, default=2)
    parser.add_argument("--horizon", type=int, default=168,
                        help="Горизонт удержания позиции (ч)")
    parser.add_argument("--step", type=int, default=4,
                        help="Шаг точек входа (4 = каждый 4-й час)")
    parser.add_argument("--cov", default="0.18,0.24,0.27,0.30,0.35,0.40",
                        help="Список coverage через запятую")
    parser.add_argument("--tp", default="0.5,0.8,1.0,1.2,1.5,2.0,2.5,3.0",
                        help="Список TP %% через запятую")
    parser.add_argument("--json", dest="json_path", help="Сохранить таблицу в JSON")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--filter", default="none",
                        choices=["none", "ema_cross", "ema_price", "rsi",
                                  "ema_rsi", "ema_price_rsi"],
                        help="Trend-фильтр для входа (только для long)")
    parser.add_argument("--tp-type", default="fixed", choices=["fixed", "trailing"],
                        help="Тип TP: fixed (limit) или trailing (скользящий)")
    parser.add_argument("--trail", type=float, default=0.3,
                        help="Callback rate для trailing TP в % (по умолчанию 0.3%)")
    parser.add_argument("--fee", type=float, default=0.04,
                        help="Комиссия за цикл (вход+выход) в %% (по умолчанию 0.04%% = maker 0.02%% × 2)")
    args = parser.parse_args(argv)

    cov_values = [float(x) for x in args.cov.split(",")]
    tp_values = [float(x) for x in args.tp.split(",")]
    vs_values = [float(x) for x in args.volume_scale.split(",")]

    print(f"[INFO] Загружаю OHLCV {args.symbol}, {args.days} дней...", file=sys.stderr)
    df = fetch_ohlcv(args.symbol, timeframe="1h", days=args.days,
                      cache_dir=args.cache_dir, use_cache=True)
    print(f"[INFO] Получено {len(df)} свечей. Запускаю {args.side.upper()} backtest "
          f"(n_orders={args.n_orders}, lev={args.leverage}x, "
          f"horizon={args.horizon}h, step={args.step})...", file=sys.stderr)

    all_dfs = []
    for vs in vs_values:
        df_part = grid_search(
            df, cov_values=cov_values, tp_values=tp_values,
            n_orders=args.n_orders, volume_scale=vs,
            leverage=args.leverage, horizon_h=args.horizon, step=args.step,
            side=args.side, filter_mode=args.filter,
            tp_type=args.tp_type, trail_pct=args.trail / 100.0,
            fee_pct=args.fee / 100.0,
        )
        df_part["volume_scale"] = vs
        all_dfs.append(df_part)
    table_df = pd.concat(all_dfs, ignore_index=True)

    console = Console(width=160, force_terminal=True)
    filter_label = f" | filter={args.filter}" if args.filter != "none" else ""
    tp_label = f" | TP={args.tp_type}" + (f" trail={args.trail}%" if args.tp_type == "trailing" else "")
    fee_label = f" | fee={args.fee}%"
    rich_tbl = Table(title=f"BACKTEST {args.side.upper()}  {args.symbol}  |  {args.days}д  |  lev={args.leverage}x{filter_label}{tp_label}{fee_label}",
                     header_style="bold cyan")
    for col in ("coverage", "price_scale", "tp_pct", "volume_scale", "n_trades",
                "win_rate", "total_pnl", "avg_pnl", "min_pnl",
                "liquidations", "avg_hold_h", "avg_entries", "max_dd", "sharpe", "profit_factor"):
        rich_tbl.add_column(col, justify="right")
    for _, row in table_df.iterrows():
        style = "[bold green]" if row["total_pnl"] > 0 and row["liquidations"] == 0 else ""
        rich_tbl.add_row(*[style + str(row[c]) for c in
                            ("coverage", "price_scale", "tp_pct", "volume_scale",
                             "n_trades", "win_rate", "total_pnl", "avg_pnl",
                             "min_pnl", "liquidations",
                             "avg_hold_h", "avg_entries", "max_dd", "sharpe", "profit_factor")])
    console.print(rich_tbl)

    # Сводка топ-5
    best = table_df.sort_values("total_pnl", ascending=False).head(5)
    console.print()
    best_tbl = Table(title="ТОП-5 ПО TOTAL_PNL", header_style="bold green")
    for col in ("coverage", "price_scale", "tp_pct", "win_rate",
                "total_pnl", "avg_pnl", "liquidations", "max_dd", "sharpe"):
        best_tbl.add_column(col, justify="right")
    for _, row in best.iterrows():
        best_tbl.add_row(*[str(row[c]) for c in
                            ("coverage", "price_scale", "tp_pct",
                             "win_rate", "total_pnl", "avg_pnl",
                             "liquidations", "max_dd", "sharpe")])
    console.print(best_tbl)

    if args.json_path:
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        table_df.to_json(args.json_path, orient="records", indent=2, force_ascii=False)
        print(f"[INFO] JSON: {args.json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
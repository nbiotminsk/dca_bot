"""CLI: отчёт по сделкам + A/B + mae-coverage."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trade_tracker.storage import load_trades
from trade_tracker.aggregator import aggregate
from trade_tracker.comparator import compare_epochs, compare_with_config
from trade_tracker.models import BotSettingsSnapshot
from trade_tracker.report import (
    render_trade_table, render_mae_coverage_check,
    render_settings_timeline, render_epoch_comparison,
)


def _filter(trades, symbol=None, side=None, frm=None, to=None):
    out = trades
    if symbol:
        out = [t for t in out if t.symbol.upper() == symbol.upper()]
    if side:
        out = [t for t in out if t.side == side]
    if frm:
        frm_d = date.fromisoformat(frm)
        out = [t for t in out if t.date >= frm_d]
    if to:
        to_d = date.fromisoformat(to)
        out = [t for t in out if t.date <= to_d]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Отчёт по сделкам")
    parser.add_argument("--symbol")
    parser.add_argument("--side", choices=["long", "short"])
    parser.add_argument("--from", dest="frm")
    parser.add_argument("--to")
    parser.add_argument("--include-volatility", action="store_true",
                        help="Сравнение с историей из кеша (без сети)")
    parser.add_argument("--group-by", help="A/B группировка (напр. bot_long_coverage)")
    parser.add_argument("--mae-coverage-check", action="store_true")
    parser.add_argument("--show-settings-timeline", action="store_true")
    parser.add_argument("--json", dest="json_path", help="Сохранить в JSON")
    parser.add_argument("--csv", default="data/trades/journal.csv")
    parser.add_argument("--json-journal", default="data/trades/journal.json")
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args(argv)

    trades = load_trades(args.csv, args.json_journal)
    trades = _filter(trades, symbol=args.symbol, side=args.side,
                      frm=args.frm, to=args.to)

    if not trades:
        print("Сделок не найдено.", file=sys.stderr)
        return 1

    stats = aggregate(trades)
    render_trade_table(trades, stats)

    if args.show_settings_timeline:
        render_settings_timeline(trades)

    if args.mae_coverage_check:
        render_mae_coverage_check(trades)

    if args.group_by:
        cmp = compare_epochs(trades, args.group_by)
        render_epoch_comparison(cmp)

    if args.include_volatility:
        try:
            current_settings = BotSettingsSnapshot.from_config(args.config)
            report = compare_with_config(trades, current_settings, historical_stats=stats)
            print()
            print("── СРАВНЕНИЕ С CONFIG ─────────────────────")
            if report.matches_config:
                print("  Настройки последней сделки совпадают с config.")
            else:
                for d in report.diffs:
                    print(f"  [diff] {d['field']}: сделка={d['current']} config={d['config']}")
            if report.vs_history:
                print(f"  vs history: pnl_pct={report.vs_history['trade_pnl_pct']} "
                      f"avg_history={report.vs_history['history_avg_pnl']} "
                      f"win_rate={report.vs_history['history_win_rate']}%")
        except FileNotFoundError:
            print("[WARN] config не найден; сравнение пропущено", file=sys.stderr)

    if args.json_path:
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        out = {
            "n_trades": stats.n_trades,
            "win_rate": stats.win_rate,
            "total_pnl": str(stats.total_pnl),
            "avg_pnl": str(stats.avg_pnl),
            "dca_distribution": stats.dca_distribution,
            "trades": [t.to_dict() for t in trades],
        }
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print(f"[INFO] JSON-отчёт: {args.json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
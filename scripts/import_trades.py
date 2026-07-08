"""CLI: импорт CSV шаблона, заполнение bot_* дефолтами, sync из CSV."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from trade_tracker.models import BotSettingsSnapshot
from trade_tracker.storage import (
    load_trades_from_csv, write_csv, write_json,
    rebuild_json_from_csv, CSV_COLUMNS,
)


def _fill_bot_defaults(csv_path: str, config_path: str) -> None:
    """Заполнить пустые bot_* колонки из config/settings.yaml прямо в CSV."""
    bot = BotSettingsSnapshot.from_config(config_path)
    with open(csv_path, "r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    fieldnames = CSV_COLUMNS
    bot_map = {
        "bot_long_orders": bot.long_orders,
        "bot_long_coverage": str(bot.long_coverage),
        "bot_long_price_scale": str(bot.long_price_scale),
        "bot_long_volume_scale": str(bot.long_volume_scale),
        "bot_short_orders": bot.short_orders,
        "bot_short_coverage": str(bot.short_coverage),
        "bot_short_price_scale": str(bot.short_price_scale),
        "bot_short_volume_scale": str(bot.short_volume_scale),
        "bot_tp": str(bot.tp),
        "bot_leverage": bot.leverage,
        "bot_base_qty_long": str(bot.long_base_qty),
        "bot_base_qty_short": str(bot.short_base_qty),
    }
    for row in rows:
        for col in bot_map:
            if row.get(col, "") == "":
                row[col] = str(bot_map[col])
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _validate_csv(csv_path: str) -> None:
    """Жёсткая проверка: все 22 колонки присутствуют, bot_* не пустые."""
    with open(csv_path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    missing_cols = [c for c in CSV_COLUMNS if c not in fieldnames]
    if missing_cols:
        raise ValueError(f"В CSV нет обязательных колонок: {missing_cols}")
    bot_cols = [c for c in CSV_COLUMNS if c.startswith("bot_")]
    for i, row in enumerate(rows, start=2):
        empty = [c for c in bot_cols if row.get(c, "") == ""]
        if empty:
            raise ValueError(
                f"Строка {i}: пустые bot_* колонки: {empty}. "
                f"Используйте --fill-bot-defaults."
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт CSV сделок")
    parser.add_argument("path", nargs="?", help="CSV файл для импорта")
    parser.add_argument("--fill-bot-defaults", action="store_true",
                        help="Заполнить пустые bot_* из config")
    parser.add_argument("--no-fill", action="store_true",
                        help="Жёсткий режим: ругаться на пустые bot_*")
    parser.add_argument("--template", action="store_true",
                        help="Вывести шаблон CSV в stdout")
    parser.add_argument("--sync-from-csv", action="store_true",
                        help="Пересоздать JSON из CSV")
    parser.add_argument("--csv", default="data/trades/journal.csv")
    parser.add_argument("--json", default="data/trades/journal.json")
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args(argv)

    if args.template:
        from trade_tracker.storage import csv_template
        sys.stdout.write(csv_template())
        return 0

    if args.sync_from_csv:
        rebuild_json_from_csv(args.csv, args.json)
        print(f"[OK] JSON пересоздан из {args.csv} → {args.json}")
        return 0

    if not args.path:
        parser.error("укажите путь к CSV или используйте --template / --sync-from-csv")

    csv_path = args.path
    if args.fill_bot_defaults:
        _fill_bot_defaults(csv_path, args.config)

    try:
        _validate_csv(csv_path)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    trades = load_trades_from_csv(csv_path)
    write_json(trades, args.json)
    write_csv(trades, args.csv)
    print(f"[OK] Импортировано {len(trades)} сделок → {args.csv}, {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
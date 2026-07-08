"""CLI: добавление одной сделки в журнал."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from trade_tracker.models import Trade, TradeEntry, BotSettingsSnapshot
from trade_tracker.storage import save_trade, DuplicateTradeError


def _parse_entries(args) -> list[TradeEntry]:
    """Запись входов: либо через --entry (повторяемый, формат price:qty), либо 1 вход по avg."""
    if args.entry:
        entries = []
        for raw in args.entry:
            try:
                price_str, qty_str = raw.split(":")
                price = Decimal(price_str)
                qty = Decimal(qty_str)
                if price <= 0:
                    raise SystemExit(f"Цена должна быть положительной: {price}")
                if qty <= 0:
                    raise SystemExit(f"Количество должно быть положительным: {qty}")
                entries.append(TradeEntry(price, qty))
            except ValueError as e:
                raise SystemExit(f"Неверный формат --entry '{raw}': ожидается price:qty. Ошибка: {e}")
        return entries
    if args.avg_entry and args.total_qty:
        avg_price = Decimal(str(args.avg_entry))
        total_qty = Decimal(str(args.total_qty))
        if avg_price <= 0:
            raise SystemExit(f"Средняя цена должна быть положительной: {avg_price}")
        if total_qty <= 0:
            raise SystemExit(f"Общее количество должно быть положительным: {total_qty}")
        return [TradeEntry(avg_price, total_qty)]
    raise SystemExit("Укажите либо --entry price:qty (можно несколько), либо --avg-entry + --total-qty")


def _bot_from_args(args, config_path: str) -> BotSettingsSnapshot:
    if args.use_config_defaults:
        return BotSettingsSnapshot.from_config(config_path)
    overrides = {}
    if args.bot_long_orders is not None:
        overrides["long_orders"] = int(args.bot_long_orders)
    if args.bot_long_coverage is not None:
        overrides["long_coverage"] = Decimal(str(args.bot_long_coverage))
    if args.bot_long_price_scale is not None:
        overrides["long_price_scale"] = Decimal(str(args.bot_long_price_scale))
    if args.bot_long_volume_scale is not None:
        overrides["long_volume_scale"] = Decimal(str(args.bot_long_volume_scale))
    if args.bot_base_qty_long is not None:
        overrides["long_base_qty"] = Decimal(str(args.bot_base_qty_long))
    if args.bot_short_orders is not None:
        overrides["short_orders"] = int(args.bot_short_orders)
    if args.bot_short_coverage is not None:
        overrides["short_coverage"] = Decimal(str(args.bot_short_coverage))
    if args.bot_short_price_scale is not None:
        overrides["short_price_scale"] = Decimal(str(args.bot_short_price_scale))
    if args.bot_short_volume_scale is not None:
        overrides["short_volume_scale"] = Decimal(str(args.bot_short_volume_scale))
    if args.bot_base_qty_short is not None:
        overrides["short_base_qty"] = Decimal(str(args.bot_base_qty_short))
    if args.bot_tp is not None:
        overrides["tp"] = Decimal(str(args.bot_tp))
    if args.bot_leverage is not None:
        overrides["leverage"] = int(args.bot_leverage)

    base = BotSettingsSnapshot.from_config(config_path)
    return _merge_bot(base, overrides)


def _merge_bot(base: BotSettingsSnapshot, overrides: dict) -> BotSettingsSnapshot:
    fields = base.to_dict()
    mapping = {
        "long_orders": "long_orders", "long_coverage": "long_coverage",
        "long_price_scale": "long_price_scale", "long_volume_scale": "long_volume_scale",
        "long_base_qty": "long_base_qty", "short_orders": "short_orders",
        "short_coverage": "short_coverage", "short_price_scale": "short_price_scale",
        "short_volume_scale": "short_volume_scale", "short_base_qty": "short_base_qty",
        "tp": "tp", "leverage": "leverage",
    }
    for cli_key, internal in mapping.items():
        if cli_key in overrides:
            if internal == "long_orders" or internal == "short_orders" or internal == "leverage":
                fields[internal] = int(overrides[cli_key])
            else:
                fields[internal] = str(overrides[cli_key])
    return BotSettingsSnapshot.from_dict(fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Добавить сделку в журнал")
    parser.add_argument("symbol")
    parser.add_argument("side", choices=["long", "short"])
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--entry", action="append",
                        help="price:qty (можно несколько — DCA входы)")
    parser.add_argument("--avg-entry", help="Средняя цена входа (для упрощённого ввода)")
    parser.add_argument("--total-qty", help="Общее кол-во (для упрощённого ввода)")
    parser.add_argument("--exit-price", required=True)
    parser.add_argument("--fees", help="Комиссии USDT")
    parser.add_argument("--mae", help="MAE в %")
    parser.add_argument("--no-mae", action="store_true", help="Явно указать, что MAE нет")
    parser.add_argument("--bot-long-coverage")
    parser.add_argument("--bot-long-orders")
    parser.add_argument("--bot-long-price-scale")
    parser.add_argument("--bot-long-volume-scale")
    parser.add_argument("--bot-base-qty-long")
    parser.add_argument("--bot-short-coverage")
    parser.add_argument("--bot-short-orders")
    parser.add_argument("--bot-short-price-scale")
    parser.add_argument("--bot-short-volume-scale")
    parser.add_argument("--bot-base-qty-short")
    parser.add_argument("--bot-tp")
    parser.add_argument("--bot-leverage")
    parser.add_argument("--use-config-defaults", action="store_true",
                        help="Взять все bot_* из config/settings.yaml")
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--csv", default="data/trades/journal.csv")
    parser.add_argument("--json", default="data/trades/journal.json")
    parser.add_argument("--notes", default="")
    parser.add_argument("--yes", action="store_true", help="Без подтверждения")
    args = parser.parse_args(argv)

    entries = _parse_entries(args)
    bot = _bot_from_args(args, args.config)

    print("── СНИМОК НАСТРОЕК ─────────────────────────")
    for k, v in bot.to_dict().items():
        print(f"  {k}: {v}")
    if not args.yes:
        confirm = input("Сохранить? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Отменено.", file=sys.stderr)
            return 1

    mae = None if args.no_mae else (Decimal(str(args.mae)) if args.mae else None)
    
    exit_price = Decimal(str(args.exit_price))
    if exit_price <= 0:
        print(f"[ERROR] Цена выхода должна быть положительной: {exit_price}", file=sys.stderr)
        return 2
    
    fees = None
    if args.fees:
        fees = Decimal(str(args.fees))
        if fees < 0:
            print(f"[ERROR] Комиссии не могут быть отрицательными: {fees}", file=sys.stderr)
            return 2
    
    trade = Trade(
        date=date.fromisoformat(args.date),
        symbol=args.symbol.upper(),
        side=args.side,
        entries=entries,
        exit_price=exit_price,
        fees_paid=fees,
        mae_pct=mae,
        notes=args.notes,
        bot=bot,
    )
    try:
        save_trade(trade, args.csv, args.json)
    except DuplicateTradeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(f"[OK] Сделка сохранена: {trade.key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
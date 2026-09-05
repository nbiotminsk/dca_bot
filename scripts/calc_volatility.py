"""CLI: расчёт волатильности и DCA-рекомендации."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from volatility_calc.data_fetcher import fetch_ohlcv, SymbolNotFoundError
from volatility_calc.drawdown_analyzer import analyze_extremes
from volatility_calc.liquidation import assess_liquidation_risk
from volatility_calc.dca_recommender import (
    recommend_all, GridConfig, CurrentSettings,
)
from volatility_calc.report import render_volatility_report


def load_config(path: str = "config/settings.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _current_from_config(cfg: dict) -> tuple[CurrentSettings, CurrentSettings]:
    cs = cfg["current_settings"]
    return (
        CurrentSettings(
            orders=cs["long"]["orders"],
            coverage=cs["long"]["price_coverage"],
            price_scale=cs["long"]["price_scale"],
            volume_scale=cs["long"]["volume_scale"],
            base_qty=cs["long"]["base_qty"],
        ),
        CurrentSettings(
            orders=cs["short"]["orders"],
            coverage=cs["short"]["price_coverage"],
            price_scale=cs["short"]["price_scale"],
            volume_scale=cs["short"]["volume_scale"],
            base_qty=cs["short"]["base_qty"],
        ),
    )


def _grid_config_from_yaml(cfg: dict) -> GridConfig:
    dca = cfg.get("dca", {})
    thresholds = dca.get("volume_scale_thresholds", {})
    return GridConfig(
        orders_range=tuple(dca.get("orders_range", (3, 7))),
        price_scale_range=tuple(dca.get("price_scale_range", (1.1, 1.5))),
        safety_factor=cfg.get("safety_factor", 1.2),
        volume_scale_conservative=thresholds.get("conservative", 2.0),
        volume_scale_moderate=thresholds.get("moderate", 1.5),
        tp_horizon_h=24,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Расчёт волатильности и DCA-рекомендации")
    parser.add_argument("symbol", help="Тикер, напр. ETHUSDT")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--tf", default=None, help="Таймфрейм (1h)")
    parser.add_argument("--horizons", default=None, help="24,72,168")
    parser.add_argument("--safety", type=float, default=None)
    parser.add_argument("--leverage", type=int, default=None)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="Сохранить отчёт в JSON")
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    days = args.days or cfg.get("history_days", 90)
    timeframe = args.tf or cfg.get("timeframe", "1h")
    horizons = (args.horizons.split(",")
                if args.horizons else cfg.get("horizons_hours", [24, 72, 168]))
    horizons = [int(h) for h in horizons]
    safety = args.safety if args.safety is not None else cfg.get("safety_factor", 1.2)
    leverage = args.leverage if args.leverage is not None else cfg.get("leverage", 2)
    mmr = cfg.get("maintenance_margin_rate", 0.005)
    rec_horizon = cfg.get("recommendation_horizon", 168)
    cache_dir = cfg.get("cache", {}).get("dir", "data/cache")

    try:
        df = fetch_ohlcv(args.symbol, timeframe=timeframe, days=days,
                          cache_dir=cache_dir, use_cache=not args.no_cache)
    except SymbolNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    grid_cfg = _grid_config_from_yaml(cfg)
    grid_cfg = GridConfig(
        orders_range=grid_cfg.orders_range,
        price_scale_range=grid_cfg.price_scale_range,
        price_scale_step=grid_cfg.price_scale_step,
        safety_factor=safety,
        volume_scale_conservative=grid_cfg.volume_scale_conservative,
        volume_scale_moderate=grid_cfg.volume_scale_moderate,
        tp_horizon_h=grid_cfg.tp_horizon_h,
    )

    stats = analyze_extremes(df, horizons_hours=horizons,
                              symbol=args.symbol.upper(), timeframe=timeframe, days=days)
    liq = assess_liquidation_risk(stats, leverage=leverage,
                                    maintenance_margin_rate=mmr,
                                    horizon_h=rec_horizon)
    current = _current_from_config(cfg)
    rec = recommend_all(stats, grid_cfg, current, horizon_h=rec_horizon, df=df)

    render_volatility_report(stats, liq, rec, current)

    if args.json_path:
        out = _serialize(stats, liq, rec, current)
        Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print(f"[INFO] JSON-отчёт сохранён: {args.json_path}", file=sys.stderr)
    return 0


def _serialize(stats, liq, rec, current) -> dict[str, Any]:
    cur_long, cur_short = current
    return {
        "symbol": stats.symbol,
        "timeframe": stats.timeframe,
        "days": stats.days,
        "n_candles": stats.n_candles,
        "horizons": [
            {
                "horizon_h": h.horizon_h,
                "long": h.long.__dict__,
                "short": h.short.__dict__,
                "long_above_thresholds": h.long_above_thresholds,
                "short_above_thresholds": h.short_above_thresholds,
            }
            for h in stats.horizons
        ],
        "liquidation": {
            "leverage": liq.leverage,
            "liq_distance_pct": liq.liq_distance_pct,
            "p99_long_dd": liq.p99_long_dd,
            "p99_short_dd": liq.p99_short_dd,
            "buffer_pct": liq.buffer_pct,
            "level": liq.level.value,
            "max_safe_leverage": liq.max_safe_leverage,
            "max_safe_leverage_buffer_pct": liq.max_safe_leverage_buffer_pct,
        },
        "recommendation": {
            "horizon_used": rec.horizon_used,
            "long": {
                "orders": rec.long.orders,
                "coverage": rec.long.coverage,
                "price_scale": rec.long.price_scale,
                "volume_scale": rec.long.volume_scale,
            },
            "short": {
                "orders": rec.short.orders,
                "coverage": rec.short.coverage,
                "price_scale": rec.short.price_scale,
                "volume_scale": rec.short.volume_scale,
            },
            "tp": rec.tp,
            "rationale": rec.rationale,
        },
        "current": {
            "long": cur_long.__dict__,
            "short": cur_short.__dict__,
        },
    }


if __name__ == "__main__":
    sys.exit(main())

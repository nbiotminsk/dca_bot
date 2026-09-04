# Hedge DCA Volatility Analyzer & Trade Tracker

Standalone-инструмент из двух модулей:

- **`volatility_calc/`** — волатильность, backtest DCA, оптимизация, regime detection (Bybit Linear Futures).
- **`trade_tracker/`** — журнал реальных сделок с per-trade метриками, A/B сравнением эпох.

## Установка

```bash
pip install -r requirements.txt
# или
pip install -e ".[dev]"
```

## Волатильность

```bash
python scripts/calc_volatility.py ETHUSDT
python scripts/calc_volatility.py HYPEUSDT --days 90 --leverage 2 --json results/hype.json
```

## Backtest / тест стратегии

По умолчанию **non-overlapping** сделки (следующий entry только после exit).

```bash
python scripts/test_strategy.py ETHUSDT
python scripts/test_strategy.py --config config/settings_hype.yaml --symbol HYPEUSDT
python scripts/test_strategy.py ETHUSDT --days 180 --overlapping   # старый режим
python scripts/backtest_long.py ETHUSDT
```

## Оптимизация DCA

```bash
python scripts/optimize_dca.py HYPEUSDT --days 180
python scripts/optimize_dca.py HYPEUSDT --days 180 --walk-forward 4 --json results/opt.json
```

## Сделки

```bash
python scripts/add_trade.py ETHUSDT long 2026-01-15 --exit-price 2450 --fees 1.85 \
    --bot-long-coverage 0.18 --bot-long-orders 5
python scripts/import_trades.py --template > trades_template.csv
python scripts/import_trades.py trades.csv --fill-bot-defaults
python scripts/trade_report.py --group-by bot_long_coverage --mae-coverage-check
```

## Ключевые улучшения симулятора

- Non-overlapping trades (по умолчанию)
- Liq price пересчитывается после DCA-fill
- Комиссии от notional in+out; опциональный funding
- Short: filter / trailing / dynamic TP (паритет с long)
- Equity curve с compound
- Walk-forward OOS в `optimize_dca.py --walk-forward N`
- Cache TTL (6ч) + проверка покрытия `days`

## Отчеты и исследования стратегии

- [Отчет по ТОП-30 монетам за 6 месяцев (Dual Grid + MACD)](docs/coins/TOP_30_COINS_6M_COMPARISON.md)
- [Сравнительный отчет 12 монет (90 дней)](docs/coins/12_COINS_COMPARISON_90D.md)

См. `PLAN.md` / `ARCHITECTURE_PLAN.md` для архитектуры.

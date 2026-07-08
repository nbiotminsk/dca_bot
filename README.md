# Hedge DCA Volatility Analyzer & Trade Tracker

Standalone-инструмент из двух модулей:

- **`volatility_calc/`** — волатильность и DCA-рекомендация по торговой паре (Bybit Linear Futures).
- **`trade_tracker/`** — журнал реальных сделок с per-trade метриками, A/B сравнением эпох и сравнением с историей.

## Установка

```bash
pip install -r requirements.txt
# или
pip install -e .
```

## Волатильность

```bash
python scripts/calc_volatility.py ETHUSDT
python scripts/calc_volatility.py HYPEUSDT --days 90 --leverage 2 --json results/eth.json
```

## Сделки

```bash
python scripts/add_trade.py ETHUSDT long 2026-01-15 --exit-price 2450 --fees 1.85 \
    --bot-long-coverage 0.18 --bot-long-orders 5
python scripts/import_trades.py --template > trades_template.csv
python scripts/import_trades.py trades.csv --fill-bot-defaults
python scripts/trade_report.py --group-by bot_long_coverage --mae-coverage-check
```

См. `PLAN.md` для полного описания архитектуры.
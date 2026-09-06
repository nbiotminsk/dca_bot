# DCA Bot — быстрый контекст для AI-агентов

Этот файл — короткая навигация для задач в этом репозитории. Не сканируй проект целиком до начала работы: сначала выбери файлы по маршруту ниже, затем ищи конкретные символы только в них. Расширяй область поиска лишь по импортам, вызовам или если задача явно является рефакторингом всей системы.

## Карта изменений

| Если нужно… | Начни с | Обычно проверь |
| --- | --- | --- |
| изменить CLI, первичный запуск, список монет или цикл | `scripts/bybit_trader.py` | `config/trade_config.yaml`, `tests/test_bybit_trader.py` |
| изменить поиск импульса, Fib, ATR, MACD или тип сетапа | `scripts/trader/setup_scanner.py` | `scripts/backtest_strategy_interactive.py`, `tests/test_bybit_trader.py`, `tests/test_manipulation_strategy.py` |
| изменить жизненный цикл позиции, трейлинг, TP/SL | `scripts/trader/state_machine.py` | `scripts/trader/models.py`, `scripts/trader/order_manager.py`, `tests/test_bybit_trader.py` |
| изменить создание, поиск или отмену ордеров | `scripts/trader/order_manager.py` | `indicators/pybit_client.py`, `tests/test_bybit_trader.py` |
| изменить API Bybit, округление, лоты, риск или маржу | `indicators/pybit_client.py` | `scripts/trader/state_machine.py`, `tests/test_risk_guard.py` |
| изменить параметры стратегии | `config/trade_config.yaml` | `scripts/trader/config.py`, `tests/test_bybit_trader.py` |
| изменить журнал завершённых импульсов | `scripts/trader/trade_journal.py` | `scripts/trader/setup_scanner.py`, `scripts/trader/state_machine.py` |
| изменить бэктест | `scripts/backtest_strategy_interactive.py` | `scripts/strategy_engine.py`, соответствующие `tests/test_*` |
| изменить учёт сделок | `trade_tracker/` | `tests/test_{storage,calculator,aggregator,comparator}.py` |
| изменить анализ риска / волатильности | `volatility_calc/` | одноимённый тест в `tests/` |

Полная справочная карта: `PROJECT_STRUCTURE.md`. Подробная стратегия и контекст Bybit: `AGENT.md`; открывай только если задача затрагивает торговую логику или API.

## Рабочий протокол

1. Назови 1–3 целевых файла из таблицы и найди нужную функцию через `rg`.
2. Прочитай только функцию, её прямые вызовы и связанный тест.
3. Внеси минимальное изменение, не дублируя стратегическую логику между scanner, state machine и backtest.
4. Запусти самый узкий тест, затем при изменениях в торговом контуре — `uv run pytest`.

## Инварианты торгового контура

- `scripts/bybit_trader.py` — оркестратор; стратегия находится в `scripts/trader/`.
- Нормализация символов — только через `format_symbol()`; Bybit API — `category="linear"`.
- Состояние сделки — `ActiveTradeMonitor`; не добавляй параллельные незафиксированные состояния.
- Отмена ордеров должна быть ограничена принадлежащими боту `orderLinkId`; не отменяй чужие ордера.
- Любое изменение расчёта лота, SL/TP или размещения ордера требует теста и dry-run; `--live` не запускай без явной команды пользователя.
- Не читай `.env` и не выводи ключи API.

## Команды

```bash
uv run pytest tests/test_bybit_trader.py -q
uv run pytest tests/test_risk_guard.py -q
uv run pytest -q
uv run python scripts/bybit_trader.py --dry-run
```

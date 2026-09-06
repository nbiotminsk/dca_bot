# DCA Bot — быстрый контекст для AI-агентов

Этот файл — короткая навигация для задач в этом репозитории. Не сканируй проект целиком до начала работы: сначала выбери файлы по маршруту ниже, затем ищи конкретные символы только в них. Расширяй область поиска лишь по импортам, вызовам или если задача явно является рефакторингом всей системы.

## Карта изменений

| Если нужно… | Начни с | Обычно проверь |
| --- | --- | --- |
| изменить CLI, первичный запуск, список монет или цикл | `scripts/bybit_trader.py` | `config/trade_config.yaml`, `tests/test_bybit_trader.py` |
| изменить поиск импульса, Fib, ATR, MACD или тип сетапа | `scripts/trader/setup_scanner.py` | `scripts/backtest_strategy_interactive.py`, `tests/test_bybit_trader.py`, `tests/test_manipulation_strategy.py` |
| изменить сетку, трейлинг, исполнение O1/O2/O3 | `scripts/trader/grid_states.py` | `scripts/trader/state_machine.py`, `tests/test_bybit_trader.py` |
| изменить логику свипа, ложного пробоя (Sweep Reclaim) | `scripts/trader/reclaim_states.py` | `scripts/trader/state_machine.py`, `tests/test_bybit_trader.py` |
| изменить сценарий Manipulation (1.414/1.618 Fib) | `scripts/trader/manipulation_states.py` | `scripts/trader/state_machine.py`, `tests/test_bybit_trader.py` |
| изменить IDLE, ожидание или перезапуск сетапов | `scripts/trader/idle_scanner.py` | `scripts/trader/setup_scanner.py`, `tests/test_bybit_trader.py` |
| изменить диспетчер жизненного цикла сделки | `scripts/trader/state_machine.py` | `scripts/trader/models.py`, `scripts/trader/order_manager.py`, `tests/test_bybit_trader.py` |
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
3. Считай уже прочитанный фрагмент актуальным в рамках текущей задачи. Не открывай один и тот же диапазон повторно и не запускай повторный поиск без новой причины. Повторное чтение допустимо лишь если файл изменился, нужен соседний вызов или прежний фрагмент был обрезан.
4. После нахождения точки изменения сразу составь краткую рабочую заметку: «файл → функция → что меняется → тест». Используй её вместо повторного обхода файлов.
5. Внеси минимальное изменение, не дублируя стратегическую логику между scanner, state machine и backtest.
6. Запусти самый узкий тест, затем при изменениях в торговом контуре — `uv run pytest`.

## Инварианты торгового контура

- `scripts/bybit_trader.py` — оркестратор; стратегия находится в `scripts/trader/`.
- Нормализация символов — только через `format_symbol()`; Bybit API — `category="linear"`.
- Состояние сделки — `ActiveTradeMonitor`; не добавляй параллельные незафиксированные состояния.
- Отмена ордеров должна быть ограничена принадлежащими боту `orderLinkId`; не отменяй чужие ордера.
- Любое изменение расчёта лота, SL/TP или размещения ордера требует теста и dry-run; `--live` не запускай без явной команды пользователя.
- При любых изменениях, связанных с Bybit API или методами `pybit` (параметры вызовов, endpoints, сигнатуры), обязательно сверяйся с актуальной документацией через MCP `context7` (`pybit`).
- Не читай `.env` и не выводи ключи API.

## Команды

```bash
uv run pytest tests/test_bybit_trader.py -q
uv run pytest tests/test_risk_guard.py -q
uv run pytest -q
uv run python scripts/bybit_trader.py --dry-run
```

# Hedge DCA Research Framework — Architecture Plan

> **Project goal**: количественно оценить, насколько использование DCA/Hedge-сеток улучшает или ухудшает результаты уже существующих торговых сигналов (Пифагор Trader).
> Сигналы — входная данность, не объект оптимизации.

---

## 1. Философия системы

- **SOLID**: каждый модуль — одна ответственность, расширение через Protocol/ABC.
- **Composition over inheritance**: сетка = композиция моделей (execution, fees, funding, slippage, liquidation).
- **Data-centric**: все параметры и конфиги — в `config/`, разделены по доменам.
- **Testability**: каждая модель изолирована, мокается, покрыта unit-тестами.
- **Reproducibility**: seed, версии данных, hash параметров в каждом отчёте.

---

## 2. Структура каталога

```
dca_bot/
├── pyproject.toml
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── config/
│   ├── settings.yaml
│   ├── exchanges/
│   │   └── bybit.yaml
│   ├── models/
│   │   ├── execution.yaml
│   │   ├── funding.yaml
│   │   ├── slippage.yaml
│   │   └── fees.yaml
│   ├── scoring.yaml
│   └── optimizer.yaml
│
├── data/
│   ├── raw/
│   │   ├── bybit/
│   │   └── signals/
│   ├── validated/
│   └── cache/
│
├── src/
│   └── dca_research/
│       ├── __init__.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── enums.py
│       │   ├── types.py
│       │   ├── value_objects.py
│       │   ├── events.py
│       │   ├── exceptions.py
│       │   └── protocols.py
│       ├── config/
│       │   ├── __init__.py
│       │   ├── loader.py
│       │   └── schemas.py
│       ├── data/
│       │   ├── __init__.py
│       │   ├── ingestion/
│       │   │   ├── ccxt_loader.py
│       │   │   ├── freqtrade_loader.py
│       │   │   └── signal_loader.py
│       │   ├── storage/
│       │   │   ├── feather_store.py
│       │   │   └── parquet_store.py
│       │   ├── validation/
│       │   │   ├── ohlc_validator.py
│       │   │   ├── signal_validator.py
│       │   │   ├── gap_detector.py
│       │   │   └── report.py
│       │   └── pipeline.py
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── grid/
│       │   │   ├── builder.py
│       │   │   ├── calculator.py
│       │   │   └── presets.py
│       │   ├── position/
│       │   │   ├── state.py
│       │   │   ├── lifecycle.py
│       │   │   └── margin.py
│       │   ├── liquidation/
│       │   │   ├── price.py
│       │   │   ├── buffer.py
│       │   │   └── checker.py
│       │   └── pnl/
│       │       ├── realized.py
│       │       ├── unrealized.py
│       │       └── funding.py
│       ├── models/
│       │   ├── __init__.py
│       │   ├── execution/
│       │   │   ├── base.py
│       │   │   ├── touch.py
│       │   │   ├── mid.py
│       │   │   ├── conservative.py
│       │   │   └── registry.py
│       │   ├── fees/
│       │   │   ├── base.py
│       │   │   ├── maker_taker.py
│       │   │   ├── mixed.py
│       │   │   ├── bybit_tiers.py
│       │   │   └── registry.py
│       │   ├── funding/
│       │   │   ├── base.py
│       │   │   ├── disabled.py
│       │   │   ├── fixed_rate.py
│       │   │   ├── historical.py
│       │   │   └── registry.py
│       │   └── slippage/
│       │       ├── base.py
│       │       ├── zero.py
│       │       ├── fixed_bps.py
│       │       ├── volume_aware.py
│       │       └── registry.py
│       ├── execution/
│       │   ├── __init__.py
│       │   ├── engine.py
│       │   ├── order_book.py
│       │   ├── matcher.py
│       │   ├── time_index.py
│       │   └── state_machine.py
│       ├── analysis/
│       │   ├── __init__.py
│       │   ├── metrics/
│       │   │   ├── base.py
│       │   │   ├── basic.py
│       │   │   ├── drawdown.py
│       │   │   ├── risk.py
│       │   │   ├── liquidation.py
│       │   │   └── expectancy.py
│       │   ├── scoring/
│       │   │   ├── weights.py
│       │   │   ├── normalizer.py
│       │   │   └── score.py
│       │   ├── bootstrap/
│       │   │   ├── base.py
│       │   │   ├── block.py
│       │   │   ├── historical.py
│       │   │   └── circular.py
│       │   ├── montecarlo/
│       │   │   ├── base.py
│       │   │   ├── trade_shuffler.py
│       │   │   ├── metrics.py
│       │   │   └── runner.py
│       │   └── comparison/
│       │       ├── signal_vs_grid.py
│       │       └── report.py
│       ├── optimization/
│       │   ├── __init__.py
│       │   ├── space.py
│       │   ├── grid_search.py
│       │   ├── walk_forward.py
│       │   ├── penalty.py
│       │   ├── parallel.py
│       │   └── selector.py
│       ├── visualization/
│       │   ├── __init__.py
│       │   ├── equity.py
│       │   ├── heatmap.py
│       │   ├── drawdown.py
│       │   ├── distribution.py
│       │   └── montecarlo.py
│       ├── reporting/
│       │   ├── __init__.py
│       │   ├── markdown.py
│       │   ├── html.py
│       │   └── export.py
│       ├── logging_setup.py
│       └── di.py
│
├── tests/
│   ├── unit/
│   │   ├── core/
│   │   ├── domain/
│   │   ├── models/
│   │   ├── execution/
│   │   ├── analysis/
│   │   └── optimization/
│   ├── integration/
│   │   ├── test_pipeline.py
│   │   ├── test_simulator.py
│   │   └── test_optimizer.py
│   ├── contracts/
│   │   └── test_model_protocols.py
│   ├── fixtures/
│   │   ├── sample_ohlcv.feather
│   │   └── sample_signals.csv
│   └── conftest.py
│
├── scripts/
│   ├── fetch_data.py
│   ├── validate_data.py
│   ├── dca_calculator.py
│   ├── run_simulation.py
│   ├── optimize.py
│   ├── bootstrap_analysis.py
│   ├── monte_carlo.py
│   ├── heatmap.py
│   ├── compare_with_signals.py
│   └── report.py
│
├── notebooks/
│   ├── 01_data_exploration.ipynb
│   ├── 02_signal_quality.ipynb
│   ├── 03_grid_anatomy.ipynb
│   ├── 04_hypothesis_tests.ipynb
│   └── 05_pythagor_analysis.ipynb
│
└── results/
    ├── runs/{run_id}/
    │   ├── config_snapshot.yaml
    │   ├── trades.parquet
    │   ├── metrics.json
    │   ├── score.json
    │   ├── bootstrap/
    │   ├── montecarlo/
    │   └── reports/
    └── aggregated/
```

---

## 3. Ключевые модули — назначение

### 3.1. `core/protocols.py` — контракты

```python
class ExecutionModel(Protocol):
    def should_fill(
        self,
        order_price: Price,
        candle: OHLC,
        tick_size: Price,
        spread: Price,
    ) -> FillDecision: ...

class FeeModel(Protocol):
    def compute(self, side: Side, order_type: OrderType, notional: Money) -> Money: ...

class FundingModel(Protocol):
    def accrual_between(self, ts_start: Timestamp, ts_end: Timestamp) -> list[FundingEvent]: ...

class SlippageModel(Protocol):
    def adjust_fill_price(self, requested: Price, side: Side, qty: Qty) -> Price: ...
```

### 3.2. `models/execution/` — модели исполнения

| Модель | Логика |
|--------|--------|
| `TouchModel` | Fill, если `low <= order_price` (long) / `high >= order_price` (short) |
| `MidModel` | Fill, если low+high пробивают уровень и close остаётся по другую сторону |
| `ConservativeModel` | Fill, если пробитие + `abs(close - order_price) >= min_penetration` |
| `TickAwareModel` | Учёт `tick_size` (округление) и `spread` (минимальная дистанция от mid) |

### 3.3. `models/fees/` — комиссии

- `MakerTakerFeeModel` — по `order_type` отдельно для entry / DCA / TP / Stop / liquidation
- `MixedFeeModel` — комбинированный (DCA как taker, TP как maker)
- `BybitTieredFeeModel` — тиры по 30-дневному объёму

### 3.4. `models/funding/`

- `DisabledFundingModel` — заглушка
- `FixedRateFundingModel` — постоянная ставка (для тестов)
- `HistoricalFundingModel` — реальные ставки из данных
- `InferredFundingModel` — прокси из price delta на funding-таймстампах

### 3.5. `models/slippage/`

- `ZeroSlippage`, `FixedBpsSlippage(bps)`, `VolumeAwareSlippage`

### 3.6. `domain/liquidation/`

```python
@dataclass(frozen=True)
class LiquidationSnapshot:
    liquidation_price: Price
    initial_margin: Money
    maintenance_margin: Money
    maintenance_margin_ratio: Decimal
    buffer_abs: Money
    buffer_pct: float
    min_buffer_pct: float
```

### 3.7. `analysis/scoring/` — Strategy Score

```python
class StrategyScore:
    def __init__(self, weights: ScoringWeights):
        self.w = weights
    def compute(self, m: Metrics) -> float:
        return (
            self.w.sharpe    * normalize(m.sharpe) +
            self.w.pf        * normalize(m.profit_factor) +
            self.w.expectancy* normalize(m.expectancy) +
            self.w.recovery  * normalize(-m.recovery_time) +
            self.w.max_dd    * normalize(-m.max_drawdown) +
            self.w.win_rate  * normalize(m.win_rate)
        )
```

### 3.8. `analysis/bootstrap/`

- `BlockBootstrap` — ресемплинг блоков фиксированной длины
- `HistoricalBootstrap` — вырезка реальных эпизодов волатильности
- `CircularBootstrap` — кольцевой сдвиг окна

### 3.9. `analysis/montecarlo/`

```python
class TradeMonteCarlo:
    def run(self, trades: list[Trade], n_permutations: int = 10_000, seed: int = 42) -> MonteCarloResult:
        # Перемешиваем ПОРЯДОК сделок, не цену
        ...
```

### 3.10. `optimization/`

- `GridSearch` — декартово произведение параметров
- `WalkForward` — rolling train/test
- `Penalty` — `score = score_test * (1 - penalty_factor * max(0, sharpe_drop - 0.3))`
- `ParallelRunner` — `multiprocessing.Pool(processes=cpu_count())`
- `Selector` — топ-N по StrategyScore

---

## 4. Зависимости (диаграмма слоёв)

```
┌─────────────────────────────────────────────────────┐
│  scripts/         (тонкие CLI)                       │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  reporting/  +  visualization/  (I/O слой)          │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  optimization/  +  analysis/  (use cases)           │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  execution/  +  domain/  (моделирование сделки)      │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  models/  (execution, fees, funding, slippage)       │
│  ← регистрируются через Protocol + Registry         │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  core/  (типы, события, протоколы)                  │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  data/  (ingestion, validation, storage)             │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│  config/  (YAML + pydantic, inject)                  │
└─────────────────────────────────────────────────────┘
```

---

## 5. Поток данных (Data Flow)

```
CCXT / CSV
    │
    ▼
ingestion/ → raw/ (feather)
    │
    ▼
validation/ → report (PASS/FAIL) → validated/
    │
    ▼
execution/engine.py
   ├─ читает signals.csv (manual Пифагор)
   ├─ читает OHLCV
   ├─ подключает: ExecutionModel, FeeModel, FundingModel, SlippageModel
   └─ гоняет симуляцию → trades.parquet
    │
    ▼
analysis/ (metrics, scoring, bootstrap, montecarlo)
    │
    ▼
optimization/ (loop over param space)
    │
    ▼
reporting/ + visualization/
    │
    ▼
results/runs/{run_id}/
```

---

## 6. Data Validation (этап 0)

| Категория | Проверка |
|-----------|----------|
| OHLC | `low <= open,close <= high` |
| OHLC | `high > 0`, `volume >= 0` |
| Свечи | уникальность ts |
| Свечи | сортировка по ts |
| Свечи | обнаружение gap > `max_gap_minutes` |
| Свечи | timezone = UTC |
| Сигналы | ts ∈ диапазону OHLCV |
| Сигналы | `entry > 0`, `side ∈ {long, short}` |
| Сигналы | уникальность `(ts, symbol, side)` |
| Формат | CSV парсится, обязательные колонки присутствуют |

Каждая проверка возвращает `ValidationIssue(severity, location, message, suggestion)`. `CRITICAL` блокирует запуск, `WARNING` пропускает с логом.

---

## 7. Расширяемость (добавление нового)

| Что добавить | Где | Действие |
|--------------|-----|----------|
| Новая execution-модель | `models/execution/new_one.py` | Реализовать `ExecutionModel`, зарегистрировать в `registry.py` |
| Новая биржа | `config/exchanges/new_exchange.yaml` + `models/fees/new_exchange.py` | Настроить fee tiers, MMR, funding interval |
| Новый источник данных | `data/ingestion/new_source.py` | Реализовать `DataSource` Protocol |
| Новый метод оптимизации | `optimization/new_method.py` | Реализовать `Optimizer` Protocol |
| Новая метрика | `analysis/metrics/new.py` | Реализовать `Metric` Protocol, добавить в `scoring.yaml` |
| Новый bootstrap | `analysis/bootstrap/new.py` | Реализовать `Bootstrap` Protocol |
| Новый визуализатор | `visualization/new.py` | Чистая функция `(data) -> Figure` |

**Ни в одном случае не нужно трогать существующие модули** → гарантия Open/Closed Principle.

---

## 8. Дополнительные архитектурные улучшения

### 8.1. Event Sourcing для сделок
Каждое изменение позиции = `PositionEvent(open/add/close)`. Trade — проекция событий. Позволяет:
- Восстановить любую сделку из лога
- Дебажить симулятор пошагово
- Строить альтернативные PnL-метрики без перезапуска

### 8.2. Кеш + versioning данных
- `data/cache/{config_hash}/{data_hash}/` — результаты расчётов
- При изменении `config` или данных — автоматическая инвалидация
- Ускоряет итерации оптимизации в 10–100x

### 8.3. Reproducibility manifest
Каждый `run` сохраняет:
- `config_snapshot.yaml` (хеш)
- `data_snapshot.json` (хеши файлов, диапазон дат, число свечей)
- `git_commit`
- `requirements_hash`
- `random_seed`
→ любой результат воспроизводим.

### 8.4. Контрактное тестирование моделей
`tests/contracts/test_model_protocols.py` — проверяет, что все зарегистрированные модели соответствуют Protocol.

### 8.5. Feature flags в конфиге
```yaml
features:
  funding: true
  slippage: true
  bootstrap: true
  monte_carlo: true
  parallel: true
```

### 8.6. Dependency Injection контейнер
`src/dca_research/di.py` — простой контейнер:
```python
container.bind(FeeModel, BybitTieredFeeModel(bybit_config))
container.bind(ExecutionModel, TouchModel(...))
```

### 8.7. Structured logging
`structlog` с JSON-выводом → парсится, агрегируется, отправляется в дашборд.

### 8.8. Адаптеры для источников сигналов
- `SignalSource` Protocol
- `CsvSignalSource`, `JsonlSignalSource`, `WebhookSignalSource`, `SyntheticSignalSource`

### 8.9. Benchmark suite
`tests/benchmark/` — проверка производительности execution engine.

### 8.10. Strict typing
- `mypy --strict` в CI
- `Decimal` для денег и цен
- `NewType` для `Price`, `Qty`, `Timestamp`, `Money`

---

## 9. Порядок реализации (12 спринтов)

1. Каркас: `pyproject.toml`, `core/`, `config/`, `logging_setup.py`
2. Data layer: `ingestion/`, `validation/`, `storage/`
3. Models: `execution/`, `fees/`, `funding/`, `slippage/` + registries
4. Domain: `grid/`, `position/`, `liquidation/`, `pnl/`
5. Execution engine: главный цикл, `matcher`, `order_book`
6. Analysis metrics + scoring: базовые метрики, drawdown, recovery, score
7. Bootstrap + Monte-Carlo
8. Optimization: grid search, walk-forward, penalty, parallel
9. Visualization + reporting
10. Freqtrade integration
11. Hypothesis testing
12. Pythagor comparison

---

## 10. Принятые решения

| # | Решение | Значение |
|---|---------|----------|
| 1 | Money тип | Hybrid: `Decimal` на границах, `float` в горячих циклах |
| 2 | DI контейнер | Свой минимальный (`di.py`) |
| 3 | Сигналы | CSV `ts,symbol,side,entry_price,exit_price,qty,confidence?,notes?` |
| 4 | Кеш | Файловый по `sha256(config|data|params)` |
| 5 | Тесты | `pytest` + `pytest-cov` |
| 6 | Логирование | `structlog` (JSON) |
| 7 | Версионирование | код в git, данные отдельно |
| 8 | Документация | `README.md` + docstrings |

---

## 11. Зависимости (`requirements.txt`)

```
# Core
python>=3.11
numpy>=1.26
pandas>=2.1
pyarrow>=14.0
polars>=0.20
pyyaml>=6.0
pydantic>=2.5

# Data
ccxt>=4.0
freqtrade[plot]>=2024.1

# Models / Numeric
scipy>=1.11
statsmodels>=0.14
numba>=0.58

# Visualization
matplotlib>=3.8
seaborn>=0.13

# Logging
structlog>=24.1

# Quality
pytest>=7.4
pytest-cov>=4.1
mypy>=1.7
ruff>=0.1
```

---

## 12. Сводка Protocol-интерфейсов

| Protocol | Реализации |
|----------|-----------|
| `ExecutionModel` | `Touch`, `Mid`, `Conservative`, `TickAware` |
| `FeeModel` | `MakerTaker`, `Mixed`, `BybitTiered`, `BinanceTiered` |
| `FundingModel` | `Disabled`, `Fixed`, `Historical`, `Inferred` |
| `SlippageModel` | `Zero`, `FixedBps`, `VolumeAware` |
| `DataSource` | `CCXT`, `Freqtrade`, `CSV`, `Parquet` |
| `SignalSource` | `CSV`, `JSONL`, `Webhook`, `Synthetic` |
| `Optimizer` | `GridSearch`, `WalkForward`, `RandomSearch` |
| `Bootstrap` | `Block`, `Historical`, `Circular` |
| `Metric` | `ProfitFactor`, `Sharpe`, `MaxDD`, `Recovery`, ... |
| `LiquidationPolicy` | `BybitStandard`, `OKXStandard`, `Conservative` |
| `Visualization` | `Equity`, `Heatmap`, `Drawdown`, `Distribution` |

---

## 13. Преимущества архитектуры

1. **Исследовательская гибкость**: добавление новой гипотезы = 1 файл.
2. **Научная воспроизводимость**: hash конфига + данных + параметров.
3. **Скорость итераций**: файловый кеш + параллелизм.
4. **Тестируемость**: Protocol + DI → изоляция.
5. **Production-ready**: type hints, mypy, structlog, tests.
6. **Соответствие ТЗ**: явно реализованы Execution, Funding, Fee, Liquidation, Scoring.
7. **Расширяемость**: новые биржи, источники, модели — без модификации.

---

## 14. Гипотезы для проверки (из книги)

| # | Гипотеза | Метод |
|---|----------|-------|
| 1 | TP 0.8% vs 1.0% | A/B, paired t-test, bootstrap CI |
| 2 | Coverage 18% vs 22% | A/B + срез по ATR |
| 3 | 5 Long vs 6 Long | A/B |
| 4 | EMA200-фильтр | 2×2 дизайн |
| 5 | Hedge vs only-L / only-S | ΔMaxDD, ΔPnL/маржа |

---

## 15. Метрики и Scoring

**Базовые метрики**:
- Profit Factor, Win Rate, Max Drawdown
- Средняя прибыль, средний убыток
- Среднее время сделки
- Количество Long / Short

**Расширенные** (по ТЗ):
- Sharpe, Sortino, Calmar
- Recovery Time (сделки до восстановления)
- Liquidation Buffer (min за сделку)
- Expectancy

**Strategy Score** (конфигурируется в `config/scoring.yaml`):
- Sharpe × weight_sharpe
- PF × weight_pf
- Expectancy × weight_expectancy
- Recovery × weight_recovery
- MaxDD × weight_max_dd
- WinRate × weight_win_rate

---

**Документ зафиксирован. Готов к старту реализации по Спринт 1: Каркас.**

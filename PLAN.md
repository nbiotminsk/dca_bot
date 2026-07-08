# Hedge DCA Volatility Analyzer & Trade Tracker

## 1. Цель проекта

Standalone-инструмент из двух модулей:

1. **`volatility_calc/`** — по вводу торговой пары (`ETHUSDT`, `HYPEUSDT`) рассчитывает:
   - волатильность (просадки long/short на горизонтах 24/72/168ч по реальным экстремумам)
   - рекомендацию DCA-сетки (coverage, orders, price_scale, volume_scale, TP)
   - риск ликвидации (buffer + максимальное безопасное плечо)
   - сравнение с текущими настройками из книги

2. **`trade_tracker/`** — журнал реальных сделок:
   - ввод через CLI или CSV
   - per-trade метрики (PnL, MAE, использование DCA)
   - агрегация по портфелю
   - A/B сравнение эпох с разными настройками
   - сравнение с историческим распределением

## 2. Принятые решения

| # | Решение | Значение |
|---|---|---|
| 1 | Scope | Standalone модули `volatility_calc/` + `trade_tracker/` (без `dca_research/`) |
| 2 | Данные волатильности | ccxt → Bybit Linear Futures |
| 3 | Таймфрейм / период | 1h / 90 дней |
| 4 | Горизонты просадки | 24ч, 72ч, 168ч (все три сразу) |
| 5 | Метрика просадки | Реальные экстремумы (max high/low после входа) |
| 6 | Output волатильности | Текстовая таблица + JSON |
| 7 | DCA-рекомендация | Полный набор: coverage + orders + price_scale + volume_scale + TP |
| 8 | Ликвидация | Рассчитывать buffer + max безопасное плечо |
| 9 | Неверный тикер | Жёсткая ошибка с подсказкой похожих |
| 10 | Ввод сделок | CLI интерактивный + CSV-файл (приоритет для ручной правки) |
| 11 | MAE в сделках | Автоматически из кеша / вручную флагом / пропуск |
| 12 | Сравнение с историей | Из кеша (без сетевых запросов) |
| 13 | Снимок бота в CSV | 21 колонка, включая 12 `bot_*` параметров |
| 14 | Хранение entries | Вариант A: плоский CSV + JSON-зеркало с полными entries |

## 3. Структура проекта

```
dca_bot/
├── pyproject.toml
├── requirements.txt
├── README.md
├── .gitignore
├── config/
│   └── settings.yaml
├── volatility_calc/
│   ├── __init__.py
│   ├── data_fetcher.py        # ccxt → Bybit linear futures, кеш parquet
│   ├── drawdown_analyzer.py   # multi-horizon расчёт long/short dd
│   ├── liquidation.py         # buffer + предупреждения
│   ├── dca_recommender.py     # coverage/orders/scale/TP
│   └── report.py              # rich-таблицы
├── trade_tracker/
│   ├── __init__.py
│   ├── models.py              # Trade, TradeEntry, BotSettingsSnapshot
│   ├── storage.py             # CSV (плоский) ↔ JSON (полный)
│   ├── calculator.py          # per-trade метрики + MAE
│   ├── aggregator.py          # агрегаты по списку
│   ├── comparator.py          # vs config + vs history + group_by_epoch
│   └── report.py              # все таблицы
├── data/
│   ├── cache/                 # OHLCV parquet
│   └── trades/
│       ├── journal.csv        # приоритетное хранилище (22 колонки)
│       └── journal.json       # зеркало с полными entries
├── results/                   # JSON-отчёты
├── scripts/
│   ├── calc_volatility.py     # CLI: волатильность + DCA-рекомендация
│   ├── add_trade.py           # CLI: интерактивный ввод одной сделки
│   ├── import_trades.py       # CLI: импорт CSV + шаблон
│   └── trade_report.py        # CLI: отчёт по сделкам + A/B
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   └── sample_ohlcv.csv
    ├── test_data_fetcher.py
    ├── test_drawdown_analyzer.py
    ├── test_liquidation.py
    ├── test_dca_recommender.py
    ├── test_models.py
    ├── test_storage.py
    ├── test_calculator.py
    ├── test_aggregator.py
    └── test_comparator.py
```

## 4. CSV — формат (22 колонки, включая notes)

```csv
date,symbol,side,entry_count,avg_entry,total_qty,exit_price,fees_paid,mae_pct,bot_long_orders,bot_long_coverage,bot_long_price_scale,bot_long_volume_scale,bot_short_orders,bot_short_coverage,bot_short_price_scale,bot_short_volume_scale,bot_tp,bot_leverage,bot_base_qty_long,bot_base_qty_short,notes
2026-01-15,ETHUSDT,long,3,2340.91,0.075,2450.00,1.85,-6.20,5,0.18,1.4,1.2,3,0.12,1.3,1.1,0.008,2,0.04,0.03,"откат после роста"
```

**Поля сделки** (9): `date, symbol, side, entry_count, avg_entry, total_qty, exit_price, fees_paid, mae_pct`
**Снимок бота** (12):
- Long: `bot_long_orders, bot_long_coverage, bot_long_price_scale, bot_long_volume_scale, bot_base_qty_long`
- Short: `bot_short_orders, bot_short_coverage, bot_short_price_scale, bot_short_volume_scale, bot_base_qty_short`
- Общие: `bot_tp, bot_leverage`
**Служебные** (1): `notes`

## 5. Ключевые модули

### 5.1. `volatility_calc/data_fetcher.py`
- Парсинг тикера: `ETHUSDT` → `ETH/USDT:USDT` (linear futures Bybit)
- Жёсткая валидация: запрос `exchange.load_markets()` → если символ не найден → `SymbolNotFoundError` с подсказкой похожих символов (`difflib.get_close_matches`)
- Пагинация: Bybit linear futures отдаёт до 1000 свечей за запрос → цикл с `since` параметром
- Кеш: `data/cache/bybit_{symbol.replace('/', '_').replace(':', '_')}_{timeframe}_{days}.parquet`
- Возврат `pd.DataFrame` с колонками `timestamp, open, high, low, close, volume` + `validate_ohlcv()`

### 5.2. `volatility_calc/drawdown_analyzer.py` — multi-horizon
```python
def analyze_extremes(
    df: pd.DataFrame,
    horizons_hours: list[int] = [24, 72, 168],
) -> MultiHorizonStats
```
- Для каждого горизонта `H`:
  - Векторизованный расчёт: `rolling_min(low, H)` и `rolling_max(high, H)` со сдвигом `-1`
  - **Long DD**: `(low_window - close) / close * 100` → массив отрицательных значений
  - **Short DD**: `(high_window - close) / close * 100` → массив положительных значений
- Агрегированные метрики для каждой стороны × каждого горизонта: mean, median, std, p90, p95, p99, max
- Доля свечей с dd > 5%, > 10%, > 15%

```python
@dataclass
class HorizonStats:
    horizon_h: int
    long: SideStats
    short: SideStats
    long_above_thresholds: dict[float, float]
    short_above_thresholds: dict[float, float]
```

### 5.3. `volatility_calc/liquidation.py`
```python
def assess_liquidation_risk(
    stats: MultiHorizonStats,
    leverage: int = 2,
    maintenance_margin_rate: float = 0.005,
) -> LiquidationAssessment
```
- Расстояние до ликвидации: `1/leverage - MMR` (для 2x = 49.5%)
- **Buffer** = расстояние до ликвидации − `p99_dd` (для 168ч)
- Если `buffer < 0` → `CRITICAL`, `buffer < 5%` → `WARNING`, иначе → `SAFE`
- Рекомендация leverage: максимальное плечо, при котором `buffer > 10%`

### 5.4. `volatility_calc/dca_recommender.py`
```python
def recommend_all(
    stats: MultiHorizonStats,
    config: GridConfig,
    current: CurrentSettings,
    horizon_h: int = 168,
) -> FullRecommendation
```

**Логика coverage + orders + price_scale**:
- Берём `p95_dd` для горизонта 168ч (worst case)
- **Coverage** = `p95_dd * safety_factor` (safety=1.2)
- Перебираем `n ∈ [3..7]`, `ps ∈ [1.1..1.5]` с шагом 0.05
- Для long: `actual_coverage = 1 - (1/ps)^(n-1)`
- Ищем минимальный `n` и `ps`, при которых `actual_coverage >= target_coverage`
- Предпочитаем меньше ордеров при прочих равных

**Логика volume_scale** (на основе `p99/p95`):
- хвост < 1.5 → scale=1.20
- хвост 1.5–2.0 → scale=1.15
- хвост > 2.0 → scale=1.10

**Логика TP**:
- Берём медиану положительных ходов `(close[t+H] - close[t]) / close[t]` за 24ч
- TP = `median_positive_move_24h * 1.2`
- Если TP < 0.5% → `WARNING: слишком мало для покрытия комиссий`
- Если TP > 2.0% → `WARNING: слишком жадно`

**Возвращает**:
```python
@dataclass
class FullRecommendation:
    long: GridRecommendation
    short: GridRecommendation
    tp: float
    horizon_used: int
    rationale: list[str]
```

### 5.5. `volatility_calc/report.py` — вывод
Текстовая панель через `rich`:

```
═══════════════════════════════════════════════════════════════════
  ВОЛАТИЛЬНОСТЬ И DCA-РЕКОМЕНДАЦИЯ  |  ETH/USDT:USDT
  Bybit Linear Futures  |  1h  |  90 дней (2160 свечей)
═══════════════════════════════════════════════════════════════════

  ── ПРОСАДКА LONG (%) ──────────────────────────────────────────
             24h           72h           168h
  mean:     -1.42         -2.81          -3.42
  ...
  >5%:      8.4%         23.1%          31.2%

  ── ПРОСАДКА SHORT (%) ─────────────────────────────────────────
  [та же структура]

  ── РИСК ЛИКВИДАЦИИ (плечо 2x) ────────────────────────────────
  Расстояние до ликвидации:  49.5%
  p99 long dd (168h):        14.21%
  p99 short dd (168h):       13.88%
  Buffer до ликвидации:      35.29%  [SAFE]
  Макс. безопасное плечо:    7x      (buffer = 9.7%)

  ── DCA-СЕТКА: ТЕКУЩАЯ vs РЕКОМЕНДАЦИЯ ─────────────────────────
                              СЕЙЧАС        РЕКОМЕНДАЦИЯ
  LONG
    Orders                    5             5
    Price Coverage            18.0%         10.5%
    Price Scale               1.40          1.40
    Volume Scale              1.20          1.20
  SHORT
    Orders                    3             4
    Price Coverage            12.0%         10.2%
    Price Scale               1.30          1.30
    Volume Scale              1.10          1.15
  TP
    Take Profit               0.80%         0.85%

  ── РЕЗЮМЕ ИЗМЕНЕНИЙ ──────────────────────────────────────────
  [LONG]  coverage 18% → 10.5%  (−7.5%) — p95 за 7д = 8.7%, запас ×1.2
  [SHORT] orders 3 → 4            — p95 short чуть выше, нужно больше усреднений
  [TP]    0.80% → 0.85%          — средний часовой возврат 0.12% × 7h ≈ 0.85%
  [B]     buffer 35% [SAFE]       — ликвидация не грозит при 2x
═══════════════════════════════════════════════════════════════════
```

### 5.6. `trade_tracker/models.py`
```python
@dataclass(frozen=True)
class TradeEntry:
    price: Decimal
    qty: Decimal

@dataclass(frozen=True)
class BotSettingsSnapshot:
    long_orders: int
    long_coverage: Decimal
    long_price_scale: Decimal
    long_volume_scale: Decimal
    long_base_qty: Decimal
    short_orders: int
    short_coverage: Decimal
    short_price_scale: Decimal
    short_volume_scale: Decimal
    short_base_qty: Decimal
    tp: Decimal
    leverage: int

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "BotSettingsSnapshot": ...
    @classmethod
    def from_config(cls, config_path: str = "config/settings.yaml") -> "BotSettingsSnapshot": ...

@dataclass
class Trade:
    date: date
    symbol: str
    side: Literal["long", "short"]
    entries: list[TradeEntry]      # полные данные (только в JSON)
    exit_price: Decimal
    fees_paid: Decimal | None = None
    mae_pct: Decimal | None = None
    notes: str = ""
    bot: BotSettingsSnapshot = field(...)   # обязательно
```

### 5.7. `trade_tracker/storage.py`
- **`journal.csv`** — 22 колонки, плоский, только агрегаты entries
- **`journal.json`** — зеркало с полными `entries`
- CSV генерируется из JSON (источник правды)
- Валидация: все 12 `bot_*` колонок обязательны, либо подставляются через `--fill-bot-defaults`
- Защита от дублей: `(date, symbol, side)` уже есть → предупреждение

```python
CSV_COLUMNS = [
    "date", "symbol", "side", "entry_count", "avg_entry", "total_qty",
    "exit_price", "fees_paid", "mae_pct",
    "bot_long_orders", "bot_long_coverage", "bot_long_price_scale", "bot_long_volume_scale",
    "bot_short_orders", "bot_short_coverage", "bot_short_price_scale", "bot_short_volume_scale",
    "bot_tp", "bot_leverage", "bot_base_qty_long", "bot_base_qty_short",
    "notes",
]
```

### 5.8. `trade_tracker/calculator.py`
**Per-trade метрики**:
- `avg_entry` — `Σ(price × qty) / Σ(qty)` (DCA-weighted)
- `total_qty` — `Σ(qty)`
- `notional_in/out` — вложено/получено USDT
- `gross_pnl` — `(exit - avg_entry) × total_qty` (знак инвертирован для short)
- `net_pnl` — `gross_pnl - fees_paid`
- `pnl_pct` — `gross_pnl / notional_in * 100`
- `dca_used` — `len(entries)`
- MAE — из кеша (если есть) или None
- `tp_efficiency` — `(exit - avg_entry) / (tp_target - avg_entry)`

### 5.9. `trade_tracker/aggregator.py`
- Win rate, средний/медианный PnL
- Распределение `dca_used` (гистограмма: сколько сделок использовали 1, 2, 3, ... ордеров)
- Средняя/максимальная MAE
- Cumulative PnL

### 5.10. `trade_tracker/comparator.py`
```python
def compare_with_config(trades, current_settings, historical_stats) -> ComparisonReport
def group_by_epoch(trades, field) -> dict[Any, EpochStats]
def compare_epochs(trades, field) -> EpochComparison
```

**A/B логика**:
- `group_by_epoch` группирует по полю (например, `bot_long_coverage`)
- В каждой группе: win rate, avg PnL, avg MAE
- Сравнение групп: дельты

### 5.11. `trade_tracker/report.py`
1. `render_single_trade(trade, metrics)` — карточка одной сделки
2. `render_trade_table(trades, aggregate)` — таблица всех сделок
3. `render_mae_coverage_check(trades)` — достаточность coverage в эпохах
4. `render_settings_timeline(trades)` — лог смены настроек во времени
5. `render_epoch_comparison(trades, field)` — A/B группировка

## 6. CLI скрипты

### 6.1. `scripts/calc_volatility.py`
```
python scripts/calc_volatility.py SYMBOL [OPTIONS]
  --days 90                период истории
  --tf 1h                  таймфрейм
  --horizons 24,72,168     горизонты (через запятую)
  --safety 1.2             множитель запаса к p95
  --leverage 2             текущее плечо
  --no-cache               игнорировать кеш
  --json PATH              сохранить отчёт
```

### 6.2. `scripts/add_trade.py`
```
python scripts/add_trade.py SYMBOL SIDE DATE [OPTIONS]
  --exit-price PRICE
  --fees USDT
  --mae PCT
  --no-mae
  --bot-long-coverage 0.15
  --bot-long-orders 6
  --bot-long-price-scale 1.5
  --bot-long-volume-scale 1.3
  --bot-base-qty-long 0.05
  --bot-short-coverage 0.10
  --bot-short-orders 4
  --bot-short-price-scale 1.4
  --bot-short-volume-scale 1.2
  --bot-base-qty-short 0.04
  --bot-tp 0.010
  --bot-leverage 3
  --notes "..."
```

**Перед сохранением** показывает снимок настроек и просит подтверждение.

### 6.3. `scripts/import_trades.py`
```
python scripts/import_trades.py [PATH]
  --fill-bot-defaults     заполнить пустые bot_* из config/settings.yaml
  --no-fill               строгий режим (по умолчанию)
  --template              вывести шаблон CSV в stdout
  --sync-from-csv         пересоздать JSON из CSV
```

### 6.4. `scripts/trade_report.py`
```
python scripts/trade_report.py [OPTIONS]
  --symbol ETHUSDT
  --side long|short
  --from 2026-01-01
  --to 2026-03-31
  --include-volatility              # сравнение с историей (только кеш)
  --group-by bot_long_coverage      # A/B группировка
  --mae-coverage-check              # проверка достаточности coverage
  --json PATH
```

## 7. Конфигурация `config/settings.yaml`

```yaml
exchange: bybit
market_type: linear
timeframe: 1h
history_days: 90
horizons_hours: [24, 72, 168]
safety_factor: 1.2
leverage: 2
maintenance_margin_rate: 0.005

dca:
  orders_range: [3, 7]
  price_scale_range: [1.1, 1.5]
  base_qty_pct_long: 0.04
  base_qty_pct_short: 0.03
  volume_scale_thresholds:
    conservative: 2.0
    moderate: 1.5

current_settings:                 # ← этот блок копируется в каждую сделку
  long:
    orders: 5
    price_coverage: 0.18
    price_scale: 1.4
    volume_scale: 1.2
    base_qty: 0.04
  short:
    orders: 3
    price_coverage: 0.12
    price_scale: 1.3
    volume_scale: 1.1
    base_qty: 0.03
  tp: 0.008

recommendation_horizon: 168

cache:
  enabled: true
  dir: data/cache

trades_journal:
  csv_path: data/trades/journal.csv
  json_path: data/trades/journal.json
```

## 8. Тесты

- `test_models.py` — `BotSettingsSnapshot.from_config`, валидация `Trade`
- `test_storage.py` — CSV↔JSON roundtrip, валидация 22 колонок, дубль `(date,symbol,side)`
- `test_calculator.py` — per-trade метрики, MAE из кеша / ручной / None
- `test_aggregator.py` — win rate, cumulative PnL, распределение DCA
- `test_comparator.py` — vs config, vs history, `group_by_epoch`, `compare_epochs`
- `test_data_fetcher.py` — парсинг тикера, жёсткая ошибка, кеш
- `test_drawdown_analyzer.py` — multi-horizon, синтетические ряды
- `test_liquidation.py` — buffer, SAFE/WARN/CRITICAL
- `test_dca_recommender.py` — coverage, orders, scale, TP, rationale

## 9. Зависимости

```
ccxt>=4.0
pandas>=2.1
numpy>=1.26
pyarrow>=14.0
pyyaml>=6.0
rich>=13.7
pytest>=7.4
```

## 10. Порядок реализации (18 шагов)

1. Каркас: `pyproject.toml` + `requirements.txt` + `.gitignore` + `config/settings.yaml` + `README.md`
2. `volatility_calc/data_fetcher.py` + тесты
3. `volatility_calc/drawdown_analyzer.py` + тесты
4. `volatility_calc/liquidation.py` + тесты
5. `volatility_calc/dca_recommender.py` + тесты
6. `volatility_calc/report.py`
7. `scripts/calc_volatility.py`
8. `trade_tracker/models.py` (+ `BotSettingsSnapshot`) + тесты
9. `trade_tracker/storage.py` (CSV 22 колонки + JSON) + тесты
10. `trade_tracker/calculator.py` + тесты
11. `trade_tracker/aggregator.py` + тесты
12. `trade_tracker/comparator.py` (+ epoch) + тесты
13. `trade_tracker/report.py`
14. `scripts/add_trade.py` (с переопределением bot_*)
15. `scripts/import_trades.py` (с `--template`, `--fill-bot-defaults`)
16. `scripts/trade_report.py` (с `--group-by`, `--mae-coverage-check`)
17. Smoke test: 3 сделки из книги + полный цикл (волатильность + A/B)
18. README с примерами

## 11. Out of scope

- Не строим `dca_research/` каркас из `ARCHITECTURE_PLAN.md`
- Не симулируем сделки, не делаем bootstrap/Monte-Carlo/оптимизацию
- Не интегрируем сигналы Пифагор и Freqtrade
- Не делаем walk-forward, A/B тесты с p-value (только описательное сравнение эпох)
- Не делаем сетевые запросы из `trade_report` (только из `add_trade` по явному согласию и из `calc_volatility`)

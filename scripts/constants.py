"""Общие константы для бэктестов стратегии двухордерной сетки."""

# Комиссии Bybit Futures (Maker / Taker)
FEE_MAKER = 0.0002
FEE_TAKER = 0.00055

# Максимальное количество свечей ожидания входа/выхода после импульса (720 = 30 дней на 1h)
MAX_HOLD_BARS = 720

# Количество месяцев в тестовом периоде (90 дней)
TEST_PERIOD_MONTHS = 3.0

# Все 12 монет портфеля (название, символ Bybit)
COINS_12: list[tuple[str, str]] = [
    ("CAKE",  "CAKEUSDT"),
    ("XRP",   "XRPUSDT"),
    ("GRAM",  "GRAMUSDT"),
    ("SUI",   "SUIUSDT"),
    ("UNI",   "UNIUSDT"),
    ("HYPE",  "HYPEUSDT"),
    ("LINK",  "LINKUSDT"),
    ("DOGE",  "DOGEUSDT"),
    ("AVAX",  "AVAXUSDT"),
    ("ICP",   "ICPUSDT"),
    ("NEAR",  "NEARUSDT"),
    ("ENA",   "ENAUSDT"),
]

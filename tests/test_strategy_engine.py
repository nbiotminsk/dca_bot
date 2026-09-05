import pandas as pd
from scripts.strategy_engine import (
    GridConfig,
    simulate_grid,
    simulate_manipulation_grid,
    trades_to_df,
    summarize_df,
    summarize,
)
from scripts.backtest_strategy_interactive import Impulse

def test_intrabar_entry_candle_no_fake_tp():
    """
    Проверка защиты от ложного тейка на свече входа:
    Если на свече входа High выше тейка, но свеча закрылась ниже тейка (красная),
    тейк не должен засчитываться на этой же свече.
    """
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 109.0, "low": 104.0, "open": 108.0, "close": 104.5},
        {"high": 105.0, "low": 99.0,  "open": 104.5, "close": 99.5},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(entry_fib_1=0.500, tp_fib_1=0.236, entry_fib_2=0.618, tp_fib_2=0.382, sl_fib=1.000)

    trades = simulate_grid(df, [imp], cfg)
    assert len(trades) == 1
    # Должен быть стоп-лосс на свече 2, а не ложный тейк на свече 1
    assert trades[0].win is False
    assert "SL" in trades[0].outcome
    assert trades[0].exit_idx == 2


def test_sweep_reclaim_trade():
    """
    Проверка модуля Sweep Reclaim:
    После выбивания стопа цена делает свип и возвращается (reclaim),
    активируя сделку на возврат к 0.500 с положительным PnL.
    """
    # 0: Импульс (100 -> 110)
    # 1: Вход 0.500 (104.88)
    # 2: Стоп 1.000 (Low 99.5 <= 100) -> SL
    # 3: Свип (Low 98.5, Close 100.5 > 100) -> Reclaim!
    # 4: Выход по TP 0.500 (High 106.0 >= 104.88) -> Sweep_TP!
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 106.0, "low": 104.0, "open": 108.0, "close": 104.5},
        {"high": 104.5, "low": 99.5,  "open": 104.0, "close": 99.8},
        {"high": 101.0, "low": 98.5,  "open": 99.8,  "close": 100.5},
        {"high": 106.5, "low": 100.0, "open": 100.5, "close": 105.0},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(entry_fib_1=0.500, tp_fib_1=0.236, entry_fib_2=0.618, tp_fib_2=0.382, sl_fib=1.000, enable_sweep_reclaim=True)

    trades = simulate_grid(df, [imp], cfg)
    assert len(trades) == 2
    # Первая сделка: стоп
    assert "SL" in trades[0].outcome
    assert trades[0].win is False
    # Вторая сделка: Sweep_TP
    assert trades[1].outcome == "Sweep_TP"
    assert trades[1].win is True
    assert trades[1].pnl > 0


def test_simulate_manipulation_grid():
    """
    Проверка стратегии Манипуляции (пробой 1.000):
    Вход 1.618, Добор 2.000, Стоп 2.400, Тейк 0.500 / Корзина 1.000.
    """
    # 0: Импульс (100 -> 110)
    # 1.618 Fib log: exp(ln(110) - 1.618 * (ln(110) - ln(100))) = 94.75
    # 2.000 Fib log: exp(ln(110) - 2.000 * (ln(110) - ln(100))) = 90.90
    # 2.400 Fib log: exp(ln(110) - 2.400 * (ln(110) - ln(100))) = 86.88
    # 1.000 Fib log: 100.0 (Basket TP)
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 98.0,  "low": 94.0,  "open": 98.0,  "close": 94.5},   # Вход 1.618 (low 94.0 <= 94.75)
        {"high": 96.0,  "low": 90.0,  "open": 94.5,  "close": 91.0},   # Добор 2.000 (low 90.0 <= 90.90)
        {"high": 102.0, "low": 91.0,  "open": 91.0,  "close": 101.0},  # Выход Basket TP 1.000 (high 102 >= 100)
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")

    trades = simulate_manipulation_grid(df, [imp], entry_fib_1=1.618, entry_fib_2=2.000, sl_fib=2.400, basket_tp=1.000)
    assert len(trades) == 1
    assert trades[0].win is True
    assert trades[0].outcome == "Manip_Basket_TP"
    assert trades[0].both_entered is True
    assert trades[0].pnl > 0


def test_quality_filter_wick_rejection():
    """
    Проверка отсечения импульса с гигантским верхним фитилём (падающая звезда > 60%).
    """
    # Свеча импульса: high=120, low=100, open=101, close=102 -> range=20, wick=(120-102)/20 = 90%
    df = pd.DataFrame([
        {"high": 120.0, "low": 100.0, "open": 101.0, "close": 102.0},
        {"high": 105.0, "low": 104.0, "open": 105.0, "close": 104.5},
        {"high": 103.0, "low": 98.0,  "open": 103.0, "close": 99.0},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=120.0, low=100.0, pct=20.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")

    # Без фильтра качества: сделка открывается
    cfg_off = GridConfig(enable_quality_filter=False)
    trades_off = simulate_grid(df, [imp], cfg_off)
    assert len(trades_off) > 0

    # С фильтром качества: импульс отклонен из-за фитиля 90% > 60%
    cfg_on = GridConfig(enable_quality_filter=True, max_wick_pct=60.0)
    trades_on = simulate_grid(df, [imp], cfg_on)
    assert len(trades_on) == 0


def test_solo_1_entry_and_tp():
    """
    Проверка одиночного входа Solo 0.500:
    Ордер 2 отключен (entry_fib_2=None / mode='solo_1').
    Вход на 0.500 -> TP на 0.236.
    """
    # 0: Импульс 100 -> 110. 0.500 Fib log = 104.88, 0.236 Fib log = 107.54
    # 1: Вход 0.500 (low 104.5 <= 104.88)
    # 2: Выход по TP 0.236 (high 108.0 >= 107.54)
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},
        {"high": 108.0, "low": 104.0, "open": 105.0, "close": 107.8},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="solo_1", entry_fib_1=0.500, tp_fib_1=0.236, entry_fib_2=None, sl_fib=1.000)

    trades = simulate_grid(df, [imp], cfg)
    assert len(trades) == 1
    assert trades[0].win is True
    assert trades[0].outcome == "TP1"
    assert trades[0].only_o1 is True
    assert trades[0].both_entered is False
    assert trades[0].only_o2 is False
    assert trades[0].pnl > 0


def test_solo_2_entry_and_tp():
    """
    Проверка одиночного входа Solo 0.618:
    Ордер 1 отключен (entry_fib_1=None / mode='solo_2').
    Вход на 0.618 -> TP на 0.382.
    """
    # 0: Импульс 100 -> 110. 0.618 Fib log = 103.69, 0.382 Fib log = 106.07
    # 1: Касание 0.500 (low 104.5), но 0.618 еще не достали -> ордер НЕ должен войти!
    # 2: Касание 0.618 (low 103.0 <= 103.69) -> вход Ордера 2
    # 3: Выход по TP 0.382 (high 107.0 >= 106.07) -> тейк
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},
        {"high": 105.0, "low": 103.0, "open": 105.0, "close": 103.5},
        {"high": 107.0, "low": 103.0, "open": 103.5, "close": 106.5},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="solo_2", entry_fib_1=None, entry_fib_2=0.618, tp_fib_2=0.382, sl_fib=1.000)

    trades = simulate_grid(df, [imp], cfg)
    assert len(trades) == 1
    assert trades[0].win is True
    assert trades[0].outcome == "TP2"
    assert trades[0].only_o2 is True
    assert trades[0].only_o1 is False
    assert trades[0].both_entered is False
    assert trades[0].pnl > 0


def test_dual_tp1_cancels_order_2():
    """
    Проверка правила One-and-Done в режиме Dual:
    Вход на 0.500, тейк закрыт до того, как цена дошла до 0.618 -> Ордер 2 отменяется!
    """
    # 0: Импульс 100 -> 110. 0.500 = 104.88, 0.618 = 103.69, TP1 0.236 = 107.54
    # 1: Вход 0.500 (low 104.5, high 106.0) -> вошел Ордер 1, до 0.618 не дошли
    # 2: Закрытие TP 0.236 (high 108.0, low 104.0) -> закрыт по TP1_only, Ордер 2 отменен!
    # 3: Резкое падение (low 101.0 <= 103.69) -> Ордер 2 НЕ должен входить, сделка уже завершена
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},
        {"high": 108.0, "low": 104.0, "open": 105.0, "close": 107.8},
        {"high": 106.0, "low": 101.0, "open": 106.0, "close": 101.5},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="dual", entry_fib_1=0.500, tp_fib_1=0.236, entry_fib_2=0.618, tp_fib_2=0.382, sl_fib=1.000)

    trades = simulate_grid(df, [imp], cfg)
    assert len(trades) == 1
    assert trades[0].win is True
    assert trades[0].outcome == "TP1_only"
    assert trades[0].only_o1 is True
    assert trades[0].both_entered is False
    assert trades[0].exit_idx == 2


def test_dual_entry_2_fills_when_no_tp1():
    """
    Проверка последовательного входа в режиме Dual:
    Вход на 0.500 -> TP1 не исполнен -> цена доходит до 0.618 -> вход Ордера 2 -> оба выходят в плюс.
    """
    # 0: Импульс 100 -> 110. 0.500 = 104.88, 0.618 = 103.69
    # 1: Вход 0.500 (low 104.5, high 106.0 < TP1 107.54) -> Ордер 1 вошел, TP не исполнен
    # 2: Достижение 0.618 (low 103.0 <= 103.69) -> вход Ордера 2!
    # 3: Рост к тейкам (high 109.0) -> оба ордера закрываются по TP
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},
        {"high": 105.0, "low": 103.0, "open": 105.0, "close": 103.5},
        {"high": 109.0, "low": 103.5, "open": 104.0, "close": 108.5},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="dual", entry_fib_1=0.500, tp_fib_1=0.236, entry_fib_2=0.618, tp_fib_2=0.382, sl_fib=1.000)

    trades = simulate_grid(df, [imp], cfg)
    assert len(trades) == 1
    assert trades[0].win is True
    assert trades[0].both_entered is True
    assert trades[0].outcome == "TP1+TP2"
    assert trades[0].pnl > 0


def test_fast_tp_scheme_05_to_0382():
    """
    Проверка выхода для 0.500 уровня на 0.382 (вместо 0.236):
    0.500 = 104.88, 0.382 = 106.07, 0.236 = 107.54.
    Цена отскакивает только до 106.50 (до 0.236 не доходит).
    - При classic (TP 0.236) тейк не исполнился бы.
    - При fast (TP 0.382) тейк успешно берется!
    """
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},  # Вход 0.500
        {"high": 106.5, "low": 104.0, "open": 104.5, "close": 106.3},  # High 106.5 >= 0.382 (106.07), но < 0.236 (107.54)
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")

    # Схема fast: TP 0.382
    cfg_fast = GridConfig(mode="solo_1", entry_fib_1=0.500, tp_scheme="fast", sl_fib=1.000)
    trades_fast = simulate_grid(df, [imp], cfg_fast)
    assert len(trades_fast) == 1
    assert trades_fast[0].win is True
    assert trades_fast[0].outcome == "TP1"
    assert trades_fast[0].pnl > 0

    # Схема classic: TP 0.236 -> тейк не взят (таймаут)
    cfg_classic = GridConfig(mode="solo_1", entry_fib_1=0.500, tp_scheme="classic", sl_fib=1.000)
    trades_classic = simulate_grid(df, [imp], cfg_classic)
    assert len(trades_classic) == 1
    assert trades_classic[0].outcome == "timeout"


def test_fast_tp_scheme_0618_to_0500():
    """
    Проверка выхода для 0.618 уровня на 0.500 (вместо 0.382):
    0.618 = 103.69, 0.500 = 104.88, 0.382 = 106.07.
    Цена отскакивает только до 105.50 (до 0.382 не доходит).
    - При classic (TP 0.382) тейк не исполнился бы.
    - При fast (TP 0.500) тейк успешно берется!
    """
    df = pd.DataFrame([
        {"high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"high": 105.0, "low": 103.0, "open": 105.0, "close": 103.5},  # Вход 0.618 (low 103.0 <= 103.69)
        {"high": 105.5, "low": 103.0, "open": 103.5, "close": 105.2},  # High 105.5 >= 0.500 (104.88), но < 0.382 (106.07)
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")

    # Схема fast: TP 0.500
    cfg_fast = GridConfig(mode="solo_2", entry_fib_2=0.618, tp_scheme="fast", sl_fib=1.000)
    trades_fast = simulate_grid(df, [imp], cfg_fast)
    assert len(trades_fast) == 1
    assert trades_fast[0].win is True
    assert trades_fast[0].outcome == "TP2"
    assert trades_fast[0].pnl > 0

    # Схема classic: TP 0.382 -> тейк не взят (таймаут)
    cfg_classic = GridConfig(mode="solo_2", entry_fib_2=0.618, tp_scheme="classic", sl_fib=1.000)
    trades_classic = simulate_grid(df, [imp], cfg_classic)
    assert len(trades_classic) == 1
    assert trades_classic[0].outcome == "timeout"


def test_trades_to_df_and_summarize_df():
    """
    Проверка конвертации результатов бэктеста в pandas.DataFrame и расчет метрик.
    """
    df = pd.DataFrame([
        {"timestamp": "2026-09-01 10:00:00", "high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"timestamp": "2026-09-01 11:00:00", "high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},
        {"timestamp": "2026-09-01 12:00:00", "high": 108.0, "low": 104.0, "open": 105.0, "close": 107.8},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="solo_1", entry_fib_1=0.500, tp_fib_1=0.236, sl_fib=1.000)

    trades = simulate_grid(df, [imp], cfg)
    assert len(trades) == 1

    # 1. trades_to_df
    tdf = trades_to_df(trades, df)
    assert isinstance(tdf, pd.DataFrame)
    assert len(tdf) == 1
    assert "pnl" in tdf.columns
    assert "cum_pnl" in tdf.columns
    assert "drawdown" in tdf.columns
    assert "entry_time" in tdf.columns
    assert "exit_time" in tdf.columns
    assert bool(tdf["win"].iloc[0]) is True
    assert tdf["cum_pnl"].iloc[0] > 0
    assert tdf["entry_time"].iloc[0] == "2026-09-01 11:00:00"

    # 2. summarize_df
    summary_df = summarize_df(tdf)
    assert isinstance(summary_df, pd.DataFrame)
    assert "Метрика" in summary_df.columns
    assert "Значение" in summary_df.columns

    # 3. summarize c DataFrame
    s = summarize(tdf)
    assert s["n"] == 1
    assert s["wins"] == 1
    assert s["wr"] == 100.0
    assert s["pnl"] > 0





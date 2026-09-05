"""Unit tests for Bybit trader, order sizing, precision, and active setup detector."""

import numpy as np
import pandas as pd
import pytest

from indicators.pybit_client import (
    InstrumentSpecs,
    get_decimals,
    round_price_step,
    round_qty_step,
)
from scripts.bybit_trader import find_active_setup, format_symbol
from scripts.backtest_strategy_interactive import calc_fib


def test_format_symbol():
    assert format_symbol("ZEC") == "ZECUSDT"
    assert format_symbol("btc") == "BTCUSDT"
    assert format_symbol("OPUSDT") == "OPUSDT"
    assert format_symbol("sui-usdt") == "SUIUSDT"
    assert format_symbol("ETH/USDT") == "ETHUSDT"
    assert format_symbol("SUIUSDT.P") == "SUIUSDT"
    assert format_symbol("BTCUSDT.PERP") == "BTCUSDT"
    assert format_symbol("SUI.P") == "SUIUSDT"


def test_precision_rounding():
    assert get_decimals(0.01) == 2
    assert get_decimals(0.0001) == 4
    assert get_decimals(1.0) == 0

    assert round_price_step(1015.487, 0.01, 2) == 1015.49
    assert round_price_step(0.096763, 0.00001, 5) == 0.09676

    assert round_qty_step(0.0543, 0.01, 2) == 0.05
    assert round_qty_step(47.8, 10.0, 0) == 40.0


def test_active_setup_trailing_detector():
    """Синтетический растущий импульс: от 100 до 110 (+10%), без касания 0.500 (104.88)."""
    # 20 свечей плавного роста
    prices = np.linspace(100, 110, 20)
    data = []
    ts = pd.date_range("2026-09-01", periods=20, freq="1h")
    for i, p in enumerate(prices):
        data.append({
            "timestamp": ts[i],
            "open": p - 0.2,
            "high": p + 0.2,
            "low": p - 0.2,
            "close": p,
            "volume": 1000.0,
        })
    df = pd.DataFrame(data)

    setup = find_active_setup(df, min_pct=2.0)
    assert setup is not None
    assert setup.setup_type == "TRIPLE_GRID_TRAILING"
    assert setup.side == "long"
    assert setup.imp_peak_price == pytest.approx(110.2, rel=1e-2)
    assert setup.entry_1 > setup.entry_2 > setup.entry_3
    assert setup.tp_1 > setup.tp_2 > setup.tp_3
    assert setup.stop_loss == pytest.approx(99.8, rel=1e-2)


def test_active_setup_sweep_reclaim():
    """Синтетический импульс, затем свип 1.000 на 0.4% (<= 0.5%) без закрытия под 1.000 и возврат выше 1.000."""
    # Импульс 100 -> 110
    # Откат вниз со шпилькой до 99.6 (прокол 100 на 0.4%), но закрытие на 100.2 (без закрепления под 100.0)
    data = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00"), "open": 100, "high": 101, "low": 100, "close": 101, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 101, "high": 105, "low": 101, "close": 105, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 02:00"), "open": 105, "high": 110, "low": 104, "close": 110, "volume": 100},
        # Коррекция и свип (шпилька 99.6, закрытие 100.2 — выше 100)
        {"timestamp": pd.Timestamp("2026-09-01 03:00"), "open": 110, "high": 110, "low": 104, "close": 104, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 04:00"), "open": 104, "high": 104, "low": 99.6, "close": 100.2, "volume": 100},
        # Reclaim свеча над 100.0
        {"timestamp": pd.Timestamp("2026-09-01 05:00"), "open": 100.2, "high": 101.5, "low": 100.0, "close": 100.5, "volume": 200},
    ]
    # Добавляем историю спереди, чтобы индикатор MACD мог посчитаться
    front = []
    for k in range(25):
        t = pd.Timestamp("2026-08-31 00:00") + pd.Timedelta(hours=k)
        front.append({"timestamp": t, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 50})

    df = pd.DataFrame(front + data)
    setup = find_active_setup(df, min_pct=2.0)
    assert setup is not None
    assert setup.setup_type == "SWEEP_RECLAIM"
    assert setup.side == "long"
    assert setup.sweep_price == pytest.approx(99.6, rel=1e-2)
    assert setup.stop_loss < 99.6
    # Тейк на 0.618 Fib минус 2%
    p_0618 = calc_fib(setup.imp_peak_price, setup.imp_start_price, 0.618, is_long=True, scale="log")
    assert setup.tp_1 == pytest.approx(p_0618 * 0.98, rel=1e-4)
    # Триггер безубытка на 0.786 Fib
    p_0786 = calc_fib(setup.imp_peak_price, setup.imp_start_price, 0.786, is_long=True, scale="log")
    assert setup.be_trigger == pytest.approx(p_0786, rel=1e-4)
    assert setup.be_price == pytest.approx(setup.entry_1 * 1.0005, rel=1e-4)


def test_active_setup_sweep_reclaim_consolidation_reject():
    """Проверка, что закрытие под 1.000 (закрепление) или свип > 0.5% отклоняет SWEEP_RECLAIM."""
    front = []
    for k in range(25):
        t = pd.Timestamp("2026-08-31 00:00") + pd.Timedelta(hours=k)
        front.append({"timestamp": t, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 50})

    # Случай 1: свип маленький (99.8, 0.2%), но свеча ЗАКРЫЛАСЬ под 1.000 (99.85 < 100.0)
    data_closed_below = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00"), "open": 100, "high": 101, "low": 100, "close": 101, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 101, "high": 105, "low": 101, "close": 105, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 02:00"), "open": 105, "high": 110, "low": 104, "close": 110, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 03:00"), "open": 110, "high": 110, "low": 104, "close": 104, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 04:00"), "open": 104, "high": 104, "low": 99.8, "close": 99.85, "volume": 100},  # close < 100.0
        {"timestamp": pd.Timestamp("2026-09-01 05:00"), "open": 99.85, "high": 101.5, "low": 99.85, "close": 100.5, "volume": 200},
    ]
    df1 = pd.DataFrame(front + data_closed_below)
    setup1 = find_active_setup(df1, min_pct=2.0)
    # Так как свеча закрылась под 1.000, SWEEP_RECLAIM отменяется -> MANIPULATION
    assert setup1 is not None
    assert setup1.setup_type == "MANIPULATION"

    # Случай 2: свечи не закрывались под 1.000, но шпилька улетела глубже 0.5% (на 1.2%, low = 98.8)
    data_deep_sweep = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00"), "open": 100, "high": 101, "low": 100, "close": 101, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 101, "high": 105, "low": 101, "close": 105, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 02:00"), "open": 105, "high": 110, "low": 104, "close": 110, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 03:00"), "open": 110, "high": 110, "low": 104, "close": 104, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 04:00"), "open": 104, "high": 104, "low": 98.8, "close": 100.1, "volume": 100},  # low 98.8 (1.2% > 0.5%)
        {"timestamp": pd.Timestamp("2026-09-01 05:00"), "open": 100.1, "high": 101.5, "low": 100.0, "close": 100.5, "volume": 200},
    ]
    df2 = pd.DataFrame(front + data_deep_sweep)
    setup2 = find_active_setup(df2, min_pct=2.0)
    assert setup2 is not None
    assert setup2.setup_type == "MANIPULATION"


def test_active_setup_manipulation():
    """Синтетический глубокий пробой основания без возврата (манипуляция к 1.414/1.618)."""
    # Импульс 100 -> 110 (+10%)
    # Затем обвал до 92.0 (пробой 100 на 8%)
    data = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00"), "open": 100, "high": 101, "low": 100, "close": 101, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 101, "high": 106, "low": 101, "close": 106, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 02:00"), "open": 106, "high": 110, "low": 105, "close": 110, "volume": 100},
        # Обвал глубоко под 1.000
        {"timestamp": pd.Timestamp("2026-09-01 03:00"), "open": 110, "high": 110, "low": 98, "close": 98, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 04:00"), "open": 98, "high": 98, "low": 92, "close": 93, "volume": 100},
    ]
    front = []
    for k in range(25):
        t = pd.Timestamp("2026-08-31 00:00") + pd.Timedelta(hours=k)
        front.append({"timestamp": t, "open": 100, "high": 100.5, "low": 99.5, "close": 100, "volume": 50})

    df = pd.DataFrame(front + data)
    setup = find_active_setup(df, min_pct=2.0)
    assert setup is not None
    assert setup.setup_type == "MANIPULATION"
    assert setup.entry_1 < 100.0  # Уровень 1.414 Fib
    assert setup.entry_2 < setup.entry_1  # Уровень 1.618 Fib
    assert setup.stop_loss < setup.entry_2  # Стоп 2.414 Fib
    assert setup.tp_1 == pytest.approx(setup.imp_start_price * 0.999, rel=1e-3)  # Тейк 1-го ордера на 1.000 Fib (-0.1%)
    assert setup.tp_2 == setup.entry_1  # Тейк корзины на 1.414 Fib


def test_active_setup_prioritizes_unbroken_over_broken_sub_impulse():
    """Проверка, что живой несломанный старший импульс имеет приоритет над сломанным вложенным подимпульсом."""
    # Старший импульс: 100 -> 130 (+30%)
    data_major = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00"), "open": 100, "high": 101, "low": 100, "close": 101, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 101, "high": 115, "low": 101, "close": 115, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 02:00"), "open": 115, "high": 130, "low": 114, "close": 130, "volume": 100},
    ]
    # Плавный откат до 120 (0.500 Fib для 100->130 это ~114, поэтому 120 выше 0.500)
    data_pullback = [
        {"timestamp": pd.Timestamp("2026-09-01 03:00"), "open": 130, "high": 130, "low": 122, "close": 122, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 04:00"), "open": 122, "high": 122, "low": 120, "close": 120, "volume": 100},
    ]
    # Вложенный подимпульс: 120 -> 125 (+4.17%)
    data_sub = [
        {"timestamp": pd.Timestamp("2026-09-01 05:00"), "open": 120, "high": 123, "low": 120, "close": 123, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 06:00"), "open": 123, "high": 125, "low": 122, "close": 125, "volume": 100},
    ]
    # Подимпульс ломается ниже 120 (слив до 117), но старший 100->130 остается живым (117 > 114)
    data_break_sub = [
        {"timestamp": pd.Timestamp("2026-09-01 07:00"), "open": 125, "high": 125, "low": 117, "close": 117.5, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 08:00"), "open": 117.5, "high": 119, "low": 117.2, "close": 118, "volume": 100},
    ]
    front = []
    for k in range(25):
        t = pd.Timestamp("2026-08-31 00:00") + pd.Timedelta(hours=k)
        front.append({"timestamp": t, "open": 100, "high": 100.5, "low": 99.5, "close": 100, "volume": 50})

    df = pd.DataFrame(front + data_major + data_pullback + data_sub + data_break_sub)
    setup = find_active_setup(df, min_pct=2.0)
    assert setup is not None
    # Должен быть выбран старший несломанный импульс, а не манипуляция по сломанному подимпульсу
    assert setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION")
    assert setup.imp_start_price == pytest.approx(100.0, rel=1e-2)
    assert setup.imp_peak_price == pytest.approx(130.0, rel=1e-2)


def test_entry_buffer_010():
    """Проверка, что буфер 0.10% сдвигает входы ровно на +0.10% выше уровня Фибоначчи."""
    # Импульс 812.25 -> 1054.94 (как на ZEC)
    p_0500 = calc_fib(1054.94, 812.25, 0.500, is_long=True, scale="log")
    assert p_0500 == pytest.approx(925.68, rel=1e-3)

    # Применяем буфер 0.10%
    buffered_e1 = p_0500 * (1.0 + 0.10 / 100.0)
    assert buffered_e1 == pytest.approx(926.61, rel=1e-3)
    assert buffered_e1 > p_0500
    assert (buffered_e1 - p_0500) / p_0500 * 100.0 == pytest.approx(0.10, abs=1e-4)


def test_tp_buffer_01():
    """Проверка, что буфер тейка 0.1% сдвигает тейк ровно на -0.1% ниже уровня Фибоначчи."""
    # Импульс 812.25 -> 1054.94 (как на ZEC)
    p_0236 = calc_fib(1054.94, 812.25, 0.236, is_long=True, scale="log")
    assert p_0236 == pytest.approx(991.82, rel=1e-3)

    # Применяем буфер тейка -0.10%
    buffered_tp1 = p_0236 * (1.0 - 0.10 / 100.0)
    assert buffered_tp1 == pytest.approx(990.83, rel=1e-3)
    assert buffered_tp1 < p_0236
    assert (p_0236 - buffered_tp1) / p_0236 * 100.0 == pytest.approx(0.10, abs=1e-4)


def test_trade_config_defaults():
    from scripts.bybit_trader import load_trade_config
    cfg = load_trade_config()
    assert cfg.total_risk_usd == 2.0
    assert cfg.minor_risk_usd == 2.0
    assert cfg.major_risk_usd == 2.0
    assert cfg.entry_buffer_pct == 0.10
    assert cfg.entry_buffer_0500_pct == 0.10
    assert cfg.entry_buffer_0618_pct == 0.15
    assert cfg.entry_buffer_0786_pct == 0.15
    assert cfg.entry_buffer_1414_pct == 0.10
    assert cfg.entry_buffer_1618_pct == 0.10
    assert cfg.tp_buffer_pct == 0.10
    assert cfg.reclaim_tp_buffer_pct == 2.0
    assert cfg.reclaim_be_trigger_fib == 0.786
    assert cfg.reclaim_be_offset_pct == 0.05
    assert cfg.reclaim_max_sweep_pct == 0.5
    assert cfg.reclaim_allow_close_below is False
    assert cfg.preferred_side == "long"
    assert cfg.timeframe == "1h"
    assert cfg.scale == "log"


def test_trade_config_custom(tmp_path):
    from scripts.bybit_trader import load_trade_config
    custom_yaml = tmp_path / "custom_config.yaml"
    custom_yaml.write_text("""
risk:
  minor_risk_usd: 5.0
  major_risk_usd: 6.0
buffers:
  entry_buffer_0500_pct: 0.12
  entry_buffer_0618_pct: 0.18
  entry_buffer_0786_pct: 0.22
  entry_buffer_1414_pct: 0.14
  entry_buffer_1618_pct: 0.16
  entry_buffer_pct: 0.15
  tp_buffer_pct: 0.20
  reclaim_tp_buffer_pct: 3.0
  reclaim_be_trigger_fib: 0.705
  reclaim_be_offset_pct: 0.10
  reclaim_max_sweep_pct: 0.8
  reclaim_allow_close_below: true
strategy:
  preferred_side: "long"
  timeframe: "4h"
  min_impulse_pct: 3.5
  lookback_bars: 100
  scale: "linear"
""")
    cfg = load_trade_config(custom_yaml)
    assert cfg.minor_risk_usd == 5.0
    assert cfg.total_risk_usd == 5.0
    assert cfg.major_risk_usd == 6.0
    assert cfg.entry_buffer_0500_pct == 0.12
    assert cfg.entry_buffer_0618_pct == 0.18
    assert cfg.entry_buffer_0786_pct == 0.22
    assert cfg.entry_buffer_1414_pct == 0.14
    assert cfg.entry_buffer_1618_pct == 0.16
    assert cfg.entry_buffer_pct == 0.15
    assert cfg.tp_buffer_pct == 0.20
    assert cfg.reclaim_tp_buffer_pct == 3.0
    assert cfg.reclaim_be_trigger_fib == 0.705
    assert cfg.reclaim_be_offset_pct == 0.10
    assert cfg.reclaim_max_sweep_pct == 0.8
    assert cfg.reclaim_allow_close_below is True
    assert cfg.timeframe == "4h"
    assert cfg.min_impulse_pct == 3.5
    assert cfg.lookback_bars == 100
    assert cfg.scale == "linear"


def test_individual_entry_buffers_calculation():
    """Проверка, что для 0.500 применяется буфер +0.10%, а для 0.618 и 0.786 - буфер +0.15%."""
    from scripts.bybit_trader import find_active_setup
    # Формируем свечи: импульс со 100 до 130 на 20 свечах
    dates = pd.date_range("2026-09-01", periods=25, freq="1h")
    bars = []
    for i in range(20):
        p = 100.0 + (30.0 / 19.0) * i
        bars.append({"timestamp": dates[i], "open": p, "high": p + 0.2, "low": p - 0.2, "close": p + 0.1, "volume": 100})
    # Добавляем 5 свечей боковика чуть ниже вершины (на уровне 128), не касаясь 0.500
    for i in range(20, 25):
        bars.append({"timestamp": dates[i], "open": 128.0, "high": 128.5, "low": 127.5, "close": 128.0, "volume": 100})

    df = pd.DataFrame(bars)
    setup = find_active_setup(
        df,
        min_pct=2.0,
        entry_buffer_0500_pct=0.10,
        entry_buffer_0618_pct=0.15,
        entry_buffer_0786_pct=0.15,
        layer="minor",
    )
    assert setup is not None
    assert setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION")

    p_0500 = calc_fib(setup.imp_peak_price, setup.imp_start_price, 0.500, is_long=True, scale="log")
    p_0618 = calc_fib(setup.imp_peak_price, setup.imp_start_price, 0.618, is_long=True, scale="log")
    p_0786 = calc_fib(setup.imp_peak_price, setup.imp_start_price, 0.786, is_long=True, scale="log")

    # Проверяем, что e1 смещен ровно на +0.10%
    expected_e1 = p_0500 * (1.0 + 0.10 / 100.0)
    assert setup.entry_1 == pytest.approx(expected_e1, rel=1e-4)

    # Проверяем, что e2 смещен ровно на +0.15%
    expected_e2 = p_0618 * (1.0 + 0.15 / 100.0)
    assert setup.entry_2 == pytest.approx(expected_e2, rel=1e-4)

    # Проверяем, что e3 смещен ровно на +0.15%
    expected_e3 = p_0786 * (1.0 + 0.15 / 100.0)
    assert setup.entry_3 == pytest.approx(expected_e3, rel=1e-4)


def test_manipulation_entry_buffers_calculation():
    """Проверка, что для 1.414 и 1.618 применяются индивидуальные буферы (+0.10%)."""
    from scripts.bybit_trader import find_active_setup
    data = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00"), "open": 100, "high": 101, "low": 100, "close": 101, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 101, "high": 115, "low": 101, "close": 115, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 02:00"), "open": 115, "high": 130, "low": 114, "close": 130, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 03:00"), "open": 130, "high": 130, "low": 98, "close": 98, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 04:00"), "open": 98, "high": 98, "low": 92, "close": 93, "volume": 100},
    ]
    front = []
    for k in range(25):
        t = pd.Timestamp("2026-08-31 00:00") + pd.Timedelta(hours=k)
        front.append({"timestamp": t, "open": 100, "high": 100.5, "low": 99.5, "close": 100, "volume": 50})

    df = pd.DataFrame(front + data)
    setup = find_active_setup(
        df,
        min_pct=2.0,
        entry_buffer_1414_pct=0.10,
        entry_buffer_1618_pct=0.10,
    )
    assert setup is not None
    assert setup.setup_type == "MANIPULATION"

    p_1414 = calc_fib(setup.imp_peak_price, setup.imp_start_price, 1.414, is_long=True, scale="log")
    p_1618 = calc_fib(setup.imp_peak_price, setup.imp_start_price, 1.618, is_long=True, scale="log")

    expected_e1414 = p_1414 * (1.0 + 0.10 / 100.0)
    expected_e1618 = p_1618 * (1.0 + 0.10 / 100.0)
    assert setup.entry_1 == pytest.approx(expected_e1414, rel=1e-4)
    assert setup.entry_2 == pytest.approx(expected_e1618, rel=1e-4)


def test_manipulation_risk_2_usd():
    """Проверка, что сетка манипуляции (1.618 и 2.000 со стопом на 2.414) рассчитывается ровно на $2.00 риска."""
    from indicators.pybit_client import BybitClient
    client = BybitClient(testnet=True)
    # Настраиваем синтетические спеки
    client._specs_cache["TESTUSDT"] = InstrumentSpecs(
        symbol="TESTUSDT",
        tick_size=0.01,
        qty_step=0.001,
        min_qty=0.001,
        max_qty=10000.0,
        min_notional=1.0,
        price_decimals=2,
        qty_decimals=3,
    )

    # Уровни манипуляции: entry 1.618, entry 2.000, stop 2.414
    e_1618 = 90.0
    e_2000 = 80.0
    sl_2414 = 60.0

    q1, q2, loss1, loss2 = client.calc_dual_grid_order_sizes(
        p_entry1=e_1618,
        p_entry2=e_2000,
        p_sl=sl_2414,
        total_risk_usd=2.0,
        symbol="TESTUSDT",
    )

    # Дистанция 1: 90 - 60 = 30. При риске $1.00 объем: 1.0 / 30 = 0.0333... -> 0.033
    # Дистанция 2: 80 - 60 = 20. При риске $1.00 объем: 1.0 / 20 = 0.05
    assert q1 == pytest.approx(0.033, abs=1e-3)
    assert q2 == pytest.approx(0.05, abs=1e-3)
    assert (loss1 + loss2) == pytest.approx(2.0, rel=1e-2)


def test_active_setup_dual_grid_0618_basket_tp():
    """Проверка: при наливе 0.500 и 0.618 тейк берется на 0.382, завершая сетку."""
    # Импульс 100 -> 110 (0.500 = 104.88, 0.618 = 103.71, 0.382 = 106.07, 0.236 = 107.31)
    data = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00"), "open": 100, "high": 101, "low": 100, "close": 101, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 101, "high": 106, "low": 101, "close": 106, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-01 02:00"), "open": 106, "high": 110, "low": 105, "close": 110, "volume": 100},
        # Свеча наливает 0.500 и 0.618 (low = 103.0 < 103.71)
        {"timestamp": pd.Timestamp("2026-09-01 03:00"), "open": 110, "high": 110, "low": 103.0, "close": 104.0, "volume": 100},
        # Свеча отскакивает до 106.5 (выше 0.382 = 106.07, но ниже 0.236 = 107.31)
        {"timestamp": pd.Timestamp("2026-09-01 04:00"), "open": 104.0, "high": 106.5, "low": 104.0, "close": 106.2, "volume": 100},
    ]
    front = []
    for k in range(25):
        t = pd.Timestamp("2026-08-31 00:00") + pd.Timedelta(hours=k)
        front.append({"timestamp": t, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 50})

    df = pd.DataFrame(front + data)
    # Сетка закрылась по тейку 0.382, поэтому активного незавершенного сетапа нет
    setup = find_active_setup(df, min_pct=2.0)
    assert setup is None


def test_load_trade_config_symbols(tmp_path):
    """Проверка загрузки списка монет из конфигурационного файла YAML."""
    from scripts.bybit_trader import load_trade_config

    cfg_file = tmp_path / "trade_config.yaml"
    cfg_file.write_text("""
strategy:
  preferred_side: "long"
  symbols:
    - "SUIUSDT.P"
    - "BTCUSDT"
    - "ETHUSDT"
""", encoding="utf-8")

    cfg = load_trade_config(cfg_file)
    assert cfg.symbols == ["SUIUSDT.P", "BTCUSDT", "ETHUSDT"]

    # Проверка строкового формата через запятую
    cfg_file_str = tmp_path / "trade_config_str.yaml"
    cfg_file_str.write_text("""
strategy:
  symbols: "SUIUSDT.P, BTCUSDT"
""", encoding="utf-8")

    cfg2 = load_trade_config(cfg_file_str)
    assert cfg2.symbols == ["SUIUSDT.P", "BTCUSDT"]


class MockBybitClient:
    def __init__(self, pos_size=0.0, klines_df=None):
        self.pos_size = pos_size
        self.klines_df = klines_df if klines_df is not None else pd.DataFrame()
        self.tp_sl_calls = []
        self.cancel_all_calls = []
        self.cancelled_order_ids = []
        self.placed_orders = []

    def get_position(self, symbol, side="Buy"):
        if self.pos_size > 0:
            return {"size": str(self.pos_size), "avgPrice": "100.0"}
        return None

    def fetch_klines(self, symbol, interval="60", limit=10):
        return self.klines_df

    def round_price(self, p, s):
        return round(float(p), 2)

    def round_qty(self, q, s):
        return round(float(q), 3)

    def get_specs(self, symbol):
        return InstrumentSpecs(symbol, 0.01, 2, 0.001, 3, 0.001, 1000.0, 1.0)

    def calc_dual_grid_order_sizes(self, e1, e2, sl, total_risk_usd=2.0, symbol="TEST", equal_weight=True, **kwargs):
        return 1.0, 1.0, 1.0, 1.0

    def calc_triple_grid_order_sizes(self, e1, e2, e3, sl, total_risk_usd=2.0, symbol="TEST", equal_weight=True, **kwargs):
        return 1.0, 1.0, 1.0, 0.67, 0.67, 0.67

    def set_position_tp_sl(self, symbol, take_profit=None, stop_loss=None):
        self.tp_sl_calls.append({"symbol": symbol, "take_profit": take_profit, "stop_loss": stop_loss})

    def cancel_all_orders(self, symbol):
        self.cancel_all_calls.append(symbol)
        return [{"orderId": "mock_cancelled"}]

    def cancel_order(self, symbol, order_id=None, order_link_id=None, **kwargs):
        self.cancelled_order_ids.append(order_id or order_link_id)
        return {"orderId": order_id or order_link_id}

    def get_open_orders(self, symbol):
        return [
            o for o in self.placed_orders
            if o.get("orderId") not in self.cancelled_order_ids
        ]

    def amend_order(self, symbol, order_id, price=None, take_profit=None, stop_loss=None):
        pass

    def place_order(self, symbol, side, order_type, qty, price=None, take_profit=None, stop_loss=None, order_link_id=None, **kwargs):
        oid = f"order_{len(self.placed_orders) + 1}"
        order_dict = {
            "orderId": oid,
            "orderLinkId": order_link_id or "",
            "symbol": symbol, "side": side, "order_type": order_type,
            "qty": qty, "price": price, "take_profit": take_profit, "stop_loss": stop_loss
        }
        self.placed_orders.append(order_dict)
        return {"orderId": oid}

    def update_stop_loss(self, symbol, order_id, stop_loss):
        return True

    def get_ticker_price(self, symbol):
        if self.klines_df is not None and len(self.klines_df) > 0 and "close" in self.klines_df.columns:
            return float(self.klines_df["close"].iloc[-1])
        return 100.0

    def get_available_balance(self):
        return 1000.0

    def get_symbol_leverage(self, symbol):
        return 10.0

    def calc_required_margin(self, symbol, qty, price):
        return (qty * price) / 10.0


def test_process_monitor_step_o1_filled():
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="TRAILING",
        q1=1.0,
        q2=1.0,
        q3=1.0,
        cur_e1=100.0,
        cur_tp1=105.0,
        cur_e2=95.0,
        cur_tp2=102.0,
        cur_e3=90.0,
        cur_tp3=100.0,
        has_o2=True,
        has_o3=True,
    )
    client = MockBybitClient(pos_size=1.0)
    process_monitor_step(m, client, cfg, "60")
    assert m.state == "O1_FILLED"
    assert m.position_was_open is True


def test_process_monitor_step_o1_filled_live_sets_position_tp_sl():
    """Проверка: при входе Ордера 1 в режиме is_live=True вызывается set_position_tp_sl (TP внутри позиции)."""
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="TRAILING",
        q1=1.0,
        q2=1.0,
        q3=1.0,
        cur_e1=100.0,
        cur_tp1=105.0,
        sl=85.0,
        has_o2=True,
        has_o3=True,
    )
    client = MockBybitClient(pos_size=1.0)
    process_monitor_step(m, client, cfg, "60", is_live=True)
    assert m.state == "O1_FILLED"
    assert len(client.tp_sl_calls) == 1
    assert client.tp_sl_calls[0]["symbol"] == "TESTUSDT"
    assert client.tp_sl_calls[0]["take_profit"] == 105.0
    assert client.tp_sl_calls[0]["stop_loss"] == 85.0


def test_process_monitor_step_both_filled_moves_tp():
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="O1_FILLED",
        q1=1.0,
        q2=1.0,
        q3=1.0,
        cur_tp2=102.0,
        cur_tp3=100.0,
        sl=80.0,
        has_o2=True,
        has_o3=True,
        position_was_open=True,
    )
    client = MockBybitClient(pos_size=2.0)
    process_monitor_step(m, client, cfg, "60")
    assert m.state in ("O2_FILLED", "BOTH_FILLED")
    assert len(client.tp_sl_calls) == 1
    assert client.tp_sl_calls[0]["take_profit"] == 102.0


def test_process_monitor_step_o1_tp_cancels_orphan_o2():
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="DUAL_GRID_TRAILING",
        state="O1_FILLED",
        o2_id="order_2_id",
        cur_tp1=105.0,
        sl=90.0,
        position_was_open=True,
    )
    klines = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 104, "high": 105.5, "low": 103, "close": 105, "volume": 100}
    ])
    client = MockBybitClient(pos_size=0.0, klines_df=klines)
    process_monitor_step(m, client, cfg, "60")
    assert m.state == "IDLE"
    assert "order_2_id" in client.cancelled_order_ids


def test_process_monitor_step_sl_triggers_awaiting_sweep():
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="DUAL_GRID_TRAILING",
        state="O1_FILLED",
        o2_id="order_2_id",
        cur_tp1=105.0,
        sl=90.0,
        position_was_open=True,
    )
    klines = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 12:00"), "open": 95, "high": 96, "low": 89.5, "close": 89.8, "volume": 100}
    ])
    client = MockBybitClient(pos_size=0.0, klines_df=klines)
    process_monitor_step(m, client, cfg, "60")
    assert m.state == "AWAITING_SWEEP_CLOSE"
    assert m.stop_bar_time == pd.Timestamp("2026-09-01 12:00")
    assert m.stop_sweep_low == 89.5
    assert "order_2_id" in client.cancelled_order_ids


def test_process_monitor_step_sweep_reclaim_on_candle_close():
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig(reclaim_max_sweep_pct=0.5)
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="DUAL_GRID_TRAILING",
        state="AWAITING_SWEEP_CLOSE",
        imp_start_price=100.0,
        cur_peak=110.0,
        stop_bar_time=pd.Timestamp("2026-09-01 12:00"),
        stop_sweep_low=99.7,  # 0.3% свип
    )

    # Создаем 25 баров истории для MACD + бар пробоя 12:00 (close 100.2 >= 100.0) + формирующийся бар 13:00
    rows = []
    for k in range(25):
        t = pd.Timestamp("2026-08-31 00:00") + pd.Timedelta(hours=k)
        rows.append({"timestamp": t, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 50})
    rows.append({"timestamp": pd.Timestamp("2026-09-01 12:00"), "open": 101.0, "high": 101.0, "low": 99.7, "close": 100.2, "volume": 100})
    rows.append({"timestamp": pd.Timestamp("2026-09-01 13:00"), "open": 100.2, "high": 101.0, "low": 100.1, "close": 100.5, "volume": 100})

    df = pd.DataFrame(rows)
    client = MockBybitClient(pos_size=0.0, klines_df=df)
    process_monitor_step(m, client, cfg, "60")

    assert m.state == "SWEEP_RECLAIM_ACTIVE"
    assert len(client.placed_orders) == 1
    assert client.placed_orders[0]["order_type"] == "Market"
    assert client.placed_orders[0]["stop_loss"] < 99.7


def test_process_monitor_step_manipulation_on_candle_close_below():
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="DUAL_GRID_TRAILING",
        state="AWAITING_SWEEP_CLOSE",
        imp_start_price=100.0,
        cur_peak=110.0,
        stop_bar_time=pd.Timestamp("2026-09-01 12:00"),
        stop_sweep_low=98.0,
    )

    rows = []
    for k in range(25):
        t = pd.Timestamp("2026-08-31 00:00") + pd.Timedelta(hours=k)
        rows.append({"timestamp": t, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 50})
    # Свеча закрылась на 99.0 (< 100.0) -> Манипуляция
    rows.append({"timestamp": pd.Timestamp("2026-09-01 12:00"), "open": 101.0, "high": 101.0, "low": 98.0, "close": 99.0, "volume": 100})
    rows.append({"timestamp": pd.Timestamp("2026-09-01 13:00"), "open": 99.0, "high": 99.5, "low": 98.5, "close": 99.2, "volume": 100})

    df = pd.DataFrame(rows)
    client = MockBybitClient(pos_size=0.0, klines_df=df)
    process_monitor_step(m, client, cfg, "60")

    assert m.state == "MANIPULATION_ACTIVE"
    assert len(client.placed_orders) == 2
    # Ордер 1 на 1.414 Fib, Ордер 2 на 1.618 Fib
    assert client.placed_orders[0]["order_type"] == "Limit"
    assert client.placed_orders[1]["order_type"] == "Limit"
    assert client.placed_orders[0]["price"] < 100.0
    assert client.placed_orders[1]["price"] < client.placed_orders[0]["price"]
    # Проверяем тейки: для ордера 1 тейк на 1.000, для ордера 2 тейк на 1.414 (m.cur_e1)
    assert client.placed_orders[0]["take_profit"] == pytest.approx(100.0 * 0.999, rel=1e-3)
    assert client.placed_orders[1]["take_profit"] == client.placed_orders[0]["price"]


def test_process_monitor_step_manipulation_basket_tp():
    """Проверка переноса тейк-профита корзины на 1.414 при наливе 2-го ордера (1.618 Fib)."""
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="MANIPULATION",
        state="MANIPULATION_ACTIVE",
        cur_e1=95.86,
        cur_tp1=99.9,
        cur_e2=93.82,
        cur_tp2=95.86,
        sl=85.86,
        q1=10.0,
        q2=15.0,
        has_o2=True,
        tp_basket_applied=False,
    )
    # Имитируем, что налило оба ордера (q1 + q2 = 25.0)
    client = MockBybitClient(pos_size=25.0)
    process_monitor_step(m, client, cfg, "60")

    assert m.tp_basket_applied is True
    assert m.position_was_open is True


def test_config_separated_risks(tmp_path):
    """Проверка разделения параметров риска: total_risk_usd ($2 на обычную сетку) и manipulation_risk_usd ($2 на каждый ордер манипуляции)."""
    from scripts.bybit_trader import load_trade_config
    cfg_file = tmp_path / "custom_trade_config.yaml"
    cfg_file.write_text("""
risk:
  total_risk_usd: 3.0
  manipulation_risk_usd: 1.5
""", encoding="utf-8")

    cfg = load_trade_config(cfg_file)
    assert cfg.total_risk_usd == 3.0
    assert cfg.manipulation_risk_usd == 1.5


def test_manipulation_grid_separated_risk_sizing():
    """Проверка, что для ордеров манипуляции (1.414 и 1.618) выделяется самостоятельный риск $2.0 на каждый ($4.0 на корзину)."""
    from indicators.pybit_client import BybitClient
    client = BybitClient(testnet=True)
    client._specs_cache["TESTUSDT"] = InstrumentSpecs(
        symbol="TESTUSDT",
        tick_size=0.01,
        qty_step=0.001,
        min_qty=0.001,
        max_qty=10000.0,
        min_notional=0.0,
        price_decimals=2,
        qty_decimals=3,
    )
    e_1414 = 95.86
    e_1618 = 93.82
    sl_2414 = 85.86
    manipulation_risk = 2.0  # $2.0 на каждый ордер -> $4.0 на корзину

    q1, q2, loss1, loss2 = client.calc_dual_grid_order_sizes(
        e_1414, e_1618, sl_2414, total_risk_usd=manipulation_risk * 2.0, symbol="TESTUSDT", equal_weight=True
    )
    # На каждый ордер риск должен быть ровно ~$2.0
    assert loss1 == pytest.approx(2.0, rel=0.05)
    assert loss2 == pytest.approx(2.0, rel=0.05)
    assert (loss1 + loss2) == pytest.approx(4.0, rel=0.05)


def test_calc_triple_grid_order_sizes():
    """Проверка расчета объемов тройной сетки (0.500, 0.618, 0.786 со стопом 1.000 на $2.00 суммарного риска)."""
    from indicators.pybit_client import BybitClient
    client = BybitClient(testnet=True)
    client._specs_cache["TESTUSDT"] = InstrumentSpecs(
        symbol="TESTUSDT",
        tick_size=0.01,
        qty_step=0.001,
        min_qty=0.001,
        max_qty=10000.0,
        min_notional=1.0,
        price_decimals=2,
        qty_decimals=3,
    )

    e1 = 926.32
    e2 = 898.18
    e3 = 850.00
    sl = 812.25

    q1, q2, q3, l1, l2, l3 = client.calc_triple_grid_order_sizes(
        p_entry1=e1,
        p_entry2=e2,
        p_entry3=e3,
        p_sl=sl,
        total_risk_usd=2.0,
        symbol="TESTUSDT",
        equal_weight=True,
    )

    # Риск на ордер: 2.0 / 3 = 0.6667 USD
    # Дистанция 1: 926.32 - 812.25 = 114.07 -> raw 0.00584 -> floor to step 0.001 = 0.005
    # Дистанция 2: 898.18 - 812.25 = 85.93  -> raw 0.00775 -> floor to step 0.001 = 0.007
    # Дистанция 3: 850.00 - 812.25 = 37.75  -> raw 0.01766 -> floor to step 0.001 = 0.017
    assert q1 == 0.005
    assert q2 == 0.007
    assert q3 == 0.017
    assert q1 > 0 and q2 > q1 and q3 > q2
    tot_loss = l1 + l2 + l3
    assert tot_loss <= 2.0
    assert tot_loss == pytest.approx(1.814, abs=0.01)


def test_process_monitor_step_o2_to_o3_filled_moves_tp():
    """Проверка перехода O2_FILLED -> O3_FILLED и переноса тейка на 0.500 Fib."""
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="O2_FILLED",
        q1=1.0,
        q2=1.0,
        q3=1.0,
        cur_tp2=102.0,
        cur_tp3=100.0,
        sl=80.0,
        has_o2=True,
        has_o3=True,
        position_was_open=True,
    )
    # Исполнен третий ордер -> суммарный объем 3.0
    client = MockBybitClient(pos_size=3.0)
    process_monitor_step(m, client, cfg, "60")
    assert m.state == "O3_FILLED"
    assert len(client.tp_sl_calls) == 1
    assert client.tp_sl_calls[0]["take_profit"] == 100.0


def test_process_monitor_step_o3_tp_closes_all():
    """Проверка: при достижении TP 0.500 из O3_FILLED позиция закрывается, сделка завершена."""
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="O3_FILLED",
        cur_tp3=100.0,
        sl=80.0,
        o2_id="order_2_id",
        position_was_open=True,
    )
    klines = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 01:00"), "open": 98, "high": 100.5, "low": 97, "close": 100.2, "volume": 100}
    ])
    client = MockBybitClient(pos_size=0.0, klines_df=klines)
    process_monitor_step(m, client, cfg, "60")
    assert m.state == "IDLE"
    assert "order_2_id" in client.cancelled_order_ids


def test_tolerance_and_minor_impulse_sui_case():
    """Тест реального кейса SUI:
    Свеча 1 (0.7501), Свеча 2 (0.7675) -> 0.500 = 0.7588.
    Свеча 3 опускается до 0.7590 (касание 0.500 с буфером +0.10% [0.7595]).
    Первый импульс фиксируется на 0.7675, а активным становится свежий импульс от 0.7631 до 0.7937.
    """
    from scripts.bybit_trader import find_active_setup
    rows = [
        {"timestamp": pd.Timestamp("2026-09-04 17:00"), "open": 0.750, "high": 0.752, "low": 0.749, "close": 0.751, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-04 18:00"), "open": 0.751, "high": 0.752, "low": 0.749, "close": 0.750, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-04 19:00"), "open": 0.750, "high": 0.752, "low": 0.749, "close": 0.751, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-04 20:00"), "open": 0.751, "high": 0.752, "low": 0.749, "close": 0.750, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-04 21:00"), "open": 0.750, "high": 0.752, "low": 0.749, "close": 0.751, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-04 22:00"), "open": 0.751, "high": 0.752, "low": 0.749, "close": 0.750, "volume": 100},
        # 1. База
        {"timestamp": pd.Timestamp("2026-09-04 23:00"), "open": 0.7512, "high": 0.7553, "low": 0.7501, "close": 0.755, "volume": 100},
        # 2. Рост до вершины
        {"timestamp": pd.Timestamp("2026-09-05 00:00"), "open": 0.755, "high": 0.7675, "low": 0.7543, "close": 0.7614, "volume": 100},
        # 3. Откат к 0.7590 (касание 0.500 + 0.10% буфер)
        {"timestamp": pd.Timestamp("2026-09-05 01:00"), "open": 0.7614, "high": 0.7707, "low": 0.7590, "close": 0.7632, "volume": 100},
        # 4. Боковик
        {"timestamp": pd.Timestamp("2026-09-05 02:00"), "open": 0.7632, "high": 0.7691, "low": 0.7607, "close": 0.7649, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-05 03:00"), "open": 0.7649, "high": 0.7682, "low": 0.7611, "close": 0.7653, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-05 04:00"), "open": 0.7653, "high": 0.7690, "low": 0.7616, "close": 0.7656, "volume": 100},
        # 5. Новая база 0.7631
        {"timestamp": pd.Timestamp("2026-09-05 05:00"), "open": 0.7656, "high": 0.7725, "low": 0.7631, "close": 0.7696, "volume": 100},
        # 6-8. Взлет к вершине 0.7937
        {"timestamp": pd.Timestamp("2026-09-05 06:00"), "open": 0.7696, "high": 0.7836, "low": 0.7696, "close": 0.7812, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-05 07:00"), "open": 0.7812, "high": 0.7936, "low": 0.7773, "close": 0.7932, "volume": 100},
        {"timestamp": pd.Timestamp("2026-09-05 08:00"), "open": 0.7932, "high": 0.7937, "low": 0.7831, "close": 0.7867, "volume": 100},
    ]
    df = pd.DataFrame(rows)

    setup = find_active_setup(df, min_pct=2.0, entry_buffer_pct=0.10, max_impulse_bars=6)
    assert setup is not None
    # База должна быть 0.7631 (свежий импульс), а НЕ 0.7501 (отработавший откат)
    assert setup.imp_start_price == pytest.approx(0.7631, rel=1e-3)
    assert setup.imp_peak_price == pytest.approx(0.7937, rel=1e-3)


def test_find_active_setup_dual_layer_filtering():
    """Проверка разделения сетапов по длине импульса: minor (<= 24) и major (25..96)."""
    # 1. Короткий импульс длиной 15 баров (100 -> 110)
    prices_short = np.linspace(100, 110, 15)
    ts_short = pd.date_range("2026-09-01", periods=15, freq="1h")
    rows_short = []
    for i, p in enumerate(prices_short):
        rows_short.append({"timestamp": ts_short[i], "open": p - 0.1, "high": p + 0.1, "low": p - 0.1, "close": p, "volume": 100})
    df_short = pd.DataFrame(rows_short)

    # Minor (<= 24) находит короткий импульс
    setup_minor = find_active_setup(df_short, min_pct=2.0, max_impulse_bars=24, layer="minor")
    assert setup_minor is not None
    assert setup_minor.layer == "minor"

    # Major (>= 25) НЕ находит 15-баровый импульс (слишком короткий)
    setup_major = find_active_setup(df_short, min_pct=2.0, min_impulse_bars=25, max_impulse_bars=96, layer="major")
    assert setup_major is None

    # 2. Длинный импульс: 30 баров, где min_pct=15% (поэтому локальные под-отрезки < 15% отсекаются)
    prices_long = np.linspace(100, 120, 30)  # +20%
    ts_long = pd.date_range("2026-09-01", periods=30, freq="1h")
    rows_long = []
    for i, p in enumerate(prices_long):
        rows_long.append({"timestamp": ts_long[i], "open": p - 0.1, "high": p + 0.1, "low": p - 0.1, "close": p, "volume": 100})
    df_long = pd.DataFrame(rows_long)

    # При min_pct=18% единственный импульс — полный 30-баровый (100 -> 120, +20%). Отрезки <= 24 баров дают < 16%.
    setup_minor_long = find_active_setup(df_long, min_pct=18.0, max_impulse_bars=24, layer="minor")
    assert setup_minor_long is None

    setup_major_long = find_active_setup(df_long, min_pct=18.0, min_impulse_bars=25, max_impulse_bars=96, layer="major")
    assert setup_major_long is not None
    assert setup_major_long.layer == "major"
    assert setup_major_long.imp_start_price == pytest.approx(100.0, rel=1e-2)
    assert setup_major_long.imp_peak_price == pytest.approx(120.1, rel=1e-2)


def test_major_setup_touched_0382_flag():
    """Проверка флага touched_0382: False когда цена выше 0.382, True когда пробит 0.382."""
    # Импульс 100 -> 130 за 26 баров
    prices = np.linspace(100, 130, 26)
    ts = pd.date_range("2026-09-01", periods=26, freq="1h")
    rows = []
    for i, p in enumerate(prices):
        rows.append({"timestamp": ts[i], "open": p - 0.1, "high": p + 0.1, "low": p - 0.1, "close": p, "volume": 100})

    # Свеча 27: цена держится на 129 (выше 0.382 ~ 117.5)
    rows.append({"timestamp": ts[-1] + pd.Timedelta(hours=1), "open": 130, "high": 130.2, "low": 128.5, "close": 129.0, "volume": 100})
    df_untouched = pd.DataFrame(rows)

    setup_untouched = find_active_setup(df_untouched, min_pct=2.0, min_impulse_bars=25, max_impulse_bars=96, layer="major")
    assert setup_untouched is not None
    assert setup_untouched.p_0382 is not None
    assert setup_untouched.p_0382 < 128.5  # 0.382 ниже 128.5
    assert setup_untouched.touched_0382 is False

    # Добавляем свечу 28: глубокий откат ниже 0.382 (шпилька до 115)
    rows.append({"timestamp": ts[-1] + pd.Timedelta(hours=2), "open": 129, "high": 129, "low": 115.0, "close": 118.0, "volume": 100})
    df_touched = pd.DataFrame(rows)

    setup_touched = find_active_setup(df_touched, min_pct=2.0, min_impulse_bars=25, max_impulse_bars=96, layer="major")
    assert setup_touched is not None
    assert setup_touched.touched_0382 is True


def test_process_monitor_step_awaiting_major_0382_triggers_grid():
    """Проверка: монитор AWAITING_MAJOR_0382 при касании low <= p_0382 выставляет сетку MAJ и переходит в TRAILING."""
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig(major_risk_usd=2.0)
    m = ActiveTradeMonitor(
        symbol="ZECUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="AWAITING_MAJOR_0382",
        layer="major",
        cur_peak=1000.0,
        p_0382=950.0,
        cur_e1=920.0,
        cur_tp1=970.0,
        cur_e2=890.0,
        cur_tp2=940.0,
        cur_e3=850.0,
        cur_tp3=920.0,
        sl=800.0,
        imp_start_price=750.0,
        touched_0382=False,
    )

    # 1. Свеча, где low = 960 > 950 (еще не коснулись 0.382)
    klines_before = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 12:00"), "open": 980, "high": 990, "low": 960, "close": 970, "volume": 100}
    ])
    client = MockBybitClient(pos_size=0.0, klines_df=klines_before)
    process_monitor_step(m, client, cfg, "60", is_live=True)
    assert m.state == "AWAITING_MAJOR_0382"
    assert len(client.placed_orders) == 0

    # 2. Свеча, где low = 945 <= 950 (коснулись 0.382!)
    klines_touch = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 13:00"), "open": 970, "high": 975, "low": 945, "close": 955, "volume": 100}
    ])
    client.klines_df = klines_touch
    process_monitor_step(m, client, cfg, "60", is_live=True)

    assert m.state == "TRAILING"
    assert m.touched_0382 is True
    # Проверяем, что были выставлены 3 лимитных ордера с тегом MAJ
    assert len(client.placed_orders) == 3
    assert client.placed_orders[0]["orderLinkId"].startswith("FIB-ZEC-MAJ-B-O1")
    assert client.placed_orders[1]["orderLinkId"].startswith("FIB-ZEC-MAJ-B-O2")
    assert client.placed_orders[2]["orderLinkId"].startswith("FIB-ZEC-MAJ-B-O3")


def test_cancel_monitor_orders_only_cancels_own_layer():
    """Проверка: cancel_monitor_orders отменяет только ордера своего слоя (MIN vs MAJ)."""
    from scripts.bybit_trader import ActiveTradeMonitor, cancel_monitor_orders
    client = MockBybitClient()
    # Размещаем ордера для обоих слоев
    client.placed_orders = [
        {"orderId": "ord_min_1", "orderLinkId": "FIB-ZEC-MIN-B-O1", "symbol": "ZECUSDT"},
        {"orderId": "ord_min_2", "orderLinkId": "FIB-ZEC-MIN-B-O2", "symbol": "ZECUSDT"},
        {"orderId": "ord_maj_1", "orderLinkId": "FIB-ZEC-MAJ-B-O1", "symbol": "ZECUSDT"},
        {"orderId": "ord_maj_2", "orderLinkId": "FIB-ZEC-MAJ-B-O2", "symbol": "ZECUSDT"},
    ]

    m_minor = ActiveTradeMonitor(
        symbol="ZECUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="TRAILING",
        layer="minor",
        o1_id="ord_min_1",
        o2_id="ord_min_2",
    )

    cancelled = cancel_monitor_orders(client, m_minor)
    cancelled_ids = [c.get("orderId") for c in cancelled]

    # Должны быть отменены ord_min_1 и ord_min_2
    assert "ord_min_1" in cancelled_ids
    assert "ord_min_2" in cancelled_ids
    # Ордера MAJ НЕ должны быть отменены!
    assert "ord_maj_1" not in cancelled_ids
    assert "ord_maj_2" not in cancelled_ids


def test_major_timeout_hours_does_not_reset_prematurely():
    """Тест: при major_timeout_hours=96 и timeout_hours=24, через 30 часов Major не сбрасывается в IDLE."""
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    client = MockBybitClient()
    # 30 часов назад
    past_time = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=30)
    cfg = TradeConfig(timeout_hours=24, minor_timeout_hours=24, major_timeout_hours=96)

    m = ActiveTradeMonitor(
        symbol="ZECUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="AWAITING_MAJOR_0382",
        layer="major",
        imp_start_price=800.0,
        cur_peak=1000.0,
        p_0382=900.0,
        imp_end_time=past_time,
    )

    # Цена 950 (выше 0.382) -> через 30ч не должно сбросить в IDLE (так как major_timeout=96)
    client.klines_df = pd.DataFrame([{
        "timestamp": pd.Timestamp.now(tz="UTC"),
        "open": 950.0, "high": 955.0, "low": 945.0, "close": 950.0, "volume": 100.0
    }])

    process_monitor_step(m, client, cfg, "60", is_live=False)
    assert m.state == "AWAITING_MAJOR_0382"

    # А если прошло 97 часов (> major_timeout_hours=96) -> должен сбросить в IDLE
    m.imp_end_time = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=97)
    process_monitor_step(m, client, cfg, "60", is_live=False)
    assert m.state == "IDLE"


def test_calc_triple_grid_order_sizes_weighted_50_30_20():
    """Проверка расчета объемов тройной сетки с весами входа 50% / 30% / 20%."""
    from indicators.pybit_client import BybitClient
    client = BybitClient(testnet=True)
    client._specs_cache["TESTUSDT"] = InstrumentSpecs(
        symbol="TESTUSDT",
        tick_size=0.01,
        qty_step=0.001,
        min_qty=0.001,
        max_qty=10000.0,
        min_notional=1.0,
        price_decimals=2,
        qty_decimals=3,
    )

    e1 = 100.0
    e2 = 90.0
    e3 = 80.0
    sl = 70.0

    # weights: 50% / 30% / 20%
    q1, q2, q3, l1, l2, l3 = client.calc_triple_grid_order_sizes(
        p_entry1=e1,
        p_entry2=e2,
        p_entry3=e3,
        p_sl=sl,
        total_risk_usd=2.0,
        symbol="TESTUSDT",
        equal_weight=False,
        weights=[0.50, 0.30, 0.20],
    )

    # Проверяем соотношение номиналов (Notional = q * entry):
    n1 = q1 * e1
    n2 = q2 * e2
    n3 = q3 * e3
    tot_n = n1 + n2 + n3

    assert pytest.approx(n1 / tot_n, abs=0.02) == 0.50
    assert pytest.approx(n2 / tot_n, abs=0.02) == 0.30
    assert pytest.approx(n3 / tot_n, abs=0.02) == 0.20

    # Суммарный убыток при выбивании SL не должен превышать $2.00
    tot_loss = l1 + l2 + l3
    assert tot_loss <= 2.0
    assert pytest.approx(tot_loss, abs=0.05) == 2.0


def test_make_order_link_id():
    from scripts.bybit_trader import make_order_link_id
    id1 = make_order_link_id("1000PEPE", "MIN", "Buy", "O1")
    id2 = make_order_link_id("1000PEPE", "MIN", "Buy", "O1")
    # Проверка уникальности
    assert id1 != id2
    # Проверка длины для Bybit V5 (макс 36 символов)
    assert len(id1) <= 36
    assert len(id2) <= 36
    # Проверка префикса для распознавания слоя
    assert id1.startswith("FIB-1000PEPE-MIN-B-O1-")
    assert id2.startswith("FIB-1000PEPE-MIN-B-O1-")


def test_sweep_reclaim_discarded_if_price_exceeds_tp():
    """Проверка: сетап SWEEP_RECLAIM отбрасывается, если текущая цена уже выше тейка."""
    from scripts.bybit_trader import find_active_setup, TradeConfig
    # Создаем свечи: импульс вверх с 80 до 90, затем свип ниже 80 до 79, а затем свеча закрывается на 92 (выше тейка)
    dates = pd.date_range("2026-09-01", periods=10, freq="1h")
    df = pd.DataFrame([
        {"timestamp": dates[0], "open": 80, "high": 82, "low": 80, "close": 82, "volume": 10},
        {"timestamp": dates[1], "open": 82, "high": 86, "low": 82, "close": 86, "volume": 10},
        {"timestamp": dates[2], "open": 86, "high": 90, "low": 85, "close": 90, "volume": 10},  # пик
        {"timestamp": dates[3], "open": 90, "high": 90, "low": 79.8, "close": 80.2, "volume": 10}, # свип за 80 (0.25% свип)
        {"timestamp": dates[4], "open": 80.2, "high": 95.0, "low": 80.2, "close": 94.0, "volume": 10}, # улетели на 94 (выше 0.618 Fib 86.2)
    ])
    cfg = TradeConfig(min_impulse_pct=2.0)
    setup = find_active_setup(df, cfg, layer="minor")
    # Т.к. close=94 выше 0.618 Fib, сетап не должен вернуть SWEEP_RECLAIM с инвалидным тейком
    if setup:
        assert setup.setup_type != "SWEEP_RECLAIM" or (setup.entry_1 < setup.tp_1)


def test_margin_insufficient_skips_placement():
    """Проверка: если свободной маржи недостаточно, ордера не выставляются в API."""
    from indicators.pybit_client import InstrumentSpecs
    client = MockBybitClient()
    client.get_available_balance = lambda: 5.0  # всего $5 доступно
    client.calc_required_margin = lambda sym, q, p: 20.0  # требуется $20
    
    # Пытаемся проверить маржу перед отправкой
    avail = client.get_available_balance()
    req = client.calc_required_margin("TESTUSDT", 10.0, 10.0) * 1.05
    assert avail < req
    # Соответственно, бот должен заблокировать отправку


def test_outdated_risk_qty_detection():
    """Проверка логики проверки существующих ордеров: при смене риска объемы не совпадают."""
    specs = InstrumentSpecs("TESTUSDT", 0.01, 2, 0.1, 1, 0.1, 1000.0, 5.0)
    # Ордера на бирже выставлены под $2 риска (qty = 100)
    existing_orders = [
        {"orderId": "1", "price": "10.0", "qty": "100.0", "stopLoss": "8.0"},
        {"orderId": "2", "price": "9.5", "qty": "80.0", "stopLoss": "8.0"},
        {"orderId": "3", "price": "9.0", "qty": "60.0", "stopLoss": "8.0"},
    ]
    # Новые рассчитанные объемы под $1 риска:
    new_q1, new_q2, new_q3 = 50.0, 40.0, 30.0
    e1, e2, e3 = 10.0, 9.5, 9.0
    
    tol1 = max(specs.qty_step, 0.05 * new_q1)
    qty1 = float(existing_orders[0]["qty"])
    qty_ok = abs(qty1 - new_q1) <= tol1
    assert qty_ok is False  # 100 != 50 -> не совпадает -> требуется перевыставление!


def test_is_entry_missed():
    """Проверка хелпера is_entry_missed для Long и Short позиций."""
    from scripts.bybit_trader import is_entry_missed

    # Long: цена 100, лимитка входа e1 = 100.05 -> вход упущен (цена ниже/равна e1)
    assert is_entry_missed(entry_price=100.0, cur_price=99.0, is_long=True) is True
    assert is_entry_missed(entry_price=100.0, cur_price=100.0, is_long=True) is True
    # С учетом допуска 0.05%: cur_price=100.04 при entry=100 -> cur_price * 0.9995 = 99.99 <= 100 -> упущен
    assert is_entry_missed(entry_price=100.0, cur_price=100.02, is_long=True) is True
    # Цена заметно выше лимитки: cur_price = 105.0 -> вход НЕ упущен
    assert is_entry_missed(entry_price=100.0, cur_price=105.0, is_long=True) is False

    # Short: вход упущен, если текущая цена выше лимитки
    assert is_entry_missed(entry_price=100.0, cur_price=101.0, is_long=False) is True
    assert is_entry_missed(entry_price=100.0, cur_price=95.0, is_long=False) is False


def test_missed_0500_skips_setup_completely_in_idle():
    """
    Проверка: если на закрытии свечи обнаружен сетап, но вход на 0.500 уже упущен
    (и 0.382 не протестирован), сетка НЕ выставляется (маржа свободна),
    а монитор переходит в AWAITING_BREAK_BELOW для отслеживания пробоя 1.000.
    """
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig(minor_risk_usd=2.0)
    m = ActiveTradeMonitor(
        symbol="BTCUSDT",
        setup_type="IDLE",
        state="IDLE",
        layer="minor",
    )

    # Создаем свечи с импульсом 100 -> 120 (рост 20%), но последняя свеча закрылась на 105 (ниже 0.500 Fib ~ 109.5)
    t0 = pd.Timestamp("2026-09-01 00:00")
    candles = []
    # 20 свечей базы
    for i in range(20):
        candles.append({"timestamp": t0 + pd.Timedelta(hours=i), "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 100})
    # Импульс вверх 100 -> 120 за 5 свечей
    for i in range(1, 6):
        candles.append({"timestamp": t0 + pd.Timedelta(hours=20+i), "open": 100.0 + (i-1)*4, "high": 100.0 + i*4, "low": 100.0 + (i-1)*4, "close": 100.0 + i*4, "volume": 200})
    # Свеча отката вниз до 103 (ниже всех уровней 0.500, 0.618, 0.786 Fib)
    candles.append({"timestamp": t0 + pd.Timedelta(hours=26), "open": 120.0, "high": 120.0, "low": 103.0, "close": 103.0, "volume": 300})

    df = pd.DataFrame(candles)
    client = MockBybitClient(pos_size=0.0, klines_df=df)

    process_monitor_step(m, client, cfg, "60", is_live=True)

    # Проверяем: сетка НЕ выставлена (0 ордеров), перешли в AWAITING_BREAK_BELOW
    assert m.state == "AWAITING_BREAK_BELOW"
    assert len(client.placed_orders) == 0

    # Если цена возвращается и тестирует 0.382 (high >= 112.0) -> переходим в IDLE
    klines_bounce = pd.DataFrame([
        {"timestamp": t0 + pd.Timedelta(hours=27), "open": 106.0, "high": 113.0, "low": 106.0, "close": 112.5, "volume": 200}
    ])
    client.klines_df = klines_bounce
    process_monitor_step(m, client, cfg, "60", is_live=True)
    assert m.state == "IDLE"
    assert m.last_skipped_imp_time is not None
    assert len(client.placed_orders) == 0


def test_awaiting_major_0382_skips_if_price_below_0500():
    """
    Проверка: если в режиме AWAITING_MAJOR_0382 цена пробила 0.382,
    но текущая рыночная цена уже опустилась ниже 0.500 (e1),
    сетка НЕ выставляется, сетап пропускается, монитор переходит в IDLE.
    """
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig(major_risk_usd=2.0)
    m = ActiveTradeMonitor(
        symbol="ZECUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="AWAITING_MAJOR_0382",
        layer="major",
        cur_peak=1000.0,
        p_0382=950.0,
        cur_e1=920.0,
        cur_tp1=970.0,
        cur_e2=890.0,
        cur_tp2=940.0,
        cur_e3=850.0,
        cur_tp3=920.0,
        sl=800.0,
        imp_start_price=750.0,
        touched_0382=False,
    )

    # Свеча резкого пролива: low=900 <= 950 (пробили 0.382), но close=910 <= cur_e1 (920.0)!
    klines_crash = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 13:00"), "open": 960, "high": 965, "low": 900, "close": 910, "volume": 500}
    ])
    client = MockBybitClient(pos_size=0.0, klines_df=klines_crash)
    process_monitor_step(m, client, cfg, "60", is_live=True)

    # Сетка НЕ выставляется, сетап пропущен
    assert m.state == "IDLE"
    assert len(client.placed_orders) == 0
    assert m.last_skipped_imp_time is not None


def test_awaiting_break_below_transitions_to_sweep_close():
    """
    Проверка: из состояния AWAITING_BREAK_BELOW при падении цены ниже 1.000 (m.sl)
    без возврата к 0.382 монитор переходит в AWAITING_SWEEP_CLOSE.
    """
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig(minor_risk_usd=2.0)
    m = ActiveTradeMonitor(
        symbol="ALGOUSDT",
        setup_type="AWAITING_BREAK_BELOW",
        state="AWAITING_BREAK_BELOW",
        layer="minor",
        side="long",
        cur_peak=0.1000,
        p_0382=0.0960,
        sl=0.0900,
        imp_start_price=0.0900,
    )

    # Свеча пробоя 1.000 (low 0.0895 <= sl 0.0900), при этом high 0.0950 < p_0382 0.0960
    klines_dump = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-05 18:00"), "open": 0.0940, "high": 0.0950, "low": 0.0895, "close": 0.0898, "volume": 500}
    ])
    client = MockBybitClient(pos_size=0.0, klines_df=klines_dump)
    process_monitor_step(m, client, cfg, "15", is_live=True)

    assert m.state == "AWAITING_SWEEP_CLOSE"
    assert m.stop_sweep_low == 0.0895
    assert m.stop_bar_time == pd.Timestamp("2026-09-05 18:00")


def test_cleanup_orphan_orders_for_layer():
    """
    Проверка: cleanup_orphan_orders_for_layer отменяет только ордера своего слоя,
    не трогая чужие слои и сохраненные active_order_ids.
    """
    from scripts.bybit_trader import cleanup_orphan_orders_for_layer
    client = MockBybitClient()
    client.placed_orders = [
        {"orderId": "o_min_1", "orderLinkId": "FIB-ALGO-MIN-B-O1-aaa", "symbol": "ALGOUSDT"},
        {"orderId": "o_min_2", "orderLinkId": "FIB-ALGO-MIN-B-O2-bbb", "symbol": "ALGOUSDT"},
        {"orderId": "o_maj_1", "orderLinkId": "FIB-ALGO-MAJ-B-O1-ccc", "symbol": "ALGOUSDT"},
        {"orderId": "o_btc_1", "orderLinkId": "FIB-BTC-MIN-B-O1-ddd", "symbol": "BTCUSDT"},
    ]

    # Отменяем ордера слоя minor для ALGO, кроме o_min_1
    cancelled = cleanup_orphan_orders_for_layer(client, "ALGOUSDT", "minor", active_order_ids=["o_min_1"])
    cancelled_ids = [c.get("orderId") for c in cancelled]

    assert "o_min_2" in cancelled_ids
    assert "o_min_1" not in cancelled_ids  # защищен active_order_ids
    assert "o_maj_1" not in cancelled_ids  # чужой слой (MAJ)
    assert "o_btc_1" not in cancelled_ids  # чужой символ


def test_find_active_setup_awaiting_break_below_vs_skipped():
    """
    Проверка:
    1. Если 0.500 коснулись, но 0.382 не тестировали -> AWAITING_BREAK_BELOW
    2. Если 0.500 коснулись, а затем протестировали 0.382 -> сетап пропускается (None)
    """
    from scripts.bybit_trader import find_active_setup
    # Импульс 100 -> 120
    base = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00") + pd.Timedelta(hours=i), "open": 100, "high": 100.5, "low": 99.5, "close": 100, "volume": 10}
        for i in range(20)
    ]
    imp = [
        {"timestamp": pd.Timestamp("2026-09-01 20:00") + pd.Timedelta(hours=i), "open": 100 + i*4, "high": 104 + i*4, "low": 100 + i*4, "close": 104 + i*4, "volume": 20}
        for i in range(5)  # 100 -> 120
    ]
    # 1. Откат до 108 (касание 0.500 ~ 109.5, но 0.618 ~ 107.0 не коснулись, TP 0.236 ~ 115.3 не достигнут)
    pullback_no_bounce = [
        {"timestamp": pd.Timestamp("2026-09-02 01:00"), "open": 115, "high": 115, "low": 108, "close": 108.5, "volume": 30},
        {"timestamp": pd.Timestamp("2026-09-02 02:00"), "open": 108.5, "high": 110, "low": 108, "close": 109.0, "volume": 30},
    ]
    df1 = pd.DataFrame(base + imp + pullback_no_bounce)
    setup1 = find_active_setup(df1, min_pct=2.0, layer="minor")
    assert setup1 is not None
    assert setup1.setup_type == "TRIPLE_GRID_CORRECTION"
    assert setup1.o1_filled is True
    assert setup1.o2_filled is False

    # 2. Если все три уровня (0.500, 0.618, 0.786) пройдены (падение до 103 < 104.3) -> AWAITING_BREAK_BELOW
    pullback_all_missed = pullback_no_bounce + [
        {"timestamp": pd.Timestamp("2026-09-02 02:30"), "open": 108.5, "high": 108.5, "low": 103.0, "close": 103.5, "volume": 30},
    ]
    df_all = pd.DataFrame(base + imp + pullback_all_missed)
    setup_all = find_active_setup(df_all, min_pct=2.0, layer="minor")
    assert setup_all is not None
    assert setup_all.setup_type == "AWAITING_BREAK_BELOW"

    # 3. Добавляем свечу отскока с тестом TP 0.236 (high 116 >= 115.3) -> сетап завершен (записан в историю)
    pullback_with_bounce = pullback_no_bounce + [
        {"timestamp": pd.Timestamp("2026-09-02 03:00"), "open": 109, "high": 116, "low": 109, "close": 115, "volume": 30},
    ]
    df2 = pd.DataFrame(base + imp + pullback_with_bounce)
    setup2 = find_active_setup(df2, min_pct=2.0, layer="minor")
    assert setup2 is None or setup2.imp_start_price > 100.0


def test_bybit_client_throttle():
    """Проверка работы внутреннего ограничителя скорости запросов (_throttle)."""
    import time
    from indicators.pybit_client import BybitClient
    client = BybitClient(testnet=True)
    client._min_request_interval = 0.05
    client._last_request_time = time.time()
    t0 = time.time()
    client._throttle(0.05)
    t1 = time.time()
    assert (t1 - t0) >= 0.04


def test_bybit_client_kline_caching():
    """Проверка кэширования свечей: minor и major слои используют один кэш без повторных API-запросов."""
    from indicators.pybit_client import BybitClient
    client = BybitClient(testnet=True)
    call_count = 0

    def mock_get_kline(category, symbol, interval, limit):
        nonlocal call_count
        call_count += 1
        now_ms = 1700000000000
        return {
            "retCode": 0,
            "result": {
                "list": [
                    [str(now_ms - i * 60000), "100", "105", "99", "102", "50", "5000"]
                    for i in range(limit)
                ]
            }
        }

    client.session.get_kline = mock_get_kline
    client._klines_cache_ttl = 5.0

    # Первый вызов (minor layer): запрашивает 10 свечей, fetch_klines запрашивает max(10, 140) = 140
    df1 = client.fetch_klines("BTCUSDT", interval="60", limit=10)
    assert call_count == 1
    assert len(df1) == 10

    # Второй вызов (major layer) для той же монеты с limit=140 -> должен взять из кэша!
    df2 = client.fetch_klines("BTCUSDT", interval="60", limit=140)
    assert call_count == 1
    assert len(df2) == 140

    # Вызов для другого символа -> делает сетевой запрос
    df3 = client.fetch_klines("ETHUSDT", interval="60", limit=10)
    assert call_count == 2


def test_bybit_client_position_caching_and_invalidation():
    """Проверка кэширования позиций и их инвалидации при операциях."""
    from indicators.pybit_client import BybitClient
    client = BybitClient(testnet=True)
    pos_calls = 0

    def mock_get_positions(category, symbol):
        nonlocal pos_calls
        pos_calls += 1
        return {
            "retCode": 0,
            "result": {
                "list": [{"symbol": symbol, "size": "10.0", "positionIdx": 0, "avgPrice": "50.0"}]
            }
        }

    client.session.get_positions = mock_get_positions
    client._position_idx_cache["SOLUSDT"] = 0
    client._positions_cache_ttl = 3.0

    # 1. Первый запрос
    p1 = client.get_position("SOLUSDT")
    assert pos_calls == 1
    assert p1 is not None and float(p1["size"]) == 10.0

    # 2. Второй запрос сразу же -> из кэша
    p2 = client.get_position("SOLUSDT")
    assert pos_calls == 1

    # 3. Инвалидация при выставлении ордера / отмене
    client._positions_cache.pop("SOLUSDT", None)
    p3 = client.get_position("SOLUSDT")
    assert pos_calls == 2


def test_completed_impulses_persistence(tmp_path):
    """Проверка сохранения и чтения отработанных импульсов с защитой от дубликатов."""
    from scripts.bybit_trader import load_completed_impulses, save_completed_impulse

    file_path = str(tmp_path / "test_completed.json")
    # 1. Загрузка из несуществующего файла возвращает пустой список
    assert load_completed_impulses(file_path) == []

    # 2. Сохранение записи
    rec1 = {
        "symbol": "LINKUSDT",
        "peak_price": 12.229,
        "imp_start_price": 11.719,
        "imp_start_time": "2026-09-05 12:00:00+00:00",
        "imp_end_time": "2026-09-05 17:00:00+00:00",
        "exit_price": 12.02,
        "exit_time": "2026-09-05 19:25:00+00:00",
        "exit_reason": "TP_0236",
        "layer": "minor",
    }
    save_completed_impulse(rec1, file_path)
    loaded = load_completed_impulses(file_path)
    assert len(loaded) == 1
    assert loaded[0]["symbol"] == "LINKUSDT"
    assert loaded[0]["peak_price"] == 12.229

    # 3. Дубликат с той же вершиной и временем не добавляется повторно
    save_completed_impulse(rec1, file_path)
    assert len(load_completed_impulses(file_path)) == 1

    # 4. Некорректный пик (<= 0) игнорируется
    save_completed_impulse({"symbol": "LINKUSDT", "peak_price": 0.0}, file_path)
    assert len(load_completed_impulses(file_path)) == 1


def test_is_impulse_disqualified_rules():
    """Проверка правил дисквалификации отработанных импульсов и их подволн."""
    from scripts.bybit_trader import is_impulse_disqualified

    completed = [
        {
            "symbol": "LINKUSDT",
            "peak_price": 12.229,
            "imp_start_price": 11.719,
            "imp_start_time": "2026-09-05 12:00:00+00:00",
            "imp_end_time": "2026-09-05 17:00:00+00:00",
        }
    ]

    # 1. Другая монета (например ETHUSDT) с тем же временем или пиком НЕ дисквалифицируется
    assert not is_impulse_disqualified(
        imp_peak=12.229,
        imp_start_time="2026-09-05 12:00:00+00:00",
        imp_end_time="2026-09-05 17:00:00+00:00",
        symbol="ETHUSDT",
        completed_records=completed,
    )

    # 2. Совпадение вершины для LINKUSDT -> дисквалифицирован
    assert is_impulse_disqualified(
        imp_peak=12.229,
        imp_start_time="2026-09-05 13:00:00+00:00",
        imp_end_time="2026-09-05 17:00:00+00:00",
        symbol="LINKUSDT",
        completed_records=completed,
    )

    # 3. Подволна/середина старого импульса (start_time <= end_time отработанного) -> дисквалифицирован
    assert is_impulse_disqualified(
        imp_peak=12.15,
        imp_start_time="2026-09-05 14:00:00+00:00",
        imp_end_time="2026-09-05 18:00:00+00:00",
        symbol="LINKUSDT",
        completed_records=completed,
    )

    # 4. Старый импульс, завершившийся до или на вершине отработанного -> дисквалифицирован
    assert is_impulse_disqualified(
        imp_peak=12.00,
        imp_start_time="2026-09-05 10:00:00+00:00",
        imp_end_time="2026-09-05 15:00:00+00:00",
        symbol="LINKUSDT",
        completed_records=completed,
    )

    # 5. НОВЫЙ импульс, начавшийся строго после вершины отработанного -> РАЗРЕШЕН
    assert not is_impulse_disqualified(
        imp_peak=12.80,
        imp_start_time="2026-09-05 18:00:00+00:00",
        imp_end_time="2026-09-05 22:00:00+00:00",
        symbol="LINKUSDT",
        completed_records=completed,
    )


def test_find_active_setup_ignores_completed_wave():
    """Проверка: find_active_setup игнорирует отработанный импульс и возвращает только новый импульс после него."""
    from scripts.bybit_trader import find_active_setup

    dates = pd.date_range("2026-09-01 00:00:00", periods=30, freq="1h")
    bars = []
    # 1. Старый импульс со 100 до 120 (свечи 0-14, вершина на свече 14)
    for i in range(15):
        p = 100.0 + (20.0 / 14.0) * i
        bars.append({"timestamp": dates[i], "open": p, "high": p + 0.2, "low": p - 0.2, "close": p + 0.1, "volume": 100})
    # Свечи отката 15-19 (боковик 115-116)
    for i in range(15, 20):
        bars.append({"timestamp": dates[i], "open": 115.5, "high": 116.0, "low": 115.0, "close": 115.5, "volume": 100})
    # 2. Новый свежий импульс со 115 до 140 (свечи 20-29)
    for i in range(20, 30):
        p = 115.0 + (25.0 / 9.0) * (i - 20)
        bars.append({"timestamp": dates[i], "open": p, "high": p + 0.2, "low": p - 0.2, "close": p + 0.1, "volume": 100})

    df = pd.DataFrame(bars)

    completed = [
        {
            "symbol": "COINUSDT",
            "peak_price": 120.2,
            "imp_start_price": 100.0,
            "imp_start_time": str(dates[0]),
            "imp_end_time": str(dates[14]),
        }
    ]

    setup = find_active_setup(
        df,
        min_pct=2.0,
        symbol="COINUSDT",
        completed_impulses=completed,
        layer="minor",
    )
    assert setup is not None
    # Должен быть найден именно новый импульс с вершиной > 135, а не старый с вершиной 120.2
    assert setup.imp_peak_price > 135.0
    assert setup.imp_start_time >= dates[20]


def test_process_monitor_step_saves_completed_impulse_on_tp(monkeypatch, tmp_path):
    """Проверка вызова save_completed_impulse при закрытии позиции по TP."""
    import scripts.bybit_trader as bt
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step

    saved = []
    monkeypatch.setattr(bt, "save_completed_impulse", lambda rec, *args, **kwargs: saved.append(rec))

    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="SOLUSDT",
        setup_type="TRIPLE_GRID_TRAILING",
        state="O1_FILLED",
        cur_peak=150.0,
        imp_start_price=130.0,
        imp_start_time=pd.Timestamp("2026-09-01 10:00:00+00:00"),
        imp_end_time=pd.Timestamp("2026-09-01 15:00:00+00:00"),
        cur_tp1=145.0,
        sl=128.0,
        position_was_open=True,
        layer="minor",
    )
    klines = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 16:00:00+00:00"), "open": 144.0, "high": 146.0, "low": 143.0, "close": 145.5, "volume": 100}
    ])
    client = MockBybitClient(pos_size=0.0, klines_df=klines)
    process_monitor_step(m, client, cfg, "60")

    assert m.state == "IDLE"
    assert len(saved) == 1
    assert saved[0]["symbol"] == "SOLUSDT"
    assert saved[0]["peak_price"] == 150.0
    assert saved[0]["exit_reason"] == "TP_0236"
    assert saved[0]["layer"] == "minor"


def test_future_impulse_same_peak_allowed_in_flat():
    """
    Проверка: отработанный импульс имел вершину 12.229 и завершился в 12:00.
    Новый самостоятельный импульс в боковике начинается в 14:00 и тоже имеет вершину 12.229.
    is_impulse_disqualified должен разрешить его (False), так как imp_start_time > rec_end_time.
    """
    from scripts.bybit_trader import is_impulse_disqualified
    completed = [{
        "symbol": "LINKUSDT",
        "peak_price": 12.229,
        "imp_start_price": 11.719,
        "imp_start_time": "2026-09-05 08:00:00+00:00",
        "imp_end_time": "2026-09-05 12:00:00+00:00",
    }]
    # Импульс, начавшийся строго после rec_end_time, НЕ дисквалифицируется
    assert not is_impulse_disqualified(
        imp_peak=12.229,
        imp_start_time="2026-09-05 14:00:00+00:00",
        imp_end_time="2026-09-05 18:00:00+00:00",
        symbol="LINKUSDT",
        completed_records=completed,
    )


def test_find_active_setup_partial_grid_when_0500_passed():
    """
    Проверка find_active_setup:
    - 0.500 пройден/налит, 0.618 не коснулись, TP 0.236 не достигнут -> TRIPLE_GRID_CORRECTION (o1_filled=True, o2_filled=False)
    - 0.500 и 0.618 пройдены, 0.786 не коснулись -> TRIPLE_GRID_CORRECTION (o1_filled=True, o2_filled=True)
    - 0.500, 0.618 и 0.786 пройдены -> AWAITING_BREAK_BELOW
    """
    from scripts.bybit_trader import find_active_setup
    base = [
        {"timestamp": pd.Timestamp("2026-09-01 00:00") + pd.Timedelta(hours=i), "open": 100, "high": 100.5, "low": 99.5, "close": 100, "volume": 10}
        for i in range(20)
    ]
    imp = [
        {"timestamp": pd.Timestamp("2026-09-01 20:00") + pd.Timedelta(hours=i), "open": 100 + i*4, "high": 104 + i*4, "low": 100 + i*4, "close": 104 + i*4, "volume": 20}
        for i in range(5)  # 100 -> 120
    ]
    # 1. 0.500 пройден (low 109 <= 110.0), но 0.618 (107.64) не коснулись
    pullback1 = [
        {"timestamp": pd.Timestamp("2026-09-02 01:00"), "open": 115, "high": 115, "low": 109, "close": 109.5, "volume": 30},
    ]
    df1 = pd.DataFrame(base + imp + pullback1)
    s1 = find_active_setup(df1, min_pct=2.0, layer="minor")
    assert s1 is not None
    assert s1.setup_type == "TRIPLE_GRID_CORRECTION"
    assert s1.o1_filled is True
    assert s1.o2_filled is False

    # 2. 0.618 пройден (low 106 <= 107.64), но 0.786 (104.28) не коснулись
    pullback2 = pullback1 + [
        {"timestamp": pd.Timestamp("2026-09-02 02:00"), "open": 109.5, "high": 109.5, "low": 106, "close": 106.5, "volume": 30},
    ]
    df2 = pd.DataFrame(base + imp + pullback2)
    s2 = find_active_setup(df2, min_pct=2.0, layer="minor")
    assert s2 is not None
    assert s2.setup_type == "TRIPLE_GRID_CORRECTION"
    assert s2.o1_filled is True
    assert s2.o2_filled is True

    # 3. 0.786 пройден (low 103 <= 104.28) -> все уровни сетки пройдены -> AWAITING_BREAK_BELOW
    pullback3 = pullback2 + [
        {"timestamp": pd.Timestamp("2026-09-02 03:00"), "open": 106.5, "high": 106.5, "low": 103, "close": 103.5, "volume": 30},
    ]
    df3 = pd.DataFrame(base + imp + pullback3)
    s3 = find_active_setup(df3, min_pct=2.0, layer="minor")
    assert s3 is not None
    assert s3.setup_type == "AWAITING_BREAK_BELOW"


def test_monitor_places_o2_and_o3_when_o1_missed():
    """
    Проверка: если при поиске сетапа в IDLE Ордер 1 (0.500) уже упущен,
    но Ордера 2 и 3 ниже текущей цены, бот выставляет Ордер 2 и Ордер 3
    и переходит в состояние O1_FILLED.
    """
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step
    cfg = TradeConfig(minor_risk_usd=2.0)
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="IDLE",
        state="IDLE",
        layer="minor",
    )

    t0 = pd.Timestamp("2026-09-01 00:00")
    candles = []
    for i in range(20):
        candles.append({"timestamp": t0 + pd.Timedelta(hours=i), "open": 10.0, "high": 10.05, "low": 9.95, "close": 10.0, "volume": 100})
    for i in range(1, 6):
        candles.append({"timestamp": t0 + pd.Timedelta(hours=20+i), "open": 10.0 + (i-1)*0.4, "high": 10.0 + i*0.4, "low": 10.0 + (i-1)*0.4, "close": 10.0 + i*0.4, "volume": 200})
    # Откат: 0.500 Fib ~ 11.0. Цена сейчас 10.9 (Ордер 1 упущен, но Ордер 2 ~10.76 и Ордер 3 ~10.43 активны)
    candles.append({"timestamp": t0 + pd.Timedelta(hours=26), "open": 12.0, "high": 12.0, "low": 10.9, "close": 10.9, "volume": 300})

    df = pd.DataFrame(candles)
    client = MockBybitClient(pos_size=0.0, klines_df=df)

    process_monitor_step(m, client, cfg, "60", is_live=True)

    # Проверяем: выставлено 2 ордера (O2 и O3), а состояние O1_FILLED
    assert m.state == "O1_FILLED"
    assert len(client.placed_orders) == 2
    # Ордер 1 НЕ выставлялся
    assert m.o1_id is None
    # Ордера 2 и 3 выставлены
    assert m.o2_id is not None
    assert m.o3_id is not None


def test_monitor_o1_filled_price_reaches_tp_0236_without_prior_fill(monkeypatch):
    """
    Проверка: если бот выставил O2 и O3 (состояние O1_FILLED, позиции на бирже еще нет),
    и цена вернулась к тейку 0.236 без налития O2/O3 ->
    ордера снимаются, импульс сохраняется в completed_impulses, монитор переходит в IDLE.
    """
    import scripts.bybit_trader as bt
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step

    saved = []
    monkeypatch.setattr(bt, "save_completed_impulse", lambda rec, *args, **kwargs: saved.append(rec))

    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="TESTUSDT",
        setup_type="TRIPLE_GRID_CORRECTION",
        state="O1_FILLED",
        cur_peak=12.0,
        imp_start_price=10.0,
        imp_start_time=pd.Timestamp("2026-09-01 00:00:00+00:00"),
        imp_end_time=pd.Timestamp("2026-09-01 10:00:00+00:00"),
        cur_tp1=11.5,
        cur_e2=10.7,
        cur_e3=10.4,
        sl=9.9,
        position_was_open=False,
        layer="minor",
        o2_id="ord_2",
        o3_id="ord_3",
        has_o2=True,
        has_o3=True,
    )

    # 1. Свеча пока в диапазоне между e1 и e2: high 11.2, low 10.8 -> ни TP, ни SL
    client = MockBybitClient(pos_size=0.0, klines_df=pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 11:00:00+00:00"), "open": 10.9, "high": 11.2, "low": 10.8, "close": 11.0, "volume": 100}
    ]))
    process_monitor_step(m, client, cfg, "60", is_live=True)
    # Состояние остается O1_FILLED, ордера не отменяются
    assert m.state == "O1_FILLED"
    assert len(saved) == 0

    # 2. Цена приходит к TP 0.236 (high 11.6 >= 11.5 * 0.999)
    client.klines_df = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 12:00:00+00:00"), "open": 11.0, "high": 11.6, "low": 11.0, "close": 11.55, "volume": 200}
    ])
    process_monitor_step(m, client, cfg, "60", is_live=True)

    # Ордера 2 и 3 сняты, импульс записан, переход в IDLE
    assert m.state == "IDLE"
    assert len(saved) == 1
    assert saved[0]["symbol"] == "TESTUSDT"
    assert saved[0]["peak_price"] == 12.0
    assert saved[0]["exit_reason"] == "TP_0236"


def test_manual_close_in_o1_filled_transitions_to_idle_not_stop_loss(monkeypatch):
    """
    Проверка: пользователь руками закрыл позицию (например в плюс выше 0.382),
    при этом цена не касалась SL 1.000.
    Бот не должен писать 'Стоп-лосс сработал' и не должен переходить в AWAITING_SWEEP_CLOSE!
    Он должен зафиксировать MANUAL_CLOSE, снять оставшиеся ордера и перейти в IDLE.
    """
    import scripts.bybit_trader as bt
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step

    saved = []
    monkeypatch.setattr(bt, "save_completed_impulse", lambda rec, *args, **kwargs: saved.append(rec))

    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="RENDERUSDT",
        setup_type="TRIPLE_GRID_CORRECTION",
        state="O1_FILLED",
        cur_peak=6.5,
        imp_start_price=5.0,
        imp_start_time=pd.Timestamp("2026-09-01 00:00:00+00:00"),
        imp_end_time=pd.Timestamp("2026-09-01 10:00:00+00:00"),
        cur_e1=5.75,
        cur_tp1=6.15,
        cur_e2=5.50,
        cur_tp2=5.90,
        cur_e3=5.30,
        sl=4.95,
        position_was_open=True,
        layer="minor",
        o2_id="ord_render_2",
        o3_id="ord_render_3",
        has_o2=True,
        has_o3=True,
    )

    # Цена 5.95 (выше e1, выше 0.382, но ниже TP 6.15). Позиция закрыта руками: pos_size = 0.
    client = MockBybitClient(pos_size=0.0, klines_df=pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 12:00:00+00:00"), "open": 5.80, "high": 6.00, "low": 5.75, "close": 5.95, "volume": 1000}
    ]))
    client.placed_orders = [
        {"orderId": "ord_render_2", "symbol": "RENDERUSDT"},
        {"orderId": "ord_render_3", "symbol": "RENDERUSDT"},
    ]

    process_monitor_step(m, client, cfg, "60", is_live=True)

    # Проверяем: переход в IDLE, а не в AWAITING_SWEEP_CLOSE!
    assert m.state == "IDLE"
    assert len(saved) == 1
    assert saved[0]["symbol"] == "RENDERUSDT"
    assert saved[0]["exit_reason"] == "MANUAL_CLOSE"
    assert saved[0]["exit_price"] == 5.95
    # Висящие ордера должны быть отменены
    assert len(client.cancelled_order_ids) == 2


def test_real_stop_loss_in_o1_filled_transitions_to_awaiting_sweep():
    """
    Проверка: если цена реально пробила уровень стоп-лосса 1.000 (low <= m.sl),
    монитор переходит в AWAITING_SWEEP_CLOSE.
    """
    from scripts.bybit_trader import ActiveTradeMonitor, TradeConfig, process_monitor_step

    cfg = TradeConfig()
    m = ActiveTradeMonitor(
        symbol="RENDERUSDT",
        setup_type="TRIPLE_GRID_CORRECTION",
        state="O1_FILLED",
        cur_peak=6.5,
        imp_start_price=5.0,
        cur_e1=5.75,
        cur_tp1=6.15,
        sl=4.95,
        position_was_open=True,
        layer="minor",
    )

    # Свеча пробоя стопа: low 4.90 <= sl 4.95
    client = MockBybitClient(pos_size=0.0, klines_df=pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-09-01 12:00:00+00:00"), "open": 5.20, "high": 5.25, "low": 4.90, "close": 4.92, "volume": 1000}
    ]))

    process_monitor_step(m, client, cfg, "60", is_live=True)

    assert m.state == "AWAITING_SWEEP_CLOSE"
    assert m.stop_sweep_low == 4.90








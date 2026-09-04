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
    assert setup.setup_type == "DUAL_GRID_TRAILING"
    assert setup.side == "long"
    assert setup.imp_peak_price == pytest.approx(110.2, rel=1e-2)
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
    """Синтетический глубокий пробой основания без возврата (манипуляция к 1.618/2.000)."""
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
    assert setup.entry_1 < 100.0  # Уровень 1.618 Fib
    assert setup.entry_2 < setup.entry_1  # Уровень 2.000 Fib
    assert setup.stop_loss < setup.entry_2  # Стоп 2.414 Fib


def test_entry_buffer_007():
    """Проверка, что буфер 0.07% сдвигает входы ровно на +0.07% выше уровня Фибоначчи."""
    # Импульс 812.25 -> 1054.94 (как на ZEC)
    p_0500 = calc_fib(1054.94, 812.25, 0.500, is_long=True, scale="log")
    assert p_0500 == pytest.approx(925.68, rel=1e-3)

    # Применяем буфер 0.07%
    buffered_e1 = p_0500 * (1.0 + 0.07 / 100.0)
    assert buffered_e1 == pytest.approx(926.32, rel=1e-3)
    assert buffered_e1 > p_0500
    assert (buffered_e1 - p_0500) / p_0500 * 100.0 == pytest.approx(0.07, abs=1e-4)


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
    assert cfg.entry_buffer_pct == 0.07
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
  total_risk_usd: 5.0
buffers:
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
    assert cfg.total_risk_usd == 5.0
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


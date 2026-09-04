import pandas as pd
import pytest

from scripts.strategy_engine import GridConfig, simulate_grid
from scripts.backtest_strategy_interactive import Impulse
from scripts.pyalgotrade_engine import run_pyalgotrade_backtest, PyAlgoTradeGridStrategy, DataFrameBarFeed


def test_pyalgotrade_solo_1_matches_simulate_grid():
    """
    Проверка паритета solo_1 между PyAlgoTrade и кастомным simulate_grid:
    Вход на 0.500, тейк на 0.236.
    """
    df = pd.DataFrame([
        {"timestamp": "2026-09-01 10:00:00", "high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"timestamp": "2026-09-01 11:00:00", "high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},
        {"timestamp": "2026-09-01 12:00:00", "high": 108.0, "low": 104.0, "open": 105.0, "close": 107.8},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="solo_1", entry_fib_1=0.500, tp_fib_1=0.236, sl_fib=1.000)

    trades_custom = simulate_grid(df, [imp], cfg)
    trades_algo, metrics = run_pyalgotrade_backtest(df, [imp], cfg)

    assert len(trades_custom) == 1
    assert len(trades_algo) == 1

    t_c = trades_custom[0]
    t_a = trades_algo[0]

    assert t_a.win == t_c.win
    assert t_a.outcome == t_c.outcome
    assert pytest.approx(t_a.pnl, rel=1e-3) == t_c.pnl
    assert t_a.exit_idx == t_c.exit_idx
    assert metrics["n_trades"] == 1


def test_pyalgotrade_solo_2_matches_simulate_grid():
    """
    Проверка паритета solo_2 между PyAlgoTrade и кастомным simulate_grid:
    Вход на 0.618, тейк на 0.382.
    """
    df = pd.DataFrame([
        {"timestamp": "2026-09-01 10:00:00", "high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"timestamp": "2026-09-01 11:00:00", "high": 105.0, "low": 103.0, "open": 105.0, "close": 103.5},
        {"timestamp": "2026-09-01 12:00:00", "high": 107.0, "low": 103.5, "open": 104.0, "close": 106.8},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="solo_2", entry_fib_2=0.618, tp_fib_2=0.382, sl_fib=1.000)

    trades_custom = simulate_grid(df, [imp], cfg)
    trades_algo, metrics = run_pyalgotrade_backtest(df, [imp], cfg)

    assert len(trades_custom) == 1
    assert len(trades_algo) == 1

    t_c = trades_custom[0]
    t_a = trades_algo[0]

    assert t_a.win is True
    assert t_a.outcome == "TP2"
    assert t_a.outcome == t_c.outcome
    assert pytest.approx(t_a.pnl, rel=1e-3) == t_c.pnl
    assert t_a.exit_idx == t_c.exit_idx


def test_pyalgotrade_dual_one_and_done():
    """
    Проверка dual режима с правилом One-and-Done:
    Вход 0.500, затем быстрый тейк 0.236 без касания 0.618 -> отмена ордера 2.
    """
    df = pd.DataFrame([
        {"timestamp": "2026-09-01 10:00:00", "high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"timestamp": "2026-09-01 11:00:00", "high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},
        {"timestamp": "2026-09-01 12:00:00", "high": 108.0, "low": 105.0, "open": 105.0, "close": 107.8},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="dual", entry_fib_1=0.500, tp_fib_1=0.236, entry_fib_2=0.618, tp_fib_2=0.382, sl_fib=1.000)

    trades_custom = simulate_grid(df, [imp], cfg)
    trades_algo, metrics = run_pyalgotrade_backtest(df, [imp], cfg)

    assert len(trades_algo) == 1
    assert trades_algo[0].outcome == "TP1_only"
    assert trades_algo[0].only_o1 is True
    assert trades_algo[0].both_entered is False
    assert pytest.approx(trades_algo[0].pnl, rel=1e-3) == trades_custom[0].pnl


def test_pyalgotrade_dual_sequential_fill():
    """
    Проверка dual режима с последовательным набором:
    Вход 0.500 -> просадка до 0.618 -> вход 2 -> оба закрываются по TP.
    """
    df = pd.DataFrame([
        {"timestamp": "2026-09-01 10:00:00", "high": 110.0, "low": 100.0, "open": 100.0, "close": 110.0},
        {"timestamp": "2026-09-01 11:00:00", "high": 106.0, "low": 104.5, "open": 106.0, "close": 105.0},
        {"timestamp": "2026-09-01 12:00:00", "high": 105.0, "low": 103.0, "open": 105.0, "close": 103.5},
        {"timestamp": "2026-09-01 13:00:00", "high": 109.0, "low": 103.5, "open": 104.0, "close": 108.5},
    ])
    imp = Impulse(start_idx=0, end_idx=0, high=110.0, low=100.0, pct=10.0, is_long=True, start_time="2026-09-01", end_time="2026-09-01")
    cfg = GridConfig(mode="dual", entry_fib_1=0.500, tp_fib_1=0.236, entry_fib_2=0.618, tp_fib_2=0.382, sl_fib=1.000)

    trades_custom = simulate_grid(df, [imp], cfg)
    trades_algo, metrics = run_pyalgotrade_backtest(df, [imp], cfg)

    assert len(trades_algo) == 1
    assert trades_algo[0].win is True
    assert trades_algo[0].both_entered is True
    assert trades_algo[0].outcome == "TP1+TP2"
    assert pytest.approx(trades_algo[0].pnl, rel=1e-3) == trades_custom[0].pnl
    assert "total_return_pct" in metrics
    assert "sharpe_ratio" in metrics
    assert "max_drawdown_pct" in metrics

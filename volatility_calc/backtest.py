"""Симулятор Long DCA-сетки на исторических OHLCV для перебора TP и coverage."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class TradeResult:
    entry_idx: int
    exit_idx: int
    n_entries: int
    avg_entry: float
    exit_price: float
    pnl_pct: float
    hit_tp: bool
    liquidated: bool
    hold_hours: int
    fee_pct: float = 0.0


@dataclass
class BacktestSummary:
    n_trades: int
    n_wins: int
    n_losses: int
    n_liquidations: int
    win_rate: float
    total_pnl_pct: float
    avg_pnl_pct: float
    median_pnl_pct: float
    max_pnl_pct: float
    min_pnl_pct: float
    avg_hold_hours: float
    avg_entries: float
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0


def _grid_levels(price: float, n: int, ps: float) -> list[float]:
    """Уровни цен DCA-ордеров: price, price/ps, price/ps^2, ..."""
    return [price * (1.0 / ps) ** i for i in range(n)]


def _quantile_grid_levels(df: pd.DataFrame, entry_idx: int, n: int, 
                           horizon_h: int, side: str = "long") -> list[float]:
    """Адаптивная DCA-сетка на основе квантилей исторического распределения.
    
    Вместо геометрической прогрессии использует эмпирические квантили
    распределения цен за горизонт horizon_h.
    
    Для long: уровни ниже entry_price (ожидание падения)
    Для short: уровни выше entry_price (ожидание роста)
    """
    close = df["close"].to_numpy(dtype=float)
    start_idx = max(0, entry_idx - horizon_h)
    end_idx = min(len(close), entry_idx + horizon_h)
    
    if end_idx - start_idx < 10:
        return [close[entry_idx]] * n
    
    historical_prices = close[start_idx:end_idx]
    entry_price = close[entry_idx]
    
    if side == "long":
        prices_below = historical_prices[historical_prices <= entry_price]
        if len(prices_below) < n:
            return _grid_levels(entry_price, n, 1.1)
        
        quantiles = np.linspace(0, 1, n + 1)[1:]
        levels = [float(np.quantile(prices_below, q)) for q in quantiles]
        levels[0] = entry_price
        return levels
    else:
        prices_above = historical_prices[historical_prices >= entry_price]
        if len(prices_above) < n:
            return _grid_levels(entry_price, n, 1.1)
        
        quantiles = np.linspace(0, 1, n + 1)[1:]
        levels = [float(np.quantile(prices_above, q)) for q in quantiles]
        levels[0] = entry_price
        return levels


def simulate_short(
    df: pd.DataFrame,
    n_orders: int,
    price_scale: float,
    volume_scale: float,
    tp_pct: float,            # в долях (0.03 = 3%)
    leverage: int = 2,
    maintenance_margin_rate: float = 0.005,
    horizon_h: int = 168,
    base_qty: float = 1.0,
    step: int = 1,
    fee_pct: float = 0.0004,
) -> list[TradeResult]:
    """Симулировать short DCA. Уровни усреднения: price, price*ps, price*ps^2, ...

    Для шорта:
      - DCA-ордера срабатывают при high[idx] >= level (усреднение вверх)
      - TP: low[idx] <= avg_entry * (1 - tp_pct)
      - Ликвидация: high[idx] >= entry_price * (1 + (1/leverage - mmr))
    """
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(close)
    liq_rise = (1.0 / leverage - maintenance_margin_rate)  # доля роста для лонга-liquidity

    results: list[TradeResult] = []
    for i in range(0, n - horizon_h, step):
        entry_price = close[i]
        levels = [entry_price * (price_scale ** k) for k in range(n_orders)]
        qtys = [base_qty * (volume_scale ** k) for k in range(n_orders)]

        filled_prices: list[float] = [entry_price]
        filled_qtys: list[float] = [qtys[0]]
        
        total_cost = entry_price * qtys[0]
        total_qty = qtys[0]
        avg_entry = total_cost / total_qty
        
        liq_price = entry_price * (1.0 + liq_rise)

        exit_idx = None
        exit_price = None
        hit_tp = False
        liquidated = False

        for k in range(1, horizon_h + 1):
            idx = i + k
            if idx >= n:
                break
            
            for ord_i in range(1, n_orders):
                if len(filled_prices) <= ord_i and high[idx] >= levels[ord_i]:
                    filled_prices.append(levels[ord_i])
                    filled_qtys.append(qtys[ord_i])
                    total_cost += levels[ord_i] * qtys[ord_i]
                    total_qty += qtys[ord_i]
                    avg_entry = total_cost / total_qty

            if high[idx] >= liq_price:
                exit_idx = idx
                exit_price = liq_price
                liquidated = True
                break

            tp_level = avg_entry * (1.0 - tp_pct)
            if low[idx] <= tp_level:
                exit_idx = idx
                exit_price = tp_level
                hit_tp = True
                break

        if exit_idx is None:
            exit_idx = min(i + horizon_h, n - 1)
            exit_price = close[exit_idx]

        pnl_pct = (avg_entry - exit_price) / avg_entry * 100.0
        if liquidated:
            pnl_pct = -liq_rise * 100.0
        
        fee_total = fee_pct * (len(filled_prices) + 1) * 100.0
        pnl_pct -= fee_total

        results.append(TradeResult(
            entry_idx=i,
            exit_idx=exit_idx,
            n_entries=len(filled_prices),
            avg_entry=avg_entry,
            exit_price=exit_price,
            pnl_pct=pnl_pct,
            hit_tp=hit_tp,
            liquidated=liquidated,
            hold_hours=exit_idx - i,
            fee_pct=fee_total,
        ))
    return results


def _precompute_indicators(df: pd.DataFrame, mode: str,
                            ema_fast: int = 50, ema_slow: int = 200,
                            rsi_period: int = 14) -> pd.Series:
    """Предрасчёт индикаторов для всех свечей.
    
    Возвращает Series[bool], где True = сигнал разрешает long вход.
    """
    close = df["close"]
    n = len(close)
    
    if mode == "none":
        return pd.Series([True] * n, index=df.index)
    
    if mode == "ema_cross":
        ema_f = close.ewm(span=ema_fast, adjust=False).mean()
        ema_s = close.ewm(span=ema_slow, adjust=False).mean()
        result = ema_f > ema_s
        result.iloc[:ema_slow] = False
        return result
    
    if mode == "ema_price":
        ema_f = close.ewm(span=ema_fast, adjust=False).mean()
        result = close > ema_f
        result.iloc[:ema_fast] = False
        return result
    
    if mode == "rsi":
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = -delta.clip(upper=0).rolling(rsi_period).mean()
        rs = gain / loss
        rsi = 100 - 100 / (1 + rs)
        result = (rsi >= 50.0) & (rsi <= 70.0)
        result.iloc[:rsi_period] = False
        result = result.fillna(False)
        return result
    
    if mode == "ema_rsi":
        ema_cross = _precompute_indicators(df, "ema_cross", ema_fast, ema_slow)
        rsi = _precompute_indicators(df, "rsi", rsi_period=rsi_period)
        return ema_cross & rsi
    
    if mode == "ema_price_rsi":
        ema_price = _precompute_indicators(df, "ema_price", ema_fast)
        rsi = _precompute_indicators(df, "rsi", rsi_period=rsi_period)
        return ema_price & rsi
    
    raise ValueError(f"unknown mode {mode!r}")


def _dynamic_tp_level(avg_entry: float, current_price: float, 
                      volatility: float, time_remaining: int,
                      side: str = "long") -> float:
    """Динамический TP на основе optimal stopping theory.
    
    Для геометрического броуновского движения:
    E[max_price | current_price, time_remaining] = current_price * exp(σ² * T / 2)
    
    Это даёт теоретически оптимальный уровень TP.
    """
    if time_remaining <= 0:
        if side == "long":
            return avg_entry * 1.001
        else:
            return avg_entry * 0.999
    
    expected_max_factor = np.exp(0.5 * volatility**2 * time_remaining / 24)
    
    if side == "long":
        expected_max = current_price * expected_max_factor
        tp = min(expected_max, avg_entry * 1.5)
        return max(tp, avg_entry * 1.001)
    else:
        expected_min = current_price / expected_max_factor
        tp = max(expected_min, avg_entry * 0.5)
        return min(tp, avg_entry * 0.999)


def simulate_long(
    df: pd.DataFrame,
    n_orders: int,
    price_scale: float,
    volume_scale: float,
    tp_pct: float,
    leverage: int = 2,
    maintenance_margin_rate: float = 0.005,
    horizon_h: int = 168,
    base_qty: float = 1.0,
    step: int = 1,
    filter_mode: str = "none",
    tp_type: str = "fixed",
    trail_pct: float = 0.003,
    grid_type: str = "geometric",
    tp_mode: str = "static",
    fee_pct: float = 0.0004,
) -> list[TradeResult]:
    """Симулировать long DCA для каждой точки входа.

    Для каждого i-го свеча как entry:
      - первый ордер сразу по close[i]
      - следующие ордера срабатывают при low[i+k] <= level
      - exit: TP (high >= avg*(1+tp)) ИЛИ horizon_h истёк ИЛИ ликвидация

    tp_type:
      fixed    — лимитный TP по уровню avg_entry*(1+tp_pct)
      trailing — TP активируется при достижении avg_entry*(1+tp_pct), далее
                 двигается вверх за max high; выходит при откате на trail_pct.
    """
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(close)
    liq_drop = (1.0 / leverage - maintenance_margin_rate)
    
    signals = _precompute_indicators(df, filter_mode).to_numpy(dtype=bool)
    
    returns = np.diff(np.log(close))
    volatility = float(np.std(returns))

    results: list[TradeResult] = []
    for i in range(0, n - horizon_h, step):
        if not signals[i]:
            continue
        entry_price = close[i]
        
        if grid_type == "quantile":
            levels = _quantile_grid_levels(df, i, n_orders, horizon_h, side="long")
        else:
            levels = _grid_levels(entry_price, n_orders, price_scale)
        
        qtys = [base_qty * (volume_scale ** k) for k in range(n_orders)]

        filled_prices: list[float] = [entry_price]
        filled_qtys: list[float] = [qtys[0]]
        
        total_cost = entry_price * qtys[0]
        total_qty = qtys[0]
        avg_entry = total_cost / total_qty
        
        liq_price = entry_price * (1.0 - liq_drop)

        exit_idx = None
        exit_price = None
        hit_tp = False
        liquidated = False

        trailing_active = False
        trailing_max_high = 0.0

        for k in range(1, horizon_h + 1):
            idx = i + k
            if idx >= n:
                break

            if low[idx] <= liq_price:
                exit_idx = idx
                exit_price = liq_price
                liquidated = True
                break

            for ord_i in range(1, n_orders):
                if len(filled_prices) <= ord_i and low[idx] <= levels[ord_i]:
                    filled_prices.append(levels[ord_i])
                    filled_qtys.append(qtys[ord_i])
                    total_cost += levels[ord_i] * qtys[ord_i]
                    total_qty += qtys[ord_i]
                    avg_entry = total_cost / total_qty

            if tp_type == "fixed":
                if tp_mode == "dynamic":
                    time_remaining = horizon_h - k
                    tp_level = _dynamic_tp_level(avg_entry, close[idx], volatility, 
                                                 time_remaining, side="long")
                else:
                    tp_level = avg_entry * (1.0 + tp_pct)
                
                if high[idx] >= tp_level:
                    exit_idx = idx
                    exit_price = tp_level
                    hit_tp = True
                    break
            else:
                act_level = avg_entry * (1.0 + tp_pct)
                if not trailing_active and high[idx] >= act_level:
                    trailing_active = True
                    trailing_max_high = high[idx]
                if trailing_active:
                    if high[idx] > trailing_max_high:
                        trailing_max_high = high[idx]
                    tp_level = trailing_max_high * (1.0 - trail_pct)
                    if tp_level < act_level:
                        tp_level = act_level
                    if low[idx] <= tp_level:
                        exit_idx = idx
                        exit_price = tp_level
                        hit_tp = True
                        break

        if exit_idx is None:
            exit_idx = min(i + horizon_h, n - 1)
            exit_price = close[exit_idx]

        pnl_pct = (exit_price - avg_entry) / avg_entry * 100.0
        if liquidated:
            pnl_pct = -liq_drop * 100.0
        
        fee_total = fee_pct * (len(filled_prices) + 1) * 100.0
        pnl_pct -= fee_total

        results.append(TradeResult(
            entry_idx=i,
            exit_idx=exit_idx,
            n_entries=len(filled_prices),
            avg_entry=avg_entry,
            exit_price=exit_price,
            pnl_pct=pnl_pct,
            hit_tp=hit_tp,
            liquidated=liquidated,
            hold_hours=exit_idx - i,
            fee_pct=fee_total,
        ))
    return results


def build_equity_curve(
    results: list[TradeResult],
    initial_balance: float = 100.0,
    position_size_pct: float = 0.04,
) -> np.ndarray:
    """Построить equity curve по списку сделок.
    
    Фиксированная позиция: pnl_abs = pnl_pct * initial_balance * position_size_pct
    """
    if not results:
        return np.array([initial_balance])
    
    sorted_trades = sorted(results, key=lambda r: r.entry_idx)
    equity = [initial_balance]
    
    for trade in sorted_trades:
        pnl_abs = trade.pnl_pct / 100.0 * initial_balance * position_size_pct
        equity.append(equity[-1] + pnl_abs)
    
    return np.array(equity)


def portfolio_metrics(equity_curve: np.ndarray) -> dict:
    """Рассчитать портфельные метрики по equity curve.
    
    Returns:
        dict с ключами:
        - max_drawdown_pct: максимальная просадка от пика (%)
        - max_drawdown_duration: длительность просадки в точках
        - sharpe_ratio: годовая (sqrt(8760) для часовых сделок)
        - sortino_ratio: downside deviation
        - profit_factor: sum(wins) / sum(losses)
        - calmar_ratio: annual_return / max_drawdown
    """
    if len(equity_curve) < 2:
        return {
            "max_drawdown_pct": 0.0,
            "max_drawdown_duration": 0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "profit_factor": 0.0,
            "calmar_ratio": 0.0,
        }
    
    returns = np.diff(equity_curve) / equity_curve[:-1]
    
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / peak * 100.0
    max_dd = float(np.min(drawdown))
    
    max_dd_duration = 0
    current_duration = 0
    for dd in drawdown:
        if dd < 0:
            current_duration += 1
            max_dd_duration = max(max_dd_duration, current_duration)
        else:
            current_duration = 0
    
    mean_return = np.mean(returns)
    std_return = np.std(returns)
    sharpe = float(mean_return / std_return * np.sqrt(8760)) if std_return > 0 else 0.0
    
    downside_returns = returns[returns < 0]
    downside_std = np.std(downside_returns) if len(downside_returns) > 0 else 0.0
    sortino = float(mean_return / downside_std * np.sqrt(8760)) if downside_std > 0 else 0.0
    
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    profit_factor = float(np.sum(wins) / np.abs(np.sum(losses))) if len(losses) > 0 and np.sum(losses) != 0 else 0.0
    
    annual_return = mean_return * 8760 * 100.0
    calmar = float(annual_return / np.abs(max_dd)) if max_dd != 0 else 0.0
    
    return {
        "max_drawdown_pct": float(max_dd),
        "max_drawdown_duration": int(max_dd_duration),
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "profit_factor": float(profit_factor),
        "calmar_ratio": float(calmar),
    }


def summarize(results: list[TradeResult]) -> BacktestSummary:
    if not results:
        return BacktestSummary(0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                               0.0, 0.0, 0.0, 0.0)
    pnls = [r.pnl_pct for r in results]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    liqs = sum(1 for r in results if r.liquidated)
    
    equity = build_equity_curve(results)
    metrics = portfolio_metrics(equity)
    
    return BacktestSummary(
        n_trades=len(results),
        n_wins=wins,
        n_losses=losses,
        n_liquidations=liqs,
        win_rate=wins / len(results) * 100.0,
        total_pnl_pct=sum(pnls),
        avg_pnl_pct=sum(pnls) / len(pnls),
        median_pnl_pct=float(np.median(pnls)),
        max_pnl_pct=max(pnls),
        min_pnl_pct=min(pnls),
        avg_hold_hours=sum(r.hold_hours for r in results) / len(results),
        avg_entries=sum(r.n_entries for r in results) / len(results),
        max_drawdown_pct=metrics["max_drawdown_pct"],
        sharpe_ratio=metrics["sharpe_ratio"],
        sortino_ratio=metrics["sortino_ratio"],
        profit_factor=metrics["profit_factor"],
    )


def coverage_to_ps(n: int, target_coverage: float,
                    ps_min: float = 1.10, ps_max: float = 1.80,
                    step: float = 0.01) -> float:
    """Подобрать price_scale, дающий actual_coverage >= target для n ордеров."""
    ps = ps_min
    while ps <= ps_max:
        actual = 1.0 - (1.0 / ps) ** (n - 1)
        if actual + 1e-9 >= target_coverage:
            return ps
        ps += step
    return ps_max


def simulate(
    df: pd.DataFrame,
    n_orders: int,
    price_scale: float,
    volume_scale: float,
    tp_pct: float,
    leverage: int = 2,
    maintenance_margin_rate: float = 0.005,
    horizon_h: int = 168,
    base_qty: float = 1.0,
    step: int = 1,
    side: str = "long",
    filter_mode: str = "none",
    tp_type: str = "fixed",
    trail_pct: float = 0.003,
    fee_pct: float = 0.0004,
) -> list[TradeResult]:
    if side == "long":
        return simulate_long(df, n_orders, price_scale, volume_scale, tp_pct,
                              leverage, maintenance_margin_rate, horizon_h,
                              base_qty, step, filter_mode=filter_mode,
                              tp_type=tp_type, trail_pct=trail_pct,
                              fee_pct=fee_pct)
    if side == "short":
        return simulate_short(df, n_orders, price_scale, volume_scale, tp_pct,
                               leverage, maintenance_margin_rate, horizon_h,
                               base_qty, step, fee_pct=fee_pct)
    raise ValueError(f"side должен быть long/short, получено {side!r}")


def grid_search(
    df: pd.DataFrame,
    cov_values: list[float],
    tp_values: list[float],
    n_orders: int = 3,
    volume_scale: float = 1.20,
    leverage: int = 2,
    horizon_h: int = 168,
    step: int = 1,
    side: str = "long",
    filter_mode: str = "none",
    tp_type: str = "fixed",
    trail_pct: float = 0.003,
    fee_pct: float = 0.0004,
) -> pd.DataFrame:
    """Перебор coverage × TP. Возвращает DataFrame с метриками для каждого сочетания."""
    rows = []
    for cov in cov_values:
        ps = coverage_to_ps(n_orders, cov)
        for tp_pct in tp_values:
            results = simulate(
                df, n_orders=n_orders, price_scale=ps,
                volume_scale=volume_scale, tp_pct=tp_pct / 100.0,
                leverage=leverage, horizon_h=horizon_h, step=step,
                side=side, filter_mode=filter_mode,
                tp_type=tp_type, trail_pct=trail_pct,
                fee_pct=fee_pct,
            )
            s = summarize(results)
            rows.append({
                "coverage": cov,
                "price_scale": round(ps, 2),
                "tp_pct": tp_pct,
                "n_trades": s.n_trades,
                "win_rate": round(s.win_rate, 1),
                "total_pnl": round(s.total_pnl_pct, 2),
                "avg_pnl": round(s.avg_pnl_pct, 3),
                "median_pnl": round(s.median_pnl_pct, 3),
                "max_pnl": round(s.max_pnl_pct, 2),
                "min_pnl": round(s.min_pnl_pct, 2),
                "liquidations": s.n_liquidations,
                "avg_hold_h": round(s.avg_hold_hours, 1),
                "avg_entries": round(s.avg_entries, 2),
                "max_dd": round(s.max_drawdown_pct, 2),
                "sharpe": round(s.sharpe_ratio, 2),
                "profit_factor": round(s.profit_factor, 2),
            })
    return pd.DataFrame(rows)


def bayesian_optimize(
    df: pd.DataFrame,
    param_ranges: dict,
    objective: str = "total_pnl",
    n_iterations: int = 50,
    n_initial: int = 10,
    **kwargs,
) -> dict:
    """Байесовская оптимизация параметров DCA-стратегии.
    
    Использует Gaussian Process для моделирования целевой функции
    и Expected Improvement для выбора следующей точки.
    
    Args:
        df: DataFrame с OHLCV данными
        param_ranges: Словарь диапазонов параметров, напр:
            {"coverage": (0.1, 0.5), "tp_pct": (0.5, 3.0), "volume_scale": (1.0, 2.0)}
        objective: Целевая метрика ("total_pnl", "sharpe", "win_rate")
        n_iterations: Количество итераций оптимизации
        n_initial: Количество начальных случайных точек
        **kwargs: Дополнительные параметры для simulate()
    
    Returns:
        Словарь с оптимальными параметрами и историей оптимизации
    """
    from scipy.stats import norm
    
    param_names = list(param_ranges.keys())
    n_params = len(param_names)
    
    def params_to_array(params_dict):
        return np.array([params_dict[name] for name in param_names])
    
    def array_to_params(arr):
        return {name: float(arr[i]) for i, name in enumerate(param_names)}
    
    def evaluate_params(params_dict):
        coverage = params_dict.get("coverage", 0.3)
        tp_pct = params_dict.get("tp_pct", 1.0)
        volume_scale = params_dict.get("volume_scale", 1.2)
        
        n_orders = kwargs.get("n_orders", 3)
        ps = coverage_to_ps(n_orders, coverage)
        
        results = simulate(
            df,
            n_orders=n_orders,
            price_scale=ps,
            volume_scale=volume_scale,
            tp_pct=tp_pct / 100.0,
            **{k: v for k, v in kwargs.items() if k not in ["n_orders"]}
        )
        
        summary = summarize(results)
        
        if objective == "total_pnl":
            return summary.total_pnl_pct
        elif objective == "sharpe":
            pnls = [r.pnl_pct for r in results]
            if len(pnls) > 1 and np.std(pnls) > 0:
                return np.mean(pnls) / np.std(pnls)
            return 0.0
        elif objective == "win_rate":
            return summary.win_rate
        else:
            return summary.total_pnl_pct
    
    X_observed = []
    y_observed = []
    
    for _ in range(n_initial):
        params = {}
        for name, (low, high) in param_ranges.items():
            params[name] = np.random.uniform(low, high)
        
        score = evaluate_params(params)
        X_observed.append(params_to_array(params))
        y_observed.append(score)
    
    X_observed = np.array(X_observed)
    y_observed = np.array(y_observed)
    
    def rbf_kernel(X1, X2, length_scale=1.0, variance=1.0):
        sqdist = np.sum(X1**2, axis=1).reshape(-1, 1) + np.sum(X2**2, axis=1) - 2 * np.dot(X1, X2.T)
        return variance * np.exp(-0.5 * sqdist / length_scale**2)
    
    def expected_improvement(X, X_observed, y_observed, xi=0.01):
        K = rbf_kernel(X_observed, X_observed) + 1e-6 * np.eye(len(X_observed))
        K_inv = np.linalg.inv(K)
        
        mu = rbf_kernel(X, X_observed) @ K_inv @ y_observed
        sigma = np.sqrt(np.diag(rbf_kernel(X, X) - rbf_kernel(X, X_observed) @ K_inv @ rbf_kernel(X_observed, X)))
        
        sigma = np.maximum(sigma, 1e-6)
        
        y_best = np.max(y_observed)
        z = (mu - y_best - xi) / sigma
        ei = (mu - y_best - xi) * norm.cdf(z) + sigma * norm.pdf(z)
        
        return ei
    
    for iteration in range(n_iterations):
        n_candidates = 1000
        X_candidates = np.zeros((n_candidates, n_params))
        
        for i, (name, (low, high)) in enumerate(param_ranges.items()):
            X_candidates[:, i] = np.random.uniform(low, high, n_candidates)
        
        ei = expected_improvement(X_candidates, X_observed, y_observed)
        best_idx = np.argmax(ei)
        
        new_params = array_to_params(X_candidates[best_idx])
        new_score = evaluate_params(new_params)
        
        X_observed = np.vstack([X_observed, X_candidates[best_idx:best_idx+1]])
        y_observed = np.append(y_observed, new_score)
    
    best_idx = np.argmax(y_observed)
    best_params = array_to_params(X_observed[best_idx])
    best_score = y_observed[best_idx]
    
    return {
        "best_params": best_params,
        "best_score": float(best_score),
        "objective": objective,
        "n_evaluations": len(y_observed),
        "history": {
            "X": X_observed.tolist(),
            "y": y_observed.tolist(),
        },
    }


def monte_carlo_simulate(
    df: pd.DataFrame,
    n_orders: int,
    price_scale: float,
    volume_scale: float,
    tp_pct: float,
    leverage: int = 2,
    maintenance_margin_rate: float = 0.005,
    horizon_h: int = 168,
    base_qty: float = 1.0,
    n_paths: int = 1000,
    side: str = "long",
    fee_pct: float = 0.0004,
) -> dict:
    """Монте-Карло симуляция DCA-стратегии на основе геометрического броуновского движения.
    
    Возвращает статистику по n_paths случайным траекториям.
    """
    close = df["close"].to_numpy(dtype=float)
    returns = np.diff(np.log(close))
    
    mu = np.mean(returns)
    sigma = np.std(returns)
    
    dt = 1.0
    n_steps = horizon_h
    
    results = []
    
    for _ in range(n_paths):
        z = np.random.standard_normal(n_steps)
        log_prices = np.zeros(n_steps + 1)
        log_prices[0] = np.log(close[-1])
        
        for t in range(1, n_steps + 1):
            log_prices[t] = log_prices[t-1] + (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z[t-1]
        
        prices = np.exp(log_prices)
        
        if side == "long":
            levels = [prices[0] * (1.0 / price_scale) ** i for i in range(n_orders)]
        else:
            levels = [prices[0] * (price_scale ** i) for i in range(n_orders)]
        
        qtys = [base_qty * (volume_scale ** k) for k in range(n_orders)]
        
        filled_prices = [prices[0]]
        filled_qtys = [qtys[0]]
        total_cost = prices[0] * qtys[0]
        total_qty = qtys[0]
        
        liq_drop = 1.0 / leverage - maintenance_margin_rate
        if side == "long":
            liq_price = prices[0] * (1.0 - liq_drop)
        else:
            liq_price = prices[0] * (1.0 + liq_drop)
        
        liquidated = False
        hit_tp = False
        exit_price = prices[-1]
        
        for t in range(1, n_steps + 1):
            price = prices[t]
            
            if side == "long" and price <= liq_price:
                liquidated = True
                exit_price = liq_price
                break
            elif side == "short" and price >= liq_price:
                liquidated = True
                exit_price = liq_price
                break
            
            for ord_i in range(1, n_orders):
                if len(filled_prices) <= ord_i:
                    if side == "long" and price <= levels[ord_i]:
                        filled_prices.append(levels[ord_i])
                        filled_qtys.append(qtys[ord_i])
                        total_cost += levels[ord_i] * qtys[ord_i]
                        total_qty += qtys[ord_i]
                    elif side == "short" and price >= levels[ord_i]:
                        filled_prices.append(levels[ord_i])
                        filled_qtys.append(qtys[ord_i])
                        total_cost += levels[ord_i] * qtys[ord_i]
                        total_qty += qtys[ord_i]
            
            avg_entry = total_cost / total_qty
            
            if side == "long":
                tp_level = avg_entry * (1.0 + tp_pct)
                if price >= tp_level:
                    hit_tp = True
                    exit_price = tp_level
                    break
            else:
                tp_level = avg_entry * (1.0 - tp_pct)
                if price <= tp_level:
                    hit_tp = True
                    exit_price = tp_level
                    break
        
        if not liquidated and not hit_tp:
            avg_entry = total_cost / total_qty
            if side == "long":
                pnl_pct = (exit_price - avg_entry) / avg_entry * 100.0
            else:
                pnl_pct = (avg_entry - exit_price) / avg_entry * 100.0
        elif liquidated:
            pnl_pct = -liq_drop * 100.0
        else:
            avg_entry = total_cost / total_qty
            if side == "long":
                pnl_pct = (exit_price - avg_entry) / avg_entry * 100.0
            else:
                pnl_pct = (avg_entry - exit_price) / avg_entry * 100.0
        
        fee_total = fee_pct * (len(filled_prices) + 1) * 100.0
        pnl_pct -= fee_total
        
        results.append(pnl_pct)
    
    results_arr = np.array(results)
    
    return {
        "n_paths": n_paths,
        "mean_pnl": float(np.mean(results_arr)),
        "median_pnl": float(np.median(results_arr)),
        "std_pnl": float(np.std(results_arr)),
        "win_rate": float(np.mean(results_arr > 0) * 100),
        "liquidation_rate": float(np.mean(results_arr <= -liq_drop * 100 + 1) * 100),
        "p5_pnl": float(np.percentile(results_arr, 5)),
        "p95_pnl": float(np.percentile(results_arr, 95)),
        "cvar_5": float(np.mean(results_arr[results_arr <= np.percentile(results_arr, 5)])),
    }
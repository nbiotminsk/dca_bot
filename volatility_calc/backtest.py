"""Симулятор Long/Short DCA-сетки на исторических OHLCV."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Intrabar order (conservative):
#   1) DCA fills on adverse extreme
#   2) liquidation check (recomputed after fills)
#   3) TP / trailing on favorable extreme
# If both extremes print on one bar, adverse path is preferred.


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
    """Уровни цен DCA-ордеров long: price, price/ps, price/ps^2, ..."""
    return [price * (1.0 / ps) ** i for i in range(n)]


def _grid_levels_short(price: float, n: int, ps: float) -> list[float]:
    """Уровни short: price, price*ps, price*ps^2, ..."""
    return [price * (ps ** i) for i in range(n)]


def _quantile_grid_levels(
    df: pd.DataFrame,
    entry_idx: int,
    n: int,
    horizon_h: int,
    side: str = "long",
) -> list[float]:
    """Адаптивная DCA-сетка по квантилям исторического распределения."""
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

    prices_above = historical_prices[historical_prices >= entry_price]
    if len(prices_above) < n:
        return _grid_levels_short(entry_price, n, 1.1)
    quantiles = np.linspace(0, 1, n + 1)[1:]
    levels = [float(np.quantile(prices_above, q)) for q in quantiles]
    levels[0] = entry_price
    return levels


def _liq_price_long(avg_entry: float, leverage: int, mmr: float) -> float:
    return avg_entry * (1.0 - (1.0 / leverage - mmr))


def _liq_price_short(avg_entry: float, leverage: int, mmr: float) -> float:
    return avg_entry * (1.0 + (1.0 / leverage - mmr))


def _fee_pct_of_notional(
    total_cost: float,
    total_qty: float,
    exit_price: float,
    fee_rate: float,
    hold_hours: int = 0,
    funding_rate_8h: float = 0.0,
) -> float:
    """Комиссии + funding как % от notional_in."""
    if total_cost <= 0 or total_qty <= 0:
        return 0.0
    notional_out = exit_price * total_qty
    fee_usd = fee_rate * (total_cost + notional_out)
    funding_usd = 0.0
    if funding_rate_8h != 0.0 and hold_hours > 0:
        # funding на среднем notional, periods = hold/8
        avg_notional = (total_cost + notional_out) / 2.0
        funding_usd = abs(funding_rate_8h) * (hold_hours / 8.0) * avg_notional
    return (fee_usd + funding_usd) / total_cost * 100.0


def _precompute_indicators(
    df: pd.DataFrame,
    mode: str,
    ema_fast: int = 50,
    ema_slow: int = 200,
    rsi_period: int = 14,
    side: str = "long",
) -> pd.Series:
    """Series[bool]: True = вход разрешён для side."""
    close = df["close"]
    n = len(close)

    if mode == "none":
        return pd.Series(np.ones(n, dtype=bool), index=df.index)

    if mode == "ema_cross":
        ema_f = close.ewm(span=ema_fast, adjust=False).mean()
        ema_s = close.ewm(span=ema_slow, adjust=False).mean()
        result = ema_f > ema_s if side == "long" else ema_f < ema_s
        result = result.copy()
        result.iloc[:ema_slow] = False
        return result

    if mode == "ema_price":
        ema_f = close.ewm(span=ema_fast, adjust=False).mean()
        result = close > ema_f if side == "long" else close < ema_f
        result = result.copy()
        result.iloc[:ema_fast] = False
        return result

    if mode == "rsi":
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)
        if side == "long":
            result = (rsi >= 50.0) & (rsi <= 70.0)
        else:
            result = (rsi >= 30.0) & (rsi <= 50.0)
        result = result.copy()
        result.iloc[:rsi_period] = False
        return result.fillna(False)

    if mode == "ema_rsi":
        ema_cross = _precompute_indicators(df, "ema_cross", ema_fast, ema_slow, side=side)
        rsi = _precompute_indicators(df, "rsi", rsi_period=rsi_period, side=side)
        return ema_cross & rsi

    if mode == "ema_price_rsi":
        ema_price = _precompute_indicators(df, "ema_price", ema_fast, side=side)
        rsi = _precompute_indicators(df, "rsi", rsi_period=rsi_period, side=side)
        return ema_price & rsi

    raise ValueError(f"unknown mode {mode!r}")


def _dynamic_tp_level(
    avg_entry: float,
    current_price: float,
    volatility: float,
    time_remaining: int,
    side: str = "long",
) -> float:
    """Динамический TP (optimal stopping / GBM max expectation)."""
    if time_remaining <= 0:
        return avg_entry * (1.001 if side == "long" else 0.999)

    expected_max_factor = np.exp(0.5 * volatility**2 * time_remaining / 24)

    if side == "long":
        expected_max = current_price * expected_max_factor
        tp = min(expected_max, avg_entry * 1.5)
        return max(tp, avg_entry * 1.001)

    expected_min = current_price / expected_max_factor
    tp = max(expected_min, avg_entry * 0.5)
    return min(tp, avg_entry * 0.999)


def _simulate_side(
    df: pd.DataFrame,
    *,
    side: str,
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
    funding_rate_8h: float = 0.0,
    non_overlapping: bool = True,
) -> list[TradeResult]:
    """Единый симулятор long/short."""
    if side not in ("long", "short"):
        raise ValueError(f"side должен быть long/short, получено {side!r}")

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(close)
    liq_dist = 1.0 / leverage - maintenance_margin_rate

    signals = _precompute_indicators(df, filter_mode, side=side).to_numpy(dtype=bool)
    returns = np.diff(np.log(np.maximum(close, 1e-12)))
    volatility = float(np.std(returns)) if len(returns) else 0.0

    results: list[TradeResult] = []
    i = 0
    while i < n - horizon_h:
        if not signals[i]:
            i += step
            continue

        entry_price = close[i]
        if grid_type == "quantile":
            levels = _quantile_grid_levels(df, i, n_orders, horizon_h, side=side)
        elif side == "long":
            levels = _grid_levels(entry_price, n_orders, price_scale)
        else:
            levels = _grid_levels_short(entry_price, n_orders, price_scale)

        qtys = [base_qty * (volume_scale ** k) for k in range(n_orders)]
        filled_mask = [True] + [False] * (n_orders - 1)
        total_cost = entry_price * qtys[0]
        total_qty = qtys[0]
        avg_entry = total_cost / total_qty
        n_filled = 1

        if side == "long":
            liq_price = _liq_price_long(avg_entry, leverage, maintenance_margin_rate)
        else:
            liq_price = _liq_price_short(avg_entry, leverage, maintenance_margin_rate)

        exit_idx: int | None = None
        exit_price: float | None = None
        hit_tp = False
        liquidated = False
        trailing_active = False
        trailing_extreme = 0.0

        for k in range(1, horizon_h + 1):
            idx = i + k
            if idx >= n:
                break

            # 1) DCA fills
            for ord_i in range(1, n_orders):
                if filled_mask[ord_i]:
                    continue
                hit = (
                    low[idx] <= levels[ord_i]
                    if side == "long"
                    else high[idx] >= levels[ord_i]
                )
                if hit:
                    filled_mask[ord_i] = True
                    n_filled += 1
                    total_cost += levels[ord_i] * qtys[ord_i]
                    total_qty += qtys[ord_i]
                    avg_entry = total_cost / total_qty
                    if side == "long":
                        liq_price = _liq_price_long(
                            avg_entry, leverage, maintenance_margin_rate
                        )
                    else:
                        liq_price = _liq_price_short(
                            avg_entry, leverage, maintenance_margin_rate
                        )

            # 2) liquidation (after fills, using updated avg)
            if side == "long" and low[idx] <= liq_price:
                exit_idx = idx
                exit_price = liq_price
                liquidated = True
                break
            if side == "short" and high[idx] >= liq_price:
                exit_idx = idx
                exit_price = liq_price
                liquidated = True
                break

            # 3) TP / trailing
            if tp_type == "fixed":
                if tp_mode == "dynamic":
                    time_remaining = horizon_h - k
                    tp_level = _dynamic_tp_level(
                        avg_entry, close[idx], volatility, time_remaining, side=side
                    )
                elif side == "long":
                    tp_level = avg_entry * (1.0 + tp_pct)
                else:
                    tp_level = avg_entry * (1.0 - tp_pct)

                hit = (
                    high[idx] >= tp_level
                    if side == "long"
                    else low[idx] <= tp_level
                )
                if hit:
                    exit_idx = idx
                    exit_price = tp_level
                    hit_tp = True
                    break
            else:
                act_level = (
                    avg_entry * (1.0 + tp_pct)
                    if side == "long"
                    else avg_entry * (1.0 - tp_pct)
                )
                if side == "long":
                    if not trailing_active and high[idx] >= act_level:
                        trailing_active = True
                        trailing_extreme = high[idx]
                    if trailing_active:
                        trailing_extreme = max(trailing_extreme, high[idx])
                        tp_level = max(
                            trailing_extreme * (1.0 - trail_pct), act_level
                        )
                        if low[idx] <= tp_level:
                            exit_idx = idx
                            exit_price = tp_level
                            hit_tp = True
                            break
                else:
                    if not trailing_active and low[idx] <= act_level:
                        trailing_active = True
                        trailing_extreme = low[idx]
                    if trailing_active:
                        trailing_extreme = min(trailing_extreme, low[idx])
                        tp_level = min(
                            trailing_extreme * (1.0 + trail_pct), act_level
                        )
                        if high[idx] >= tp_level:
                            exit_idx = idx
                            exit_price = tp_level
                            hit_tp = True
                            break

        if exit_idx is None:
            exit_idx = min(i + horizon_h, n - 1)
            exit_price = close[exit_idx]

        hold_hours = exit_idx - i
        if liquidated:
            pnl_pct = -liq_dist * 100.0
        elif side == "long":
            pnl_pct = (exit_price - avg_entry) / avg_entry * 100.0
        else:
            pnl_pct = (avg_entry - exit_price) / avg_entry * 100.0

        fee_total = _fee_pct_of_notional(
            total_cost,
            total_qty,
            exit_price,
            fee_pct,
            hold_hours=hold_hours,
            funding_rate_8h=funding_rate_8h,
        )
        pnl_pct -= fee_total

        results.append(
            TradeResult(
                entry_idx=i,
                exit_idx=exit_idx,
                n_entries=n_filled,
                avg_entry=avg_entry,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                hit_tp=hit_tp,
                liquidated=liquidated,
                hold_hours=hold_hours,
                fee_pct=fee_total,
            )
        )

        if non_overlapping:
            i = exit_idx + 1
        else:
            i += step

    return results


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
    funding_rate_8h: float = 0.0,
    non_overlapping: bool = True,
) -> list[TradeResult]:
    return _simulate_side(
        df,
        side="long",
        n_orders=n_orders,
        price_scale=price_scale,
        volume_scale=volume_scale,
        tp_pct=tp_pct,
        leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate,
        horizon_h=horizon_h,
        base_qty=base_qty,
        step=step,
        filter_mode=filter_mode,
        tp_type=tp_type,
        trail_pct=trail_pct,
        grid_type=grid_type,
        tp_mode=tp_mode,
        fee_pct=fee_pct,
        funding_rate_8h=funding_rate_8h,
        non_overlapping=non_overlapping,
    )


def simulate_short(
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
    funding_rate_8h: float = 0.0,
    non_overlapping: bool = True,
) -> list[TradeResult]:
    return _simulate_side(
        df,
        side="short",
        n_orders=n_orders,
        price_scale=price_scale,
        volume_scale=volume_scale,
        tp_pct=tp_pct,
        leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate,
        horizon_h=horizon_h,
        base_qty=base_qty,
        step=step,
        filter_mode=filter_mode,
        tp_type=tp_type,
        trail_pct=trail_pct,
        grid_type=grid_type,
        tp_mode=tp_mode,
        fee_pct=fee_pct,
        funding_rate_8h=funding_rate_8h,
        non_overlapping=non_overlapping,
    )


def build_equity_curve(
    results: list[TradeResult],
    initial_balance: float = 100.0,
    position_size_pct: float = 0.04,
    compound: bool = True,
) -> np.ndarray:
    """Equity curve. compound=True: PnL от текущего баланса."""
    if not results:
        return np.array([initial_balance])

    sorted_trades = sorted(results, key=lambda r: (r.exit_idx, r.entry_idx))
    equity = [initial_balance]

    for trade in sorted_trades:
        base = equity[-1] if compound else initial_balance
        pnl_abs = trade.pnl_pct / 100.0 * base * position_size_pct
        equity.append(equity[-1] + pnl_abs)

    return np.array(equity)


def portfolio_metrics(equity_curve: np.ndarray) -> dict:
    """Портфельные метрики по equity curve."""
    if len(equity_curve) < 2:
        return {
            "max_drawdown_pct": 0.0,
            "max_drawdown_duration": 0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "profit_factor": 0.0,
            "calmar_ratio": 0.0,
        }

    prev = np.maximum(equity_curve[:-1], 1e-12)
    returns = np.diff(equity_curve) / prev

    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / np.maximum(peak, 1e-12) * 100.0
    max_dd = float(np.min(drawdown))

    max_dd_duration = 0
    current_duration = 0
    for dd in drawdown:
        if dd < 0:
            current_duration += 1
            max_dd_duration = max(max_dd_duration, current_duration)
        else:
            current_duration = 0

    mean_return = float(np.mean(returns))
    std_return = float(np.std(returns))
    # trades ~ not hourly bars; annualize by ~sqrt(N_trades_per_year) heuristic
    # use sqrt(252) trade-day style when few points, else scale lightly
    ann = np.sqrt(min(len(returns), 252))
    sharpe = float(mean_return / std_return * ann) if std_return > 0 else 0.0

    downside_returns = returns[returns < 0]
    downside_std = float(np.std(downside_returns)) if len(downside_returns) else 0.0
    sortino = (
        float(mean_return / downside_std * ann) if downside_std > 0 else 0.0
    )

    wins = returns[returns > 0]
    losses = returns[returns < 0]
    if len(losses) and np.sum(losses) != 0:
        profit_factor = float(np.sum(wins) / np.abs(np.sum(losses)))
    else:
        profit_factor = 0.0

    total_return_pct = (equity_curve[-1] / equity_curve[0] - 1.0) * 100.0
    calmar = float(total_return_pct / abs(max_dd)) if max_dd != 0 else 0.0

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
        return BacktestSummary(
            0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0,
        )
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


def coverage_to_ps(
    n: int,
    target_coverage: float,
    ps_min: float = 1.10,
    ps_max: float = 1.80,
    step: float = 0.01,
) -> float:
    """price_scale для actual_coverage >= target: 1 - (1/ps)^(n-1)."""
    if n <= 1:
        return ps_min
    target_coverage = min(max(target_coverage, 0.0), 0.999999)
    # closed form: ps = (1 - cov)^(-1/(n-1))
    remaining = 1.0 - target_coverage
    if remaining <= 0:
        return ps_max
    ps = remaining ** (-1.0 / (n - 1))
    ps = max(ps_min, min(ps_max, ps))
    # snap up to step grid so actual >= target
    ps = np.ceil(ps / step) * step
    return float(min(ps_max, max(ps_min, round(ps, 10))))


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
    grid_type: str = "geometric",
    tp_mode: str = "static",
    fee_pct: float = 0.0004,
    funding_rate_8h: float = 0.0,
    non_overlapping: bool = True,
) -> list[TradeResult]:
    return _simulate_side(
        df,
        side=side,
        n_orders=n_orders,
        price_scale=price_scale,
        volume_scale=volume_scale,
        tp_pct=tp_pct,
        leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate,
        horizon_h=horizon_h,
        base_qty=base_qty,
        step=step,
        filter_mode=filter_mode,
        tp_type=tp_type,
        trail_pct=trail_pct,
        grid_type=grid_type,
        tp_mode=tp_mode,
        fee_pct=fee_pct,
        funding_rate_8h=funding_rate_8h,
        non_overlapping=non_overlapping,
    )


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
    funding_rate_8h: float = 0.0,
    non_overlapping: bool = True,
) -> pd.DataFrame:
    """Перебор coverage × TP."""
    rows = []
    for cov in cov_values:
        ps = coverage_to_ps(n_orders, cov)
        for tp_pct in tp_values:
            results = simulate(
                df,
                n_orders=n_orders,
                price_scale=ps,
                volume_scale=volume_scale,
                tp_pct=tp_pct / 100.0,
                leverage=leverage,
                horizon_h=horizon_h,
                step=step,
                side=side,
                filter_mode=filter_mode,
                tp_type=tp_type,
                trail_pct=trail_pct,
                fee_pct=fee_pct,
                funding_rate_8h=funding_rate_8h,
                non_overlapping=non_overlapping,
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


def walk_forward(
    df: pd.DataFrame,
    param_fn,
    *,
    n_folds: int = 4,
    train_ratio: float = 0.7,
    embargos: int = 0,
    **simulate_kwargs,
) -> dict:
    """Walk-forward: на train подбираем params через param_fn(train_df)->dict,
    на test гоняем simulate с этими params.

    param_fn(train_df) must return dict with keys accepted by simulate
    (n_orders, price_scale, volume_scale, tp_pct, ...).
    """
    n = len(df)
    if n_folds < 2:
        raise ValueError("n_folds >= 2")
    fold_size = n // n_folds
    if fold_size < 50:
        raise ValueError("недостаточно данных для walk-forward")

    folds = []
    oos_trades: list[TradeResult] = []

    for fold in range(n_folds - 1):
        # expanding train, next fold test
        train_end = fold_size * (fold + 1)
        test_start = train_end + embargos
        test_end = min(n, fold_size * (fold + 2))
        if test_start >= test_end:
            continue

        # optional train_ratio within train window (use last portion)
        train_start = 0
        train_len = train_end - train_start
        if train_ratio < 1.0:
            train_start = max(0, int(train_end - train_len * train_ratio))

        train_df = df.iloc[train_start:train_end].reset_index(drop=True)
        test_df = df.iloc[test_start:test_end].reset_index(drop=True)

        params = param_fn(train_df)
        sim_params = {**simulate_kwargs, **params}
        trades = simulate(test_df, **sim_params)
        summary = summarize(trades)
        folds.append({
            "fold": fold,
            "train_range": (train_start, train_end),
            "test_range": (test_start, test_end),
            "params": params,
            "summary": summary,
            "n_trades": summary.n_trades,
            "total_pnl": summary.total_pnl_pct,
            "win_rate": summary.win_rate,
            "sharpe": summary.sharpe_ratio,
            "max_dd": summary.max_drawdown_pct,
            "n_liquidations": summary.n_liquidations,
        })
        oos_trades.extend(trades)

    oos_summary = summarize(oos_trades)
    return {
        "folds": folds,
        "oos_summary": oos_summary,
        "n_folds_evaluated": len(folds),
        "oos_n_trades": oos_summary.n_trades,
        "oos_total_pnl": oos_summary.total_pnl_pct,
        "oos_win_rate": oos_summary.win_rate,
        "oos_sharpe": oos_summary.sharpe_ratio,
        "oos_max_dd": oos_summary.max_drawdown_pct,
    }


def bayesian_optimize(
    df: pd.DataFrame,
    param_ranges: dict,
    objective: str = "total_pnl",
    n_iterations: int = 50,
    n_initial: int = 10,
    **kwargs,
) -> dict:
    """Байесовская оптимизация (GP + EI). Предпочтительно optuna для prod."""
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
            **{k: v for k, v in kwargs.items() if k != "n_orders"},
        )
        summary = summarize(results)
        if objective == "total_pnl":
            return summary.total_pnl_pct
        if objective == "sharpe":
            return summary.sharpe_ratio
        if objective == "win_rate":
            return summary.win_rate
        return summary.total_pnl_pct

    X_observed = []
    y_observed = []
    rng = np.random.default_rng(42)

    for _ in range(n_initial):
        params = {
            name: float(rng.uniform(low, high))
            for name, (low, high) in param_ranges.items()
        }
        score = evaluate_params(params)
        X_observed.append(params_to_array(params))
        y_observed.append(score)

    X_observed = np.array(X_observed)
    y_observed = np.array(y_observed)

    def rbf_kernel(X1, X2, length_scale=1.0, variance=1.0):
        sqdist = (
            np.sum(X1**2, axis=1).reshape(-1, 1)
            + np.sum(X2**2, axis=1)
            - 2 * np.dot(X1, X2.T)
        )
        return variance * np.exp(-0.5 * sqdist / length_scale**2)

    def expected_improvement(X, X_obs, y_obs, xi=0.01):
        K = rbf_kernel(X_obs, X_obs) + 1e-6 * np.eye(len(X_obs))
        try:
            K_inv = np.linalg.inv(K)
        except np.linalg.LinAlgError:
            K_inv = np.linalg.pinv(K)
        mu = rbf_kernel(X, X_obs) @ K_inv @ y_obs
        var = np.diag(
            rbf_kernel(X, X)
            - rbf_kernel(X, X_obs) @ K_inv @ rbf_kernel(X_obs, X)
        )
        sigma = np.sqrt(np.maximum(var, 1e-12))
        y_best = np.max(y_obs)
        z = (mu - y_best - xi) / sigma
        return (mu - y_best - xi) * norm.cdf(z) + sigma * norm.pdf(z)

    for _ in range(n_iterations):
        n_candidates = 1000
        X_candidates = np.zeros((n_candidates, n_params))
        for i, (name, (low, high)) in enumerate(param_ranges.items()):
            X_candidates[:, i] = rng.uniform(low, high, n_candidates)
        ei = expected_improvement(X_candidates, X_observed, y_observed)
        best_idx = int(np.argmax(ei))
        new_params = array_to_params(X_candidates[best_idx])
        new_score = evaluate_params(new_params)
        X_observed = np.vstack([X_observed, X_candidates[best_idx : best_idx + 1]])
        y_observed = np.append(y_observed, new_score)

    best_idx = int(np.argmax(y_observed))
    return {
        "best_params": array_to_params(X_observed[best_idx]),
        "best_score": float(y_observed[best_idx]),
        "objective": objective,
        "n_evaluations": len(y_observed),
        "history": {"X": X_observed.tolist(), "y": y_observed.tolist()},
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
    seed: int = 42,
) -> dict:
    """Монте-Карло DCA на GBM-путях."""
    close = df["close"].to_numpy(dtype=float)
    returns = np.diff(np.log(np.maximum(close, 1e-12)))
    mu = float(np.mean(returns))
    sigma = float(np.std(returns))
    liq_dist = 1.0 / leverage - maintenance_margin_rate
    rng = np.random.default_rng(seed)
    results = []

    for _ in range(n_paths):
        z = rng.standard_normal(horizon_h)
        log_prices = np.zeros(horizon_h + 1)
        log_prices[0] = np.log(close[-1])
        for t in range(1, horizon_h + 1):
            log_prices[t] = (
                log_prices[t - 1]
                + (mu - 0.5 * sigma**2)
                + sigma * z[t - 1]
            )
        prices = np.exp(log_prices)

        if side == "long":
            levels = [prices[0] * (1.0 / price_scale) ** i for i in range(n_orders)]
        else:
            levels = [prices[0] * (price_scale ** i) for i in range(n_orders)]
        qtys = [base_qty * (volume_scale ** k) for k in range(n_orders)]

        filled = [True] + [False] * (n_orders - 1)
        total_cost = prices[0] * qtys[0]
        total_qty = qtys[0]
        avg_entry = total_cost / total_qty
        if side == "long":
            liq_price = _liq_price_long(avg_entry, leverage, maintenance_margin_rate)
        else:
            liq_price = _liq_price_short(avg_entry, leverage, maintenance_margin_rate)

        liquidated = False
        hit_tp = False
        exit_price = prices[-1]
        n_filled = 1

        for t in range(1, horizon_h + 1):
            price = prices[t]
            for ord_i in range(1, n_orders):
                if filled[ord_i]:
                    continue
                hit = (
                    price <= levels[ord_i]
                    if side == "long"
                    else price >= levels[ord_i]
                )
                if hit:
                    filled[ord_i] = True
                    n_filled += 1
                    total_cost += levels[ord_i] * qtys[ord_i]
                    total_qty += qtys[ord_i]
                    avg_entry = total_cost / total_qty
                    if side == "long":
                        liq_price = _liq_price_long(
                            avg_entry, leverage, maintenance_margin_rate
                        )
                    else:
                        liq_price = _liq_price_short(
                            avg_entry, leverage, maintenance_margin_rate
                        )

            if side == "long" and price <= liq_price:
                liquidated = True
                exit_price = liq_price
                break
            if side == "short" and price >= liq_price:
                liquidated = True
                exit_price = liq_price
                break

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

        if liquidated:
            pnl_pct = -liq_dist * 100.0
        elif side == "long":
            pnl_pct = (exit_price - avg_entry) / avg_entry * 100.0
        else:
            pnl_pct = (avg_entry - exit_price) / avg_entry * 100.0

        fee_total = _fee_pct_of_notional(
            total_cost, total_qty, exit_price, fee_pct
        )
        pnl_pct -= fee_total
        results.append(pnl_pct)

    arr = np.array(results)
    return {
        "n_paths": n_paths,
        "mean_pnl": float(np.mean(arr)),
        "median_pnl": float(np.median(arr)),
        "std_pnl": float(np.std(arr)),
        "win_rate": float(np.mean(arr > 0) * 100),
        "liquidation_rate": float(np.mean(arr <= -liq_dist * 100 + 1) * 100),
        "p5_pnl": float(np.percentile(arr, 5)),
        "p95_pnl": float(np.percentile(arr, 95)),
        "cvar_5": float(np.mean(arr[arr <= np.percentile(arr, 5)])),
    }

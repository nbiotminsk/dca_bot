"""Portfolio Optimizer: Nash Equilibrium для hedge-стратегии (long+short).

Оптимальное распределение капитала между long и short позициями
на основе теории игр и современной портфельной теории.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class HedgeAllocation:
    long_weight: float
    short_weight: float
    expected_return: float
    expected_volatility: float
    sharpe_ratio: float
    nash_equilibrium: bool
    rationale: str


def compute_optimal_hedge_weights(
    long_returns: np.ndarray,
    short_returns: np.ndarray,
    risk_free_rate: float = 0.0,
) -> HedgeAllocation:
    """Вычислить оптимальные веса для hedge-стратегии (Nash Equilibrium).
    
    Для двух активов (long и short) оптимальное соотношение:
    w_long* = (σ_short² - ρ×σ_long×σ_short) / (σ_long² + σ_short² - 2ρ×σ_long×σ_short)
    
    где ρ — корреляция между long и short returns.
    
    Args:
        long_returns: Массив доходностей long позиции
        short_returns: Массив доходностей short позиции
        risk_free_rate: Безрисковая ставка
    
    Returns:
        HedgeAllocation с оптимальными весами
    """
    if len(long_returns) != len(short_returns):
        raise ValueError("long_returns и short_returns должны иметь одинаковую длину")
    
    if len(long_returns) < 10:
        return HedgeAllocation(
            long_weight=0.5,
            short_weight=0.5,
            expected_return=0.0,
            expected_volatility=0.0,
            sharpe_ratio=0.0,
            nash_equilibrium=False,
            rationale="Недостаточно данных для оптимизации",
        )
    
    mu_long = np.mean(long_returns)
    mu_short = np.mean(short_returns)
    
    sigma_long = np.std(long_returns)
    sigma_short = np.std(short_returns)
    
    corr = np.corrcoef(long_returns, short_returns)[0, 1]
    
    if sigma_long == 0 or sigma_short == 0:
        return HedgeAllocation(
            long_weight=0.5,
            short_weight=0.5,
            expected_return=(mu_long + mu_short) / 2,
            expected_volatility=0.0,
            sharpe_ratio=0.0,
            nash_equilibrium=False,
            rationale="Нулевая волатильность",
        )
    
    var_long = sigma_long ** 2
    var_short = sigma_short ** 2
    cov = corr * sigma_long * sigma_short
    
    denominator = var_long + var_short - 2 * cov
    
    if abs(denominator) < 1e-10:
        w_long = 0.5
    else:
        w_long = (var_short - cov) / denominator
    
    w_long = max(0.0, min(1.0, w_long))
    w_short = 1.0 - w_long
    
    portfolio_return = w_long * mu_long + w_short * mu_short
    portfolio_var = (w_long**2 * var_long + 
                     w_short**2 * var_short + 
                     2 * w_long * w_short * cov)
    portfolio_vol = np.sqrt(max(0, portfolio_var))
    
    sharpe = 0.0
    if portfolio_vol > 0:
        sharpe = (portfolio_return - risk_free_rate) / portfolio_vol
    
    is_nash = abs(w_long - 0.5) < 0.1 or abs(corr) < 0.3
    
    rationale = (
        f"Nash Equilibrium: w_long={w_long:.3f}, w_short={w_short:.3f} | "
        f"corr={corr:.3f}, σ_long={sigma_long:.4f}, σ_short={sigma_short:.4f} | "
        f"E[R]={portfolio_return:.4f}, σ={portfolio_vol:.4f}, Sharpe={sharpe:.3f}"
    )
    
    return HedgeAllocation(
        long_weight=w_long,
        short_weight=w_short,
        expected_return=portfolio_return,
        expected_volatility=portfolio_vol,
        sharpe_ratio=sharpe,
        nash_equilibrium=is_nash,
        rationale=rationale,
    )


def compute_min_variance_portfolio(
    returns_matrix: np.ndarray,
    risk_free_rate: float = 0.0,
) -> tuple[np.ndarray, float, float]:
    """Вычислить портфель минимальной дисперсии для N активов.
    
    Args:
        returns_matrix: Матрица доходностей (n_samples × n_assets)
        risk_free_rate: Безрисковая ставка
    
    Returns:
        (weights, expected_return, volatility)
    """
    n_assets = returns_matrix.shape[1]
    
    if n_assets == 0:
        return np.array([]), 0.0, 0.0
    
    cov_matrix = np.cov(returns_matrix, rowvar=False)
    
    ones = np.ones(n_assets)
    cov_inv = np.linalg.inv(cov_matrix + 1e-6 * np.eye(n_assets))
    
    weights = cov_inv @ ones
    weights /= np.sum(weights)
    
    expected_return = float(np.mean(returns_matrix @ weights))
    portfolio_var = float(weights @ cov_matrix @ weights)
    volatility = np.sqrt(max(0, portfolio_var))
    
    return weights, expected_return, volatility


def compute_max_sharpe_portfolio(
    returns_matrix: np.ndarray,
    risk_free_rate: float = 0.0,
    n_simulations: int = 10000,
) -> tuple[np.ndarray, float, float, float]:
    """Вычислить портфель максимального Sharpe ratio методом Монте-Карло.
    
    Args:
        returns_matrix: Матрица доходностей (n_samples × n_assets)
        risk_free_rate: Безрисковая ставка
        n_simulations: Количество случайных портфелей
    
    Returns:
        (weights, expected_return, volatility, sharpe_ratio)
    """
    n_assets = returns_matrix.shape[1]
    
    if n_assets == 0:
        return np.array([]), 0.0, 0.0, 0.0
    
    mean_returns = np.mean(returns_matrix, axis=0)
    cov_matrix = np.cov(returns_matrix, rowvar=False)
    
    best_sharpe = -np.inf
    best_weights = np.ones(n_assets) / n_assets
    
    for _ in range(n_simulations):
        weights = np.random.dirichlet(np.ones(n_assets))
        
        portfolio_return = float(weights @ mean_returns)
        portfolio_var = float(weights @ cov_matrix @ weights)
        portfolio_vol = np.sqrt(max(0, portfolio_var))
        
        if portfolio_vol > 0:
            sharpe = (portfolio_return - risk_free_rate) / portfolio_vol
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_weights = weights
    
    expected_return = float(best_weights @ mean_returns)
    portfolio_var = float(best_weights @ cov_matrix @ best_weights)
    volatility = np.sqrt(max(0, portfolio_var))
    
    return best_weights, expected_return, volatility, best_sharpe


def analyze_hedge_efficiency(
    df: pd.DataFrame,
    long_params: dict,
    short_params: dict,
) -> dict:
    """Анализ эффективности hedge-стратегии.
    
    Args:
        df: DataFrame с OHLCV данными
        long_params: Параметры long стратегии
        short_params: Параметры short стратегии
    
    Returns:
        Словарь с метриками эффективности
    """
    close = df["close"].to_numpy(dtype=float)
    returns = np.diff(close) / close[:-1]
    
    long_returns = returns
    short_returns = -returns
    
    allocation = compute_optimal_hedge_weights(long_returns, short_returns)
    
    hedge_ratio = allocation.long_weight / allocation.short_weight if allocation.short_weight > 0 else float('inf')
    
    return {
        "allocation": allocation,
        "hedge_ratio": hedge_ratio,
        "correlation": float(np.corrcoef(long_returns, short_returns)[0, 1]),
        "diversification_benefit": 1.0 - allocation.expected_volatility / np.mean([np.std(long_returns), np.std(short_returns)]),
    }

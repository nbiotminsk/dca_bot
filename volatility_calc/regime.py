"""Regime Detection с использованием Hidden Markov Model (HMM).

Определяет режимы рынка (trending/mean-reverting/volatile) для адаптации стратегии.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    MEAN_REVERTING = "mean_reverting"
    HIGH_VOLATILITY = "high_volatility"


@dataclass
class RegimeStats:
    current_regime: MarketRegime
    regime_probabilities: dict[MarketRegime, float]
    transition_matrix: np.ndarray
    regime_history: list[MarketRegime]
    confidence: float


def _gaussian_pdf(x: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    """Плотность нормального распределения."""
    return np.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * np.sqrt(2 * np.pi))


def _baum_welch(observations: np.ndarray, n_states: int = 4, 
                max_iter: int = 100, tol: float = 1e-6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Baum-Welch алгоритм для обучения HMM.
    
    Returns:
        (transition_matrix, emission_means, emission_stds)
    """
    n = len(observations)
    
    transition = np.ones((n_states, n_states)) / n_states
    means = np.array([np.percentile(observations, p) for p in [25, 50, 75, 90]])
    stds = np.array([np.std(observations)] * n_states)
    
    prev_log_likelihood = -np.inf
    
    for iteration in range(max_iter):
        alpha = np.zeros((n, n_states))
        beta = np.zeros((n, n_states))
        
        for j in range(n_states):
            alpha[0, j] = _gaussian_pdf(observations[0], means[j], stds[j]) / n_states
        
        alpha_sum = np.sum(alpha[0, :])
        if alpha_sum > 0:
            alpha[0, :] /= alpha_sum
        
        for t in range(1, n):
            for j in range(n_states):
                alpha[t, j] = sum(alpha[t-1, i] * transition[i, j] for i in range(n_states))
                alpha[t, j] *= _gaussian_pdf(observations[t], means[j], stds[j])
            
            alpha_sum = np.sum(alpha[t, :])
            if alpha_sum > 0:
                alpha[t, :] /= alpha_sum
        
        beta[n-1, :] = 1.0
        
        for t in range(n-2, -1, -1):
            for i in range(n_states):
                beta[t, i] = sum(
                    transition[i, j] * _gaussian_pdf(observations[t+1], means[j], stds[j]) * beta[t+1, j]
                    for j in range(n_states)
                )
            
            beta_sum = np.sum(beta[t, :])
            if beta_sum > 0:
                beta[t, :] /= beta_sum
        
        gamma = alpha * beta
        gamma_sum = np.sum(gamma, axis=1, keepdims=True)
        gamma_sum[gamma_sum == 0] = 1
        gamma /= gamma_sum
        
        xi = np.zeros((n-1, n_states, n_states))
        for t in range(n-1):
            for i in range(n_states):
                for j in range(n_states):
                    xi[t, i, j] = (alpha[t, i] * transition[i, j] * 
                                   _gaussian_pdf(observations[t+1], means[j], stds[j]) * 
                                   beta[t+1, j])
            
            xi_sum = np.sum(xi[t, :, :])
            if xi_sum > 0:
                xi[t, :, :] /= xi_sum
        
        for i in range(n_states):
            gamma_sum_i = np.sum(gamma[:-1, i])
            if gamma_sum_i > 0:
                transition[i, :] = np.sum(xi[:, i, :], axis=0) / gamma_sum_i
            
            gamma_sum_all = np.sum(gamma[:, i])
            if gamma_sum_all > 0:
                means[i] = np.sum(gamma[:, i] * observations) / gamma_sum_all
                
                diff = observations - means[i]
                stds[i] = np.sqrt(np.sum(gamma[:, i] * diff**2) / gamma_sum_all)
                stds[i] = max(stds[i], 1e-6)
        
        log_likelihood = sum(np.log(np.sum(alpha[t, :]) + 1e-10) for t in range(n))
        
        if abs(log_likelihood - prev_log_likelihood) < tol:
            break
        prev_log_likelihood = log_likelihood
    
    return transition, means, stds


def detect_regime(df: pd.DataFrame, window: int = 100) -> RegimeStats:
    """Определить текущий режим рынка с помощью HMM.
    
    Args:
        df: DataFrame с OHLCV данными
        window: Размер окна для анализа
    
    Returns:
        RegimeStats с текущим режимом и статистикой
    """
    close = df["close"].to_numpy(dtype=float)
    returns = np.diff(np.log(close))
    
    if len(returns) < window:
        window = len(returns)
    
    recent_returns = returns[-window:]
    
    transition, means, stds = _baum_welch(recent_returns, n_states=4)
    
    n = len(recent_returns)
    gamma = np.zeros((n, 4))
    
    for j in range(4):
        gamma[0, j] = _gaussian_pdf(recent_returns[0], means[j], stds[j]) / 4
    
    gamma_sum = np.sum(gamma[0, :])
    if gamma_sum > 0:
        gamma[0, :] /= gamma_sum
    
    for t in range(1, n):
        for j in range(4):
            gamma[t, j] = sum(gamma[t-1, i] * transition[i, j] for i in range(4))
            gamma[t, j] *= _gaussian_pdf(recent_returns[t], means[j], stds[j])
        
        gamma_sum = np.sum(gamma[t, :])
        if gamma_sum > 0:
            gamma[t, :] /= gamma_sum
    
    current_probs = gamma[-1, :]
    current_state = int(np.argmax(current_probs))
    
    sorted_indices = np.argsort(means)
    state_mapping = {}
    
    state_mapping[sorted_indices[0]] = MarketRegime.TRENDING_DOWN
    state_mapping[sorted_indices[1]] = MarketRegime.MEAN_REVERTING
    state_mapping[sorted_indices[2]] = MarketRegime.TRENDING_UP
    state_mapping[sorted_indices[3]] = MarketRegime.HIGH_VOLATILITY
    
    current_regime = state_mapping[current_state]
    
    regime_probs = {}
    for state_idx, regime in state_mapping.items():
        regime_probs[regime] = float(current_probs[state_idx])
    
    regime_history = []
    for t in range(n):
        state = int(np.argmax(gamma[t, :]))
        regime_history.append(state_mapping[state])
    
    confidence = float(current_probs[current_state])
    
    return RegimeStats(
        current_regime=current_regime,
        regime_probabilities=regime_probs,
        transition_matrix=transition,
        regime_history=regime_history,
        confidence=confidence,
    )


def adapt_parameters_to_regime(regime: MarketRegime, 
                                base_params: dict) -> dict:
    """Адаптировать параметры стратегии под текущий режим.
    
    Args:
        regime: Текущий режим рынка
        base_params: Базовые параметры (coverage, tp, volume_scale)
    
    Returns:
        Адаптированные параметры
    """
    params = base_params.copy()
    
    if regime == MarketRegime.TRENDING_UP:
        params["coverage"] = params.get("coverage", 0.2) * 0.8
        params["tp"] = params.get("tp", 1.0) * 1.2
        params["volume_scale"] = params.get("volume_scale", 1.2) * 1.1
    
    elif regime == MarketRegime.TRENDING_DOWN:
        params["coverage"] = params.get("coverage", 0.2) * 1.2
        params["tp"] = params.get("tp", 1.0) * 0.8
        params["volume_scale"] = params.get("volume_scale", 1.2) * 0.9
    
    elif regime == MarketRegime.MEAN_REVERTING:
        params["coverage"] = params.get("coverage", 0.2) * 1.0
        params["tp"] = params.get("tp", 1.0) * 1.0
        params["volume_scale"] = params.get("volume_scale", 1.2) * 1.0
    
    elif regime == MarketRegime.HIGH_VOLATILITY:
        params["coverage"] = params.get("coverage", 0.2) * 1.5
        params["tp"] = params.get("tp", 1.0) * 1.5
        params["volume_scale"] = params.get("volume_scale", 1.2) * 0.8
    
    return params

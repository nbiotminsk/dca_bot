"""volatility_calc: расчёт волатильности и DCA-рекомендаций."""

from .data_fetcher import fetch_ohlcv, SymbolNotFoundError, validate_ohlcv
from .drawdown_analyzer import analyze_extremes, MultiHorizonStats, HorizonStats, SideStats
from .liquidation import assess_liquidation_risk, LiquidationAssessment
from .dca_recommender import recommend_all, FullRecommendation, GridConfig, CurrentSettings
from .report import render_volatility_report

__all__ = [
    "fetch_ohlcv",
    "SymbolNotFoundError",
    "validate_ohlcv",
    "analyze_extremes",
    "MultiHorizonStats",
    "HorizonStats",
    "SideStats",
    "assess_liquidation_risk",
    "LiquidationAssessment",
    "recommend_all",
    "FullRecommendation",
    "GridConfig",
    "CurrentSettings",
    "render_volatility_report",
]

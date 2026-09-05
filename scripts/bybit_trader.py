#!/usr/bin/env python3
"""
Интерактивный терминал торговли по стратегии Fibonacci Dual Grid на Bybit V5.

Функции:
  1. Запрос монеты у пользователя (например, ZEC -> ZECUSDT).
  2. Загрузка свечей через Bybit V5 API (pybit).
  3. Поиск актуального импульса (глубина 60 свечей, мин. размах 2.0%, лог. шкала):
     - Растущий импульс без коррекции -> Трейлинг сетки (сдвиг входов и тейков вверх за ценой).
     - Идет коррекция -> Выставление ордеров 0.500 и 0.618 с тейками 0.236 и 0.382, стоп 1.000.
     - Пробой 1.000 со свипом и дивергенцией MACD -> Вход в Reclaim со стопом за шпильку.
     - Манипуляция -> Сетка на 1.618 и 2.000 со стопом на 2.414 Fib.
  4. Точный расчет объема под риск $1.00 с учетом ограничений Bybit (minOrderQty, tickSize).
  5. Поддержка Dry-Run (безопасный предпросмотр) и Live Execution.
"""

import argparse
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

# Обеспечиваем доступность корневых пакетов проекта
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from indicators.macd import calculate_macd
from indicators.pybit_client import BybitClient
from scripts.backtest_strategy_interactive import calc_fib

console = Console()


@dataclass
class TradeConfig:
    total_risk_usd: float = 2.0
    minor_risk_usd: float = 2.0
    major_risk_usd: float = 2.0
    manipulation_risk_usd: float = 2.0
    grid_weights: list[float] = field(default_factory=lambda: [0.50, 0.30, 0.20])
    entry_buffer_pct: float = 0.10
    entry_buffer_0500_pct: float = 0.10
    entry_buffer_0618_pct: float = 0.15
    entry_buffer_0786_pct: float = 0.15
    entry_buffer_1414_pct: float = 0.10
    entry_buffer_1618_pct: float = 0.10
    tp_buffer_pct: float = 0.10
    reclaim_tp_buffer_pct: float = 2.0
    reclaim_be_trigger_fib: float = 0.786
    reclaim_be_offset_pct: float = 0.05
    reclaim_max_sweep_pct: float = 0.5
    reclaim_allow_close_below: bool = False
    preferred_side: Literal["long", "short"] = "long"
    min_impulse_pct: float = 2.0
    atr_multiplier: float = 2.5
    timeout_hours: int = 24
    minor_timeout_hours: int = 24
    major_timeout_hours: int = 96
    lookback_bars: int = 120
    max_impulse_bars: int = 24
    minor_max_impulse_bars: int = 24
    major_max_impulse_bars: int = 96
    timeframe: str = "1h"
    scale: Literal["log", "linear"] = "log"
    symbols: list[str] = field(default_factory=list)
    config_path: Optional[str] = None


def load_trade_config(config_path: Optional[str | Path] = None) -> TradeConfig:
    """Загружает параметры стратегии и риск-менеджмента из файла YAML."""
    default_path = root_dir / "config" / "trade_config.yaml"
    path = Path(config_path) if config_path else default_path

    cfg = TradeConfig()
    if not path.exists():
        return cfg

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        cfg.config_path = str(path)

        risk_data = data.get("risk", {})
        if "total_risk_usd" in risk_data:
            cfg.total_risk_usd = float(risk_data["total_risk_usd"])
            cfg.minor_risk_usd = float(risk_data["total_risk_usd"])
        if "minor_risk_usd" in risk_data:
            cfg.minor_risk_usd = float(risk_data["minor_risk_usd"])
            cfg.total_risk_usd = float(risk_data["minor_risk_usd"])
        if "major_risk_usd" in risk_data:
            cfg.major_risk_usd = float(risk_data["major_risk_usd"])
        if "manipulation_risk_usd" in risk_data:
            cfg.manipulation_risk_usd = float(risk_data["manipulation_risk_usd"])
        if "grid_weights" in risk_data:
            raw_w = risk_data["grid_weights"]
            if isinstance(raw_w, list) and len(raw_w) == 3:
                cfg.grid_weights = [float(x) for x in raw_w]

        buffer_data = data.get("buffers", {})
        if "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_pct = float(buffer_data["entry_buffer_pct"])
        if "entry_buffer_0500_pct" in buffer_data:
            cfg.entry_buffer_0500_pct = float(buffer_data["entry_buffer_0500_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_0500_pct = cfg.entry_buffer_pct
        if "entry_buffer_0618_pct" in buffer_data:
            cfg.entry_buffer_0618_pct = float(buffer_data["entry_buffer_0618_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_0618_pct = cfg.entry_buffer_pct
        if "entry_buffer_0786_pct" in buffer_data:
            cfg.entry_buffer_0786_pct = float(buffer_data["entry_buffer_0786_pct"])
        elif "entry_buffer_0718_pct" in buffer_data:
            cfg.entry_buffer_0786_pct = float(buffer_data["entry_buffer_0718_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_0786_pct = cfg.entry_buffer_pct

        if "entry_buffer_1414_pct" in buffer_data:
            cfg.entry_buffer_1414_pct = float(buffer_data["entry_buffer_1414_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_1414_pct = cfg.entry_buffer_pct
        if "entry_buffer_1618_pct" in buffer_data:
            cfg.entry_buffer_1618_pct = float(buffer_data["entry_buffer_1618_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_1618_pct = cfg.entry_buffer_pct

        if "tp_buffer_pct" in buffer_data:
            cfg.tp_buffer_pct = float(buffer_data["tp_buffer_pct"])
        if "reclaim_tp_buffer_pct" in buffer_data:
            cfg.reclaim_tp_buffer_pct = float(buffer_data["reclaim_tp_buffer_pct"])
        if "reclaim_be_trigger_fib" in buffer_data:
            cfg.reclaim_be_trigger_fib = float(buffer_data["reclaim_be_trigger_fib"])
        if "reclaim_be_offset_pct" in buffer_data:
            cfg.reclaim_be_offset_pct = float(buffer_data["reclaim_be_offset_pct"])
        if "reclaim_max_sweep_pct" in buffer_data:
            cfg.reclaim_max_sweep_pct = float(buffer_data["reclaim_max_sweep_pct"])
        if "reclaim_allow_close_below" in buffer_data:
            cfg.reclaim_allow_close_below = bool(buffer_data["reclaim_allow_close_below"])

        strat_data = data.get("strategy", {})
        if "preferred_side" in strat_data:
            s_side = str(strat_data["preferred_side"]).lower()
            if s_side in ("long", "short"):
                cfg.preferred_side = s_side  # type: ignore[assignment]
        if "min_impulse_pct" in strat_data:
            cfg.min_impulse_pct = float(strat_data["min_impulse_pct"])
        if "atr_multiplier" in strat_data:
            val_atr = strat_data["atr_multiplier"]
            cfg.atr_multiplier = float(val_atr) if val_atr is not None else 0.0
        if "timeout_hours" in strat_data:
            val_to = strat_data["timeout_hours"]
            cfg.timeout_hours = int(val_to) if val_to is not None else 0
            cfg.minor_timeout_hours = cfg.timeout_hours
        if "minor_timeout_hours" in strat_data:
            val_mto = strat_data["minor_timeout_hours"]
            cfg.minor_timeout_hours = int(val_mto) if val_mto is not None else 0
            cfg.timeout_hours = cfg.minor_timeout_hours
        if "major_timeout_hours" in strat_data:
            val_majto = strat_data["major_timeout_hours"]
            cfg.major_timeout_hours = int(val_majto) if val_majto is not None else 0
        if "lookback_bars" in strat_data:
            cfg.lookback_bars = int(strat_data["lookback_bars"])
        if "max_impulse_bars" in strat_data:
            cfg.max_impulse_bars = int(strat_data["max_impulse_bars"])
            cfg.minor_max_impulse_bars = int(strat_data["max_impulse_bars"])
        if "minor_max_impulse_bars" in strat_data:
            cfg.minor_max_impulse_bars = int(strat_data["minor_max_impulse_bars"])
            cfg.max_impulse_bars = cfg.minor_max_impulse_bars
        if "major_max_impulse_bars" in strat_data:
            cfg.major_max_impulse_bars = int(strat_data["major_max_impulse_bars"])
        if "timeframe" in strat_data:
            cfg.timeframe = str(strat_data["timeframe"])
        if "scale" in strat_data:
            s_scale = str(strat_data["scale"]).lower()
            if s_scale in ("log", "linear"):
                cfg.scale = s_scale  # type: ignore[assignment]
        if "symbols" in strat_data:
            raw_syms = strat_data["symbols"]
            if isinstance(raw_syms, list):
                cfg.symbols = [str(s).strip() for s in raw_syms if str(s).strip()]
            elif isinstance(raw_syms, str):
                cfg.symbols = [s.strip() for s in raw_syms.split(",") if s.strip()]
        elif "symbol" in strat_data:
            raw_sym = str(strat_data["symbol"]).strip()
            if raw_sym:
                cfg.symbols = [raw_sym]

    except Exception as e:
        console.print(f"[yellow]⚠️ Ошибка при загрузке конфига {path}: {e}. Используются значения по умолчанию.[/yellow]")

    return cfg


@dataclass
class SetupSignal:
    setup_type: Literal[
        "TRIPLE_GRID_TRAILING",
        "TRIPLE_GRID_CORRECTION",
        "DUAL_GRID_TRAILING",
        "DUAL_GRID_CORRECTION",
        "SWEEP_RECLAIM",
        "MANIPULATION",
        "AWAITING_BREAK_BELOW",
        "NONE",
    ]
    side: Literal["long", "short"]
    imp_start_time: pd.Timestamp
    imp_end_time: pd.Timestamp
    imp_start_price: float
    imp_peak_price: float
    imp_pct: float
    # Уровни
    entry_1: float
    tp_1: float
    entry_2: Optional[float] = None
    tp_2: Optional[float] = None
    entry_3: Optional[float] = None
    tp_3: Optional[float] = None
    stop_loss: float = 0.0
    # Безубыток (Sweep Reclaim)
    be_trigger: Optional[float] = None
    be_price: Optional[float] = None
    # Детали свипа / дивергенции
    sweep_price: Optional[float] = None
    sweep_pct: Optional[float] = None
    macd_divergent: bool = False
    description: str = ""
    # Двухуровневая сетка (minor до 24 свечей / major до 96 свечей)
    layer: Literal["minor", "major"] = "minor"
    p_0382: Optional[float] = None
    touched_0382: bool = True


def find_active_setup(
    df: pd.DataFrame,
    min_pct: float = 2.0,
    lookback_bars: int = 60,
    preferred_side: Literal["long", "short", "both"] = "long",
    scale: Literal["log", "linear"] = "log",
    max_sweep_pct: float = 0.5,
    allow_close_below: bool = False,
    entry_buffer_pct: float = 0.10,
    entry_buffer_0500_pct: Optional[float] = None,
    entry_buffer_0618_pct: Optional[float] = None,
    entry_buffer_0786_pct: Optional[float] = None,
    entry_buffer_1414_pct: Optional[float] = None,
    entry_buffer_1618_pct: Optional[float] = None,
    tp_buffer_pct: float = 0.1,
    reclaim_tp_buffer_pct: float = 2.0,
    reclaim_be_trigger_fib: float = 0.786,
    reclaim_be_offset_pct: float = 0.05,
    atr_multiplier: Optional[float] = None,
    timeout_hours: Optional[int] = None,
    max_impulse_bars: Optional[int] = None,
    min_impulse_bars: Optional[int] = None,
    layer: Literal["minor", "major"] = "minor",
) -> Optional[SetupSignal]:
    """
    Анализирует свечи на наличие активного не отработанного торгового сетапа (ТОЛЬКО В LONG):
    1. Трейлинг / Активная тройная сетка (0.500, 0.618, 0.786) с буфером входа перед каждым уровнем и -0.1% перед тейком.
    2. Свип ликвидности + MACD Reclaim.
    3. Манипуляция (1.414 / 1.618) с индивидуальными отступами и тейками -0.1%.
    """
    if len(df) < 15:
        return None

    # Ограничиваем глубину анализа lookback_bars последними свечами
    if len(df) > lookback_bars:
        df = df.iloc[-lookback_bars:].reset_index(drop=True)

    # Рассчитываем волатильность ATR (14)
    from indicators.atr import calculate_atr
    atr_df = calculate_atr(df["high"], df["low"], df["close"], period=14)
    atr_pct = float(atr_df["atr_pct"].iloc[-1]) if len(atr_df) >= 14 else 2.0

    # Эффективный порог импульса с учетом динамического множителя ATR
    effective_min_pct = min_pct
    if atr_multiplier is not None and atr_multiplier > 0:
        effective_min_pct = max(min_pct, atr_multiplier * atr_pct)

    # Рассчитываем MACD на том же окне
    macd_df = calculate_macd(df["close"])
    hist = macd_df["hist"].values

    from scripts.backtest_strategy_interactive import detect_impulses

    buf_0500 = entry_buffer_0500_pct if entry_buffer_0500_pct is not None else entry_buffer_pct
    buf_0618 = entry_buffer_0618_pct if entry_buffer_0618_pct is not None else entry_buffer_pct
    buf_0786 = entry_buffer_0786_pct if entry_buffer_0786_pct is not None else entry_buffer_pct
    buf_1414 = entry_buffer_1414_pct if entry_buffer_1414_pct is not None else entry_buffer_pct
    buf_1618 = entry_buffer_1618_pct if entry_buffer_1618_pct is not None else entry_buffer_pct

    sides_to_check: list[Literal["long", "short"]] = (
        ["long"] if preferred_side == "long" else (["short"] if preferred_side == "short" else ["long", "short"])
    )

    for side in sides_to_check:
        is_long = (side == "long")
        # Ищем импульсы по нашей стратегии с фильтром ATR, учетом буфера входа (buf_0500) и скользящим поиском
        imps = detect_impulses(
            df,
            min_pct=effective_min_pct,
            side=side,
            scale=scale,
            tolerance_pct=buf_0500,
            allow_internal=True,
        )
        if not imps:
            continue

        # Ограничение по длительности импульса (Minor <= minor_max_impulse_bars; Major 25..96 баров)
        if max_impulse_bars is not None and max_impulse_bars > 0:
            imps = [imp for imp in imps if (imp.end_idx - imp.start_idx + 1) <= max_impulse_bars]
        if min_impulse_bars is not None and min_impulse_bars > 0:
            imps = [imp for imp in imps if (imp.end_idx - imp.start_idx + 1) >= min_impulse_bars]
        if not imps:
            continue

        unbroken_setups: list[SetupSignal] = []
        reclaim_setups: list[SetupSignal] = []
        manipulation_setups: list[SetupSignal] = []
        buf_desc = f"+{buf_0500:.2f}%/+{buf_0618:.2f}%/+{buf_0786:.2f}%" if (buf_0500 != buf_0618 or buf_0618 != buf_0786) else f"+{buf_0500:.2f}%"

        # Перебираем от самых свежих к старым в поиске неотработанного
        for imp in reversed(imps):
            p_0236 = calc_fib(imp.high, imp.low, 0.236, is_long=is_long, scale=scale)
            p_0382 = calc_fib(imp.high, imp.low, 0.382, is_long=is_long, scale=scale)
            p_0500 = calc_fib(imp.high, imp.low, 0.500, is_long=is_long, scale=scale)
            p_0618 = calc_fib(imp.high, imp.low, 0.618, is_long=is_long, scale=scale)
            p_0786 = calc_fib(imp.high, imp.low, 0.786, is_long=is_long, scale=scale)
            p_1000 = imp.low if is_long else imp.high

            p_1414 = calc_fib(imp.high, imp.low, 1.414, is_long=is_long, scale=scale)
            p_1618 = calc_fib(imp.high, imp.low, 1.618, is_long=is_long, scale=scale)
            p_2000 = calc_fib(imp.high, imp.low, 2.000, is_long=is_long, scale=scale)
            p_2414 = calc_fib(imp.high, imp.low, 2.414, is_long=is_long, scale=scale)

            # Буферы перед входом (для Лонга сдвиг вверх перед уровнем)
            buf_mult_0500 = 1.0 + (buf_0500 / 100.0) if is_long else 1.0 - (buf_0500 / 100.0)
            buf_mult_0618 = 1.0 + (buf_0618 / 100.0) if is_long else 1.0 - (buf_0618 / 100.0)
            buf_mult_0786 = 1.0 + (buf_0786 / 100.0) if is_long else 1.0 - (buf_0786 / 100.0)
            buf_mult_1414 = 1.0 + (buf_1414 / 100.0) if is_long else 1.0 - (buf_1414 / 100.0)
            buf_mult_1618 = 1.0 + (buf_1618 / 100.0) if is_long else 1.0 - (buf_1618 / 100.0)
            buf_mult_default = 1.0 + (entry_buffer_pct / 100.0) if is_long else 1.0 - (entry_buffer_pct / 100.0)

            e_0500 = p_0500 * buf_mult_0500
            e_0618 = p_0618 * buf_mult_0618
            e_0786 = p_0786 * buf_mult_0786
            e_1414 = p_1414 * buf_mult_1414
            e_1618 = p_1618 * buf_mult_1618
            p_2000 * buf_mult_default

            # Буфер перед тейком (-0.1% для Лонга для гарантированного раннего закрытия)
            tp_mult = 1.0 - (tp_buffer_pct / 100.0) if is_long else 1.0 + (tp_buffer_pct / 100.0)
            tp_0236 = p_0236 * tp_mult
            tp_0382 = p_0382 * tp_mult
            tp_0500 = p_0500 * tp_mult
            tp_1000 = p_1000 * tp_mult

            # Тейк для ложного пробоя (Sweep Reclaim): уровень 0.618 Fib минус 2.0% (reclaim_tp_buffer_pct)
            tp_reclaim_mult = 1.0 - (reclaim_tp_buffer_pct / 100.0) if is_long else 1.0 + (reclaim_tp_buffer_pct / 100.0)
            tp_reclaim_0618 = p_0618 * tp_reclaim_mult

            post_df = df.iloc[imp.end_idx + 1:]

            # Для Большой фибы: проверяем, коснулась ли коррекция уровня 0.382
            if layer == "major":
                if len(post_df) == 0:
                    touched_0382 = False
                else:
                    touched_0382 = bool((post_df["low"].min() <= p_0382) if is_long else (post_df["high"].max() >= p_0382))
            else:
                touched_0382 = True

            # Проверка тайм-аута свежести:
            # Для Minor: если за timeout_hours не коснулась 0.500 — остыл
            # Для Major: если за timeout_hours не коснулась 0.382 — остыл
            if timeout_hours is not None and timeout_hours > 0 and len(post_df) > timeout_hours:
                post_slice = post_df.iloc[:timeout_hours]
                check_level = p_0382 if layer == "major" else p_0500
                touched_early = (post_slice["low"] <= check_level).any() if is_long else (post_slice["high"] >= check_level).any()
                if not touched_early:
                    continue  # Пропускаем остывший в боковике импульс

            if len(post_df) == 0:
                # Импульс находится на самой последней свече -> ТРЕЙЛИНГ
                desc = (
                    f"Большая фиба (+{imp.pct:.2f}%): цена на вершине выше 0.382 (${p_0382:.4f}). Ожидание отката к 0.382, лимитки не выставляются, маржа свободна."
                    if (layer == "major" and not touched_0382)
                    else f"Растущий импульс (+{imp.pct:.2f}%) на текущей свече [ATR {atr_pct:.2f}%]. Трейлинг тройной сетки (вход {buf_desc}, тейк -{tp_buffer_pct}%)."
                )
                unbroken_setups.append(SetupSignal(
                    setup_type="TRIPLE_GRID_TRAILING",
                    side=side,
                    imp_start_time=imp.start_time,
                    imp_end_time=imp.end_time,
                    imp_start_price=p_1000,
                    imp_peak_price=imp.high if is_long else imp.low,
                    imp_pct=imp.pct,
                    entry_1=e_0500,
                    tp_1=tp_0236,
                    entry_2=e_0618,
                    tp_2=tp_0382,
                    entry_3=e_0786,
                    tp_3=tp_0500,
                    stop_loss=p_1000,
                    description=desc,
                    layer=layer,
                    p_0382=p_0382,
                    touched_0382=touched_0382,
                ))
                continue

            touched_0500 = False
            touch_05_idx = -1
            touched_0618 = False
            touch_0618_idx = -1
            touched_0786 = False
            touch_0786_idx = -1
            tested_0382_after_05 = False
            hit_tp = False
            broken = False
            sweep_val = p_1000
            sweep_idx = -1

            tp_tol_mult = 0.0005  # 0.05% допуск

            for idx in range(len(post_df)):
                bar_h = float(post_df["high"].iloc[idx])
                bar_l = float(post_df["low"].iloc[idx])
                abs_idx = imp.end_idx + 1 + idx

                if is_long:
                    if bar_l <= p_1000:
                        broken = True
                        if bar_l < sweep_val:
                            sweep_val = bar_l
                            sweep_idx = abs_idx

                    # 1. Налив ордера 0.500
                    if not broken and not touched_0500 and bar_l <= p_0500:
                        touched_0500 = True
                        touch_05_idx = abs_idx

                    # Проверка возврата/теста 0.382 ПОСЛЕ касания 0.500
                    if not broken and touched_0500 and abs_idx > touch_05_idx:
                        if bar_h >= p_0382:
                            tested_0382_after_05 = True

                    # 2. Налив ордера 0.618 (если уже налило 0.500)
                    if not broken and touched_0500 and not touched_0618 and bar_l <= p_0618:
                        touched_0618 = True
                        touch_0618_idx = abs_idx

                    # 3. Налив ордера 0.786 (если уже налило 0.618)
                    if not broken and touched_0618 and not touched_0786 and bar_l <= p_0786:
                        touched_0786 = True
                        touch_0786_idx = abs_idx

                    # 4. Взятие тейк-профита:
                    if not broken and touched_0500:
                        if touched_0786:
                            eff_tp = tp_0500 * (1.0 - tp_tol_mult)
                            if abs_idx > touch_0786_idx and bar_h >= eff_tp:
                                hit_tp = True
                                break
                        elif touched_0618:
                            eff_tp = tp_0382 * (1.0 - tp_tol_mult)
                            if abs_idx > touch_0618_idx and bar_h >= eff_tp:
                                hit_tp = True
                                break
                        else:
                            eff_tp = tp_0236 * (1.0 - tp_tol_mult)
                            if abs_idx > touch_05_idx and bar_h >= eff_tp:
                                hit_tp = True
                                break
                else:
                    if bar_h >= p_1000:
                        broken = True
                        if bar_h > sweep_val:
                            sweep_val = bar_h
                            sweep_idx = abs_idx

                    if not broken and not touched_0500 and bar_h >= p_0500:
                        touched_0500 = True
                        touch_05_idx = abs_idx

                    if not broken and touched_0500 and abs_idx > touch_05_idx:
                        if bar_l <= p_0382:
                            tested_0382_after_05 = True

                    if not broken and touched_0500 and not touched_0618 and bar_h >= p_0618:
                        touched_0618 = True
                        touch_0618_idx = abs_idx

                    if not broken and touched_0618 and not touched_0786 and bar_h >= p_0786:
                        touched_0786 = True
                        touch_0786_idx = abs_idx

                    if not broken and touched_0500:
                        if touched_0786:
                            eff_tp = tp_0500 * (1.0 + tp_tol_mult)
                            if abs_idx > touch_0786_idx and bar_l <= eff_tp:
                                hit_tp = True
                                break
                        elif touched_0618:
                            eff_tp = tp_0382 * (1.0 + tp_tol_mult)
                            if abs_idx > touch_0618_idx and bar_l <= eff_tp:
                                hit_tp = True
                                break
                        else:
                            eff_tp = tp_0236 * (1.0 + tp_tol_mult)
                            if abs_idx > touch_05_idx and bar_l <= eff_tp:
                                hit_tp = True
                                break

            # Если импульс уже завершил свой цикл (вход + тейк) — пропускаем
            if hit_tp:
                continue

            # ─── Сценарий 1: Пробой 1.000 (Свип или Манипуляция) ───────────────
            if broken:
                # Если после касания 0.500 цена вернулась и протестировала 0.382 до пробоя 1.000 — импульс уже отработан
                if touched_0500 and tested_0382_after_05:
                    continue

                swp_pct = abs(p_1000 - sweep_val) / p_1000 * 100.0
                latest_c = float(df["close"].iloc[-1])
                is_reclaimed = (latest_c >= p_1000) if is_long else (latest_c <= p_1000)

                # Проверка отсутствия закрепления цены под уровнем 1.000:
                closed_below = (post_df["close"] < p_1000) if is_long else (post_df["close"] > p_1000)
                has_consolidated = closed_below.any() if not allow_close_below else (closed_below.sum() > 1)

                # Дивергенция MACD (гистограмма растет на лонге или падает на шорте)
                swp_bar_idx = sweep_idx if sweep_idx != -1 else (len(df) - 1)
                macd_div = (hist[-1] > hist[swp_bar_idx] or hist[-1] > -0.01) if is_long else (hist[-1] < hist[swp_bar_idx] or hist[-1] < 0.01)

                if swp_pct <= max_sweep_pct and is_reclaimed and not has_consolidated and macd_div:
                    # Валидация: вход должен быть строго до тейка (для Long: latest_c < TP; для Short: latest_c > TP)
                    if is_long and latest_c >= tp_reclaim_0618:
                        continue
                    if not is_long and latest_c <= tp_reclaim_0618:
                        continue
                    sl_target = sweep_val * (0.998 if is_long else 1.002)
                    p_0786_f = calc_fib(imp.high, imp.low, reclaim_be_trigger_fib, is_long=is_long, scale=scale)
                    be_mult = 1.0 + (reclaim_be_offset_pct / 100.0) if is_long else 1.0 - (reclaim_be_offset_pct / 100.0)
                    be_price_val = latest_c * be_mult
                    reclaim_setups.append(SetupSignal(
                        setup_type="SWEEP_RECLAIM",
                        side=side,
                        imp_start_time=imp.start_time,
                        imp_end_time=imp.end_time,
                        imp_start_price=p_1000,
                        imp_peak_price=imp.high if is_long else imp.low,
                        imp_pct=imp.pct,
                        entry_1=latest_c,
                        tp_1=tp_reclaim_0618,
                        stop_loss=sl_target,
                        be_trigger=p_0786_f,
                        be_price=be_price_val,
                        sweep_price=sweep_val,
                        sweep_pct=swp_pct,
                        macd_divergent=True,
                        description=f"Ложный пробой 1.000 ({swp_pct:.2f}% <= {max_sweep_pct}%) без закрепления с дивергенцией MACD. Тейк 0.618 Fib (-{reclaim_tp_buffer_pct}%), БУ на {reclaim_be_trigger_fib} Fib.",
                        layer=layer,
                        p_0382=p_0382,
                        touched_0382=touched_0382,
                    ))
                elif swp_pct > max_sweep_pct or has_consolidated:
                    manipulation_setups.append(SetupSignal(
                        setup_type="MANIPULATION",
                        side=side,
                        imp_start_time=imp.start_time,
                        imp_end_time=imp.end_time,
                        imp_start_price=p_1000,
                        imp_peak_price=imp.high if is_long else imp.low,
                        imp_pct=imp.pct,
                        entry_1=e_1414,
                        tp_1=tp_1000,
                        entry_2=e_1618,
                        tp_2=e_1414,
                        stop_loss=p_2414,
                        sweep_price=sweep_val,
                        sweep_pct=swp_pct,
                        description=f"Манипуляция: выход за 1.000 на {swp_pct:.2f}% (порог {max_sweep_pct}%)" + (" с закреплением" if has_consolidated else "") + ". Сетка на 1.414 и 1.618, стоп 2.414.",
                        layer=layer,
                        p_0382=p_0382,
                        touched_0382=touched_0382,
                    ))
                continue

            # ─── Сценарий 2: Уровень 1.000 НЕ пробит ────────────────────────────
            if not touched_0500:
                # Импульс еще развивается без отката к 0.500 -> ТРЕЙЛИНГ
                desc = (
                    f"Большая фиба (+{imp.pct:.2f}%): цена выше 0.382 (${p_0382:.4f}). Ожидание отката к 0.382, лимитки не выставляются, маржа свободна."
                    if (layer == "major" and not touched_0382)
                    else f"Растущий импульс (+{imp.pct:.2f}%) без коррекции к 0.500. Режим трейлинга тройной сетки (вход {buf_desc}, тейк -{tp_buffer_pct}%)."
                )
                unbroken_setups.append(SetupSignal(
                    setup_type="TRIPLE_GRID_TRAILING",
                    side=side,
                    imp_start_time=imp.start_time,
                    imp_end_time=imp.end_time,
                    imp_start_price=p_1000,
                    imp_peak_price=imp.high if is_long else imp.low,
                    imp_pct=imp.pct,
                    entry_1=e_0500,
                    tp_1=tp_0236,
                    entry_2=e_0618,
                    tp_2=tp_0382,
                    entry_3=e_0786,
                    tp_3=tp_0500,
                    stop_loss=p_1000,
                    description=desc,
                    layer=layer,
                    p_0382=p_0382,
                    touched_0382=touched_0382,
                ))
            else:
                # Касание 0.500 было!
                if tested_0382_after_05:
                    # Вход на 0.500 упущен, и отскок к 0.382 уже состоялся -> импульс отыгран
                    continue
                else:
                    # Вход на 0.500 упущен, но 0.382 НЕ протестирован:
                    # Оставляем следить за монетой (AWAITING_BREAK_BELOW), маржа свободна, сетка не выставляется.
                    unbroken_setups.append(SetupSignal(
                        setup_type="AWAITING_BREAK_BELOW",
                        side=side,
                        imp_start_time=imp.start_time,
                        imp_end_time=imp.end_time,
                        imp_start_price=p_1000,
                        imp_peak_price=imp.high if is_long else imp.low,
                        imp_pct=imp.pct,
                        entry_1=e_0500,
                        tp_1=tp_0236,
                        entry_2=e_0618,
                        tp_2=tp_0382,
                        entry_3=e_0786,
                        tp_3=tp_0500,
                        stop_loss=p_1000,
                        description=f"Вход на 0.500 (${e_0500:.4f}) упущен, 0.382 (${p_0382:.4f}) не протестирован. Ожидание пробоя 1.000 (${p_1000:.4f}) без возврата к 0.382 (маржа свободна).",
                        layer=layer,
                        p_0382=p_0382,
                        touched_0382=touched_0382,
                    ))

        # Приоритет: живые несломанные импульсы > ложный пробой (свип) > манипуляция
        # Внутри каждой категории берем самый свежий пик, а при одинаковом пике — наибольший размах (best_pct)
        if unbroken_setups:
            unbroken_setups.sort(key=lambda s: (s.imp_end_time, s.imp_pct), reverse=True)
            return unbroken_setups[0]
        if reclaim_setups:
            reclaim_setups.sort(key=lambda s: (s.imp_end_time, s.imp_pct), reverse=True)
            return reclaim_setups[0]
        if manipulation_setups:
            manipulation_setups.sort(key=lambda s: (s.imp_end_time, s.imp_pct), reverse=True)
            return manipulation_setups[0]

    return None


def format_symbol(user_input: str) -> str:
    """Приводит пользовательский ввод монеты к формату Bybit USDT Linear (например, ZEC -> ZECUSDT, SUIUSDT.P -> SUIUSDT)."""
    clean = user_input.strip().upper().replace("/", "").replace("-", "")
    # Стрипаем суффиксы TradingView для фьючерсов (.P, .PERP)
    for suffix in (".PERP", ".P"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    if not clean.endswith("USDT") and not clean.endswith("PERP"):
        clean += "USDT"
    return clean


@dataclass
class ActiveTradeMonitor:
    symbol: str
    setup_type: str = "IDLE"
    state: str = "TRAILING"  # "TRAILING", "O1_FILLED", "O2_FILLED", "O3_FILLED", "AWAITING_SWEEP_CLOSE", "SWEEP_RECLAIM_ACTIVE", "MANIPULATION_ACTIVE", "IDLE"
    o1_id: Optional[str] = None
    o2_id: Optional[str] = None
    o3_id: Optional[str] = None
    cur_peak: float = 0.0
    cur_e1: float = 0.0
    cur_tp1: float = 0.0
    cur_e2: float = 0.0
    cur_tp2: float = 0.0
    cur_e3: float = 0.0
    cur_tp3: float = 0.0
    imp_start_price: float = 0.0
    sl: float = 0.0
    q1: float = 0.0
    q2: float = 0.0
    q3: float = 0.0
    has_o2: bool = False
    has_o3: bool = False
    be_trigger: Optional[float] = None
    be_price: Optional[float] = None
    be_applied: bool = False
    tp_basket_applied: bool = False
    position_was_open: bool = False
    stop_bar_time: Optional[pd.Timestamp] = None
    stop_sweep_low: float = 0.0
    last_candle_time: Optional[pd.Timestamp] = None
    imp_end_time: Optional[pd.Timestamp] = None
    close_only: bool = False
    done: bool = False
    layer: Literal["minor", "major"] = "minor"
    side: Literal["long", "short"] = "long"
    p_0382: Optional[float] = None
    touched_0382: bool = True
    timeout_hours: Optional[int] = None
    last_skipped_imp_time: Optional[pd.Timestamp] = None


def make_order_link_id(sym_short: str, layer_tag: str, side_str: str, order_tag: str) -> str:
    """
    Генерирует уникальный orderLinkId (до 36 символов), устойчивый к ошибке 110072 на Bybit.
    Формат: FIB-{SYM}-{LAYER}-{SIDE}-{ORDER}-{HEX} (например, FIB-BTC-MIN-B-O1-8f3a1b).
    """
    side_code = "B" if str(side_str).lower() in ("buy", "long") else "S"
    uid = uuid.uuid4().hex[:6]
    return f"FIB-{sym_short}-{layer_tag}-{side_code}-{order_tag}-{uid}"


def is_entry_missed(entry_price: float, cur_price: float, is_long: bool = True) -> bool:
    """
    Проверяет, не упущен ли вход (цена уже опустилась ниже уровня лимитки на лонге).
    Для Long: вход упущен, если текущая цена <= цены входа (с допуском 0.05%).
    Для Short: вход упущен, если текущая цена >= цены входа (с допуском 0.05%).
    """
    if cur_price <= 0:
        return False
    if is_long:
        return (cur_price <= entry_price) or (entry_price >= cur_price * 0.9995)
    else:
        return (cur_price >= entry_price) or (entry_price <= cur_price * 1.0005)


def cleanup_orphan_orders_for_layer(
    client: Any,
    symbol: str,
    layer_name: Literal["minor", "major"],
    active_order_ids: Optional[set[str] | list[str]] = None,
) -> list[dict[str, Any]]:
    """
    Отменяет все открытые ордера на бирже Bybit для данного символа и слоя (MIN или MAJ),
    кроме тех, чьи orderId или orderLinkId переданы в active_order_ids.
    Позволяет безопасно счищать 'висящие' (сиротские) ордера старых сеток/импульсов,
    не затрагивая ордера другого слоя на том же символе.
    """
    cancelled: list[dict[str, Any]] = []
    if not hasattr(client, "get_open_orders") or not hasattr(client, "cancel_order"):
        return cancelled

    active_set = set(active_order_ids or [])
    layer_tag = "MAJ" if layer_name == "major" else "MIN"
    sym_short = symbol.replace("USDT.P", "").replace("USDT", "")
    prefix = f"FIB-{sym_short}-{layer_tag}-"

    try:
        open_orders = client.get_open_orders(symbol)
        for o in open_orders:
            link_id = str(o.get("orderLinkId", ""))
            oid = str(o.get("orderId", ""))
            is_layer_match = link_id.startswith(prefix) or (
                layer_name == "minor"
                and "-MIN-" not in link_id
                and "-MAJ-" not in link_id
                and link_id.startswith(f"FIB-{sym_short}-")
            )
            if is_layer_match and oid and (oid not in active_set and link_id not in active_set):
                try:
                    res = client.cancel_order(symbol, oid)
                    cancelled.append(res)
                    console.print(f"[yellow]🧹 [{symbol}] Снят висящий ордер слоя {layer_name.upper()}: {link_id} (ID {oid})[/yellow]")
                except Exception:
                    pass
    except Exception:
        pass

    return cancelled


def cancel_monitor_orders(client: Any, m: ActiveTradeMonitor) -> list[dict[str, Any]]:
    """
    Отменяет только ордера, принадлежащие данному монитору и его слою (MIN или MAJ),
    не затрагивая ордера другого слоя на том же символе.
    """
    cancelled: list[dict[str, Any]] = []

    # 1. Отмена по известным ID ордеров монитора
    for oid in (m.o1_id, m.o2_id, m.o3_id):
        if oid and hasattr(client, "cancel_order"):
            try:
                cancelled.append(client.cancel_order(m.symbol, oid))
            except Exception:
                pass

    # 2. Поиск открытых ордеров по orderLinkId с префиксом слоя
    layer_tag = "MAJ" if m.layer == "major" else "MIN"
    sym_short = m.symbol.replace("USDT.P", "").replace("USDT", "")
    prefix = f"FIB-{sym_short}-{layer_tag}-"
    try:
        if hasattr(client, "get_open_orders") and hasattr(client, "cancel_order"):
            open_orders = client.get_open_orders(m.symbol)
            for o in open_orders:
                link_id = str(o.get("orderLinkId", ""))
                oid = str(o.get("orderId", ""))
                is_layer_match = link_id.startswith(prefix) or (
                    m.layer == "minor"
                    and "-MIN-" not in link_id
                    and "-MAJ-" not in link_id
                    and link_id.startswith(f"FIB-{sym_short}-")
                )
                if is_layer_match and oid and oid not in (m.o1_id, m.o2_id, m.o3_id):
                    try:
                        cancelled.append(client.cancel_order(m.symbol, oid))
                    except Exception:
                        pass
        elif not hasattr(client, "cancel_order") and hasattr(client, "cancel_all_orders"):
            cancelled.extend(client.cancel_all_orders(m.symbol))
    except Exception:
        pass

    m.o1_id = None
    m.o2_id = None
    m.o3_id = None
    return cancelled


def process_monitor_step(
    m: ActiveTradeMonitor,
    client: BybitClient,
    cfg: TradeConfig,
    interval: str,
    is_live: bool = True,
) -> None:
    """Выполняет один шаг конечного автомата (State Machine) для заданной монеты."""
    # ─── 1. Состояние: ТРЕЙЛИНГ СЕТКИ ──────────────────────────────────────────
    if m.state == "TRAILING":
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        # Проверка тайм-аута свежести для незаполненной сетки в режиме TRAILING
        effective_timeout = cfg.major_timeout_hours if m.layer == "major" else cfg.timeout_hours
        if pos_size == 0 and effective_timeout > 0 and m.imp_end_time is not None:
            now_ts = pd.Timestamp.now(tz="UTC")
            imp_ts = pd.to_datetime(m.imp_end_time, utc=True)
            elapsed_hours = (now_ts - imp_ts).total_seconds() / 3600.0
            if elapsed_hours > effective_timeout:
                layer_log = "[MAJOR]" if m.layer == "major" else "[MINOR]"
                console.print(f"\n[bold yellow]⏰ [{m.symbol}] {layer_log} Истек тайм-аут свежести импульса ({elapsed_hours:.1f}ч > {effective_timeout}ч без коррекции к 0.500).[/bold yellow]")
                console.print(f"  ➜ Снимаем ордера сетки и переводим {m.symbol} в режим ожидания нового импульса (IDLE).")
                if is_live:
                    cancel_monitor_orders(client, m)
                if m.close_only:
                    m.state = "FINISHED"
                    m.done = True
                    console.print(f"🏁 [{m.symbol}] Тайм-аут сетки в режиме Close-Only. Монета завершает работу.")
                    return
                m.state = "IDLE"
                m.last_skipped_imp_time = m.imp_end_time
                m.o1_id = None
                m.o2_id = None
                m.o3_id = None
                m.has_o2 = False
                m.has_o3 = False
                return

        if pos_size > 0:
            # Если o1_id выставлен, проверяем, не остался ли он еще открытым в стакане
            o1_still_open = False
            if m.o1_id and hasattr(client, "get_open_orders"):
                try:
                    open_ords = client.get_open_orders(m.symbol)
                    open_ids = {o.get("orderId") for o in open_ords}
                    if m.o1_id in open_ids:
                        o1_still_open = True
                except Exception:
                    pass

            if not o1_still_open:
                m.position_was_open = True
                # Проверяем, налило ли сразу 3 ордера, 2 ордера или 1 ордер
                if m.has_o3 and m.q3 > 0 and pos_size >= (m.q1 + m.q2 + 0.5 * m.q3):
                    m.state = "O3_FILLED"
                    console.print(f"\n[bold green]⚡ [{m.symbol}] Налиты все 3 ордера (0.500, 0.618, 0.786)! Позиция: {pos_size}.[/bold green]")
                    console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
                    if is_live:
                        try:
                            client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                            m.tp_basket_applied = True
                        except Exception as err:
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
                elif m.has_o2 and m.q2 > 0 and pos_size >= (m.q1 + 0.5 * m.q2):
                    m.state = "O2_FILLED"
                    console.print(f"\n[bold green]⚡ [{m.symbol}] Налиты 2 ордера (0.500 и 0.618)! Позиция: {pos_size}.[/bold green]")
                    console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.382 Fib (${m.cur_tp2}). Ордер 3 в стакане (${m.cur_e3})...")
                    if is_live:
                        try:
                            client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp2, stop_loss=m.sl)
                            m.tp_basket_applied = True
                        except Exception as err:
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.382: {err}")
                else:
                    m.state = "O1_FILLED"
                    console.print(f"\n[bold cyan]🎉 [{m.symbol}] Ордер 1 (0.500) вошел в позицию! Объем: {pos_size}.[/bold cyan]")
                    console.print(f"  ➜ Тейк-профит на 0.236 Fib (${m.cur_tp1}). Ордер 2 (${m.cur_e2}) и Ордер 3 (${m.cur_e3}) активны в стакане.")
                return

        # Если позиция еще не открыта — сдвигаем сетку за новыми максимумами
        df_now = client.fetch_klines(m.symbol, interval=interval, limit=10)
        if len(df_now) == 0:
            return
        latest_h = float(df_now["high"].iloc[-1])

        if latest_h > m.cur_peak:
            new_peak = latest_h
            new_pct = (new_peak - m.imp_start_price) / m.imp_start_price * 100.0

            new_e1 = client.round_price(
                calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale)
                * (1.0 + cfg.entry_buffer_0500_pct / 100.0), m.symbol
            )
            new_tp1 = client.round_price(
                calc_fib(new_peak, m.imp_start_price, 0.236, is_long=True, scale=cfg.scale)
                * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol
            )
            new_e2 = client.round_price(
                calc_fib(new_peak, m.imp_start_price, 0.618, is_long=True, scale=cfg.scale)
                * (1.0 + cfg.entry_buffer_0618_pct / 100.0), m.symbol
            ) if m.has_o2 else None
            new_tp2 = client.round_price(
                calc_fib(new_peak, m.imp_start_price, 0.382, is_long=True, scale=cfg.scale)
                * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol
            ) if m.has_o2 else None

            new_e3 = client.round_price(
                calc_fib(new_peak, m.imp_start_price, 0.786, is_long=True, scale=cfg.scale)
                * (1.0 + cfg.entry_buffer_0786_pct / 100.0), m.symbol
            ) if m.has_o3 else None
            new_tp3 = client.round_price(
                calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale)
                * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol
            ) if m.has_o3 else None

            # Сдвигаем Ордер 1
            if new_e1 != m.cur_e1 or new_tp1 != m.cur_tp1:
                console.print(f"\n[bold green]🚀 [{m.symbol}] Новый максимум ${new_peak} (+{new_pct:.2f}%). Сдвигаем уровни...[/bold green]")
                if is_live and m.o1_id:
                    try:
                        client.amend_order(m.symbol, m.o1_id, price=new_e1, take_profit=new_tp1, stop_loss=m.sl)
                        m.cur_e1 = new_e1
                        m.cur_tp1 = new_tp1
                        console.print(f"  ✓ [{m.symbol}] Ордер 1 сдвинут: Вход ${new_e1}, TP ${new_tp1}")
                    except Exception as err:
                        if "order not modified" not in str(err).lower():
                            console.print(f"  ⚠️ [{m.symbol}] Не удалось изменить Ордер 1: {err}")
                else:
                    m.cur_e1 = new_e1
                    m.cur_tp1 = new_tp1

            # Сдвигаем Ордер 2
            if m.has_o2 and new_e2 and new_tp2 and (new_e2 != m.cur_e2 or new_tp2 != m.cur_tp2):
                if is_live and m.o2_id:
                    try:
                        client.amend_order(m.symbol, m.o2_id, price=new_e2, take_profit=new_tp2, stop_loss=m.sl)
                        m.cur_e2 = new_e2
                        m.cur_tp2 = new_tp2
                        console.print(f"  ✓ [{m.symbol}] Ордер 2 сдвинут: Вход ${new_e2}, TP ${new_tp2}")
                    except Exception as err:
                        if "order not modified" not in str(err).lower():
                            console.print(f"  ⚠️ [{m.symbol}] Не удалось изменить Ордер 2: {err}")
                else:
                    m.cur_e2 = new_e2
                    m.cur_tp2 = new_tp2

            # Сдвигаем Ордер 3
            if m.has_o3 and new_e3 and new_tp3 and (new_e3 != m.cur_e3 or new_tp3 != m.cur_tp3):
                if is_live and m.o3_id:
                    try:
                        client.amend_order(m.symbol, m.o3_id, price=new_e3, take_profit=new_tp3, stop_loss=m.sl)
                        m.cur_e3 = new_e3
                        m.cur_tp3 = new_tp3
                        console.print(f"  ✓ [{m.symbol}] Ордер 3 сдвинут: Вход ${new_e3}, TP ${new_tp3}")
                    except Exception as err:
                        if "order not modified" not in str(err).lower():
                            console.print(f"  ⚠️ [{m.symbol}] Не удалось изменить Ордер 3: {err}")
                else:
                    m.cur_e3 = new_e3
                    m.cur_tp3 = new_tp3

            m.cur_peak = new_peak
            m.imp_end_time = pd.Timestamp.now(tz="UTC")

    # ─── 1.1 Состояние: ОЖИДАНИЕ ПРОБОЯ 0.382 (БОЛЬШАЯ ФИБА) ────────────────────
    elif m.state == "AWAITING_MAJOR_0382":
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            return

        df_now = client.fetch_klines(m.symbol, interval=interval, limit=15)
        if len(df_now) == 0:
            return

        latest_h = float(df_now["high"].iloc[-1])
        latest_l = float(df_now["low"].iloc[-1])

        # 1. Трейлинг вершины: если цена обновила максимум
        if latest_h > m.cur_peak:
            new_peak = latest_h
            m.cur_peak = new_peak
            m.p_0382 = calc_fib(new_peak, m.imp_start_price, 0.382, is_long=True, scale=cfg.scale)
            m.cur_e1 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale) * (1.0 + cfg.entry_buffer_0500_pct / 100.0), m.symbol)
            m.cur_tp1 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.236, is_long=True, scale=cfg.scale) * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol)
            m.cur_e2 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.618, is_long=True, scale=cfg.scale) * (1.0 + cfg.entry_buffer_0618_pct / 100.0), m.symbol) if m.has_o2 else None
            m.cur_tp2 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.382, is_long=True, scale=cfg.scale) * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol) if m.has_o2 else None
            m.cur_e3 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.786, is_long=True, scale=cfg.scale) * (1.0 + cfg.entry_buffer_0786_pct / 100.0), m.symbol) if m.has_o3 else None
            m.cur_tp3 = client.round_price(calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale) * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol) if m.has_o3 else None
            m.imp_end_time = pd.Timestamp.now(tz="UTC")
            console.print(f"📈 [{m.symbol}] [MAJOR] Новый максимум ${new_peak}! Уровень 0.382 скорректирован до ${m.p_0382:.4f}.")

        # 2. Проверка тайм-аута свежести:
        if cfg.major_timeout_hours > 0 and m.imp_end_time is not None:
            now_ts = pd.Timestamp.now(tz="UTC")
            imp_ts = pd.to_datetime(m.imp_end_time, utc=True)
            elapsed_hours = (now_ts - imp_ts).total_seconds() / 3600.0
            if elapsed_hours > cfg.major_timeout_hours:
                console.print(f"\n[bold yellow]⏰ [{m.symbol}] [MAJOR] Истек тайм-аут свежести ({elapsed_hours:.1f}ч > {cfg.major_timeout_hours}ч без отката к 0.382). Переход в IDLE.[/bold yellow]")
                m.state = "IDLE"
                m.last_skipped_imp_time = m.imp_end_time
                return

        # 3. Проверка пробоя уровня 0.382 (Long: low <= p_0382)
        if m.p_0382 is not None and latest_l <= m.p_0382:
            console.print(f"\n[bold green]🎯 [{m.symbol}] [MAJOR] Цена (${latest_l}) пробила/коснулась уровня 0.382 (${m.p_0382:.4f})![/bold green]")
            cur_p = client.get_ticker_price(m.symbol) if hasattr(client, "get_ticker_price") else latest_l
            if cur_p <= 0:
                cur_p = latest_l
            if is_entry_missed(m.cur_e1, cur_p, is_long=True):
                console.print(f"  [yellow]⚠️ [{m.symbol}] [MAJOR] Вход на 0.500 (${m.cur_e1}) уже упущен (рыночная цена ${cur_p} <= ${m.cur_e1}). Полностью пропускаем сетап по этой монете до появления нового импульса.[/yellow]")
                m.last_skipped_imp_time = m.imp_end_time or (df_now["timestamp"].iloc[-1] if len(df_now) > 0 else None)
                m.state = "IDLE"
                return

            console.print("  ➜ Большая фиба АКТИВИРОВАНА. Выставляем тройную сетку в стакан Bybit...")
            m.touched_0382 = True

            sym_short = m.symbol.replace("USDT.P", "").replace("USDT", "")
            layer_tag = "MAJ"
            setup_risk = cfg.major_risk_usd

            if m.cur_e3 and m.cur_e2:
                q1, q2, q3, _, _, _ = client.calc_triple_grid_order_sizes(
                    m.cur_e1, m.cur_e2, m.cur_e3, m.sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=False, weights=cfg.grid_weights
                )
            elif m.cur_e2:
                q1, q2, _, _ = client.calc_dual_grid_order_sizes(m.cur_e1, m.cur_e2, m.sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=True)
                q3 = 0.0
            else:
                dist1 = abs(m.cur_e1 - m.sl)
                specs = client.get_specs(m.symbol)
                q1 = client.round_qty(setup_risk / dist1 if dist1 > 0 else specs.min_qty, m.symbol)
                q2 = 0.0
                q3 = 0.0

            m.q1, m.q2, m.q3 = q1, q2, q3
            m.has_o2 = (m.cur_e2 is not None and q2 > 0)
            m.has_o3 = (m.cur_e3 is not None and q3 > 0)

            o1_id, o2_id, o3_id = None, None, None
            if is_live:
                # Проверка маржи перед размещением сетки
                if hasattr(client, "get_available_balance") and hasattr(client, "calc_required_margin"):
                    avail_m = client.get_available_balance()
                    req_m = client.calc_required_margin(m.symbol, q1, m.cur_e1)
                    if m.cur_e2 and q2 > 0:
                        req_m += client.calc_required_margin(m.symbol, q2, m.cur_e2)
                    if m.cur_e3 and q3 > 0:
                        req_m += client.calc_required_margin(m.symbol, q3, m.cur_e3)
                    if avail_m < req_m * 1.05:
                        console.print(f"[yellow]⏸️ [{m.symbol}] [MAJOR] Недостаточно свободной маржи (${avail_m:.2f} < ${req_m * 1.05:.2f}). Откладываем выставление сетки.[/yellow]")
                        return

                try:
                    r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1, price=m.cur_e1, take_profit=m.cur_tp1, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O1"))
                    o1_id = r1.get("orderId")
                    if m.cur_e2 and q2 > 0 and m.cur_tp2:
                        r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2, price=m.cur_e2, take_profit=m.cur_tp2, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O2"))
                        o2_id = r2.get("orderId")
                    if m.cur_e3 and q3 > 0 and m.cur_tp3:
                        r3 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q3, price=m.cur_e3, take_profit=m.cur_tp3, stop_loss=m.sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O3"))
                        o3_id = r3.get("orderId")
                    console.print(f"  ✓ [{m.symbol}] [MAJOR] Размещена сетка: Вход 1 ${m.cur_e1}, Вход 2 ${m.cur_e2 or '-'}, Вход 3 ${m.cur_e3 or '-'}")
                except Exception as err:
                    console.print(f"[red]❌ [{m.symbol}] [MAJOR] Ошибка размещения сетки: {err}[/red]")
                    return

            m.o1_id = o1_id
            m.o2_id = o2_id
            m.o3_id = o3_id
            m.state = "TRAILING"
            return

    # ─── 1.2 Состояние: ОЖИДАНИЕ ПРОБОЯ 1.000 БЕЗ ВОЗВРАТА К 0.382 ─────────────
    elif m.state == "AWAITING_BREAK_BELOW":
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            return

        if is_live:
            cleanup_orphan_orders_for_layer(client, m.symbol, m.layer)

        df_now = client.fetch_klines(m.symbol, interval=interval, limit=15)
        if len(df_now) == 0:
            return

        latest_h = float(df_now["high"].iloc[-1])
        latest_l = float(df_now["low"].iloc[-1])
        is_long = (m.side == "long")

        # 1. Трейлинг вершины: если цена обновила вершину импульса — импульс перебит новым
        if (latest_h > m.cur_peak) if is_long else (latest_l < m.cur_peak):
            console.print(f"📈 [{m.symbol}] [{m.layer.upper()}] Новый экстремум цены. Импульс завершен, переход в IDLE.")
            m.state = "IDLE"
            m.last_skipped_imp_time = df_now["timestamp"].iloc[-1] if len(df_now) > 0 else None
            return

        # 2. Проверка тайм-аута свежести:
        if m.timeout_hours and m.timeout_hours > 0 and m.imp_end_time is not None:
            now_ts = pd.Timestamp.now(tz="UTC")
            imp_ts = pd.to_datetime(m.imp_end_time, utc=True)
            elapsed_hours = (now_ts - imp_ts).total_seconds() / 3600.0
            if elapsed_hours > m.timeout_hours:
                console.print(f"\n[bold yellow]⏰ [{m.symbol}] [{m.layer.upper()}] Истек тайм-аут свежести ({elapsed_hours:.1f}ч > {m.timeout_hours}ч). Переход в IDLE.[/bold yellow]")
                m.state = "IDLE"
                m.last_skipped_imp_time = m.imp_end_time
                return

        # 3. Если цена вернулась и протестировала 0.382 — отскок завершен, сетап исчерпан!
        tested_0382 = (latest_h >= m.p_0382) if is_long else (latest_l <= m.p_0382)
        if m.p_0382 is not None and tested_0382:
            console.print(f"\n[yellow]ℹ️ [{m.symbol}] [{m.layer.upper()}] Цена вернулась и протестировала 0.382 (${m.p_0382:.4f}). Отскок завершен без пробоя 1.000, переход в IDLE.[/yellow]")
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time or (df_now["timestamp"].iloc[-1] if len(df_now) > 0 else None)
            return

        # 4. Если цена упала ниже 1.000 без возврата к 0.382:
        broken_1000 = (latest_l <= m.sl) if is_long else (latest_h >= m.sl)
        if broken_1000:
            console.print(f"\n[bold cyan]⚡ [{m.symbol}] [{m.layer.upper()}] Цена ({latest_l if is_long else latest_h}) пробила 1.000 (${m.sl:.4f}) без возврата к 0.382! Переход в AWAITING_SWEEP_CLOSE...[/bold cyan]")
            m.state = "AWAITING_SWEEP_CLOSE"
            m.stop_sweep_low = min(latest_l, m.sl) if is_long else max(latest_h, m.sl)
            m.stop_bar_time = df_now["timestamp"].iloc[-1]
            return

    # ─── 2. Состояние: НАЛИТ ОРДЕР 1 (Ожидание Ордера 2/3 или TP 0.236) ────────
    elif m.state == "O1_FILLED":
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        if pos_size > 0:
            m.position_was_open = True
            # Проверяем, налился ли Ордер 3 или Ордер 2 при проливе
            if m.has_o3 and m.q3 > 0 and pos_size >= (m.q1 + m.q2 + 0.5 * m.q3):
                m.state = "O3_FILLED"
                console.print(f"\n[bold green]🎯 [{m.symbol}] Глубокий пролив: исполнены Ордера 2 и 3! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
                        console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
            elif m.has_o2 and m.q2 > 0 and pos_size >= (m.q1 + 0.5 * m.q2):
                m.state = "O2_FILLED"
                console.print(f"\n[bold green]🎯 [{m.symbol}] Добор: Ордер 2 (0.618) исполнен! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.382 Fib (${m.cur_tp2}). Ордер 3 (${m.cur_e3}) активен в стакане...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp2, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
                        console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.382: {err}")
            return

        # Если pos_size == 0 — позиция закрылась (по TP 0.236 или по SL 1.000)
        df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
        latest_h = float(df_now["high"].iloc[-1])
        latest_l = float(df_now["low"].iloc[-1])

        if latest_h >= m.cur_tp1 * 0.999:
            console.print(f"\n[bold green]💰 [{m.symbol}] ТЕЙК-ПРОФИТ 0.236 ДОСТИГНУТ! Позиция закрыта в прибыль.[/bold green]")
            if is_live:
                cancelled = cancel_monitor_orders(client, m)
                console.print(f"[dim][{m.symbol}] Сняты висящие ордера 2 и 3 [отменено: {len(cancelled)}]. Сделка успешно завершена.[/dim]")
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                m.position_was_open = False
                console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
                return
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time
            m.position_was_open = False
        else:
            console.print(f"\n[bold red]🛑 [{m.symbol}] Стоп-лосс на уровне 1.000 (${m.sl}) сработал![/bold red]")
            if is_live:
                cancel_monitor_orders(client, m)
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                m.position_was_open = False
                console.print(f"[bold red]🏁 [{m.symbol}] Сделка закрыта по стоп-лоссу. Монета находилась в режиме Close-Only и завершает работу.[/bold red]")
                return
            m.stop_bar_time = df_now["timestamp"].iloc[-1]
            m.stop_sweep_low = min(latest_l, m.sl)
            m.state = "AWAITING_SWEEP_CLOSE"
            m.position_was_open = False
            console.print(f"[bold yellow]⏳ [{m.symbol}] Ожидаем закрытия часовой свечи ({m.stop_bar_time}) для проверки Ложного пробоя или Сетки манипуляции...[/bold yellow]")

    # ─── 3. Состояние: НАЛИТЫ ОРДЕРА 1 И 2 (Выход на 0.382 или добор 3) ────────
    elif m.state in ("O2_FILLED", "BOTH_FILLED"):
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        if pos_size > 0:
            m.position_was_open = True
            # Проверяем, налился ли Ордер 3 (0.786)
            if m.has_o3 and m.q3 > 0 and pos_size >= (m.q1 + m.q2 + 0.5 * m.q3):
                m.state = "O3_FILLED"
                console.print(f"\n[bold green]🎯 [{m.symbol}] Добор: Ордер 3 (0.786) исполнен! Позиция: {pos_size}.[/bold green]")
                console.print(f"  ➜ Переносим Take-Profit всей позиции на общий уровень 0.500 Fib (${m.cur_tp3})...")
                if is_live:
                    try:
                        client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                        m.tp_basket_applied = True
                    except Exception as err:
                        console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP на 0.500: {err}")
            return

        # Позиция закрылась!
        df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
        latest_h = float(df_now["high"].iloc[-1])
        latest_l = float(df_now["low"].iloc[-1])

        if latest_h >= m.cur_tp2 * 0.999:
            console.print(f"\n[bold green]💰 [{m.symbol}] КОРЗИННЫЙ ТЕЙК-ПРОФИТ 0.382 ДОСТИГНУТ! Ордера 1 и 2 закрыты в плюс.[/bold green]")
            if is_live:
                cancelled = cancel_monitor_orders(client, m)
                console.print(f"[dim][{m.symbol}] Снят висящий Ордер 3 (0.786) [отменено: {len(cancelled)}].[/dim]")
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                m.position_was_open = False
                console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
                return
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time
            m.position_was_open = False
        else:
            console.print(f"\n[bold red]🛑 [{m.symbol}] Стоп-лосс на уровне 1.000 (${m.sl}) сработал![/bold red]")
            if is_live:
                cancel_monitor_orders(client, m)
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                m.position_was_open = False
                console.print(f"[bold red]🏁 [{m.symbol}] Сделка закрыта по стоп-лоссу. Монета находилась в режиме Close-Only и завершает работу.[/bold red]")
                return
            m.stop_bar_time = df_now["timestamp"].iloc[-1]
            m.stop_sweep_low = min(latest_l, m.sl)
            m.state = "AWAITING_SWEEP_CLOSE"
            m.position_was_open = False
            console.print(f"[bold yellow]⏳ [{m.symbol}] Ожидаем закрытия часовой свечи ({m.stop_bar_time}) для проверки Ложного пробоя или Сетки манипуляции...[/bold yellow]")

    # ─── 4. Состояние: НАЛИТЫ ВСЕ 3 ОРДЕРА (Выход всей тройки на 0.500) ─────────
    elif m.state == "O3_FILLED":
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        if pos_size > 0:
            m.position_was_open = True
            if not m.tp_basket_applied and is_live:
                try:
                    client.set_position_tp_sl(m.symbol, take_profit=m.cur_tp3, stop_loss=m.sl)
                    m.tp_basket_applied = True
                except Exception as err:
                    console.print(f"  ⚠️ [{m.symbol}] Ошибка установки TP на 0.500: {err}")
            return

        # Позиция закрылась!
        df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
        latest_h = float(df_now["high"].iloc[-1])
        latest_l = float(df_now["low"].iloc[-1])

        if latest_h >= m.cur_tp3 * 0.999:
            console.print(f"\n[bold green]💰 [{m.symbol}] СУПЕР-ТЕЙК-ПРОФИТ 0.500 ДОСТИГНУТ! Все 3 ордера закрыты (Ордер 3 в макси-плюс, Ордер 2 в плюс, Ордер 1 в БУ).[/bold green]")
            if is_live:
                cancel_monitor_orders(client, m)
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                m.position_was_open = False
                console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
                return
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time
            m.position_was_open = False
        else:
            console.print(f"\n[bold red]🛑 [{m.symbol}] Стоп-лосс на уровне 1.000 (${m.sl}) сработал![/bold red]")
            if is_live:
                cancel_monitor_orders(client, m)
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                m.position_was_open = False
                console.print(f"[bold red]🏁 [{m.symbol}] Сделка закрыта по стоп-лоссу. Монета находилась в режиме Close-Only и завершает работу.[/bold red]")
                return
            m.stop_bar_time = df_now["timestamp"].iloc[-1]
            m.stop_sweep_low = min(latest_l, m.sl)
            m.state = "AWAITING_SWEEP_CLOSE"
            m.position_was_open = False
            console.print(f"[bold yellow]⏳ [{m.symbol}] Ожидаем закрытия часовой свечи ({m.stop_bar_time}) для проверки Ложного пробоя или Сетки манипуляции...[/bold yellow]")

    # ─── 5. Состояние: ОЖИДАНИЕ ЗАКРЫТИЯ СВЕЧИ СВИПА 1.000 ────────────────────
    elif m.state == "AWAITING_SWEEP_CLOSE":
        df_now = client.fetch_klines(m.symbol, interval=interval, limit=15)
        if len(df_now) < 2:
            return
        curr_l = float(df_now["low"].iloc[-1])
        if curr_l < m.stop_sweep_low:
            m.stop_sweep_low = curr_l

        latest_time = df_now["timestamp"].iloc[-1]
        # Свеча закрылась, если время текущей формирующейся свечи больше времени свечи стопа
        if m.stop_bar_time is not None and latest_time <= m.stop_bar_time:
            return

        # Свеча закрылась! Находим закрытую свечу пробоя
        closed_matches = df_now[df_now["timestamp"] == m.stop_bar_time]
        if len(closed_matches) > 0:
            closed_bar = closed_matches.iloc[0]
        else:
            closed_bar = df_now.iloc[-2]

        bar_close = float(closed_bar["close"])
        bar_low = float(closed_bar["low"])
        sweep_low = min(bar_low, m.stop_sweep_low)
        p_1000 = m.imp_start_price
        swp_pct = abs(p_1000 - sweep_low) / p_1000 * 100.0 if p_1000 > 0 else 0.0

        # Расчет индикатора MACD
        macd_df = calculate_macd(df_now["close"])
        hist = macd_df["hist"].values
        macd_div = (len(hist) >= 2 and (hist[-1] > hist[-2] or hist[-1] > -0.01))

        # Проверка условий Варианта 1 (Ложный пробой) vs Варианта 3 (Манипуляция)
        if bar_close >= p_1000 and swp_pct <= cfg.reclaim_max_sweep_pct and macd_div:
            console.print(f"\n[bold green]🟢 [{m.symbol}] ЛОЖНЫЙ ПРОБОЙ ПОДТВЕРЖДЕН (SWEEP RECLAIM)![/bold green]")
            console.print(f"  Закрытие ${bar_close} >= ${p_1000}, свип {swp_pct:.2f}% (<= {cfg.reclaim_max_sweep_pct}%), MACD разворот.")
            reclaim_entry = bar_close
            reclaim_sl = client.round_price(sweep_low * 0.998, m.symbol)
            p_0618 = calc_fib(m.cur_peak, m.imp_start_price, 0.618, is_long=True, scale=cfg.scale)
            reclaim_tp = client.round_price(p_0618 * (1.0 - cfg.reclaim_tp_buffer_pct / 100.0), m.symbol)
            be_trig = client.round_price(calc_fib(m.cur_peak, m.imp_start_price, cfg.reclaim_be_trigger_fib, is_long=True, scale=cfg.scale), m.symbol)
            be_price = client.round_price(reclaim_entry * (1.0 + cfg.reclaim_be_offset_pct / 100.0), m.symbol)
            dist = abs(reclaim_entry - reclaim_sl)
            specs = client.get_specs(m.symbol)
            setup_risk = cfg.major_risk_usd if m.layer == "major" else cfg.minor_risk_usd
            q_reclaim = client.round_qty(setup_risk / dist if dist > 0 else specs.min_qty, m.symbol)
            if q_reclaim < specs.min_qty:
                q_reclaim = specs.min_qty

            if is_live:
                try:
                    resp = client.place_order(
                        symbol=m.symbol,
                        side="Buy",
                        order_type="Market",
                        qty=q_reclaim,
                        take_profit=reclaim_tp,
                        stop_loss=reclaim_sl,
                    )
                    m.o1_id = resp.get("orderId")
                    console.print(f"  ✓ Вход по рынку: {q_reclaim} @ ${reclaim_entry}, TP: ${reclaim_tp}, SL: ${reclaim_sl}")
                except Exception as err:
                    console.print(f"  ❌ Ошибка входа в Sweep Reclaim: {err}")
                    m.state = "IDLE"
                    return

            m.state = "SWEEP_RECLAIM_ACTIVE"
            m.cur_e1 = reclaim_entry
            m.cur_tp1 = reclaim_tp
            m.sl = reclaim_sl
            m.be_trigger = be_trig
            m.be_price = be_price
            m.be_applied = False
            m.position_was_open = True
        else:
            # Сетка Манипуляции (Вариант 3)
            reason = f"закрытие свечи ниже 1.000 (${bar_close} < ${p_1000})" if bar_close < p_1000 else f"глубокий свип ({swp_pct:.2f}% > {cfg.reclaim_max_sweep_pct}%)"
            console.print(f"\n[bold magenta]🟣 [{m.symbol}] МАНИПУЛЯЦИЯ ({reason}). ВЫСТАВЛЯЕМ СЕТКУ 1.414 & 1.618...[/bold magenta]")

            p_1414 = calc_fib(m.cur_peak, m.imp_start_price, 1.414, is_long=True, scale=cfg.scale)
            p_1618 = calc_fib(m.cur_peak, m.imp_start_price, 1.618, is_long=True, scale=cfg.scale)
            p_2414 = calc_fib(m.cur_peak, m.imp_start_price, 2.414, is_long=True, scale=cfg.scale)

            e_1414 = client.round_price(p_1414 * (1.0 + cfg.entry_buffer_1414_pct / 100.0), m.symbol)
            e_1618 = client.round_price(p_1618 * (1.0 + cfg.entry_buffer_1618_pct / 100.0), m.symbol)
            tp_1000 = client.round_price(p_1000 * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol)
            sl_2414 = client.round_price(p_2414, m.symbol)

            # На каждый ордер выделяется cfg.manipulation_risk_usd ($2.0), на корзину 2 * manipulation_risk_usd ($4.0)
            q1_m, q2_m, l1, l2 = client.calc_dual_grid_order_sizes(
                e_1414, e_1618, sl_2414, total_risk_usd=cfg.manipulation_risk_usd * 2.0, symbol=m.symbol, equal_weight=True
            )

            if is_live:
                if hasattr(client, "get_available_balance") and hasattr(client, "calc_required_margin"):
                    avail_m = client.get_available_balance()
                    req_m = (client.calc_required_margin(m.symbol, q1_m, e_1414) + client.calc_required_margin(m.symbol, q2_m, e_1618)) * 1.05
                    if avail_m < req_m:
                        console.print(f"  [yellow]⏸️ [{m.symbol}] Недостаточно свободной маржи (${avail_m:.2f} < ${req_m:.2f}). Откладываем выставление сетки манипуляции.[/yellow]")
                        m.state = "IDLE"
                        return

                try:
                    cancel_monitor_orders(client, m)
                    layer_tag = "MAJ" if m.layer == "major" else "MIN"
                    sym_short = m.symbol.replace("USDT.P", "").replace("USDT", "")
                    r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1_m, price=e_1414, take_profit=tp_1000, stop_loss=sl_2414, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "M1"))
                    r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2_m, price=e_1618, take_profit=e_1414, stop_loss=sl_2414, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "M2"))
                    m.o1_id = r1.get("orderId")
                    m.o2_id = r2.get("orderId")
                    console.print(f"  ✓ Ордер 1: Limit Buy {q1_m} @ ${e_1414}, TP: ${tp_1000}, SL: ${sl_2414}")
                    console.print(f"  ✓ Ордер 2: Limit Buy {q2_m} @ ${e_1618}, TP: ${e_1414}, SL: ${sl_2414}")
                except Exception as err:
                    console.print(f"  ❌ Ошибка выставления сетки манипуляции: {err}")
                    m.state = "IDLE"
                    return

            m.state = "MANIPULATION_ACTIVE"
            m.cur_e1 = e_1414
            m.cur_tp1 = tp_1000
            m.cur_e2 = e_1618
            m.cur_tp2 = e_1414
            m.sl = sl_2414
            m.q1 = q1_m
            m.q2 = q2_m
            m.has_o2 = True
            m.tp_basket_applied = False
            m.position_was_open = False

    # ─── 6. Состояние: АКТИВНЫЙ ЛОЖНЫЙ ПРОБОЙ (Следим за БУ и выходом) ─────────
    elif m.state == "SWEEP_RECLAIM_ACTIVE":
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        if pos_size > 0:
            m.position_was_open = True
            # Проверяем триггер переноса в безубыток
            if m.be_trigger and m.be_price and not m.be_applied:
                df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
                latest_h = float(df_now["high"].iloc[-1])
                if latest_h >= m.be_trigger:
                    if is_live:
                        success = client.update_stop_loss(m.symbol, m.o1_id, m.be_price)
                    else:
                        success = True
                    if success:
                        m.be_applied = True
                        console.print(f"\n[bold green]🛡️ [{m.symbol}] Достигнут уровень БУ (${latest_h} >= ${m.be_trigger})! SL перенесен в ${m.be_price}.[/bold green]")
            return

        if m.position_was_open and pos_size == 0:
            console.print(f"\n[bold green]🏁 [{m.symbol}] Сделка по Ложному пробою закрыта (TP или SL). Фибоначчи завершена.[/bold green]")
            if is_live:
                cancel_monitor_orders(client, m)
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                m.position_was_open = False
                console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
                return
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time
            m.position_was_open = False

    # ─── 7. Состояние: АКТИВНАЯ СЕТКА МАНИПУЛЯЦИИ ──────────────────────────────
    elif m.state == "MANIPULATION_ACTIVE":
        pos = client.get_position(m.symbol, "Buy") if is_live else None
        pos_size = float(pos.get("size", 0.0)) if pos else 0.0

        if pos_size > 0:
            m.position_was_open = True
            # Проверяем налитие 2-го ордера (1.618 Fib) для переноса корзины в TP = 1.414
            if m.has_o2 and m.q2 > 0 and pos_size >= (m.q1 + 0.5 * m.q2):
                if not m.tp_basket_applied:
                    console.print(f"\n[bold green]⚡ [{m.symbol}] Налиты оба ордера манипуляции (1.414 и 1.618)! Позиция: {pos_size}.[/bold green]")
                    console.print(f"  ➜ Переносим общий Take-Profit корзины на уровень 1.414 Fib (${m.cur_e1})...")
                    if is_live:
                        try:
                            client.set_position_tp_sl(m.symbol, take_profit=m.cur_e1, stop_loss=m.sl)
                            m.tp_basket_applied = True
                        except Exception as err:
                            console.print(f"  ⚠️ [{m.symbol}] Ошибка переноса TP корзины на 1.414: {err}")
                    else:
                        m.tp_basket_applied = True
            return

        if m.position_was_open and pos_size == 0:
            console.print(f"\n[bold green]🏁 [{m.symbol}] Сетка Манипуляции закрыта (TP или SL). Работа с данной Фибоначчи полностью завершена.[/bold green]")
            if is_live:
                cancel_monitor_orders(client, m)
            if m.close_only:
                m.state = "FINISHED"
                m.done = True
                m.position_was_open = False
                console.print(f"[bold green]🏁 [{m.symbol}] Позиция закрыта. Монета находилась в режиме Close-Only и завершает работу.[/bold green]")
                return
            m.state = "IDLE"
            m.last_skipped_imp_time = m.imp_end_time
            m.position_was_open = False

    # ─── 8. Состояние: IDLE (Поиск новых импульсов на закрытии свечи) ───────────
    elif m.state == "IDLE":
        if m.close_only:
            m.state = "FINISHED"
            m.done = True
            return
        df_now = client.fetch_klines(m.symbol, interval=interval, limit=max(140, cfg.lookback_bars + 20))
        if len(df_now) < 15:
            return
        latest_time = df_now["timestamp"].iloc[-1]
        if m.last_candle_time is not None and latest_time == m.last_candle_time:
            return  # Новая свеча еще не появилась

        m.last_candle_time = latest_time
        is_major = (m.layer == "major")
        min_bars = (cfg.minor_max_impulse_bars + 1) if is_major else None
        max_bars = cfg.major_max_impulse_bars if is_major else cfg.minor_max_impulse_bars

        setup_timeout = cfg.major_timeout_hours if is_major else cfg.minor_timeout_hours
        setup = find_active_setup(
            df_now,
            min_pct=cfg.min_impulse_pct,
            lookback_bars=cfg.lookback_bars,
            preferred_side=cfg.preferred_side,
            scale=cfg.scale,
            max_sweep_pct=cfg.reclaim_max_sweep_pct,
            allow_close_below=cfg.reclaim_allow_close_below,
            entry_buffer_pct=cfg.entry_buffer_pct,
            entry_buffer_0500_pct=cfg.entry_buffer_0500_pct,
            entry_buffer_0618_pct=cfg.entry_buffer_0618_pct,
            entry_buffer_0786_pct=cfg.entry_buffer_0786_pct,
            entry_buffer_1414_pct=cfg.entry_buffer_1414_pct,
            entry_buffer_1618_pct=cfg.entry_buffer_1618_pct,
            tp_buffer_pct=cfg.tp_buffer_pct,
            reclaim_tp_buffer_pct=cfg.reclaim_tp_buffer_pct,
            reclaim_be_trigger_fib=cfg.reclaim_be_trigger_fib,
            reclaim_be_offset_pct=cfg.reclaim_be_offset_pct,
            atr_multiplier=cfg.atr_multiplier,
            timeout_hours=setup_timeout,
            min_impulse_bars=min_bars,
            max_impulse_bars=max_bars,
            layer=m.layer,
        )

        if setup is not None:
            # Если этот импульс уже был пропущен (вход на 0.500 упущен) — ждем появления более свежего импульса
            if m.last_skipped_imp_time is not None and setup.imp_end_time <= m.last_skipped_imp_time:
                return

            layer_tag_log = "[MAJOR]" if is_major else "[MINOR]"
            console.print(f"\n[bold green]✨ [{m.symbol}] {layer_tag_log} На закрытии свечи обнаружен импульс:[/bold green] {setup.description}")

            # Если это большая фиба и цена еще выше 0.382 — не выставляем лимитки, ждем 0.382
            if is_major and not setup.touched_0382:
                console.print("  ➜ Переход в режим ожидания 0.382 (AWAITING_MAJOR_0382). Лимитки не выставляются, маржа свободна.")
                if is_live:
                    cleanup_orphan_orders_for_layer(client, m.symbol, m.layer)
                m.setup_type = setup.setup_type
                m.state = "AWAITING_MAJOR_0382"
                m.cur_peak = setup.imp_peak_price
                m.p_0382 = setup.p_0382
                m.cur_e1 = client.round_price(setup.entry_1, m.symbol)
                m.cur_tp1 = client.round_price(setup.tp_1, m.symbol)
                m.cur_e2 = client.round_price(setup.entry_2, m.symbol) if setup.entry_2 else None
                m.cur_tp2 = client.round_price(setup.tp_2, m.symbol) if setup.tp_2 else None
                m.cur_e3 = client.round_price(setup.entry_3, m.symbol) if setup.entry_3 else None
                m.cur_tp3 = client.round_price(setup.tp_3, m.symbol) if setup.tp_3 else None
                m.sl = client.round_price(setup.stop_loss, m.symbol)
                m.imp_start_price = setup.imp_start_price
                m.imp_end_time = setup.imp_end_time
                m.touched_0382 = False
                m.timeout_hours = setup_timeout
                return

            if setup.setup_type == "AWAITING_BREAK_BELOW":
                console.print(f"  ➜ [{m.symbol}] [{layer_tag_log}] Вход на 0.500 упущен, но 0.382 не протестирован. Переход в режим ожидания пробоя 1.000 (AWAITING_BREAK_BELOW). Маржа свободна.")
                if is_live:
                    cleanup_orphan_orders_for_layer(client, m.symbol, m.layer)
                m.setup_type = "AWAITING_BREAK_BELOW"
                m.state = "AWAITING_BREAK_BELOW"
                m.cur_peak = setup.imp_peak_price
                m.imp_start_price = setup.imp_start_price
                m.p_0382 = setup.p_0382
                m.cur_e1 = client.round_price(setup.entry_1, m.symbol)
                m.cur_tp1 = client.round_price(setup.tp_1, m.symbol)
                m.cur_e2 = client.round_price(setup.entry_2, m.symbol) if setup.entry_2 else None
                m.cur_tp2 = client.round_price(setup.tp_2, m.symbol) if setup.tp_2 else None
                m.cur_e3 = client.round_price(setup.entry_3, m.symbol) if setup.entry_3 else None
                m.cur_tp3 = client.round_price(setup.tp_3, m.symbol) if setup.tp_3 else None
                m.sl = client.round_price(setup.stop_loss, m.symbol)
                m.imp_end_time = setup.imp_end_time
                m.timeout_hours = setup_timeout
                return

            e1 = client.round_price(setup.entry_1, m.symbol)
            tp1 = client.round_price(setup.tp_1, m.symbol)
            sl = client.round_price(setup.stop_loss, m.symbol)
            e2 = client.round_price(setup.entry_2, m.symbol) if setup.entry_2 else None
            tp2 = client.round_price(setup.tp_2, m.symbol) if setup.tp_2 else None
            e3 = client.round_price(setup.entry_3, m.symbol) if setup.entry_3 else None
            tp3 = client.round_price(setup.tp_3, m.symbol) if setup.tp_3 else None

            if setup.setup_type == "MANIPULATION":
                setup_risk = cfg.manipulation_risk_usd * 2.0
            else:
                setup_risk = cfg.major_risk_usd if is_major else cfg.minor_risk_usd

            if e3 is not None and e2 is not None:
                q1, q2, q3, _, _, _ = client.calc_triple_grid_order_sizes(
                    e1, e2, e3, sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=False, weights=cfg.grid_weights
                )
            elif e2 is not None:
                q1, q2, _, _ = client.calc_dual_grid_order_sizes(e1, e2, sl, total_risk_usd=setup_risk, symbol=m.symbol, equal_weight=True)
                q3 = 0.0
            else:
                dist1 = abs(e1 - sl)
                specs = client.get_specs(m.symbol)
                q1 = client.round_qty(setup_risk / dist1 if dist1 > 0 else specs.min_qty, m.symbol)
                q2 = 0.0
                q3 = 0.0

            layer_tag = "MAJ" if is_major else "MIN"
            sym_short = m.symbol.replace("USDT.P", "").replace("USDT", "")
            o1_id, o2_id, o3_id = None, None, None
            if is_live:
                # Проверяем, нет ли уже открытой позиции перед выставлением новой сетки
                pos = client.get_position(m.symbol, "Buy")
                pos_size = float(pos.get("size", 0.0)) if pos else 0.0
                if pos_size > 0:
                    console.print(f"ℹ️ [{m.symbol}] Позиция уже открыта ({pos_size} шт.). Подключаем монитор без выставления новой сетки.")
                    m.state = "O1_FILLED"
                    m.position_was_open = True
                    return

                # Проверка текущей цены относительно уровней входа (защита от покупок выше рынка)
                cur_p = client.get_ticker_price(m.symbol) if hasattr(client, "get_ticker_price") else 0.0
                if cur_p <= 0:
                    try:
                        cur_p = float(df_now["close"].iloc[-1])
                    except Exception:
                        cur_p = e1

                is_fib_grid = setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION")
                is_long = (setup.side == "long")
                o1_missed = is_entry_missed(e1, cur_p, is_long=is_long)

                # Если вход на 0.500 уже упущен: переходим в AWAITING_BREAK_BELOW (ожидание пробоя 1.000 без возврата к 0.382)
                if is_fib_grid and o1_missed:
                    cmp_op = "<=" if is_long else ">="
                    reason = f"рыночная цена ${cur_p} {cmp_op} ${e1}"
                    console.print(f"  [yellow]⚠️ [{m.symbol}] [{layer_tag}] Вход на 0.500 (${e1}) уже упущен ({reason}). Переход в AWAITING_BREAK_BELOW (маржа свободна).[/yellow]")
                    if is_live:
                        cleanup_orphan_orders_for_layer(client, m.symbol, m.layer)
                    m.setup_type = "AWAITING_BREAK_BELOW"
                    m.state = "AWAITING_BREAK_BELOW"
                    m.cur_peak = setup.imp_peak_price
                    m.imp_start_price = setup.imp_start_price
                    m.p_0382 = setup.p_0382
                    m.cur_e1 = e1
                    m.cur_tp1 = tp1
                    m.cur_e2 = e2
                    m.cur_tp2 = tp2
                    m.cur_e3 = e3
                    m.cur_tp3 = tp3
                    m.sl = sl
                    m.imp_end_time = setup.imp_end_time
                    m.timeout_hours = setup_timeout
                    return

                place_o1 = not o1_missed
                place_o2 = bool(e2 and q2 > 0 and tp2 and not is_entry_missed(e2, cur_p, is_long=is_long))
                place_o3 = bool(e3 and q3 > 0 and tp3 and not is_entry_missed(e3, cur_p, is_long=is_long))

                if not place_o1:
                    console.print(f"  [yellow]ℹ️ [{m.symbol}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 1 (${e1}). Ордер 1 пропущен.[/yellow]")
                if not place_o2 and e2 and q2 > 0:
                    console.print(f"  [yellow]ℹ️ [{m.symbol}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 2 (${e2}). Ордер 2 пропущен.[/yellow]")
                if not place_o3 and e3 and q3 > 0:
                    console.print(f"  [yellow]ℹ️ [{m.symbol}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 3 (${e3}). Ордер 3 пропущен.[/yellow]")

                if not (place_o1 or place_o2 or place_o3):
                    console.print(f"  [yellow]⚠️ [{m.symbol}] [{layer_tag}] Все уровни сетки выше текущей цены (${cur_p}). Пропуск выставления.[/yellow]")
                    m.last_skipped_imp_time = setup.imp_end_time
                    m.state = "IDLE"
                    return

                # Проверка свободной маржи
                if hasattr(client, "get_available_balance") and hasattr(client, "calc_required_margin"):
                    avail_m = client.get_available_balance()
                    req_m = 0.0
                    if place_o1:
                        req_m += client.calc_required_margin(m.symbol, q1, e1)
                    if place_o2:
                        req_m += client.calc_required_margin(m.symbol, q2, e2)
                    if place_o3:
                        req_m += client.calc_required_margin(m.symbol, q3, e3)
                    if avail_m < req_m * 1.05:
                        console.print(f"[yellow]⏸️ [{m.symbol}] [{layer_tag}] Недостаточно свободной маржи (${avail_m:.2f} < ${req_m * 1.05:.2f}). Откладываем выставление новой сетки.[/yellow]")
                        return

                try:
                    if place_o1:
                        r1 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q1, price=e1, take_profit=tp1, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O1"))
                        o1_id = r1.get("orderId")
                    if place_o2:
                        r2 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q2, price=e2, take_profit=tp2, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O2"))
                        o2_id = r2.get("orderId")
                    if place_o3:
                        r3 = client.place_order(symbol=m.symbol, side="Buy", order_type="Limit", qty=q3, price=e3, take_profit=tp3, stop_loss=sl, order_link_id=make_order_link_id(sym_short, layer_tag, "Buy", "O3"))
                        o3_id = r3.get("orderId")
                    console.print(f"  ✓ [{m.symbol}] [{layer_tag}] Размещена новая сетка: Вход 1 ${e1 if place_o1 else '(пропущен)'}, Вход 2 ${e2 if place_o2 else '(пропущен)'}, Вход 3 ${e3 if place_o3 else '(пропущен)'}")
                except Exception as err:
                    console.print(f"[red]❌ [{m.symbol}] [{layer_tag}] Ошибка выставления новой сетки: {err}[/red]")
                    return

            m.setup_type = setup.setup_type
            m.state = "TRAILING" if setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION") else setup.setup_type
            m.o1_id = o1_id
            m.o2_id = o2_id
            m.o3_id = o3_id
            m.cur_peak = setup.imp_peak_price
            m.cur_e1 = e1
            m.cur_tp1 = tp1
            m.cur_e2 = e2 if e2 else 0.0
            m.cur_tp2 = tp2 if tp2 else 0.0
            m.cur_e3 = e3 if e3 else 0.0
            m.cur_tp3 = tp3 if tp3 else 0.0
            m.imp_start_price = setup.imp_start_price
            m.imp_end_time = setup.imp_end_time
            m.sl = sl
            m.q1 = q1
            m.q2 = q2
            m.q3 = q3
            m.has_o2 = (e2 is not None and q2 > 0)
            m.has_o3 = (e3 is not None and q3 > 0)
            m.position_was_open = False
            m.be_applied = False
            m.tp_basket_applied = False
            m.touched_0382 = True


def main():
    parser = argparse.ArgumentParser(description="Bybit Fibonacci Dual Grid Trader")
    parser.add_argument("--symbol", "--symbols", dest="symbols", type=str, default=None, help="Монета или список монет через запятую (например, SUIUSDT.P,BTCUSDT)")
    parser.add_argument("--interval", type=str, default=None, help="Интервал свечей (15m, 1h, 4h, 1d)")
    parser.add_argument("--risk", type=float, default=None, help="Суммарный риск на сделку ($)")
    parser.add_argument("--entry-buffer", type=float, default=None, help="Буфер входа (%%)")
    parser.add_argument("--tp-buffer", type=float, default=None, help="Буфер тейка (%%)")
    parser.add_argument("--config", type=str, default=None, help="Путь к файлу конфигурации (по умолчанию config/trade_config.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Режим симуляции (без выставления ордеров)")
    parser.add_argument("--live", action="store_true", help="Боевой режим выставления ордеров")
    parser.add_argument("-y", "--yes", action="store_true", help="Автоматическое подтверждение выставления ордеров без интерактивного вопроса")
    parser.add_argument("--once", action="store_true", help="Одиночный проход без непрерывного фонового цикла")
    parser.add_argument("--atr-mult", type=float, default=None, help="Множитель ATR для динамического порога импульса (например, 2.5)")
    parser.add_argument("--timeout-hours", type=int, default=None, help="Тайм-аут свежести импульса в часах (например, 24)")
    args = parser.parse_args()

    cfg = load_trade_config(args.config)
    if args.risk is not None:
        cfg.total_risk_usd = args.risk
        cfg.minor_risk_usd = args.risk
    if args.entry_buffer is not None:
        cfg.entry_buffer_pct = args.entry_buffer
        cfg.entry_buffer_0500_pct = args.entry_buffer
        cfg.entry_buffer_0618_pct = args.entry_buffer
        cfg.entry_buffer_0786_pct = args.entry_buffer
        cfg.entry_buffer_1414_pct = args.entry_buffer
        cfg.entry_buffer_1618_pct = args.entry_buffer
    if args.tp_buffer is not None:
        cfg.tp_buffer_pct = args.tp_buffer
    if args.interval:
        cfg.timeframe = args.interval
    if args.atr_mult is not None:
        cfg.atr_multiplier = args.atr_mult
    if args.timeout_hours is not None:
        cfg.timeout_hours = args.timeout_hours

    atr_desc = f"{cfg.atr_multiplier:.1f}x" if cfg.atr_multiplier > 0 else "выкл"
    to_desc = f"{cfg.timeout_hours}ч" if cfg.timeout_hours > 0 else "выкл"
    buf_desc = (
        f"+{cfg.entry_buffer_pct:.2f}%"
        if (cfg.entry_buffer_0500_pct == cfg.entry_buffer_0618_pct == cfg.entry_buffer_0786_pct == cfg.entry_buffer_pct)
        else f"+{cfg.entry_buffer_0500_pct:.2f}%/+{cfg.entry_buffer_0618_pct:.2f}%/+{cfg.entry_buffer_0786_pct:.2f}%"
    )
    console.print(Panel.fit(
        "[bold cyan]🤖 Bybit Fibonacci Dual Grid & Trailing Trader[/bold cyan]\n"
        f"[dim]Конфиг: {Path(cfg.config_path).name if cfg.config_path else 'default'} | Стоп Minor: ${cfg.minor_risk_usd:.2f} | Стоп Major: ${cfg.major_risk_usd:.2f} | Стоп манипуляции: ${cfg.manipulation_risk_usd:.2f}/ордер | Вход: {buf_desc} | Тейк: -{cfg.tp_buffer_pct:.2f}% | ATR: {atr_desc} | Таймаут: {to_desc}[/dim]",
        border_style="cyan",
    ))

    # 1. Запрос монет: CLI -> Конфиг -> Интерактивный ввод
    if args.symbols:
        raw_coins = [s.strip() for s in args.symbols.split(",") if s.strip()]
        console.print(f"[bold yellow]Монеты (из CLI):[/bold yellow] [green]{', '.join(raw_coins)}[/green]")
    elif cfg.symbols:
        raw_coins = cfg.symbols
        console.print(f"[bold yellow]Монеты (из конфига):[/bold yellow] [green]{', '.join(raw_coins)}[/green]")
    else:
        console.print(Panel(
            "[bold red]❌ Ошибка: В конфигурационном файле (trade_config.yaml) не указан список монет для торговли![/bold red]\n\n"
            "Пожалуйста, добавьте монеты в раздел [cyan]strategy.symbols[/cyan] в [bold]config/trade_config.yaml[/bold], например:\n"
            "[green]strategy:\n  symbols:\n    - \"SUIUSDT.P\"\n    - \"BNBUSDT.P\"\n    - \"ICPUSDT.P\"[/green]\n\n"
            "Или укажите монеты через флаг командной строки: [yellow]--symbols SUI,BNB,ICP[/yellow]",
            title="⚠️ Монеты не заданы",
            border_style="red",
        ))
        return

    # Приводим к формату Bybit Linear (с поддержкой .P, ZEC -> ZECUSDT) и удаляем дубликаты
    symbols = list(dict.fromkeys([format_symbol(c) for c in raw_coins]))

    # 2. Запрос таймфрейма
    tf_map = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}
    if args.interval:
        interval = tf_map.get(args.interval.lower(), args.interval)
        console.print(f"[bold yellow]Таймфрейм свечей:[/bold yellow] [green]{args.interval}[/green]")
    elif args.symbols or cfg.symbols:
        interval = tf_map.get(cfg.timeframe.lower(), "60")
        console.print(f"[bold yellow]Таймфрейм свечей:[/bold yellow] [green]{cfg.timeframe} (из конфига)[/green]")
    else:
        tf_input = console.input(f"[bold yellow]Таймфрейм свечей [{cfg.timeframe}][/bold yellow] (15m, 1h, 4h, 1d): ").strip().lower()
        interval = tf_map.get(tf_input, tf_map.get(cfg.timeframe.lower(), "60"))

    # 3. Запрос риска
    if args.risk is not None:
        total_risk = args.risk
        console.print(f"[bold yellow]Суммарный риск на сделку:[/bold yellow] [green]${total_risk:.2f}[/green]")
    elif args.symbols or cfg.symbols:
        total_risk = cfg.total_risk_usd
        console.print(f"[bold yellow]Суммарный риск на сделку:[/bold yellow] [green]${total_risk:.2f} (из конфига)[/green]")
    else:
        risk_input = console.input(f"[bold yellow]Суммарный риск на сделку ($) [{cfg.total_risk_usd:.1f}]: [/bold yellow]").strip()
        try:
            total_risk = float(risk_input) if risk_input else cfg.total_risk_usd
        except ValueError:
            total_risk = cfg.total_risk_usd

    # 4. Режим
    if args.live:
        is_live = True
        console.print("[bold red]Режим: LIVE (боевые ордера)[/bold red]")
    elif args.dry_run:
        is_live = False
        console.print("[bold green]Режим: DRY-RUN (симуляция)[/bold green]")
    else:
        mode_input = console.input("[bold yellow]Режим работы: 1) Dry-Run (предпросмотр)  2) Live (боевые ордера) [1]: [/bold yellow]").strip()
        is_live = mode_input == "2"

    console.print("\n[dim]Подключение к Bybit V5...[/dim]")
    try:
        client = BybitClient()
    except Exception as e:
        console.print(f"[bold red]❌ Ошибка инициализации Bybit клиента:[/bold red] {e}")
        return

    # Сканирование монет по двум независимым слоям: Minor (локальная) и Major (старшая)
    actionable_setups = []
    awaiting_major_setups = []
    active_monitors: list[ActiveTradeMonitor] = []

    layers = [
        ("minor", None, cfg.minor_max_impulse_bars, cfg.minor_risk_usd),
        ("major", cfg.minor_max_impulse_bars + 1, cfg.major_max_impulse_bars, cfg.major_risk_usd),
    ]

    for symbol in symbols:
        console.print(f"\n[bold cyan]─── Анализ {symbol} ───[/bold cyan]")
        try:
            specs = client.get_specs(symbol)
        except Exception as e:
            console.print(f"[red]❌ Ошибка получения спецификации {symbol}: {e}. Пропускаем.[/red]")
            continue

        try:
            df = client.fetch_klines(symbol, interval=interval, limit=max(140, cfg.lookback_bars + 20))
        except Exception as e:
            console.print(f"[red]❌ Ошибка загрузки свечей {symbol}: {e}. Пропускаем.[/red]")
            continue

        if len(df) == 0:
            console.print(f"[yellow]⚠️ Нет свечей для {symbol}. Пропускаем.[/yellow]")
            continue

        cur_price = df["close"].iloc[-1]
        console.print(f"[dim]{symbol}: Tick: {specs.tick_size}, Step: {specs.qty_step}, MinQty: {specs.min_qty}, MinNotional: ${specs.min_notional}, Цена: {cur_price}[/dim]")

        for layer_name, min_bars, max_bars, layer_risk in layers:
            is_major = (layer_name == "major")
            layer_tag_title = f"[MAJOR FIB {min_bars}-{max_bars} свечей]" if is_major else f"[MINOR FIB <= {max_bars} свечей]"

            setup_timeout = cfg.major_timeout_hours if is_major else cfg.minor_timeout_hours
            setup = find_active_setup(
                df,
                min_pct=cfg.min_impulse_pct,
                lookback_bars=cfg.lookback_bars,
                preferred_side=cfg.preferred_side,
                scale=cfg.scale,
                max_sweep_pct=cfg.reclaim_max_sweep_pct,
                allow_close_below=cfg.reclaim_allow_close_below,
                entry_buffer_pct=cfg.entry_buffer_pct,
                entry_buffer_0500_pct=cfg.entry_buffer_0500_pct,
                entry_buffer_0618_pct=cfg.entry_buffer_0618_pct,
                entry_buffer_0786_pct=cfg.entry_buffer_0786_pct,
                entry_buffer_1414_pct=cfg.entry_buffer_1414_pct,
                entry_buffer_1618_pct=cfg.entry_buffer_1618_pct,
                tp_buffer_pct=cfg.tp_buffer_pct,
                reclaim_tp_buffer_pct=cfg.reclaim_tp_buffer_pct,
                reclaim_be_trigger_fib=cfg.reclaim_be_trigger_fib,
                reclaim_be_offset_pct=cfg.reclaim_be_offset_pct,
                atr_multiplier=cfg.atr_multiplier,
                timeout_hours=setup_timeout,
                min_impulse_bars=min_bars,
                max_impulse_bars=max_bars,
                layer=layer_name,
            )

            if not setup:
                console.print(f"[dim]{symbol} {layer_tag_title}: активный импульс не найден.[/dim]")
                if is_live:
                    cleanup_orphan_orders_for_layer(client, symbol, layer_name)
                continue

            # Расчет позиций
            e1 = client.round_price(setup.entry_1, symbol)
            tp1 = client.round_price(setup.tp_1, symbol)
            sl = client.round_price(setup.stop_loss, symbol)

            is_fib_grid = setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION")
            is_long = (setup.side == "long")

            # Проверяем, нет ли уже открытой позиции на Bybit для данного символа
            pos_open = False
            if is_live:
                try:
                    curr_pos = client.get_position(symbol, side=("Buy" if is_long else "Sell"))
                    pos_open = float(curr_pos.get("size", "0")) > 0
                except Exception:
                    pos_open = False

            o1_missed = is_entry_missed(e1, cur_price, is_long=is_long)
            if not pos_open and (setup.setup_type == "AWAITING_BREAK_BELOW" or (is_fib_grid and o1_missed)):
                cmp_op = "<=" if is_long else ">="
                reason = "уровень 0.500 уже был протестирован ранее" if setup.setup_type == "AWAITING_BREAK_BELOW" else f"текущая цена ${cur_price} {cmp_op} ${e1}"
                p_0382_str = f"${setup.p_0382:.4f}" if setup.p_0382 else "-"
                console.print(f"[yellow]ℹ️ [{symbol}] {layer_tag_title}: вход на 0.500 (${e1}) уже упущен ({reason}), 0.382 ({p_0382_str}) не протестирован. Ожидание пробоя 1.000 (${sl}) без возврата к 0.382 (маржа свободна).[/yellow]")
                if is_live:
                    cleanup_orphan_orders_for_layer(client, symbol, layer_name)
                active_monitors.append(ActiveTradeMonitor(
                    symbol=symbol,
                    setup_type="AWAITING_BREAK_BELOW",
                    state="AWAITING_BREAK_BELOW",
                    layer=layer_name,
                    side=setup.side,
                    cur_peak=setup.imp_peak_price,
                    imp_start_price=setup.imp_start_price,
                    p_0382=setup.p_0382,
                    cur_e1=e1,
                    cur_tp1=tp1,
                    cur_e2=client.round_price(setup.entry_2, symbol) if setup.entry_2 else 0.0,
                    cur_tp2=client.round_price(setup.tp_2, symbol) if setup.tp_2 else 0.0,
                    cur_e3=client.round_price(setup.entry_3, symbol) if setup.entry_3 else 0.0,
                    cur_tp3=client.round_price(setup.tp_3, symbol) if setup.tp_3 else 0.0,
                    sl=sl,
                    imp_end_time=setup.imp_end_time,
                    last_candle_time=df["timestamp"].iloc[-1] if len(df) > 0 else None,
                    timeout_hours=setup_timeout,
                ))
                continue

            e2 = client.round_price(setup.entry_2, symbol) if setup.entry_2 else None
            tp2 = client.round_price(setup.tp_2, symbol) if setup.tp_2 else None

            e3 = client.round_price(setup.entry_3, symbol) if setup.entry_3 else None
            tp3 = client.round_price(setup.tp_3, symbol) if setup.tp_3 else None

            # Расчет лотов
            if setup.setup_type == "MANIPULATION":
                setup_risk = cfg.manipulation_risk_usd * 2.0
            else:
                setup_risk = layer_risk

            if e3 is not None and e2 is not None:
                q1, q2, q3, loss1, loss2, loss3 = client.calc_triple_grid_order_sizes(
                    e1, e2, e3, sl, total_risk_usd=setup_risk, symbol=symbol, equal_weight=False, weights=cfg.grid_weights
                )
                tot_loss = loss1 + loss2 + loss3
            elif e2 is not None:
                q1, q2, loss1, loss2 = client.calc_dual_grid_order_sizes(e1, e2, sl, total_risk_usd=setup_risk, symbol=symbol, equal_weight=True)
                q3 = 0.0
                loss3 = 0.0
                tot_loss = loss1 + loss2
            else:
                dist1 = abs(e1 - sl)
                q1 = client.round_qty(setup_risk / dist1 if dist1 > 0 else specs.min_qty, symbol)
                if q1 < specs.min_qty:
                    q1 = specs.min_qty
                loss1 = q1 * dist1
                q2 = 0.0
                loss2 = 0.0
                q3 = 0.0
                loss3 = 0.0
                tot_loss = loss1

            title_map = {
                "TRIPLE_GRID_TRAILING": "🚀 ТРОЙНАЯ СЕТКА (РЕЖИМ ТРЕЙЛИНГА)",
                "TRIPLE_GRID_CORRECTION": "🎯 ТРОЙНАЯ СЕТКА (АКТИВНАЯ КОРРЕКЦИЯ)",
                "DUAL_GRID_TRAILING": "🚀 ТРОЙНАЯ СЕТКА (РЕЖИМ ТРЕЙЛИНГА)",
                "DUAL_GRID_CORRECTION": "🎯 ТРОЙНАЯ СЕТКА (АКТИВНАЯ КОРРЕКЦИЯ)",
                "SWEEP_RECLAIM": "🟢 ЛОЖНЫЙ ПРОБОЙ (SWEEP RECLAIM + MACD)",
                "MANIPULATION": "🟣 СЕТКА МАНИПУЛЯЦИИ (1.414 & 1.618)",
            }

            t = Table(title=f"{layer_tag_title} {title_map.get(setup.setup_type, setup.setup_type)} — {symbol} [LONG ONLY]", show_header=True, header_style="bold magenta")
            t.add_column("Параметр", style="cyan")
            t.add_column("Значение", style="bold white")

            t.add_row("Конфиг", f"{Path(cfg.config_path).name if cfg.config_path else 'по умолчанию'}")
            t.add_row("Слой / Риск", f"{layer_name.upper()} (лимит ${setup_risk:.2f})")
            t.add_row("Импульс старт", f"{setup.imp_start_time.strftime('%Y-%m-%d %H:%M')} (${setup.imp_start_price})")
            t.add_row("Импульс вершина", f"{setup.imp_end_time.strftime('%Y-%m-%d %H:%M')} (${setup.imp_peak_price}) [{setup.imp_pct:+.2f}%]")
            t.add_row("Текущая цена", f"${cur_price}")
            if is_major:
                p_0382_str = f"${setup.p_0382:.4f}" if setup.p_0382 else "-"
                status_0382 = "[bold green]Пробит (готов к выставлению сетки)[/bold green]" if setup.touched_0382 else f"[bold yellow]Выше 0.382 ({p_0382_str}) — ожидание отката (маржа свободна)[/bold yellow]"
                t.add_row("Уровень 0.382 Фибы", status_0382)
            if cfg.atr_multiplier > 0:
                t.add_row("ATR волатильность", f"Множитель {cfg.atr_multiplier:.1f}x ATR(14)")
            if setup_timeout > 0:
                t.add_row("Тайм-аут свежести", f"{setup_timeout} часов")
            if e3 is not None and e2 is not None and cfg.grid_weights:
                t.add_row("Пропорция входа", f"{int(cfg.grid_weights[0]*100)}% / {int(cfg.grid_weights[1]*100)}% / {int(cfg.grid_weights[2]*100)}% (0.500/0.618/0.786)")
            if setup.setup_type == "MANIPULATION":
                if cfg.entry_buffer_1414_pct == cfg.entry_buffer_1618_pct:
                    t.add_row("Буфер входа", f"+{cfg.entry_buffer_1414_pct:.2f}% перед уровнем")
                else:
                    t.add_row("Буфер входа", f"+{cfg.entry_buffer_1414_pct:.2f}% (1.414) / +{cfg.entry_buffer_1618_pct:.2f}% (1.618)")
            else:
                if cfg.entry_buffer_0500_pct == cfg.entry_buffer_0618_pct == cfg.entry_buffer_0786_pct:
                    t.add_row("Буфер входа", f"+{cfg.entry_buffer_0500_pct:.2f}% перед уровнем")
                else:
                    t.add_row("Буфер входа", f"+{cfg.entry_buffer_0500_pct:.2f}% (0.500) / +{cfg.entry_buffer_0618_pct:.2f}% (0.618) / +{cfg.entry_buffer_0786_pct:.2f}% (0.786)")
            t.add_row("Буфер тейка", f"-{cfg.tp_buffer_pct:.2f}% от уровня")
            t.add_row("─" * 20, "─" * 30)

            t.add_row("Ордер 1 (Вход / Тейк)", f"Вход: ${e1}  |  TP: ${tp1}")
            t.add_row("Объем Ордера 1", f"{q1} шт. (${q1 * e1:.2f} notional, риск ${loss1:.2f})")

            if e2 is not None and tp2 is not None:
                lbl_o2 = "Ордер 2 (Вход 0.618 / Тейк 0.382)" if e3 is not None else "Ордер 2 (Вход / Тейк)"
                t.add_row(lbl_o2, f"Вход: ${e2}  |  TP: ${tp2}")
                t.add_row("Объем Ордера 2", f"{q2} шт. (${q2 * e2:.2f} notional, риск ${loss2:.2f})")

            if e3 is not None and tp3 is not None:
                t.add_row("Ордер 3 (Вход 0.786 / Тейк 0.500)", f"Вход: ${e3}  |  TP: ${tp3}")
                t.add_row("Объем Ордера 3", f"{q3} шт. (${q3 * e3:.2f} notional, риск ${loss3:.2f})")

            risk_label = f"лимит ${setup_risk:.2f} ($4.00 на корзину манипуляции)" if setup.setup_type == "MANIPULATION" else f"лимит ${setup_risk:.2f}"
            t.add_row("Стоп-Лосс (SL)", f"${sl} (расчетный суммарный убыток: ${tot_loss:.2f} / {risk_label})")
            if setup.be_trigger is not None and setup.be_price is not None:
                be_trig_str = f"${client.round_price(setup.be_trigger, symbol)}"
                be_price_str = f"${client.round_price(setup.be_price, symbol)}"
                t.add_row("Безубыток (БУ)", f"Триггер: {be_trig_str} ({cfg.reclaim_be_trigger_fib} Fib)  ->  Перенос SL в: {be_price_str}")
            t.add_row("Статус стратегии", f"[green]{setup.description}[/green]")

            console.print(t)

            if q1 * e1 < specs.min_notional:
                console.print(f"[yellow]⚠️ Внимание: Notional Ордера 1 (${q1 * e1:.2f}) меньше биржевого минимума ${specs.min_notional}![/yellow]")

            setup_item = {
                "symbol": symbol,
                "layer": layer_name,
                "setup": setup,
                "specs": specs,
                "e1": e1,
                "tp1": tp1,
                "e2": e2,
                "tp2": tp2,
                "e3": e3,
                "tp3": tp3,
                "sl": sl,
                "q1": q1,
                "q2": q2,
                "q3": q3,
                "setup_risk": setup_risk,
            }

            if is_major and not setup.touched_0382:
                if is_live:
                    cleanup_orphan_orders_for_layer(client, symbol, layer_name)
                awaiting_major_setups.append(setup_item)
            else:
                actionable_setups.append(setup_item)

    # Если режим Dry-Run — завершаем после отображения всех сетапов
    if not is_live:
        console.print(f"\n[bold green]Режим Dry-Run завершен.[/bold green] Найдено: [bold cyan]{len(actionable_setups)} активных сетапов, {len(awaiting_major_setups)} в ожидании 0.382[/bold cyan].")
        return

    # В Live режиме — если нет активных сетапов и нет ожидающих сетапов и запрошен одиночный проход
    if not actionable_setups and not awaiting_major_setups and args.once:
        console.print(f"\n[yellow]Нет активных сетапов для выставления ордеров среди монет: {', '.join(symbols)}.[/yellow]")
        return

    if actionable_setups:
        symbols_to_trade_str = ", ".join(f"{item['symbol']} ({item['layer'].upper()})" for item in actionable_setups)
        if not args.yes:
            confirm = console.input(f"\n[bold red]ВЫСТАВИТЬ ОРДЕРА НА BYBIT ДЛЯ {symbols_to_trade_str}?[/bold red] (y/N): ").strip().lower()
            if confirm != "y":
                console.print("[yellow]Отменено пользователем.[/yellow]")
                return
        else:
            console.print(f"[bold green]Автоподтверждение (-y): выставляем ордера для {symbols_to_trade_str}...[/bold green]")

    # Выставляем ордера для всех подтвержденных сетапов
    # (active_monitors уже может содержать IDLE-мониторы монет с упущенным 0.500)

    # Проверяем доступную свободную маржу на аккаунте Bybit перед выставлением ордеров
    available_margin = client.get_available_balance() if (is_live and hasattr(client, "get_available_balance")) else 999999.0
    if is_live:
        console.print(f"\n[cyan]💰 Свободная маржа на Unified аккаунте: ${available_margin:.2f}[/cyan]")

    for item in actionable_setups:
        sym = item["symbol"]
        layer_name = item["layer"]
        setup = item["setup"]
        e1, tp1, e2, tp2, e3, tp3, sl = item["e1"], item["tp1"], item["e2"], item["tp2"], item["e3"], item["tp3"], item["sl"]
        q1, q2, q3 = item["q1"], item["q2"], item["q3"]
        layer_tag = "MAJ" if layer_name == "major" else "MIN"

        console.print(f"\n[bold cyan]Проверка/выставление ордеров для {sym} [{layer_name.upper()}]...[/bold cyan]")
        if is_live and hasattr(client, "get_available_balance"):
            available_margin = client.get_available_balance()
        o1_id = None
        o2_id = None
        o3_id = None
        pos_open_initially = False
        initial_state = "TRAILING"
        place_o1 = True
        place_o2 = bool(e2 is not None and q2 > 0)
        place_o3 = bool(e3 is not None and q3 > 0)
        try:
            side_str = "Buy" if setup.side == "long" else "Sell"

            # 1. Проверяем, есть ли уже открытая позиция
            curr_pos = client.get_position(sym, side=side_str)
            pos_sz = float(curr_pos.get("size", "0")) if curr_pos else 0.0
            if pos_sz > 0:
                pos_open_initially = True

            # 2. Ищем существующие ордера ТОЛЬКО для своего слоя
            all_open = client.get_open_orders(sym)
            existing_orders = [
                o for o in all_open
                if o.get("side") == side_str and o.get("orderType") == "Limit" and (
                    layer_tag in str(o.get("orderLinkId", "")) or (layer_name == "minor" and "-MAJ-" not in str(o.get("orderLinkId", "")))
                )
            ]
            existing_orders.sort(key=lambda x: float(x.get("price", 0.0)), reverse=(setup.side == "long"))

            sym_short = sym.replace("USDT.P", "").replace("USDT", "")
            link_id_1 = make_order_link_id(sym_short, layer_tag, side_str, "O1")
            link_id_2 = make_order_link_id(sym_short, layer_tag, side_str, "O2")
            link_id_3 = make_order_link_id(sym_short, layer_tag, side_str, "O3")

            setup_risk = item["setup_risk"]
            specs = client.get_specs(sym)

            if pos_open_initially:
                # ─── СИТУАЦИЯ 1: Позиция уже открыта на Bybit ───────────
                avg_p = float(curr_pos.get("avgPrice", 0.0))
                current_risk = pos_sz * abs(avg_p - sl)
                remaining_risk = max(0.0, setup_risk - current_risk)
                console.print(f"ℹ️ [{sym}] [{layer_tag}] Открыта позиция: {pos_sz} шт. @ {avg_p}. Задействованный риск: ${current_risk:.2f} из лимита ${setup_risk:.2f}.")

                if remaining_risk <= 0.05:
                    console.print(f"🛡️ [{sym}] [{layer_tag}] Лимит риска ${setup_risk:.2f} уже исчерпан открытой позицией (${current_risk:.2f}). Новые ордера блокируются!")
                    if existing_orders:
                        console.print(f"  ➜ Снимаем {len(existing_orders)} лишних лимитных ордеров своего слоя...")
                        for o in existing_orders:
                            if o.get("orderId"):
                                client.cancel_order(sym, o.get("orderId"))
                        existing_orders = []
                    initial_state = "O1_FILLED"
                else:
                    console.print(f"ℹ️ [{sym}] [{layer_tag}] Остаточный бюджет риска на добор: ${remaining_risk:.2f}.")
                    q2_res, q3_res, _, _, _ = client.calc_residual_order_sizes(
                        pos_sz, avg_p, e2, e3, sl, total_risk_usd=setup_risk, symbol=sym, weights=cfg.grid_weights
                    )
                    
                    # Проверяем, соответствуют ли существующие ордера добора новому расчету риска
                    res_match = False
                    tol2 = max(specs.qty_step, 0.05 * q2_res) if q2_res > 0 else specs.qty_step
                    tol3 = max(specs.qty_step, 0.05 * q3_res) if q3_res > 0 else specs.qty_step

                    if len(existing_orders) >= 2 and e2 is not None and e3 is not None:
                        eq2 = float(existing_orders[0].get("qty", 0.0))
                        eq3 = float(existing_orders[1].get("qty", 0.0))
                        ep2 = float(existing_orders[0].get("price", 0.0))
                        ep3 = float(existing_orders[1].get("price", 0.0))
                        if abs(eq2 - q2_res) <= tol2 and abs(eq3 - q3_res) <= tol3 and abs(ep2 - e2) / e2 < 0.002 and abs(ep3 - e3) / e3 < 0.002:
                            res_match = True
                            o2_id = existing_orders[0].get("orderId")
                            o3_id = existing_orders[1].get("orderId")
                            console.print(f"  ✓ [{layer_tag}] Подключены существующие ордера добора: Ордер 2 ID {o2_id} @ {e2} (qty {eq2}), Ордер 3 ID {o3_id} @ {e3} (qty {eq3})")
                    elif len(existing_orders) == 1 and e2 is not None and (e3 is None or q3_res <= 0):
                        eq2 = float(existing_orders[0].get("qty", 0.0))
                        ep2 = float(existing_orders[0].get("price", 0.0))
                        if abs(eq2 - q2_res) <= tol2 and abs(ep2 - e2) / e2 < 0.002:
                            res_match = True
                            o2_id = existing_orders[0].get("orderId")
                            console.print(f"  ✓ [{layer_tag}] Подключен существующий ордер: Ордер 2 ID {o2_id} @ {e2} (qty {eq2})")

                    if not res_match and existing_orders:
                        console.print(f"🔄 [{sym}] [{layer_tag}] Параметры существующих ордеров добора не соответствуют новому риску. Снимаем для актуализации...")
                        for o in existing_orders:
                            if o.get("orderId"):
                                client.cancel_order(sym, o.get("orderId"))
                        existing_orders = []
                        if is_live and hasattr(client, "get_available_balance"):
                            available_margin = client.get_available_balance()

                    initial_state = "O1_FILLED"

                    if not res_match:
                        cur_p = client.get_ticker_price(sym) if hasattr(client, "get_ticker_price") else 0.0
                        if cur_p <= 0:
                            cur_p = avg_p

                        place_o2 = bool(e2 is not None and q2_res > 0 and tp2 is not None and (e2 < cur_p * 0.9995))
                        place_o3 = bool(e3 is not None and q3_res > 0 and tp3 is not None and (e3 < cur_p * 0.9995))

                        if e2 is not None and q2_res > 0 and not place_o2:
                            console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 2 (${e2}). Добор 2 пропущен.[/yellow]")
                        if e3 is not None and q3_res > 0 and not place_o3:
                            console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 3 (${e3}). Добор 3 пропущен.[/yellow]")

                        needed_margin = 0.0
                        if place_o2 and hasattr(client, "calc_required_margin"):
                            needed_margin += client.calc_required_margin(sym, q2_res, e2)
                        if place_o3 and hasattr(client, "calc_required_margin"):
                            needed_margin += client.calc_required_margin(sym, q3_res, e3)
                        needed_margin *= 1.05

                        if is_live and available_margin < needed_margin:
                            console.print(f"[yellow]⏸️ [{sym}] [{layer_tag}] Недостаточно свободной маржи для добора (${available_margin:.2f} < ${needed_margin:.2f}). Ордера добора пропущены.[/yellow]")
                        else:
                            if place_o2 and e2 is not None and tp2 is not None:
                                resp2 = client.place_order(symbol=sym, side=side_str, order_type="Limit", qty=q2_res, price=e2, take_profit=tp2, stop_loss=sl, order_link_id=link_id_2)
                                o2_id = resp2.get("orderId")
                                console.print(f"  ✅ [{layer_tag}] [Остаточный риск] Ордер 2 размещен: ID {o2_id} (Limit {side_str} {q2_res} @ {e2})")
                            if place_o3 and e3 is not None and tp3 is not None:
                                resp3 = client.place_order(symbol=sym, side=side_str, order_type="Limit", qty=q3_res, price=e3, take_profit=tp3, stop_loss=sl, order_link_id=link_id_3)
                                o3_id = resp3.get("orderId")
                                console.print(f"  ✅ [{layer_tag}] [Остаточный риск] Ордер 3 размещен: ID {o3_id} (Limit {side_str} {q3_res} @ {e3})")
                            if is_live:
                                available_margin = max(0.0, available_margin - needed_margin)
            else:
                # ─── СИТУАЦИЯ 2: Позиции нет (размещение или синхронизация сетки) ─
                match_existing = False
                cur_p = client.get_ticker_price(sym) if hasattr(client, "get_ticker_price") else 0.0
                if cur_p <= 0:
                    try:
                        df_tmp = client.fetch_klines(sym, interval=interval, limit=2)
                        cur_p = float(df_tmp["close"].iloc[-1])
                    except Exception:
                        cur_p = e1

                tol1 = max(specs.qty_step, 0.05 * q1) if q1 > 0 else specs.qty_step
                tol2 = max(specs.qty_step, 0.05 * q2) if q2 > 0 else specs.qty_step
                tol3 = max(specs.qty_step, 0.05 * q3) if q3 > 0 else specs.qty_step

                if len(existing_orders) == 3 and e2 is not None and e3 is not None:
                    p1 = float(existing_orders[0].get("price", 0.0))
                    p2 = float(existing_orders[1].get("price", 0.0))
                    p3 = float(existing_orders[2].get("price", 0.0))
                    qty1 = float(existing_orders[0].get("qty", 0.0))
                    qty2 = float(existing_orders[1].get("qty", 0.0))
                    qty3 = float(existing_orders[2].get("qty", 0.0))
                    sl1 = float(existing_orders[0].get("stopLoss") or 0.0)

                    price_ok = (abs(p1 - e1) / e1 < 0.002 and abs(p2 - e2) / e2 < 0.002 and abs(p3 - e3) / e3 < 0.002)
                    qty_ok = (abs(qty1 - q1) <= tol1 and abs(qty2 - q2) <= tol2 and abs(qty3 - q3) <= tol3)
                    sl_ok = (sl <= 0 or sl1 <= 0 or abs(sl1 - sl) / sl < 0.002)

                    if price_ok and qty_ok and sl_ok:
                        match_existing = True
                        o1_id = existing_orders[0].get("orderId")
                        o2_id = existing_orders[1].get("orderId")
                        o3_id = existing_orders[2].get("orderId")
                        initial_state = "TRAILING" if setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION") else setup.setup_type
                        console.print(f"ℹ️ [{sym}] [{layer_tag}] Найдена готовая сетка из 3 ордеров на Bybit (e1={p1}, e2={p2}, e3={p3} | q1={qty1}, q2={qty2}, q3={qty3}). Подключаем к мониторингу без перевыставления!")
                    elif price_ok and not qty_ok:
                        console.print(f"🔄 [{sym}] [{layer_tag}] Риск в конфиге изменился! Существующие объемы ({qty1}, {qty2}, {qty3}) не совпадают с новыми ({q1}, {q2}, {q3}). Перевыставляем сетку под новый риск...")

                elif len(existing_orders) == 2 and e2 is not None and e3 is None and setup.setup_type == "MANIPULATION":
                    p1 = float(existing_orders[0].get("price", 0.0))
                    p2 = float(existing_orders[1].get("price", 0.0))
                    qty1 = float(existing_orders[0].get("qty", 0.0))
                    qty2 = float(existing_orders[1].get("qty", 0.0))
                    price_ok = (abs(p1 - e1) / e1 < 0.002 and abs(p2 - e2) / e2 < 0.002)
                    qty_ok = (abs(qty1 - q1) <= tol1 and abs(qty2 - q2) <= tol2)
                    if price_ok and qty_ok:
                        match_existing = True
                        o1_id = existing_orders[0].get("orderId")
                        o2_id = existing_orders[1].get("orderId")
                        initial_state = "MANIPULATION_ACTIVE"
                        console.print(f"ℹ️ [{sym}] [{layer_tag}] Найдена готовая сетка манипуляции на Bybit. Подключаем к мониторингу без перевыставления!")
                    elif price_ok and not qty_ok:
                        console.print(f"🔄 [{sym}] [{layer_tag}] Риск манипуляции изменился! Существующие объемы ({qty1}, {qty2}) не совпадают с новыми ({q1}, {q2}). Перевыставляем сетку...")

                if not match_existing:
                    if existing_orders:
                        console.print(f"🔄 [{sym}] [{layer_tag}] Найдено {len(existing_orders)} старых ордеров своего слоя. Снимаем их для актуализации сетки...")
                        for o in existing_orders:
                            if o.get("orderId"):
                                client.cancel_order(sym, o.get("orderId"))
                        existing_orders = []
                        if is_live and hasattr(client, "get_available_balance"):
                            available_margin = client.get_available_balance()

                    # Проверка цен ордеров относительно текущей рыночной цены (защита от покупки выше рынка)
                    is_fib_grid = setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION")
                    is_long = (setup.side == "long")
                    o1_missed = is_entry_missed(e1, cur_p, is_long=is_long)

                    # Если вход на 0.500 уже упущен: переходим в AWAITING_BREAK_BELOW (ожидание пробоя 1.000 без возврата к 0.382)
                    if is_fib_grid and (setup.setup_type == "AWAITING_BREAK_BELOW" or o1_missed):
                        cmp_op = "<=" if is_long else ">="
                        reason = "уровень 0.500 уже был протестирован ранее" if setup.setup_type == "AWAITING_BREAK_BELOW" else f"рыночная цена ${cur_p} {cmp_op} ${e1}"
                        p_0382_str = f"${setup.p_0382:.4f}" if setup.p_0382 else "-"
                        console.print(f"  [yellow]⚠️ [{sym}] [{layer_tag}] Вход на 0.500 (${e1}) уже упущен ({reason}), 0.382 ({p_0382_str}) не протестирован. Ожидание пробоя 1.000 (${sl}) без возврата к 0.382 (маржа свободна).[/yellow]")
                        if is_live:
                            cleanup_orphan_orders_for_layer(client, sym, layer_name)
                        active_monitors.append(ActiveTradeMonitor(
                            symbol=sym,
                            setup_type="AWAITING_BREAK_BELOW",
                            state="AWAITING_BREAK_BELOW",
                            layer=layer_name,
                            side=setup.side,
                            cur_peak=setup.imp_peak_price,
                            imp_start_price=setup.imp_start_price,
                            p_0382=setup.p_0382,
                            cur_e1=e1,
                            cur_tp1=tp1,
                            cur_e2=e2 if e2 else 0.0,
                            cur_tp2=tp2 if tp2 else 0.0,
                            cur_e3=e3 if e3 else 0.0,
                            cur_tp3=tp3 if tp3 else 0.0,
                            sl=sl,
                            imp_end_time=setup.imp_end_time,
                            timeout_hours=setup_timeout,
                        ))
                        continue

                    place_o1 = not o1_missed
                    place_o2 = bool(e2 is not None and q2 > 0 and tp2 is not None and not is_entry_missed(e2, cur_p, is_long=is_long))
                    place_o3 = bool(e3 is not None and q3 > 0 and tp3 is not None and not is_entry_missed(e3, cur_p, is_long=is_long))

                    if not place_o1:
                        console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 1 (${e1}). Ордер 1 пропущен (опоздание на вход).[/yellow]")
                    if not place_o2 and e2 is not None and q2 > 0:
                        console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 2 (${e2}). Ордер 2 пропущен.[/yellow]")
                    if not place_o3 and e3 is not None and q3 > 0:
                        console.print(f"  [yellow]ℹ️ [{sym}] [{layer_tag}] Текущая цена (${cur_p}) ниже Ордера 3 (${e3}). Ордер 3 пропущен.[/yellow]")

                    if not (place_o1 or place_o2 or place_o3):
                        console.print(f"  [yellow]⚠️ [{sym}] [{layer_tag}] Все уровни сетки выше текущей цены (${cur_p}). Сетка не выставляется, монета переходит в IDLE.[/yellow]")
                        active_monitors.append(ActiveTradeMonitor(
                            symbol=sym,
                            setup_type="IDLE",
                            state="IDLE",
                            layer=layer_name,
                            last_skipped_imp_time=setup.imp_end_time,
                        ))
                        continue

                    # Проверка свободной маржи
                    needed_margin = 0.0
                    if place_o1 and hasattr(client, "calc_required_margin"):
                        needed_margin += client.calc_required_margin(sym, q1, e1)
                    if place_o2 and hasattr(client, "calc_required_margin"):
                        needed_margin += client.calc_required_margin(sym, q2, e2)
                    if place_o3 and hasattr(client, "calc_required_margin"):
                        needed_margin += client.calc_required_margin(sym, q3, e3)
                    needed_margin *= 1.05

                    if is_live and available_margin < needed_margin:
                        console.print(f"[yellow]⏸️ [{sym}] [{layer_tag}] Недостаточно свободной маржи (${available_margin:.2f} < ${needed_margin:.2f}). Пропуск выставления сетки.[/yellow]")
                        active_monitors.append(ActiveTradeMonitor(symbol=sym, setup_type="IDLE", state="IDLE", layer=layer_name))
                        continue

                    if place_o1:
                        resp1 = client.place_order(
                            symbol=sym, side=side_str, order_type="Limit", qty=q1, price=e1, take_profit=tp1, stop_loss=sl, order_link_id=link_id_1
                        )
                        o1_id = resp1.get("orderId")
                        console.print(f"✅ [{sym}] [{layer_tag}] Ордер 1 размещен: ID {o1_id} (Limit {side_str} {q1} @ {e1}, TP {tp1}, SL {sl})")

                    if place_o2 and e2 is not None and tp2 is not None:
                        resp2 = client.place_order(
                            symbol=sym, side=side_str, order_type="Limit", qty=q2, price=e2, take_profit=tp2, stop_loss=sl, order_link_id=link_id_2
                        )
                        o2_id = resp2.get("orderId")
                        console.print(f"✅ [{sym}] [{layer_tag}] Ордер 2 размещен: ID {o2_id} (Limit {side_str} {q2} @ {e2}, TP {tp2}, SL {sl})")

                    if place_o3 and e3 is not None and tp3 is not None:
                        resp3 = client.place_order(
                            symbol=sym, side=side_str, order_type="Limit", qty=q3, price=e3, take_profit=tp3, stop_loss=sl, order_link_id=link_id_3
                        )
                        o3_id = resp3.get("orderId")
                        console.print(f"✅ [{sym}] [{layer_tag}] Ордер 3 размещен: ID {o3_id} (Limit {side_str} {q3} @ {e3}, TP {tp3}, SL {sl})")

                    if is_live:
                        available_margin = max(0.0, available_margin - needed_margin)

                    initial_state = "TRAILING" if setup.setup_type in ("TRIPLE_GRID_TRAILING", "TRIPLE_GRID_CORRECTION", "DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION") else setup.setup_type

            active_monitors.append(ActiveTradeMonitor(
                symbol=sym,
                setup_type=setup.setup_type,
                state=initial_state,
                layer=layer_name,
                side=setup.side,
                o1_id=o1_id,
                o2_id=o2_id,
                o3_id=o3_id,
                cur_peak=setup.imp_peak_price,
                p_0382=setup.p_0382,
                cur_e1=e1,
                cur_tp1=tp1,
                cur_e2=e2 if e2 else 0.0,
                cur_tp2=tp2 if tp2 else 0.0,
                cur_e3=e3 if e3 else 0.0,
                cur_tp3=tp3 if tp3 else 0.0,
                imp_start_price=setup.imp_start_price,
                sl=sl,
                q1=q1,
                q2=q2,
                q3=q3,
                has_o2=(o2_id is not None),
                has_o3=(o3_id is not None),
                be_trigger=client.round_price(setup.be_trigger, sym) if setup.be_trigger is not None else None,
                be_price=client.round_price(setup.be_price, sym) if setup.be_price is not None else None,
                position_was_open=pos_open_initially,
                imp_end_time=setup.imp_end_time,
                touched_0382=True,
                timeout_hours=setup_timeout,
            ))

            if is_live:
                active_ids = [o for o in (o1_id, o2_id, o3_id) if o]
                cleanup_orphan_orders_for_layer(client, sym, layer_name, active_order_ids=active_ids)

        except Exception as e:
            console.print(f"[bold red]❌ [{sym}] [{layer_tag}] Ошибка выставления ордеров:[/bold red] {e}")
            if o1_id:
                try:
                    client.cancel_order(sym, o1_id)
                    console.print(f"[yellow][{sym}] [{layer_tag}] Ордер 1 {o1_id} отменен.[/yellow]")
                except Exception:
                    pass

    # 2. Подключаем мониторы AWAITING_MAJOR_0382 (Большие фибы выше 0.382 — без выставления ордеров)
    for item in awaiting_major_setups:
        active_monitors.append(ActiveTradeMonitor(
            symbol=item["symbol"],
            setup_type=item["setup"].setup_type,
            state="AWAITING_MAJOR_0382",
            layer="major",
            cur_peak=item["setup"].imp_peak_price,
            p_0382=item["setup"].p_0382,
            cur_e1=item["e1"],
            cur_tp1=item["tp1"],
            cur_e2=item["e2"] if item["e2"] else 0.0,
            cur_tp2=item["tp2"] if item["tp2"] else 0.0,
            cur_e3=item["e3"] if item["e3"] else 0.0,
            cur_tp3=item["tp3"] if item["tp3"] else 0.0,
            sl=item["sl"],
            q1=item["q1"],
            q2=item["q2"],
            q3=item["q3"],
            has_o2=(item["e2"] is not None and item["q2"] > 0),
            has_o3=(item["e3"] is not None and item["q3"] > 0),
            imp_start_price=item["setup"].imp_start_price,
            imp_end_time=item["setup"].imp_end_time,
            touched_0382=False,
        ))
        p_0382_val = f"${item['setup'].p_0382:.4f}" if item['setup'].p_0382 else "-"
        console.print(f"⏳ [{item['symbol']}] [MAJOR] Добавлен монитор в режиме AWAITING_MAJOR_0382 (ожидание касания 0.382: {p_0382_val}). Маржа свободна.")

    # 3. Для монет без сетапа на определенном слое создаем IDLE-монитор для автопоиска
    monitored_pairs = {(m.symbol, m.layer) for m in active_monitors}
    for sym in symbols:
        sym_has_monitor = any(m.symbol == sym for m in active_monitors)
        if is_live and not sym_has_monitor:
            try:
                pos_info = client.get_position(sym)
                pos_size = float(pos_info.get("size", 0)) if pos_info else 0.0
                if pos_size > 0:
                    tp_val = float(pos_info.get("takeProfit", 0)) if pos_info.get("takeProfit") else None
                    sl_val = float(pos_info.get("stopLoss", 0)) if pos_info.get("stopLoss") else None
                    console.print(f"ℹ️ [{sym}] Обнаружена открытая позиция {pos_size} шт. (TP: {tp_val}, SL: {sl_val}). Подключаем к мониторингу!")
                    active_monitors.append(ActiveTradeMonitor(
                        symbol=sym,
                        setup_type="TRIPLE_GRID_TRAILING",
                        state="O1_FILLED",
                        layer="minor",
                        position_was_open=True,
                        cur_e1=float(pos_info.get("avgPrice", 0)),
                        cur_tp1=tp_val or 0.0,
                        sl=sl_val or 0.0,
                    ))
                    monitored_pairs.add((sym, "minor"))
            except Exception as err:
                console.print(f"⚠️ [{sym}] Ошибка проверки позиции для неактивной монеты: {err}")

        for layer_name in ("minor", "major"):
            if (sym, layer_name) not in monitored_pairs:
                active_monitors.append(ActiveTradeMonitor(
                    symbol=sym,
                    setup_type="IDLE",
                    state="IDLE",
                    layer=layer_name,
                ))

    # Проверяем наличие открытых позиций на Bybit по монетам вне списка торговли (Close-Only режим)
    if is_live:
        try:
            resp_all = client.session.get_positions(category="linear", settleCoin="USDT")
            pos_list = resp_all.get("result", {}).get("list", [])
            monitored_symbols = {m.symbol for m in active_monitors}
            for p in pos_list:
                p_sym = p.get("symbol", "")
                p_size = float(p.get("size", 0.0))
                if p_size > 0 and p_sym not in monitored_symbols:
                    tp_val = float(p.get("takeProfit", 0)) if p.get("takeProfit") else None
                    sl_val = float(p.get("stopLoss", 0)) if p.get("stopLoss") else None
                    avg_p = float(p.get("avgPrice", 0))
                    console.print(f"ℹ️ [{p_sym}] Обнаружена открытая позиция {p_size} шт. вне списка торговли (TP: {tp_val}, SL: {sl_val}). Подключаем в режиме Close-Only (сопровождение до закрытия без новых сделок)!")
                    active_monitors.append(ActiveTradeMonitor(
                        symbol=p_sym,
                        setup_type="TRIPLE_GRID_TRAILING",
                        state="O1_FILLED",
                        layer="minor",
                        position_was_open=True,
                        cur_e1=avg_p,
                        cur_tp1=tp_val or 0.0,
                        sl=sl_val or 0.0,
                        close_only=True,
                    ))
        except Exception as err:
            console.print(f"⚠️ Ошибка проверки открытых позиций вне списка торговли: {err}")

    if not active_monitors:
        console.print("[yellow]Нет монет для мониторинга. Завершение.[/yellow]")
        return

    # Единый цикл мониторинга для всех монет
    mode_desc = "Одиночный (--once)" if args.once else "Непрерывный фоновый (Daemon / автопоиск новых импульсов)"
    monitor_items_str = ", ".join(f"{m.symbol} [{m.layer.upper()}:{m.state}]" + (" [Close-Only]" if m.close_only else "") for m in active_monitors)
    console.print(Panel(
        f"[bold yellow]Запущен автоматический мониторинг {len(active_monitors)} слоев по монетам:[/bold yellow]\n"
        f"[green]{monitor_items_str}[/green]\n"
        "Бот отслеживает трейлинг, налив ордеров, закрытие по SL/TP, ложный пробой и сетку манипуляции.\n"
        f"Режим: [cyan]{mode_desc}[/cyan].\n"
        "[dim]Для остановки нажмите Ctrl+C.[/dim]",
        border_style="yellow",
    ))

    config_file = Path(cfg.config_path) if cfg.config_path else (root_dir / "config" / "trade_config.yaml")
    last_cfg_mtime = config_file.stat().st_mtime if config_file.exists() else 0.0

    try:
        while True:
            time.sleep(15)

            # Горячая перезагрузка параметров стратегии и риска при изменении файла конфига
            if config_file.exists():
                try:
                    cur_mtime = config_file.stat().st_mtime
                    if cur_mtime > last_cfg_mtime:
                        last_cfg_mtime = cur_mtime
                        old_risk = cfg.total_risk_usd
                        cfg = load_trade_config(config_file)
                        console.print(f"\n[bold magenta]⚡ Обнаружено изменение файла {config_file.name}![/bold magenta]")
                        console.print(f"  ➜ Риск обновлен: ${old_risk:.2f} -> ${cfg.total_risk_usd:.2f} (Minor: ${cfg.minor_risk_usd:.2f}, Major: ${cfg.major_risk_usd:.2f}).")
                except Exception:
                    pass

            for m in active_monitors:
                if m.done:
                    continue
                try:
                    process_monitor_step(m, client, cfg, interval, is_live=is_live)
                except Exception as sym_err:
                    console.print(f"[red]⚠️ [{m.symbol}] Ошибка мониторинга: {sym_err}[/red]")
                # Плавная пауза между мониторами во избежание пиковых всплесков запросов
                time.sleep(0.08)

            if args.once and all(m.done or m.state == "IDLE" for m in active_monitors):
                break

    except KeyboardInterrupt:
        console.print("\n[yellow]Мониторинг остановлен пользователем.[/yellow]")

    console.print("\n[bold green]Завершено.[/bold green]")


if __name__ == "__main__":
    main()



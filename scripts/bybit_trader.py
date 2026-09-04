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
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml

# Обеспечиваем доступность корневых пакетов проекта
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import numpy as np
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from indicators.macd import calculate_macd
from indicators.pybit_client import BybitClient, InstrumentSpecs
from scripts.backtest_strategy_interactive import calc_fib

console = Console()


@dataclass
class TradeConfig:
    total_risk_usd: float = 2.0
    entry_buffer_pct: float = 0.07
    tp_buffer_pct: float = 0.10
    reclaim_tp_buffer_pct: float = 2.0
    reclaim_be_trigger_fib: float = 0.786
    reclaim_be_offset_pct: float = 0.05
    reclaim_max_sweep_pct: float = 0.5
    reclaim_allow_close_below: bool = False
    preferred_side: str = "long"
    min_impulse_pct: float = 2.0
    lookback_bars: int = 60
    timeframe: str = "1h"
    scale: str = "log"
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

        buffer_data = data.get("buffers", {})
        if "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_pct = float(buffer_data["entry_buffer_pct"])
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
            cfg.preferred_side = str(strat_data["preferred_side"]).lower()
        if "min_impulse_pct" in strat_data:
            cfg.min_impulse_pct = float(strat_data["min_impulse_pct"])
        if "lookback_bars" in strat_data:
            cfg.lookback_bars = int(strat_data["lookback_bars"])
        if "timeframe" in strat_data:
            cfg.timeframe = str(strat_data["timeframe"])
        if "scale" in strat_data:
            cfg.scale = str(strat_data["scale"])
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
    setup_type: Literal["DUAL_GRID_TRAILING", "DUAL_GRID_CORRECTION", "SWEEP_RECLAIM", "MANIPULATION", "NONE"]
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
    stop_loss: float = 0.0
    # Безубыток (Sweep Reclaim)
    be_trigger: Optional[float] = None
    be_price: Optional[float] = None
    # Детали свипа / дивергенции
    sweep_price: Optional[float] = None
    sweep_pct: Optional[float] = None
    macd_divergent: bool = False
    description: str = ""


def find_active_setup(
    df: pd.DataFrame,
    min_pct: float = 2.0,
    lookback_bars: int = 60,
    preferred_side: str = "long",
    scale: str = "log",
    max_sweep_pct: float = 0.5,
    allow_close_below: bool = False,
    entry_buffer_pct: float = 0.07,
    tp_buffer_pct: float = 0.1,
    reclaim_tp_buffer_pct: float = 2.0,
    reclaim_be_trigger_fib: float = 0.786,
    reclaim_be_offset_pct: float = 0.05,
) -> Optional[SetupSignal]:
    """
    Анализирует свечи на наличие активного не отработанного торгового сетапа (ТОЛЬКО В LONG):
    1. Трейлинг / Активная сетка (0.500 / 0.618) с отступом +0.07% перед входом и -0.1% перед тейком.
    2. Свип ликвидности + MACD Reclaim.
    3. Манипуляция (1.618 / 2.000) с отступом +0.07% и тейками -0.1%.
    """
    if len(df) < 15:
        return None

    # Ограничиваем глубину анализа lookback_bars последними свечами
    if len(df) > lookback_bars:
        df = df.iloc[-lookback_bars:].reset_index(drop=True)

    # Рассчитываем MACD на том же окне
    macd_df = calculate_macd(df["close"])
    hist = macd_df["hist"].values

    from scripts.backtest_strategy_interactive import detect_impulses

    sides_to_check = [preferred_side] if preferred_side in ("long", "short") else ["long"]

    for side in sides_to_check:
        is_long = (side == "long")
        # Ищем импульсы по нашей стратегии
        imps = detect_impulses(df, min_pct=min_pct, side=side, scale=scale)
        if not imps:
            continue

        # Перебираем от самых свежих к старым в поиске неотработанного
        for imp in reversed(imps):
            p_0236 = calc_fib(imp.high, imp.low, 0.236, is_long=is_long, scale=scale)
            p_0382 = calc_fib(imp.high, imp.low, 0.382, is_long=is_long, scale=scale)
            p_0500 = calc_fib(imp.high, imp.low, 0.500, is_long=is_long, scale=scale)
            p_0618 = calc_fib(imp.high, imp.low, 0.618, is_long=is_long, scale=scale)
            p_1000 = imp.low if is_long else imp.high

            p_1618 = calc_fib(imp.high, imp.low, 1.618, is_long=is_long, scale=scale)
            p_2000 = calc_fib(imp.high, imp.low, 2.000, is_long=is_long, scale=scale)
            p_2414 = calc_fib(imp.high, imp.low, 2.414, is_long=is_long, scale=scale)

            # Буфер перед входом (+0.07% для Лонга)
            buf_mult = 1.0 + (entry_buffer_pct / 100.0) if is_long else 1.0 - (entry_buffer_pct / 100.0)
            e_0500 = p_0500 * buf_mult
            e_0618 = p_0618 * buf_mult
            e_1618 = p_1618 * buf_mult
            e_2000 = p_2000 * buf_mult

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
            if len(post_df) == 0:
                # Импульс находится на самой последней свече -> ТРЕЙЛИНГ
                return SetupSignal(
                    setup_type="DUAL_GRID_TRAILING",
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
                    stop_loss=p_1000,
                    description=f"Растущий импульс (+{imp.pct:.2f}%) на текущей свече. Трейлинг сетки (вход +{entry_buffer_pct}%, тейк -{tp_buffer_pct}%).",
                )

            touched_0500 = False
            touch_05_idx = -1
            touched_0618 = False
            touch_0618_idx = -1
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

                    # 2. Налив ордера 0.618 (если уже налило 0.500)
                    if not broken and touched_0500 and not touched_0618 and bar_l <= p_0618:
                        touched_0618 = True
                        touch_0618_idx = abs_idx

                    # 3. Взятие тейк-профита:
                    # Если налиты оба ордера (0.500 и 0.618) -> выход обоих на 0.382!
                    # Если налит только 0.500 -> выход на 0.236!
                    if not broken and touched_0500:
                        if touched_0618:
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

                    if not broken and touched_0500 and not touched_0618 and bar_h >= p_0618:
                        touched_0618 = True
                        touch_0618_idx = abs_idx

                    if not broken and touched_0500:
                        if touched_0618:
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
                swp_pct = abs(p_1000 - sweep_val) / p_1000 * 100.0
                latest_c = float(df["close"].iloc[-1])
                is_reclaimed = (latest_c >= p_1000) if is_long else (latest_c <= p_1000)

                # Проверка отсутствия закрепления цены под уровнем 1.000:
                # Если allow_close_below == False, ни одна свеча после импульса не должна закрываться ниже 1.000.
                closed_below = (post_df["close"] < p_1000) if is_long else (post_df["close"] > p_1000)
                has_consolidated = closed_below.any() if not allow_close_below else (closed_below.sum() > 1)

                # Дивергенция MACD (гистограмма растет на лонге или падает на шорте)
                swp_bar_idx = sweep_idx if sweep_idx != -1 else (len(df) - 1)
                macd_div = (hist[-1] > hist[swp_bar_idx] or hist[-1] > -0.01) if is_long else (hist[-1] < hist[swp_bar_idx] or hist[-1] < 0.01)

                if swp_pct <= max_sweep_pct and is_reclaimed and not has_consolidated and macd_div:
                    sl_target = sweep_val * (0.998 if is_long else 1.002)
                    p_0786 = calc_fib(imp.high, imp.low, reclaim_be_trigger_fib, is_long=is_long, scale=scale)
                    be_mult = 1.0 + (reclaim_be_offset_pct / 100.0) if is_long else 1.0 - (reclaim_be_offset_pct / 100.0)
                    be_price_val = latest_c * be_mult
                    return SetupSignal(
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
                        be_trigger=p_0786,
                        be_price=be_price_val,
                        sweep_price=sweep_val,
                        sweep_pct=swp_pct,
                        macd_divergent=True,
                        description=f"Ложный пробой 1.000 ({swp_pct:.2f}% <= {max_sweep_pct}%) без закрепления с дивергенцией MACD. Тейк 0.618 Fib (-{reclaim_tp_buffer_pct}%), БУ на {reclaim_be_trigger_fib} Fib.",
                    )
                elif swp_pct > max_sweep_pct or has_consolidated:
                    return SetupSignal(
                        setup_type="MANIPULATION",
                        side=side,
                        imp_start_time=imp.start_time,
                        imp_end_time=imp.end_time,
                        imp_start_price=p_1000,
                        imp_peak_price=imp.high if is_long else imp.low,
                        imp_pct=imp.pct,
                        entry_1=e_1618,
                        tp_1=tp_0500,
                        entry_2=e_2000,
                        tp_2=tp_1000,
                        stop_loss=p_2414,
                        sweep_price=sweep_val,
                        sweep_pct=swp_pct,
                        description=f"Манипуляция: выход за 1.000 на {swp_pct:.2f}% (порог {max_sweep_pct}%)" + (" с закреплением" if has_consolidated else "") + f". Сетка на 1.618 и 2.000, стоп 2.414.",
                    )
                continue

            # ─── Сценарий 2: Уровень 1.000 НЕ пробит ────────────────────────────
            if not touched_0500:
                # Импульс еще развивается без отката к 0.500 -> ТРЕЙЛИНГ
                return SetupSignal(
                    setup_type="DUAL_GRID_TRAILING",
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
                    stop_loss=p_1000,
                    description=f"Растущий импульс (+{imp.pct:.2f}%) без коррекции к 0.500. Режим трейлинга (вход +{entry_buffer_pct}%, тейк -{tp_buffer_pct}%).",
                )
            else:
                # Касание 0.500 было, но тейк еще не взят -> АКТИВНАЯ СЕТКА
                desc = (
                    f"Активная коррекция к сетке (+{imp.pct:.2f}%): налиты оба уровня (0.500 и 0.618), совместный тейк на 0.382 (-{tp_buffer_pct}%)."
                    if touched_0618
                    else f"Активная коррекция к сетке (+{imp.pct:.2f}%): налит ордер 0.500, тейк 0.236 (-{tp_buffer_pct}%)."
                )
                return SetupSignal(
                    setup_type="DUAL_GRID_CORRECTION",
                    side=side,
                    imp_start_time=imp.start_time,
                    imp_end_time=imp.end_time,
                    imp_start_price=p_1000,
                    imp_peak_price=imp.high if is_long else imp.low,
                    imp_pct=imp.pct,
                    entry_1=e_0500,
                    tp_1=tp_0382 if touched_0618 else tp_0236,
                    entry_2=e_0618,
                    tp_2=tp_0382,
                    stop_loss=p_1000,
                    description=desc,
                )

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
    setup_type: str
    o1_id: Optional[str] = None
    o2_id: Optional[str] = None
    cur_peak: float = 0.0
    cur_e1: float = 0.0
    cur_tp1: float = 0.0
    cur_e2: float = 0.0
    cur_tp2: float = 0.0
    imp_start_price: float = 0.0
    sl: float = 0.0
    has_o2: bool = False
    be_trigger: Optional[float] = None
    be_price: Optional[float] = None
    position_was_open: bool = False
    done: bool = False


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
    args = parser.parse_args()

    cfg = load_trade_config(args.config)
    if args.risk is not None:
        cfg.total_risk_usd = args.risk
    if args.entry_buffer is not None:
        cfg.entry_buffer_pct = args.entry_buffer
    if args.tp_buffer is not None:
        cfg.tp_buffer_pct = args.tp_buffer
    if args.interval:
        cfg.timeframe = args.interval

    console.print(Panel.fit(
        "[bold cyan]🤖 Bybit Fibonacci Dual Grid & Trailing Trader[/bold cyan]\n"
        f"[dim]Конфиг: {Path(cfg.config_path).name if cfg.config_path else 'default'} | Суммарный стоп: ${cfg.total_risk_usd:.2f} | Вход: +{cfg.entry_buffer_pct:.2f}% | Тейк: -{cfg.tp_buffer_pct:.2f}%[/dim]",
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
        user_coin_input = console.input("[bold yellow]Какую монету(ы) торгуем?[/bold yellow] (например, [green]SUI, BTC, ETH[/green]): ")
        raw_coins = [s.strip() for s in user_coin_input.split(",") if s.strip()]

    if not raw_coins:
        console.print("[red]Символы не указаны. Завершение работы.[/red]")
        return

    # Приводим к формату Bybit Linear (с поддержкой .P, ZEC -> ZECUSDT) и удаляем дубликаты
    symbols = list(dict.fromkeys([format_symbol(c) for c in raw_coins]))

    # 2. Запрос таймфрейма
    tf_map = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
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

    console.print(f"\n[dim]Подключение к Bybit V5...[/dim]")
    try:
        client = BybitClient()
    except Exception as e:
        console.print(f"[bold red]❌ Ошибка инициализации Bybit клиента:[/bold red] {e}")
        return

    # Сканирование монет
    actionable_setups = []

    for symbol in symbols:
        console.print(f"\n[bold cyan]─── Анализ {symbol} ───[/bold cyan]")
        try:
            specs = client.get_specs(symbol)
        except Exception as e:
            console.print(f"[red]❌ Ошибка получения спецификации {symbol}: {e}. Пропускаем.[/red]")
            continue

        try:
            df = client.fetch_klines(symbol, interval=interval, limit=80)
        except Exception as e:
            console.print(f"[red]❌ Ошибка загрузки свечей {symbol}: {e}. Пропускаем.[/red]")
            continue

        if len(df) == 0:
            console.print(f"[yellow]⚠️ Нет свечей для {symbol}. Пропускаем.[/yellow]")
            continue

        cur_price = df["close"].iloc[-1]
        console.print(f"[dim]{symbol}: Tick: {specs.tick_size}, Step: {specs.qty_step}, MinQty: {specs.min_qty}, MinNotional: ${specs.min_notional}, Цена: {cur_price}[/dim]")

        setup = find_active_setup(
            df,
            min_pct=cfg.min_impulse_pct,
            lookback_bars=cfg.lookback_bars,
            preferred_side=cfg.preferred_side,
            scale=cfg.scale,
            max_sweep_pct=cfg.reclaim_max_sweep_pct,
            allow_close_below=cfg.reclaim_allow_close_below,
            entry_buffer_pct=cfg.entry_buffer_pct,
            tp_buffer_pct=cfg.tp_buffer_pct,
            reclaim_tp_buffer_pct=cfg.reclaim_tp_buffer_pct,
            reclaim_be_trigger_fib=cfg.reclaim_be_trigger_fib,
            reclaim_be_offset_pct=cfg.reclaim_be_offset_pct,
        )

        if not setup:
            console.print(Panel(
                f"[yellow]На монете {symbol} в последних {cfg.lookback_bars} свечах не найдено активного LONG-импульса (от {cfg.min_impulse_pct:.1f}%).[/yellow]\n"
                "Все предыдущие сетки либо отработали тейк, либо выбиты по стопу.",
                title=f"🔍 {symbol}: Сетап не найден",
                border_style="yellow",
            ))
            continue

        # Расчет позиций
        e1 = client.round_price(setup.entry_1, symbol)
        tp1 = client.round_price(setup.tp_1, symbol)
        sl = client.round_price(setup.stop_loss, symbol)

        e2 = client.round_price(setup.entry_2, symbol) if setup.entry_2 else None
        tp2 = client.round_price(setup.tp_2, symbol) if setup.tp_2 else None

        # Расчет лотов
        if e2 is not None:
            q1, q2, loss1, loss2 = client.calc_dual_grid_order_sizes(e1, e2, sl, total_risk_usd=total_risk, symbol=symbol, equal_weight=True)
            tot_loss = loss1 + loss2
        else:
            dist1 = abs(e1 - sl)
            q1 = client.round_qty(total_risk / dist1 if dist1 > 0 else specs.min_qty, symbol)
            if q1 < specs.min_qty:
                q1 = specs.min_qty
            loss1 = q1 * dist1
            q2 = 0.0
            loss2 = 0.0
            tot_loss = loss1

        # Отображение информации
        title_map = {
            "DUAL_GRID_TRAILING": "🚀 ДВОЙНАЯ СЕТКА (РЕЖИМ ТРЕЙЛИНГА)",
            "DUAL_GRID_CORRECTION": "🎯 ДВОЙНАЯ СЕТКА (АКТИВНАЯ КОРРЕКЦИЯ)",
            "SWEEP_RECLAIM": "🟢 ЛОЖНЫЙ ПРОБОЙ (SWEEP RECLAIM + MACD)",
            "MANIPULATION": "🟣 СЕТКА МАНИПУЛЯЦИИ (1.618 & 2.000)",
        }

        t = Table(title=f"{title_map.get(setup.setup_type, setup.setup_type)} — {symbol} [LONG ONLY]", show_header=True, header_style="bold magenta")
        t.add_column("Параметр", style="cyan")
        t.add_column("Значение", style="bold white")

        t.add_row("Конфиг", f"{Path(cfg.config_path).name if cfg.config_path else 'по умолчанию'}")
        t.add_row("Импульс старт", f"{setup.imp_start_time.strftime('%Y-%m-%d %H:%M')} (${setup.imp_start_price})")
        t.add_row("Импульс вершина", f"{setup.imp_end_time.strftime('%Y-%m-%d %H:%M')} (${setup.imp_peak_price}) [{setup.imp_pct:+.2f}%]")
        t.add_row("Текущая цена", f"${cur_price}")
        t.add_row("Буфер входа", f"+{cfg.entry_buffer_pct:.2f}% перед уровнем (вход чуть выше Фибы)")
        t.add_row("Буфер тейка", f"-{cfg.tp_buffer_pct:.2f}% от уровня (раннее закрытие перед Фибой)")
        t.add_row("─" * 20, "─" * 30)

        t.add_row("Ордер 1 (Вход / Тейк)", f"Вход: ${e1}  |  TP: ${tp1}")
        t.add_row("Объем Ордера 1", f"{q1} шт. (${q1 * e1:.2f} notional, риск ${loss1:.2f})")

        if e2 is not None and tp2 is not None:
            t.add_row("Ордер 2 (Вход / Тейк)", f"Вход: ${e2}  |  TP: ${tp2}")
            t.add_row("Объем Ордера 2", f"{q2} шт. (${q2 * e2:.2f} notional, риск ${loss2:.2f})")

        t.add_row("Стоп-Лосс (SL)", f"${sl} (расчетный суммарный убыток: ${tot_loss:.2f} / лимит ${total_risk:.2f})")
        if setup.be_trigger is not None and setup.be_price is not None:
            be_trig_str = f"${client.round_price(setup.be_trigger, symbol)}"
            be_price_str = f"${client.round_price(setup.be_price, symbol)}"
            t.add_row("Безубыток (БУ)", f"Триггер: {be_trig_str} ({cfg.reclaim_be_trigger_fib} Fib)  ->  Перенос SL в: {be_price_str}")
        t.add_row("Статус стратегии", f"[green]{setup.description}[/green]")

        console.print(t)

        if q1 * e1 < specs.min_notional:
            console.print(f"[yellow]⚠️ Внимание: Notional Ордера 1 (${q1 * e1:.2f}) меньше биржевого минимума ${specs.min_notional}![/yellow]")

        actionable_setups.append({
            "symbol": symbol,
            "setup": setup,
            "specs": specs,
            "e1": e1,
            "tp1": tp1,
            "e2": e2,
            "tp2": tp2,
            "sl": sl,
            "q1": q1,
            "q2": q2,
        })

    # Если режим Dry-Run — завершаем после отображения всех сетапов
    if not is_live:
        console.print(f"\n[bold green]Режим Dry-Run завершен.[/bold green] Найдено активных сетапов: [bold cyan]{len(actionable_setups)} из {len(symbols)}[/bold cyan] монет.")
        return

    # В Live режиме — проверяем, есть ли что торговать
    if not actionable_setups:
        console.print(f"\n[yellow]Нет активных сетапов для выставления ордеров среди монет: {', '.join(symbols)}.[/yellow]")
        return

    symbols_to_trade_str = ", ".join(item["symbol"] for item in actionable_setups)
    if not args.yes:
        confirm = console.input(f"\n[bold red]ВЫСТАВИТЬ ОРДЕРА НА BYBIT ДЛЯ {symbols_to_trade_str}?[/bold red] (y/N): ").strip().lower()
        if confirm != "y":
            console.print("[yellow]Отменено пользователем.[/yellow]")
            return
    else:
        console.print(f"[bold green]Автоподтверждение (-y): выставляем ордера для {symbols_to_trade_str}...[/bold green]")


    # Выставляем ордера для всех подтвержденных сетапов
    active_monitors: list[ActiveTradeMonitor] = []

    for item in actionable_setups:
        sym = item["symbol"]
        setup = item["setup"]
        e1, tp1, e2, tp2, sl = item["e1"], item["tp1"], item["e2"], item["tp2"], item["sl"]
        q1, q2 = item["q1"], item["q2"]

        console.print(f"\n[bold cyan]Проверка/выставление ордеров для {sym}...[/bold cyan]")
        o1_id = None
        o2_id = None
        pos_open_initially = False
        try:
            side_str = "Buy" if setup.side == "long" else "Sell"

            # Проверяем, есть ли уже открытая позиция
            curr_pos = client.get_position(sym, side=side_str)
            if curr_pos is not None:
                pos_open_initially = True
                console.print(f"ℹ️ [{sym}] Уже есть открытая позиция (объем: {curr_pos.get('size')}, вход: {curr_pos.get('avgPrice')}).")

            # Проверяем, есть ли уже активные лимитные ордера на Bybit
            existing_orders = [
                o for o in client.get_open_orders(sym)
                if o.get("side") == side_str and o.get("orderType") == "Limit"
            ]

            if existing_orders:
                # Сортируем: для Buy по убыванию цены (верхний e1, нижний e2)
                existing_orders.sort(key=lambda x: float(x.get("price", 0.0)), reverse=(setup.side == "long"))
                console.print(f"ℹ️ [{sym}] Найдено {len(existing_orders)} активных лимитных ордера(ов) на Bybit. Подключаем к мониторингу!")
                o1_info = existing_orders[0]
                o1_id = o1_info.get("orderId")
                e1 = float(o1_info.get("price", e1))
                tp1 = float(o1_info.get("takeProfit") or tp1)
                console.print(f"  ✓ Ордер 1 (активен): ID {o1_id} @ {e1}, TP {tp1}")

                if len(existing_orders) > 1:
                    o2_info = existing_orders[1]
                    o2_id = o2_info.get("orderId")
                    e2 = float(o2_info.get("price", e2 or 0.0))
                    tp2 = float(o2_info.get("takeProfit") or (tp2 or 0.0))
                    console.print(f"  ✓ Ордер 2 (активен): ID {o2_id} @ {e2}, TP {tp2}")
            else:
                # Ордер 1
                resp1 = client.place_order(
                    symbol=sym,
                    side=side_str,
                    order_type="Limit",
                    qty=q1,
                    price=e1,
                    take_profit=tp1,
                    stop_loss=sl,
                )
                o1_id = resp1.get("orderId")
                console.print(f"✅ [{sym}] Ордер 1 размещен: ID {o1_id} (Limit {side_str} {q1} @ {e1}, TP {tp1}, SL {sl})")

                # Ордер 2
                if e2 is not None and q2 > 0 and tp2 is not None:
                    resp2 = client.place_order(
                        symbol=sym,
                        side=side_str,
                        order_type="Limit",
                        qty=q2,
                        price=e2,
                        take_profit=tp2,
                        stop_loss=sl,
                    )
                    o2_id = resp2.get("orderId")
                    console.print(f"✅ [{sym}] Ордер 2 размещен: ID {o2_id} (Limit {side_str} {q2} @ {e2}, TP {tp2}, SL {sl})")

            active_monitors.append(ActiveTradeMonitor(
                symbol=sym,
                setup_type=setup.setup_type,
                o1_id=o1_id,
                o2_id=o2_id,
                cur_peak=setup.imp_peak_price,
                cur_e1=e1,
                cur_tp1=tp1,
                cur_e2=e2 if e2 else 0.0,
                cur_tp2=tp2 if tp2 else 0.0,
                imp_start_price=setup.imp_start_price,
                sl=sl,
                has_o2=(o2_id is not None),
                be_trigger=client.round_price(setup.be_trigger, sym) if setup.be_trigger is not None else None,
                be_price=client.round_price(setup.be_price, sym) if setup.be_price is not None else None,
                position_was_open=pos_open_initially,
            ))



        except Exception as e:
            console.print(f"[bold red]❌ [{sym}] Ошибка выставления ордеров:[/bold red] {e}")
            if o1_id:
                try:
                    client.cancel_order(sym, o1_id)
                    console.print(f"[yellow][{sym}] Ордер 1 {o1_id} отменен.[/yellow]")
                except Exception:
                    pass

    if not active_monitors:
        console.print("[yellow]Ни один ордер не был выставлен. Завершение.[/yellow]")
        return

    # Единый цикл мониторинга для всех активных монет
    console.print(Panel(
        f"[bold yellow]Запущен автоматический мониторинг {len(active_monitors)} монет:[/bold yellow]\n"
        f"[green]{', '.join(m.symbol for m in active_monitors)}[/green]\n"
        "Бот отслеживает трейлинг, закрытие по SL и перенос в безубыток.\n"
        "[dim]Для остановки нажмите Ctrl+C.[/dim]",
        border_style="yellow",
    ))

    try:
        while any(not m.done for m in active_monitors):
            time.sleep(15)

            for m in active_monitors:
                if m.done:
                    continue

                try:
                    pos = client.get_position(m.symbol)
                    is_open = (pos is not None)

                    # 1. Трейлинг сетки
                    if m.setup_type == "DUAL_GRID_TRAILING":
                        if is_open:
                            m.position_was_open = True

                        if m.position_was_open and not is_open:
                            console.print(f"\n[red]⛔ [{m.symbol}] Позиция закрыта (SL). Отменяем незаполненные ордера...[/red]")
                            cancelled = client.cancel_all_orders(m.symbol)
                            console.print(f"[yellow][{m.symbol}] Отменено: {len(cancelled)} ордер(ов).[/yellow]")
                            m.done = True
                            continue

                        df_now = client.fetch_klines(m.symbol, interval=interval, limit=10)
                        latest_h = df_now["high"].iloc[-1]
                        latest_c = df_now["close"].iloc[-1]

                        if latest_h > m.cur_peak and not m.position_was_open:
                            new_peak = latest_h
                            new_pct = (new_peak - m.imp_start_price) / m.imp_start_price * 100.0

                            new_e1 = client.round_price(
                                calc_fib(new_peak, m.imp_start_price, 0.500, is_long=True, scale=cfg.scale)
                                * (1.0 + cfg.entry_buffer_pct / 100.0), m.symbol
                            )
                            new_tp1 = client.round_price(
                                calc_fib(new_peak, m.imp_start_price, 0.236, is_long=True, scale=cfg.scale)
                                * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol
                            )
                            new_e2 = client.round_price(
                                calc_fib(new_peak, m.imp_start_price, 0.618, is_long=True, scale=cfg.scale)
                                * (1.0 + cfg.entry_buffer_pct / 100.0), m.symbol
                            ) if m.has_o2 else None
                            new_tp2 = client.round_price(
                                calc_fib(new_peak, m.imp_start_price, 0.382, is_long=True, scale=cfg.scale)
                                * (1.0 - cfg.tp_buffer_pct / 100.0), m.symbol
                            ) if m.has_o2 else None

                            # Сдвигаем Ордер 1 только если уровни изменились хотя бы на 1 шаг цены
                            if new_e1 != m.cur_e1 or new_tp1 != m.cur_tp1:
                                console.print(f"\n[bold green]🚀 [{m.symbol}] Новый максимум ${new_peak} (+{new_pct:.2f}%). Сдвигаем уровни...[/bold green]")
                                try:
                                    client.amend_order(m.symbol, m.o1_id, price=new_e1, take_profit=new_tp1, stop_loss=m.sl)
                                    m.cur_e1 = new_e1
                                    m.cur_tp1 = new_tp1
                                    console.print(f"  ✓ [{m.symbol}] Ордер 1 сдвинут: Вход ${new_e1}, TP ${new_tp1}")
                                except Exception as err:
                                    if "order not modified" not in str(err).lower():
                                        console.print(f"  ⚠️ [{m.symbol}] Не удалось изменить Ордер 1: {err}")


                            # Сдвигаем Ордер 2 только если уровни изменились
                            if m.o2_id and new_e2 and new_tp2 and (new_e2 != m.cur_e2 or new_tp2 != m.cur_tp2):
                                try:
                                    client.amend_order(m.symbol, m.o2_id, price=new_e2, take_profit=new_tp2, stop_loss=m.sl)
                                    m.cur_e2 = new_e2
                                    m.cur_tp2 = new_tp2
                                    console.print(f"  ✓ [{m.symbol}] Ордер 2 сдвинут: Вход ${new_e2}, TP ${new_tp2}")
                                except Exception as err:
                                    if "order not modified" not in str(err).lower():
                                        console.print(f"  ⚠️ [{m.symbol}] Не удалось изменить Ордер 2: {err}")

                            m.cur_peak = new_peak


                        if latest_c <= m.cur_e1 and not m.position_was_open:
                            console.print(f"\n[bold cyan]🎉 [{m.symbol}] Ордер 1 вошел в позицию! Трейлинг завершен. Сделка на бирже.[/bold cyan]")
                            m.done = True

                    # 2. Мониторинг активной коррекционной сетки
                    elif m.setup_type == "DUAL_GRID_CORRECTION" and m.o2_id:
                        if is_open and not m.position_was_open:
                            m.position_was_open = True
                            console.print(f"✅ [{m.symbol}] Позиция открыта (Ордер 1 исполнен).")

                        if m.position_was_open and not is_open:
                            console.print(f"\n[red]⛔ [{m.symbol}] Позиция закрыта (SL или TP). Отменяем незаполненные ордера...[/red]")
                            cancelled = client.cancel_all_orders(m.symbol)
                            console.print(f"[yellow][{m.symbol}] Отменено: {len(cancelled)} ордер(ов).[/yellow]")
                            m.done = True

                    # 3. Мониторинг безубытка для ложного пробоя (Sweep Reclaim)
                    elif m.setup_type == "SWEEP_RECLAIM" and m.be_trigger is not None and m.be_price is not None:
                        if not is_open and m.position_was_open:
                            console.print(f"\n[yellow][{m.symbol}] Позиция уже закрыта (SL или TP). Мониторинг завершен.[/yellow]")
                            m.done = True
                            continue
                        if is_open:
                            m.position_was_open = True

                        df_now = client.fetch_klines(m.symbol, interval=interval, limit=5)
                        latest_h = df_now["high"].iloc[-1]

                        if latest_h >= m.be_trigger:
                            success = client.update_stop_loss(m.symbol, m.o1_id, m.be_price)
                            if success:
                                console.print(f"\n[bold green]🛡️ [{m.symbol}] Достигнут триггер (${latest_h})! SL перенесен в БЕЗУБЫТОК (${m.be_price}).[/bold green]")
                                m.done = True
                            else:
                                console.print(f"\n[yellow]⚠️ [{m.symbol}] Триггер БУ достигнут, но не удалось перенести SL. Повторим в следующей итерации...[/yellow]")

                except Exception as sym_err:
                    console.print(f"[red]⚠️ [{m.symbol}] Ошибка мониторинга: {sym_err}[/red]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Мониторинг остановлен пользователем.[/yellow]")

    console.print("\n[bold green]Завершено.[/bold green]")


if __name__ == "__main__":
    main()



"""Поиск активных торговых сетапов (Fibonacci Dual Grid) на свечных данных."""

from typing import Any, Literal, Optional

import pandas as pd

from indicators.macd import calculate_macd
from scripts.backtest_strategy_interactive import calc_fib
from scripts.trader.models import SetupSignal
from scripts.trader.trade_journal import is_impulse_disqualified, save_completed_impulse


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
    symbol: Optional[str] = None,
    completed_impulses: Optional[list[dict[str, Any]]] = None,
) -> Optional[SetupSignal]:
    """
    Анализирует свечи на наличие активного не отработанного торгового сетапа (ТОЛЬКО В LONG):
    1. Трейлинг / Активная тройная сетка (0.500, 0.618, 0.786) с буфером входа перед каждым уровнем и -0.1% перед тейком.
    2. Свип ликвидности + MACD Reclaim.
    3. Манипуляция (1.414 / 1.618) с индивидуальными отступами и тейками -0.1%.
    """
    if len(df) < 15:
        return None

    effective_completed: list[dict[str, Any]] = list(completed_impulses or [])

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

        # Фильтруем импульсы, которые относятся к уже отработанным сделкам
        filtered_imps = []
        for imp in imps:
            peak_val = imp.high if is_long else imp.low
            if is_impulse_disqualified(peak_val, imp.start_time, imp.end_time, symbol or "", effective_completed, layer=layer):
                continue
            filtered_imps.append(imp)
        imps = filtered_imps
        if not imps:
            continue

        unbroken_setups: list[SetupSignal] = []
        reclaim_setups: list[SetupSignal] = []
        manipulation_setups: list[SetupSignal] = []
        buf_desc = f"+{buf_0500:.2f}%/+{buf_0618:.2f}%/+{buf_0786:.2f}%" if (buf_0500 != buf_0618 or buf_0618 != buf_0786) else f"+{buf_0500:.2f}%"

        # Перебираем от самых свежих к старым в поиске неотработанного
        for imp in reversed(imps):
            peak_val = imp.high if is_long else imp.low
            if is_impulse_disqualified(peak_val, imp.start_time, imp.end_time, symbol or "", effective_completed, layer=layer):
                continue
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
            # Для Minor: если в последние timeout_hours баров цена не касалась 0.500 — остыл
            # Для Major: если в последние timeout_hours баров цена не касалась 0.382 — остыл
            # Проверяем ПОСЛЕДНИЕ N баров (а не первые): сетап актуален если цена
            # вблизи зоны коррекции СЕЙЧАС, даже если импульс сформировался давно
            # (например, пока была открыта главная позиция).
            if timeout_hours is not None and timeout_hours > 0 and len(post_df) > timeout_hours:
                post_slice = post_df.iloc[-timeout_hours:]
                check_level = p_0382 if layer == "major" else p_0500
                touched_recent = (post_slice["low"] <= check_level).any() if is_long else (post_slice["high"] >= check_level).any()
                if not touched_recent:
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

            # Если импульс уже завершил свой цикл (вход + тейк 0.236) — закрыть и записать этот импульс
            if hit_tp:
                if symbol:
                    save_completed_impulse({
                        "symbol": symbol,
                        "peak_price": peak_val,
                        "imp_start_price": p_1000,
                        "imp_start_time": str(imp.start_time),
                        "imp_end_time": str(imp.end_time),
                        "exit_price": eff_tp,
                        "exit_time": str(post_df["timestamp"].iloc[-1]),
                        "exit_reason": "TP_0236",
                        "layer": layer,
                    })
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
                # Касание 0.500 было (уровень 0.500 пройден / налит)!
                # Проверяем, куда пришла цена:
                if not touched_0618:
                    # Уровень 0.500 пройден, цена не дошла до TP 0.236 и не касалась 0.618:
                    # Выставляем Ордер 2 и Ордер 3, тейк позиции на 0.236!
                    unbroken_setups.append(SetupSignal(
                        setup_type="TRIPLE_GRID_CORRECTION",
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
                        description=f"Уровень 0.500 (${e_0500:.4f}) налит, цена не дошла до тейка 0.236 (${tp_0236:.4f}) и не касалась 0.618 (${e_0618:.4f}). Выставляем Ордер 2 и Ордер 3.",
                        layer=layer,
                        p_0382=p_0382,
                        touched_0382=touched_0382,
                        o1_filled=True,
                        o2_filled=False,
                    ))
                elif not touched_0786:
                    # Уровни 0.500 и 0.618 пройдены, 0.786 не коснулись:
                    # Выставляем Ордер 3, тейк позиции на 0.382!
                    unbroken_setups.append(SetupSignal(
                        setup_type="TRIPLE_GRID_CORRECTION",
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
                        description=f"Уровни 0.500 (${e_0500:.4f}) и 0.618 (${e_0618:.4f}) налиты. Выставляем Ордер 3 (${e_0786:.4f}), тейк 0.382.",
                        layer=layer,
                        p_0382=p_0382,
                        touched_0382=touched_0382,
                        o1_filled=True,
                        o2_filled=True,
                    ))
                else:
                    # Все три ордера (0.500, 0.618, 0.786) упали ниже точки входа ->
                    # Ожидание манипуляции или ложного пробоя (1.000)
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
                        description=f"Все уровни сетки (0.500, 0.618, 0.786) пройдены. Ожидание пробоя 1.000 (${p_1000:.4f}) без возврата (маржа свободна).",
                        layer=layer,
                        p_0382=p_0382,
                        touched_0382=touched_0382,
                        o1_filled=True,
                        o2_filled=True,
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

"""Форматирование и отображение торговых данных (Rich-таблицы, панели)."""

from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from scripts.trader.config import TradeConfig
from scripts.trader.models import SetupSignal

console = Console()


def build_setup_table(
    symbol: str,
    layer_name: str,
    setup: SetupSignal,
    cfg: TradeConfig,
    cur_price: float,
    e1: float,
    tp1: float,
    e2: Optional[float],
    tp2: Optional[float],
    e3: Optional[float],
    tp3: Optional[float],
    sl: float,
    q1: float,
    q2: float,
    q3: float,
    loss1: float,
    loss2: float,
    loss3: float,
    tot_loss: float,
    setup_risk: float,
    client: object,
    is_major: bool = False,
    min_bars: Optional[int] = None,
    max_bars: Optional[int] = None,
    setup_timeout: int = 0,
) -> Table:
    """Строит Rich-таблицу с параметрами обнаруженного торгового сетапа."""
    layer_tag_title = f"[MAJOR FIB {min_bars}-{max_bars} свечей]" if is_major else f"[MINOR FIB <= {max_bars} свечей]"

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

    # Чистый R:R с учетом комиссий и проскальзывания
    fee_open = getattr(cfg, "fee_maker_pct", 0.02) / 100.0
    fee_close = getattr(cfg, "fee_taker_pct", 0.055) / 100.0
    slip = getattr(cfg, "slippage_buffer_pct", 0.10) / 100.0
    worst_sl = sl * (1.0 - slip) if getattr(setup, "side", "long") == "long" else sl * (1.0 + slip)
    net_reward = abs(tp1 - e1) - (e1 * fee_open + tp1 * fee_open)
    net_risk = abs(e1 - worst_sl) + (e1 * fee_open + worst_sl * fee_close)
    net_rr = net_reward / net_risk if net_risk > 0 else 0.0
    t.add_row("Чистый R:R (Ордер 1)", f"{net_rr:.2f} (Maker {fee_open*100:.2f}%, Taker {fee_close*100:.3f}%, Slip {slip*100:.2f}%)")

    if setup.be_trigger is not None and setup.be_price is not None:
        be_trig_str = f"${client.round_price(setup.be_trigger, symbol)}"
        be_price_str = f"${client.round_price(setup.be_price, symbol)}"
        t.add_row("Безубыток (БУ)", f"Триггер: {be_trig_str} ({cfg.reclaim_be_trigger_fib} Fib)  ->  Перенос SL в: {be_price_str}")
    t.add_row("Статус стратегии", f"[green]{setup.description}[/green]")

    return t

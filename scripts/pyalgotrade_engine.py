"""
Событийный движок бэктеста стратегии Fibonacci Grid на базе библиотеки PyAlgoTrade.
Служит альтернативным/эталонным симулятором (Sanity Check) для сравнения с кастомным движком `strategy_engine.py`.

Поддерживает:
  - Режимы: "solo_1" (только 0.500), "solo_2" (только 0.618), "dual" (последовательный 0.500 -> 0.618)
  - Схемы тейка: "classic" (0.236 / 0.382) и "fast" (0.382 / 0.500)
  - Правило One-and-Done (ранний тейк отменяет ордер 2)
  - Корзинный тейк (basket_tp)
  - Анализаторы доходности, просадки и Sharpe ratio PyAlgoTrade
"""
from __future__ import annotations

import datetime
from typing import Optional, Literal
import numpy as np
import pandas as pd

from pyalgotrade import strategy, bar
from pyalgotrade.barfeed import membf
from pyalgotrade.broker import backtesting
from pyalgotrade.stratanalyzer import returns, sharpe, drawdown

from scripts.strategy_engine import GridConfig, TradeResult, _calc_order_metrics
from scripts.backtest_strategy_interactive import Impulse, calc_fib


class DataFrameBarFeed(membf.BarFeed):
    """Адаптер для загрузки pandas.DataFrame в память PyAlgoTrade."""

    def __init__(self, frequency=bar.Frequency.HOUR, maxLen=None):
        super().__init__(frequency, maxLen)

    def barsHaveAdjClose(self) -> bool:
        return False

    def load_df(self, instrument: str, df: pd.DataFrame):
        bars = []
        has_ts = "timestamp" in df.columns
        for idx, row in df.iterrows():
            if has_ts:
                ts = pd.to_datetime(row["timestamp"])
                if ts.tzinfo is not None:
                    ts = ts.tz_localize(None)
            else:
                ts = datetime.datetime(2026, 1, 1) + datetime.timedelta(hours=int(idx))

            b = bar.BasicBar(
                dateTime=ts,
                open_=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
                adjClose=None,
                frequency=self.getFrequency(),
            )
            bars.append(b)
        self.addBarsFromSequence(instrument, bars)


class PyAlgoTradeGridStrategy(strategy.BacktestingStrategy):
    """Событийная реализация стратегии Fibonacci Grid на PyAlgoTrade."""

    def __init__(
        self,
        feed: DataFrameBarFeed,
        instrument: str,
        impulses: list[Impulse],
        config: GridConfig,
        initial_cash: float = 10000.0,
    ):
        super().__init__(feed, initial_cash)
        self._instrument = instrument
        self._impulses = impulses
        self._cfg = config
        self._trades: list[TradeResult] = []

        # Индекс текущего бара
        self._bar_idx = -1
        self._imp_idx = 0
        self._cur_imp: Optional[Impulse] = None
        self._last_exit_idx = -1

        # Состояние текущей сделки
        self._o1_filled = False
        self._o1_closed = False
        self._o2_filled = False
        self._o2_closed = False
        self._o2_active = True
        self._o1_pnl = 0.0
        self._o2_pnl = 0.0
        self._outcome = ""
        self._o1_entry_bar = -1
        self._o2_entry_bar = -1
        self._event_exit_idx = -1

        # Рассчитанные уровни цен
        self._p_e1 = 0.0
        self._p_e2 = 0.0
        self._p_sl = 0.0
        self._p_tp1 = 0.0
        self._p_tp2 = 0.0
        self._p_basket: Optional[float] = None
        self._qty1 = 0.0
        self._qty2 = 0.0
        self._gain1 = 0.0
        self._gain2 = 0.0
        self._gain1_basket = 0.0
        self._gain2_basket = 0.0

        # Комиссии брокера
        self.getBroker().setCommission(
            backtesting.TradePercentage(config.fee_maker)
        )

    def _select_next_impulse(self):
        """Выбирает следующий доступный импульс после выхода из предыдущей сделки."""
        while self._imp_idx < len(self._impulses):
            imp = self._impulses[self._imp_idx]
            self._imp_idx += 1
            if imp.end_idx > self._last_exit_idx and imp.end_idx >= self._bar_idx:
                self._cur_imp = imp
                self._init_impulse_levels(imp)
                return
        self._cur_imp = None

    def _init_impulse_levels(self, imp: Impulse):
        """Инициализация уровней ордеров для выбранного импульса."""
        cfg = self._cfg
        is_long = imp.is_long
        enable_o1 = (cfg.mode in ("dual", "solo_1")) and (cfg.entry_fib_1 is not None)
        enable_o2 = (cfg.mode in ("dual", "solo_2")) and (cfg.entry_fib_2 is not None)

        self._p_sl = calc_fib(imp.high, imp.low, cfg.sl_fib, is_long=is_long, scale="log")

        if enable_o1:
            tp1_lvl = cfg.tp_fib_1 if cfg.tp_fib_1 is not None else 0.236
            self._p_e1, self._p_tp1, _, self._qty1, self._gain1 = _calc_order_metrics(
                imp.high, imp.low, cfg.entry_fib_1, tp1_lvl, cfg.sl_fib,
                is_long, cfg.risk_per_order, cfg.fee_maker, cfg.fee_taker
            )
        else:
            self._p_e1 = self._p_tp1 = self._qty1 = self._gain1 = 0.0

        if enable_o2:
            tp2_lvl = cfg.tp_fib_2 if cfg.tp_fib_2 is not None else 0.382
            self._p_e2, self._p_tp2, _, self._qty2, self._gain2 = _calc_order_metrics(
                imp.high, imp.low, cfg.entry_fib_2, tp2_lvl, cfg.sl_fib,
                is_long, cfg.risk_per_order, cfg.fee_maker, cfg.fee_taker
            )
        else:
            self._p_e2 = self._p_tp2 = self._qty2 = self._gain2 = 0.0

        if cfg.basket_tp is not None and enable_o1 and enable_o2:
            self._p_basket = calc_fib(imp.high, imp.low, cfg.basket_tp, is_long=is_long, scale="log")
            if is_long:
                self._gain1_basket = (self._p_basket - self._p_e1) - self._p_e1 * cfg.fee_maker - self._p_basket * cfg.fee_maker
                self._gain2_basket = (self._p_basket - self._p_e2) - self._p_e2 * cfg.fee_maker - self._p_basket * cfg.fee_maker
            else:
                self._gain1_basket = (self._p_e1 - self._p_basket) - self._p_e1 * cfg.fee_maker - self._p_basket * cfg.fee_maker
                self._gain2_basket = (self._p_e2 - self._p_basket) - self._p_e2 * cfg.fee_maker - self._p_basket * cfg.fee_maker
        else:
            self._p_basket = None
            self._gain1_basket = self._gain2_basket = 0.0

        self._o1_filled = self._o1_closed = False
        self._o2_filled = self._o2_closed = False
        self._o2_active = enable_o2
        self._o1_pnl = self._o2_pnl = 0.0
        self._outcome = ""
        self._o1_entry_bar = self._o2_entry_bar = -1
        self._event_exit_idx = -1

    @staticmethod
    def _is_tp_hit(high: float, low: float, close: float, tp_level: float, is_long: bool, after_entry: bool) -> bool:
        if after_entry:
            return (high >= tp_level) if is_long else (low <= tp_level)
        return (close >= tp_level) if is_long else (close <= tp_level)

    def onBars(self, bars):
        self._bar_idx += 1
        k = self._bar_idx

        # Если текущего импульса нет, ищем следующий
        if self._cur_imp is None:
            self._select_next_impulse()
            if self._cur_imp is None:
                return

        imp = self._cur_imp
        # Ждем пока свеча начнется после импульса
        if k <= imp.end_idx:
            return

        bar_item = bars[self._instrument]
        h_k = bar_item.getHigh()
        l_k = bar_item.getLow()
        c_k = bar_item.getClose()
        o_k = bar_item.getOpen()

        is_long = imp.is_long
        cfg = self._cfg
        enable_o1 = (cfg.mode in ("dual", "solo_1")) and (cfg.entry_fib_1 is not None)
        enable_o2 = (cfg.mode in ("dual", "solo_2")) and (cfg.entry_fib_2 is not None)

        # Отмена при обновлении экстремума до входа
        if not self._o1_filled and not self._o2_filled:
            if (is_long and h_k > imp.high) or (not is_long and l_k < imp.low):
                self._cur_imp = None
                return

        sl_hit = (l_k <= self._p_sl) if is_long else (h_k >= self._p_sl)

        # 1. Режим solo_1
        if cfg.mode == "solo_1" or (enable_o1 and not enable_o2):
            if not self._o1_filled:
                hit = (l_k <= self._p_e1) if is_long else (h_k >= self._p_e1)
                if hit:
                    self._o1_filled = True
                    self._o1_entry_bar = k
            if not self._o1_filled:
                return

            tp_hit = self._is_tp_hit(h_k, l_k, c_k, self._p_tp1, is_long, k > self._o1_entry_bar)
            if sl_hit:
                self._record_trade(pnl=-cfg.risk_per_order, o1_pnl=-cfg.risk_per_order, outcome="SL1", exit_k=k)
            elif tp_hit:
                self._record_trade(pnl=self._qty1 * self._gain1, o1_pnl=self._qty1 * self._gain1, outcome="TP1", exit_k=k)

        # 2. Режим solo_2
        elif cfg.mode == "solo_2" or (enable_o2 and not enable_o1):
            if not self._o2_filled:
                hit = (l_k <= self._p_e2) if is_long else (h_k >= self._p_e2)
                if hit:
                    self._o2_filled = True
                    self._o2_entry_bar = k
            if not self._o2_filled:
                return

            tp_hit = self._is_tp_hit(h_k, l_k, c_k, self._p_tp2, is_long, k > self._o2_entry_bar)
            if sl_hit:
                self._record_trade(pnl=-cfg.risk_per_order, o2_pnl=-cfg.risk_per_order, outcome="SL2", exit_k=k)
            elif tp_hit:
                self._record_trade(pnl=self._qty2 * self._gain2, o2_pnl=self._qty2 * self._gain2, outcome="TP2", exit_k=k)

        # 3. Режим dual
        else:
            if not self._o1_filled:
                hit_1 = (l_k <= self._p_e1) if is_long else (h_k >= self._p_e1)
                if hit_1:
                    self._o1_filled = True
                    self._o1_entry_bar = k

                    tp1_closed = (c_k >= self._p_tp1) if is_long else (c_k <= self._p_tp1)
                    if tp1_closed:
                        self._record_trade(pnl=self._qty1 * self._gain1, o1_pnl=self._qty1 * self._gain1, outcome="TP1_only", exit_k=k)
                        return

                    hit_2 = (l_k <= self._p_e2) if is_long else (h_k >= self._p_e2)
                    if hit_2 and self._o2_active:
                        self._o2_filled = True
                        self._o2_entry_bar = k

            if not self._o1_filled:
                return

            if self._o1_filled and not self._o1_closed and self._o2_active and not self._o2_filled:
                hit_e2 = (l_k <= self._p_e2) if is_long else (h_k >= self._p_e2)
                tp1_hit = self._is_tp_hit(h_k, l_k, c_k, self._p_tp1, is_long, k > self._o1_entry_bar)

                if sl_hit:
                    self._record_trade(pnl=-cfg.risk_per_order * 2, o1_pnl=-cfg.risk_per_order, o2_pnl=-cfg.risk_per_order, outcome="SL_both", exit_k=k, both=True)
                    return
                elif tp1_hit and not hit_e2:
                    self._record_trade(pnl=self._qty1 * self._gain1, o1_pnl=self._qty1 * self._gain1, outcome="TP1_only", exit_k=k)
                    return
                elif hit_e2 and not tp1_hit:
                    self._o2_filled = True
                    self._o2_entry_bar = k
                elif hit_e2 and tp1_hit:
                    tp_first = (c_k < o_k) if is_long else (c_k > o_k)
                    if tp_first:
                        self._record_trade(pnl=self._qty1 * self._gain1, o1_pnl=self._qty1 * self._gain1, outcome="TP1_only", exit_k=k)
                        return
                    else:
                        self._o2_filled = True
                        self._o2_entry_bar = k

            # Сопровождение открытых позиций
            if self._o1_filled and not self._o1_closed:
                if cfg.basket_tp is not None and self._o2_filled:
                    b_bar = max(self._o1_entry_bar, self._o2_entry_bar)
                    tp_hit = self._is_tp_hit(h_k, l_k, c_k, self._p_basket, is_long, k > b_bar)
                    if sl_hit:
                        self._o1_closed = True
                        self._o1_pnl = -cfg.risk_per_order
                    elif tp_hit:
                        self._o1_closed = True
                        self._o1_pnl = self._qty1 * self._gain1_basket
                else:
                    tp_hit = self._is_tp_hit(h_k, l_k, c_k, self._p_tp1, is_long, k > self._o1_entry_bar)
                    if sl_hit:
                        self._o1_closed = True
                        self._o1_pnl = -cfg.risk_per_order
                    elif tp_hit:
                        self._o1_closed = True
                        self._o1_pnl = self._qty1 * self._gain1
                        if not self._o2_filled:
                            self._record_trade(pnl=self._o1_pnl, o1_pnl=self._o1_pnl, outcome="TP1_only", exit_k=k)
                            return

            if self._o2_filled and not self._o2_closed:
                if cfg.basket_tp is not None:
                    b_bar = max(self._o1_entry_bar, self._o2_entry_bar)
                    tp_hit = self._is_tp_hit(h_k, l_k, c_k, self._p_basket, is_long, k > b_bar)
                    if sl_hit:
                        self._o2_closed = True
                        self._o2_pnl = -cfg.risk_per_order
                    elif tp_hit:
                        self._o2_closed = True
                        self._o2_pnl = self._qty2 * self._gain2_basket
                else:
                    tp_hit = self._is_tp_hit(h_k, l_k, c_k, self._p_tp2, is_long, k > self._o2_entry_bar)
                    if sl_hit:
                        self._o2_closed = True
                        self._o2_pnl = -cfg.risk_per_order
                    elif tp_hit:
                        self._o2_closed = True
                        self._o2_pnl = self._qty2 * self._gain2

            all_done = False
            if self._o1_filled and not self._o2_active:
                all_done = self._o1_closed
            elif self._o1_filled and self._o2_filled:
                all_done = self._o1_closed and self._o2_closed
            elif not self._o1_filled and self._o2_filled:
                all_done = self._o2_closed

            if all_done:
                tot_pnl = self._o1_pnl + self._o2_pnl
                outcome = "Basket" if (cfg.basket_tp is not None and self._o1_filled and self._o2_filled and tot_pnl > 0) else ("TP1+TP2" if tot_pnl > 0 else "SL_both")
                self._record_trade(
                    pnl=tot_pnl,
                    o1_pnl=self._o1_pnl,
                    o2_pnl=self._o2_pnl,
                    outcome=outcome,
                    exit_k=k,
                    both=(self._o1_filled and self._o2_filled),
                )

    def _record_trade(
        self,
        pnl: float,
        outcome: str,
        exit_k: int,
        o1_pnl: float = 0.0,
        o2_pnl: float = 0.0,
        both: bool = False,
    ):
        """Сохранение сделки и сброс состояния."""
        first_entry = self._o1_entry_bar if self._o1_filled else self._o2_entry_bar
        if self._o1_filled and self._o2_filled:
            first_entry = min(self._o1_entry_bar, self._o2_entry_bar)

        trade = TradeResult(
            pnl=round(pnl, 4),
            win=(pnl > 0),
            o1_pnl=round(o1_pnl, 4),
            o2_pnl=round(o2_pnl, 4),
            both_entered=both or (self._o1_filled and self._o2_filled),
            only_o1=(self._o1_filled and not self._o2_filled),
            only_o2=(self._o2_filled and not self._o1_filled),
            outcome=outcome,
            exit_idx=exit_k,
            entry_idx=first_entry,
            side="long" if (self._cur_imp and self._cur_imp.is_long) else "short",
            hold_bars=exit_k - first_entry if (exit_k >= 0 and first_entry >= 0) else 0,
        )
        self._trades.append(trade)
        self._last_exit_idx = exit_k
        self._cur_imp = None

    def get_trades(self) -> list[TradeResult]:
        return self._trades


def run_pyalgotrade_backtest(
    df: pd.DataFrame,
    impulses: list[Impulse],
    config: Optional[GridConfig] = None,
    symbol: str = "ASSET",
    initial_cash: float = 10000.0,
) -> tuple[list[TradeResult], dict]:
    """
    Запуск бэктеста через движок PyAlgoTrade.
    Возвращает список завершенных сделок и сводку метрик (Returns, Sharpe, DrawDown).
    """
    cfg = config if config is not None else GridConfig()
    feed = DataFrameBarFeed(bar.Frequency.HOUR)
    feed.load_df(symbol, df)

    strat = PyAlgoTradeGridStrategy(feed, symbol, impulses, cfg, initial_cash)

    # Подключение анализаторов
    ret_analyzer = returns.Returns()
    strat.attachAnalyzer(ret_analyzer)
    sharpe_analyzer = sharpe.SharpeRatio()
    strat.attachAnalyzer(sharpe_analyzer)
    dd_analyzer = drawdown.DrawDown()
    strat.attachAnalyzer(dd_analyzer)

    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        strat.run()

        trades = strat.get_trades()
        metrics = {
            "final_equity": strat.getResult(),
            "total_return_pct": ret_analyzer.getCumulativeReturns()[-1] * 100.0 if len(ret_analyzer.getCumulativeReturns()) > 0 else 0.0,
            "max_drawdown_pct": dd_analyzer.getMaxDrawDown() * 100.0,
            "sharpe_ratio": sharpe_analyzer.getSharpeRatio(0.0) if sharpe_analyzer.getSharpeRatio(0.0) is not None else 0.0,
            "n_trades": len(trades),
        }

    return trades, metrics

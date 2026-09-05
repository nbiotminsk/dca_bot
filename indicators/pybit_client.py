"""Bybit V5 Unified Trading API wrapper for order placement and market data."""

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv
from pybit.unified_trading import HTTP


@dataclass
class InstrumentSpecs:
    symbol: str
    tick_size: float
    price_decimals: int
    qty_step: float
    qty_decimals: int
    min_qty: float
    max_qty: float
    min_notional: float


def get_decimals(step: float) -> int:
    """Определяет количество знаков после запятой по шагу."""
    step_str = f"{step:.10f}".rstrip("0")
    if "." in step_str:
        return len(step_str.split(".")[1])
    return 0


def round_price_step(price: float, tick_size: float, decimals: int) -> float:
    """Округляет цену до ближайшего допустимого тика."""
    if tick_size <= 0:
        return round(price, decimals)
    ticks = round(price / tick_size)
    return round(ticks * tick_size, decimals)


def round_qty_step(qty: float, qty_step: float, decimals: int) -> float:
    """Округляет объем вниз до допустимого шага лота."""
    if qty_step <= 0:
        return round(qty, decimals)
    steps = math.floor(qty / qty_step + 1e-9)
    return round(steps * qty_step, decimals)


class BybitClient:
    """Обертка над Bybit V5 Unified Trading API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: Optional[bool] = None,
        demo: Optional[bool] = None,
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("BYBIT_API_KEY", "").strip()
        self.api_secret = api_secret or os.getenv("BYBIT_API_SECRET", "").strip()

        if testnet is None:
            t_str = os.getenv("BYBIT_TESTNET", "false").strip().lower()
            self.testnet = t_str in ("true", "1", "yes")
        else:
            self.testnet = testnet

        if demo is None:
            d_str = os.getenv("BYBIT_DEMO", "false").strip().lower()
            self.demo = d_str in ("true", "1", "yes")
        else:
            self.demo = demo

        self.session = HTTP(
            testnet=self.testnet,
            demo=self.demo,
            api_key=self.api_key,
            api_secret=self.api_secret,
            force_retry=True,
            max_retries=10,
            retry_delay=3,
            timeout=20,
        )
        self._specs_cache: dict[str, InstrumentSpecs] = {}
        self._position_idx_cache: dict[str, int] = {}
        self._last_request_time: float = 0.0
        self._min_request_interval: float = 0.08  # ~12.5 req/sec max to avoid Bybit 10006
        self._klines_cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
        self._klines_cache_ttl: float = 8.0  # 8.0s TTL
        self._positions_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._positions_cache_ttl: float = 3.0  # 3.0s TTL
        self._ticker_cache: dict[str, tuple[float, float]] = {}
        self._ticker_cache_ttl: float = 3.0  # 3.0s TTL
        self._balance_cache: Optional[tuple[float, float]] = None
        self._balance_cache_ttl: float = 5.0  # 5.0s TTL

    def _throttle(self, min_interval: Optional[float] = None) -> None:
        """Ограничивает частоту исходящих запросов к Bybit API во избежание 10006 Rate Limit."""
        interval = min_interval if min_interval is not None else self._min_request_interval
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_time = time.time()

    def get_position_idx(self, symbol: str, side: str = "Buy") -> int:
        """
        Определяет positionIdx для символа:
        0 = One-Way Mode
        1 = Long (Buy) в Hedge Mode
        2 = Short (Sell) в Hedge Mode
        """
        sym = symbol.upper()
        if sym not in self._position_idx_cache:
            try:
                self._throttle()
                resp = self.session.get_positions(category="linear", symbol=sym)
                positions = resp.get("result", {}).get("list", [])
                has_hedge = any(p.get("positionIdx") in (1, 2) for p in positions)
                self._position_idx_cache[sym] = 1 if has_hedge else 0
            except Exception:
                return 0

        is_hedge = (self._position_idx_cache[sym] == 1)
        if not is_hedge:
            return 0
        return 1 if side.lower() in ("buy", "long") else 2

    def get_specs(self, symbol: str) -> InstrumentSpecs:
        """Получает и кэширует спецификацию инструмента."""
        sym = symbol.upper()
        if sym in self._specs_cache:
            return self._specs_cache[sym]

        self._throttle()
        resp = self.session.get_instruments_info(category="linear", symbol=sym)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
            raise ValueError(f"Ошибка получения спецификации {sym}: {resp.get('retMsg')}")

        items = resp.get("result", {}).get("list", [])
        if not items:
            raise ValueError(f"Инструмент {sym} не найден в linear!")

        info = items[0]
        p_filter = info.get("priceFilter", {})
        l_filter = info.get("lotSizeFilter", {})

        tick_size = float(p_filter.get("tickSize", "0.01"))
        qty_step = float(l_filter.get("qtyStep", "0.01"))
        min_qty = float(l_filter.get("minOrderQty", "0.01"))
        max_qty = float(l_filter.get("maxOrderQty", "1000000.0"))
        min_notional = float(l_filter.get("minNotionalValue", "5.0"))

        specs = InstrumentSpecs(
            symbol=sym,
            tick_size=tick_size,
            price_decimals=get_decimals(tick_size),
            qty_step=qty_step,
            qty_decimals=get_decimals(qty_step),
            min_qty=min_qty,
            max_qty=max_qty,
            min_notional=min_notional,
        )
        self._specs_cache[sym] = specs
        return specs

    def fetch_klines(self, symbol: str, interval: str = "60", limit: int = 100) -> pd.DataFrame:
        """
        Загружает исторические свечи Bybit в хронологическом порядке.
        interval: 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, M, W
        """
        sym = symbol.upper()
        cache_key = (sym, str(interval))
        now = time.time()
        if cache_key in self._klines_cache:
            cached_time, cached_df = self._klines_cache[cache_key]
            if (now - cached_time) < self._klines_cache_ttl and len(cached_df) >= limit:
                return cached_df.tail(limit).copy()

        self._throttle()
        fetch_limit = max(limit, 140)
        resp = self.session.get_kline(category="linear", symbol=sym, interval=str(interval), limit=fetch_limit)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
            raise ValueError(f"Ошибка загрузки kline для {sym}: {resp.get('retMsg')}")

        raw_list = resp.get("result", {}).get("list", [])
        if not raw_list:
            raise ValueError(f"Пустой список свечей для {sym}!")

        # Bybit отдает свечи от новых к старым -> разворачиваем
        raw_list = raw_list[::-1]

        data = []
        for r in raw_list:
            # [startTime, open, high, low, close, volume, turnover]
            ts = pd.to_datetime(int(r[0]), unit="ms", utc=True)
            data.append(
                {
                    "timestamp": ts,
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
            )

        df = pd.DataFrame(data)
        self._klines_cache[cache_key] = (time.time(), df)
        return df.tail(limit).copy()

    def round_price(self, price: float, symbol: str) -> float:
        specs = self.get_specs(symbol)
        return round_price_step(price, specs.tick_size, specs.price_decimals)

    def round_qty(self, qty: float, symbol: str) -> float:
        specs = self.get_specs(symbol)
        return round_qty_step(qty, specs.qty_step, specs.qty_decimals)

    def calc_dual_grid_order_sizes(
        self,
        p_entry1: float,
        p_entry2: float,
        p_sl: float,
        symbol: str,
        total_risk_usd: float = 2.0,
        is_long: bool = True,
        equal_weight: bool = True,
    ) -> tuple[float, float, float, float]:
        """
        Рассчитывает объемы ордеров 1 и 2 так, чтобы суммарный риск при выбивании стопа был равен total_risk_usd.
        Возвращает: (qty1, qty2, loss1_est, loss2_est).
        """
        specs = self.get_specs(symbol)
        risk1 = total_risk_usd / 2.0 if equal_weight else total_risk_usd
        risk2 = total_risk_usd / 2.0 if equal_weight else total_risk_usd

        dist1 = abs(p_entry1 - p_sl)
        dist2 = abs(p_entry2 - p_sl)

        raw_q1 = risk1 / dist1 if dist1 > 0 else 0.0
        raw_q2 = risk2 / dist2 if dist2 > 0 else 0.0

        q1 = max(specs.min_qty, self.round_qty(raw_q1, symbol)) if raw_q1 > 0 else 0.0
        q2 = max(specs.min_qty, self.round_qty(raw_q2, symbol)) if raw_q2 > 0 else 0.0

        loss1 = q1 * dist1
        loss2 = q2 * dist2
        return q1, q2, loss1, loss2

    def calc_triple_grid_order_sizes(
        self,
        p_entry1: float,
        p_entry2: float,
        p_entry3: float,
        p_sl: float,
        symbol: str,
        total_risk_usd: float = 2.0,
        equal_weight: bool = True,
        weights: Optional[list[float]] = None,
    ) -> tuple[float, float, float, float, float, float]:
        """
        Рассчитывает объемы ордеров 1, 2 и 3 так, чтобы суммарный риск при выбивании стопа был равен total_risk_usd.
        Если weights задан (например, [0.50, 0.30, 0.20]), объемы (notional) распределяются в данной пропорции:
        50% на Ордер 1 (0.500), 30% на Ордер 2 (0.618), 20% на Ордер 3 (0.786).
        Возвращает: (qty1, qty2, qty3, loss1_est, loss2_est, loss3_est).
        """
        specs = self.get_specs(symbol)
        dist1 = abs(p_entry1 - p_sl)
        dist2 = abs(p_entry2 - p_sl)
        dist3 = abs(p_entry3 - p_sl)

        if weights is not None and len(weights) == 3 and not equal_weight:
            w1, w2, w3 = float(weights[0]), float(weights[1]), float(weights[2])
            tot_w = w1 + w2 + w3
            if tot_w > 0:
                w1, w2, w3 = w1 / tot_w, w2 / tot_w, w3 / tot_w
            # Суммарный номинал TotalNotional = N
            # Номиналы: N1 = w1 * N, N2 = w2 * N, N3 = w3 * N
            # Лоты: raw_q_i = N_i / p_entry_i = (w_i / p_entry_i) * N
            # Убыток: Loss_i = raw_q_i * dist_i = N * (w_i * dist_i / p_entry_i)
            # Суммарный убыток = N * sum(w_i * dist_i / p_entry_i) = total_risk_usd
            # Отсюда N = total_risk_usd / sum(w_i * dist_i / p_entry_i)
            loss_per_notional = 0.0
            if p_entry1 > 0 and dist1 > 0:
                loss_per_notional += w1 * (dist1 / p_entry1)
            if p_entry2 > 0 and dist2 > 0:
                loss_per_notional += w2 * (dist2 / p_entry2)
            if p_entry3 > 0 and dist3 > 0:
                loss_per_notional += w3 * (dist3 / p_entry3)

            if loss_per_notional > 0:
                total_notional = total_risk_usd / loss_per_notional
                raw_q1 = (total_notional * w1) / p_entry1 if p_entry1 > 0 else 0.0
                raw_q2 = (total_notional * w2) / p_entry2 if p_entry2 > 0 else 0.0
                raw_q3 = (total_notional * w3) / p_entry3 if p_entry3 > 0 else 0.0
            else:
                raw_q1 = raw_q2 = raw_q3 = 0.0
        else:
            risk_per_order = total_risk_usd / 3.0
            raw_q1 = risk_per_order / dist1 if dist1 > 0 else 0.0
            raw_q2 = risk_per_order / dist2 if dist2 > 0 else 0.0
            raw_q3 = risk_per_order / dist3 if dist3 > 0 else 0.0

        min_q1 = specs.min_qty
        min_q2 = specs.min_qty
        min_q3 = specs.min_qty

        if specs.min_notional > 0:
            if p_entry1 > 0:
                req1 = round(math.ceil((specs.min_notional / p_entry1) / specs.qty_step - 1e-9) * specs.qty_step, specs.qty_decimals)
                min_q1 = max(min_q1, req1)
            if p_entry2 > 0:
                req2 = round(math.ceil((specs.min_notional / p_entry2) / specs.qty_step - 1e-9) * specs.qty_step, specs.qty_decimals)
                min_q2 = max(min_q2, req2)
            if p_entry3 > 0:
                req3 = round(math.ceil((specs.min_notional / p_entry3) / specs.qty_step - 1e-9) * specs.qty_step, specs.qty_decimals)
                min_q3 = max(min_q3, req3)

        q1 = max(min_q1, self.round_qty(raw_q1, symbol)) if raw_q1 > 0 else 0.0
        q2 = max(min_q2, self.round_qty(raw_q2, symbol)) if raw_q2 > 0 else 0.0
        q3 = max(min_q3, self.round_qty(raw_q3, symbol)) if raw_q3 > 0 else 0.0

        loss1 = q1 * dist1
        loss2 = q2 * dist2
        loss3 = q3 * dist3
        return q1, q2, q3, loss1, loss2, loss3

    def calc_residual_order_sizes(
        self,
        current_pos_size: float,
        current_pos_avg_price: float,
        p_entry2: Optional[float],
        p_entry3: Optional[float],
        p_sl: float,
        symbol: str,
        total_risk_usd: float = 2.0,
        weights: Optional[list[float]] = None,
    ) -> tuple[float, float, float, float, float]:
        """
        Рассчитывает объемы Ордеров 2 и 3 с учетом уже открытой позиции и задействованного риска.
        Суммарный риск: CurrentRisk + Loss2 + Loss3 <= total_risk_usd.
        Возвращает: (q2, q3, current_risk, loss2_est, loss3_est).
        """
        specs = self.get_specs(symbol)
        current_risk = current_pos_size * abs(current_pos_avg_price - p_sl) if current_pos_size > 0 else 0.0
        remaining_risk = max(0.0, total_risk_usd - current_risk)

        if remaining_risk <= 0.05 or (p_entry2 is None and p_entry3 is None):
            return 0.0, 0.0, current_risk, 0.0, 0.0

        dist2 = abs(p_entry2 - p_sl) if (p_entry2 is not None and p_entry2 > 0) else 0.0
        dist3 = abs(p_entry3 - p_sl) if (p_entry3 is not None and p_entry3 > 0) else 0.0

        # Если заданы веса (например, w2=0.30, w3=0.20):
        if weights is not None and len(weights) == 3 and p_entry2 is not None and p_entry3 is not None:
            w2, w3 = float(weights[1]), float(weights[2])
            tot_w = w2 + w3
            if tot_w > 0:
                w2, w3 = w2 / tot_w, w3 / tot_w
            loss_per_notional = 0.0
            if p_entry2 > 0 and dist2 > 0:
                loss_per_notional += w2 * (dist2 / p_entry2)
            if p_entry3 > 0 and dist3 > 0:
                loss_per_notional += w3 * (dist3 / p_entry3)

            if loss_per_notional > 0:
                tot_n = remaining_risk / loss_per_notional
                raw_q2 = (tot_n * w2) / p_entry2
                raw_q3 = (tot_n * w3) / p_entry3
            else:
                raw_q2 = raw_q3 = 0.0
        else:
            num_orders = (1 if p_entry2 is not None else 0) + (1 if p_entry3 is not None else 0)
            risk_each = remaining_risk / num_orders
            raw_q2 = (risk_each / dist2) if dist2 > 0 else 0.0
            raw_q3 = (risk_each / dist3) if dist3 > 0 else 0.0

        q2 = loss2 = 0.0
        if p_entry2 is not None and p_entry2 > 0 and raw_q2 > 0:
            min_q2 = specs.min_qty
            if specs.min_notional > 0:
                req2 = round(math.ceil((specs.min_notional / p_entry2) / specs.qty_step - 1e-9) * specs.qty_step, specs.qty_decimals)
                min_q2 = max(min_q2, req2)
            q2 = max(min_q2, self.round_qty(raw_q2, symbol))
            loss2 = q2 * dist2

        q3 = loss3 = 0.0
        if p_entry3 is not None and p_entry3 > 0 and raw_q3 > 0:
            min_q3 = specs.min_qty
            if specs.min_notional > 0:
                req3 = round(math.ceil((specs.min_notional / p_entry3) / specs.qty_step - 1e-9) * specs.qty_step, specs.qty_decimals)
                min_q3 = max(min_q3, req3)
            q3 = max(min_q3, self.round_qty(raw_q3, symbol))
            loss3 = q3 * dist3

        return q2, q3, current_risk, loss2, loss3

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        reduce_only: bool = False,
        order_filter: str = "Order",
        tpsl_mode: str = "Partial",
        order_link_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Размещает ордер на Bybit V5."""
        specs = self.get_specs(symbol)
        qty_str = f"{self.round_qty(qty, symbol):.{specs.qty_decimals}f}"

        pos_idx = self.get_position_idx(symbol, side)
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol.upper(),
            "side": side,
            "orderType": order_type,
            "qty": qty_str,
            "timeInForce": "GTC",
            "orderFilter": order_filter,
            "tpslMode": tpsl_mode,
            "positionIdx": pos_idx,
        }
        if order_link_id:
            params["orderLinkId"] = order_link_id
        if price is not None:
            params["price"] = f"{self.round_price(price, symbol):.{specs.price_decimals}f}"
        if take_profit is not None:
            params["takeProfit"] = f"{self.round_price(take_profit, symbol):.{specs.price_decimals}f}"
            params["tpTriggerBy"] = "MarkPrice"
        if stop_loss is not None:
            params["stopLoss"] = f"{self.round_price(stop_loss, symbol):.{specs.price_decimals}f}"
            params["slTriggerBy"] = "MarkPrice"
        if reduce_only:
            params["reduceOnly"] = True

        self._throttle()
        self._positions_cache.pop(symbol.upper(), None)
        self._balance_cache = None
        resp = self.session.place_order(**params)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
            ret_msg = str(resp.get("retMsg", ""))
            if order_link_id and ("orderlinkid" in ret_msg.lower() or "duplicate" in ret_msg.lower() or ret_code in (10001, 110007)):
                open_ords = self.get_open_orders(symbol)
                for o in open_ords:
                    if o.get("orderLinkId") == order_link_id:
                        return o
            raise RuntimeError(f"Bybit place_order failed ({ret_code}): {resp.get('retMsg')}")
        return resp.get("result", {})

    def amend_order(
        self,
        symbol: str,
        order_id: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> dict[str, Any]:
        """Изменяет активный ордер на Bybit V5 (Trailing)."""
        specs = self.get_specs(symbol)
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol.upper(),
            "orderId": order_id,
        }
        if qty is not None:
            params["qty"] = f"{self.round_qty(qty, symbol):.{specs.qty_decimals}f}"
        if price is not None:
            params["price"] = f"{self.round_price(price, symbol):.{specs.price_decimals}f}"
        if take_profit is not None:
            params["takeProfit"] = f"{self.round_price(take_profit, symbol):.{specs.price_decimals}f}"
        if stop_loss is not None:
            params["stopLoss"] = f"{self.round_price(stop_loss, symbol):.{specs.price_decimals}f}"

        self._throttle()
        self._positions_cache.pop(symbol.upper(), None)
        resp = self.session.amend_order(**params)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
            raise RuntimeError(f"Bybit amend_order failed ({ret_code}): {resp.get('retMsg')}")
        return resp.get("result", {})

    def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Отменяет ордер."""
        self._throttle()
        self._positions_cache.pop(symbol.upper(), None)
        resp = self.session.cancel_order(category="linear", symbol=symbol.upper(), orderId=order_id)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
            raise RuntimeError(f"Bybit cancel_order failed ({ret_code}): {resp.get('retMsg')}")
        return resp.get("result", {})

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Получает список открытых ордеров по символу."""
        self._throttle()
        resp = self.session.get_open_orders(category="linear", symbol=symbol.upper())
        return resp.get("result", {}).get("list", [])

    def get_position(self, symbol: str, side: str = "Buy") -> Optional[dict[str, Any]]:
        """Возвращает открытую позицию по символу или None если позиции нет."""
        sym = symbol.upper()
        now = time.time()
        if sym in self._positions_cache and (now - self._positions_cache[sym][0]) < self._positions_cache_ttl:
            positions = self._positions_cache[sym][1]
        else:
            self._throttle()
            resp = self.session.get_positions(category="linear", symbol=sym)
            positions = resp.get("result", {}).get("list", [])
            self._positions_cache[sym] = (time.time(), positions)

        target_idx = self.get_position_idx(symbol, side)
        for pos in positions:
            if float(pos.get("size", "0")) > 0:
                if target_idx == 0 or pos.get("positionIdx") == target_idx:
                    return pos
        return None

    def cancel_all_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Отменяет все открытые ордера по символу. Возвращает список отмененных."""
        orders = self.get_open_orders(symbol)
        results = []
        for order in orders:
            oid = order.get("orderId", "")
            if oid:
                try:
                    results.append(self.cancel_order(symbol, oid))
                except Exception:
                    pass
        return results

    def set_position_tp_sl(
        self,
        symbol: str,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        position_idx: Optional[int] = None,
        tp_trigger_by: str = "MarkPrice",
        sl_trigger_by: str = "MarkPrice",
    ) -> dict[str, Any]:
        """Устанавливает или изменяет Take-Profit и/или Stop-Loss для открытой позиции на Bybit V5."""
        if position_idx is None:
            position_idx = self.get_position_idx(symbol, "Buy")
        specs = self.get_specs(symbol)
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol.upper(),
            "positionIdx": position_idx,
        }
        if take_profit is not None:
            params["takeProfit"] = f"{self.round_price(take_profit, symbol):.{specs.price_decimals}f}"
            params["tpTriggerBy"] = tp_trigger_by
        if stop_loss is not None:
            params["stopLoss"] = f"{self.round_price(stop_loss, symbol):.{specs.price_decimals}f}"
            params["slTriggerBy"] = sl_trigger_by

        self._throttle()
        self._positions_cache.pop(symbol.upper(), None)
        resp = self.session.set_trading_stop(**params)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
            raise RuntimeError(f"Bybit set_trading_stop failed ({ret_code}): {resp.get('retMsg')}")
        return resp.get("result", {})

    def set_position_stop_loss(
        self,
        symbol: str,
        stop_loss: float,
        position_idx: Optional[int] = None,
        sl_trigger_by: str = "MarkPrice",
    ) -> dict[str, Any]:
        """Устанавливает или изменяет Stop-Loss для открытой позиции на Bybit V5."""
        return self.set_position_tp_sl(
            symbol=symbol,
            stop_loss=stop_loss,
            position_idx=position_idx,
            sl_trigger_by=sl_trigger_by,
        )

    def get_order_status(self, symbol: str, order_id: str) -> Optional[dict[str, Any]]:
        """Проверяет статус ордера: сначала в открытых, затем в истории."""
        open_orders = self.get_open_orders(symbol)
        for o in open_orders:
            if o.get("orderId") == order_id:
                return o
        try:
            self._throttle()
            resp = self.session.get_order_history(category="linear", symbol=symbol.upper(), orderId=order_id)
            if resp.get("retCode", 0) == 0:
                orders = resp.get("result", {}).get("list", [])
                if orders:
                    return orders[0]
        except Exception:
            pass
        return None

    def update_stop_loss(self, symbol: str, order_id: Optional[str], stop_loss: float) -> bool:
        """Передвигает Stop-Loss позиции или активного лимитного ордера в безубыток."""
        # 1. Пробуем передвинуть стоп у позиции
        try:
            self.set_position_stop_loss(symbol, stop_loss)
            return True
        except Exception:
            pass
        # 2. Если позиция еще не открыта (висит лимитка), изменяем стоп у ордера
        if order_id:
            try:
                self.amend_order(symbol, order_id, stop_loss=stop_loss)
                return True
            except Exception:
                pass
        return False

    def get_available_balance(self) -> float:
        """
        Возвращает доступную свободную маржу на Unified Trading Account (в USD/USDT).
        """
        now = time.time()
        if self._balance_cache is not None and (now - self._balance_cache[0]) < self._balance_cache_ttl:
            return self._balance_cache[1]

        try:
            self._throttle()
            resp = self.session.get_wallet_balance(accountType="UNIFIED")
            if resp.get("retCode", 0) == 0:
                acc_list = resp.get("result", {}).get("list", [])
                if acc_list:
                    tot_avail = acc_list[0].get("totalAvailableBalance")
                    if tot_avail is not None and str(tot_avail).strip():
                        val = float(tot_avail)
                        self._balance_cache = (time.time(), val)
                        return val
                    # fallback к монете USDT
                    for coin_info in acc_list[0].get("coin", []):
                        if coin_info.get("coin") == "USDT":
                            w_avail = coin_info.get("availableToWithdraw") or coin_info.get("walletBalance")
                            if w_avail:
                                val = float(w_avail)
                                self._balance_cache = (time.time(), val)
                                return val
        except Exception:
            pass
        return 0.0

    def get_symbol_leverage(self, symbol: str) -> float:
        """
        Возвращает текущее кредитное плечо для символа (по умолчанию 10.0).
        """
        sym = symbol.upper()
        if not hasattr(self, "_leverage_cache"):
            self._leverage_cache: dict[str, float] = {}
        if sym in self._leverage_cache:
            return self._leverage_cache[sym]

        try:
            self._throttle()
            resp = self.session.get_positions(category="linear", symbol=sym)
            if resp.get("retCode", 0) == 0:
                p_list = resp.get("result", {}).get("list", [])
                if p_list:
                    lev_str = p_list[0].get("leverage", "10")
                    lev = float(lev_str) if float(lev_str) > 0 else 10.0
                    self._leverage_cache[sym] = lev
                    return lev
        except Exception:
            pass
        self._leverage_cache[sym] = 10.0
        return 10.0

    def calc_required_margin(self, symbol: str, qty: float, price: float) -> float:
        """
        Рассчитывает ориентировочную начальную маржу для ордера: (qty * price) / leverage.
        """
        if qty <= 0 or price <= 0:
            return 0.0
        leverage = self.get_symbol_leverage(symbol)
        return (qty * price) / max(1.0, leverage)

    def get_ticker_price(self, symbol: str) -> float:
        """
        Возвращает текущую рыночную цену (lastPrice) инструмента.
        """
        sym = symbol.upper()
        now = time.time()
        if sym in self._ticker_cache and (now - self._ticker_cache[sym][0]) < self._ticker_cache_ttl:
            return self._ticker_cache[sym][1]

        try:
            self._throttle()
            resp = self.session.get_tickers(category="linear", symbol=sym)
            if resp.get("retCode", 0) == 0:
                t_list = resp.get("result", {}).get("list", [])
                if t_list:
                    p = t_list[0].get("lastPrice")
                    if p:
                        price = float(p)
                        self._ticker_cache[sym] = (time.time(), price)
                        return price
        except Exception:
            pass
        return 0.0

"""Bybit V5 Unified Trading API wrapper for order placement and market data."""

import math
import os
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
        )
        self._specs_cache: dict[str, InstrumentSpecs] = {}
        self._position_idx_cache: dict[str, int] = {}

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
        resp = self.session.get_kline(category="linear", symbol=sym, interval=str(interval), limit=limit)
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
        return df

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
        total_risk_usd: float = 2.0,
        symbol: str = "ZECUSDT",
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
        total_risk_usd: float = 2.0,
        symbol: str = "ZECUSDT",
        equal_weight: bool = True,
    ) -> tuple[float, float, float, float, float, float]:
        """
        Рассчитывает объемы ордеров 1, 2 и 3 так, чтобы суммарный риск при выбивании стопа был равен total_risk_usd.
        Возвращает: (qty1, qty2, qty3, loss1_est, loss2_est, loss3_est).
        """
        specs = self.get_specs(symbol)
        risk_per_order = total_risk_usd / 3.0 if equal_weight else total_risk_usd / 3.0

        dist1 = abs(p_entry1 - p_sl)
        dist2 = abs(p_entry2 - p_sl)
        dist3 = abs(p_entry3 - p_sl)

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

        resp = self.session.place_order(**params)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
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

        resp = self.session.amend_order(**params)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
            raise RuntimeError(f"Bybit amend_order failed ({ret_code}): {resp.get('retMsg')}")
        return resp.get("result", {})

    def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Отменяет ордер."""
        resp = self.session.cancel_order(category="linear", symbol=symbol.upper(), orderId=order_id)
        ret_code = resp.get("retCode", 0)
        if ret_code != 0:
            raise RuntimeError(f"Bybit cancel_order failed ({ret_code}): {resp.get('retMsg')}")
        return resp.get("result", {})

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Получает список открытых ордеров по символу."""
        resp = self.session.get_open_orders(category="linear", symbol=symbol.upper())
        return resp.get("result", {}).get("list", [])

    def get_position(self, symbol: str, side: str = "Buy") -> Optional[dict[str, Any]]:
        """Возвращает открытую позицию по символу или None если позиции нет."""
        resp = self.session.get_positions(category="linear", symbol=symbol.upper())
        positions = resp.get("result", {}).get("list", [])
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

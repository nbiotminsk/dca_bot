import uuid
from typing import Any, Literal, Optional

from rich.console import Console

from scripts.trader.models import ActiveTradeMonitor

console = Console()


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

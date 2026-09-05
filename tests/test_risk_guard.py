import pytest
from unittest.mock import MagicMock
from indicators.pybit_client import BybitClient, InstrumentSpecs


@pytest.fixture
def mock_client():
    client = BybitClient(api_key="test", api_secret="test", testnet=True)
    specs = InstrumentSpecs(
        symbol="SUIUSDT",
        tick_size=0.0001,
        price_decimals=4,
        qty_step=0.1,
        qty_decimals=1,
        min_qty=0.1,
        max_qty=10000.0,
        min_notional=5.0,
    )
    client._specs_cache["SUIUSDT"] = specs
    return client


def test_calc_residual_order_sizes_partial_risk(mock_client):
    """
    Лимит $2.00.
    Уже в позиции: 40 SUI @ 0.7790. Стоп: 0.7631.
    Риск открытой позиции = 40 * (0.7790 - 0.7631) = 40 * 0.0159 = $0.636.
    Остаток риска = $2.00 - $0.636 = $1.364.
    Ордер 2 (0.7754) и Ордер 3 (0.7703) должны получить не более ~$0.682 риска каждый.
    """
    p_sl = 0.7631
    q2, q3, cur_risk, loss2, loss3 = mock_client.calc_residual_order_sizes(
        current_pos_size=40.0,
        current_pos_avg_price=0.7790,
        p_entry2=0.7754,
        p_entry3=0.7703,
        p_sl=p_sl,
        total_risk_usd=2.0,
        symbol="SUIUSDT",
    )

    assert cur_risk == pytest.approx(0.636, rel=1e-2)
    assert q2 > 0
    assert q3 > 0
    tot_risk = cur_risk + loss2 + loss3
    assert tot_risk <= 2.05


def test_calc_residual_order_sizes_exhausted_risk(mock_client):
    """
    Лимит $2.00.
    Уже в позиции крупный объем (риск $2.10 >= $2.00).
    Ордера 2 и 3 должны получить объем 0.0 (блокировка добора).
    """
    p_sl = 0.1288
    q2, q3, cur_risk, loss2, loss3 = mock_client.calc_residual_order_sizes(
        current_pos_size=711.9,
        current_pos_avg_price=0.13172,
        p_entry2=0.1295,
        p_entry3=0.1290,
        p_sl=p_sl,
        total_risk_usd=2.0,
        symbol="SUIUSDT",
    )

    assert cur_risk > 2.0
    assert q2 == 0.0
    assert q3 == 0.0
    assert loss2 == 0.0
    assert loss3 == 0.0


def test_place_order_handles_duplicate_order_link_id(mock_client):
    """
    Проверка идемпотентности:
    Если Bybit возвращает retCode 10001 / 'orderLinkId already exists',
    клиент не выбрасывает ошибку, а находит и возвращает существующий ордер.
    """
    existing_order = {
        "orderId": "existing-order-id-123",
        "orderLinkId": "FIB-SUI-B-O1",
        "price": "0.7790",
        "qty": "40.0",
        "side": "Buy",
    }
    mock_client.session.place_order = MagicMock(return_value={
        "retCode": 10001,
        "retMsg": "orderLinkId already exists",
    })
    mock_client.session.get_open_orders = MagicMock(return_value={
        "retCode": 0,
        "result": {"list": [existing_order]},
    })

    res = mock_client.place_order(
        symbol="SUIUSDT",
        side="Buy",
        order_type="Limit",
        qty=40.0,
        price=0.7790,
        order_link_id="FIB-SUI-B-O1",
    )

    assert res.get("orderId") == "existing-order-id-123"

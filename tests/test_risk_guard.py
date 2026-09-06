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


def test_fee_adjusted_risk_reduces_lot_and_protects_limit(mock_client):
    """
    Проверка модели Fee-Adjusted Risk:
    Учет комиссий (maker 0.02%, taker 0.055%) и буфера проскальзывания стопа (0.10%)
    снижает объем лота так, чтобы суммарный убыток со всеми расходами не превысил лимит $2.00.
    """
    e1 = 100.0
    e2 = 90.0
    sl = 80.0

    # Без комиссий и проскальзывания
    q1_gross, q2_gross, loss1_gross, loss2_gross = mock_client.calc_dual_grid_order_sizes(
        p_entry1=e1,
        p_entry2=e2,
        p_sl=sl,
        total_risk_usd=2.0,
        symbol="SUIUSDT",
        equal_weight=True,
        fee_maker_pct=0.0,
        fee_taker_pct=0.0,
        slippage_buffer_pct=0.0,
    )

    # С комиссиями и проскальзыванием
    q1_net, q2_net, loss1_net, loss2_net = mock_client.calc_dual_grid_order_sizes(
        p_entry1=e1,
        p_entry2=e2,
        p_sl=sl,
        total_risk_usd=2.0,
        symbol="SUIUSDT",
        equal_weight=True,
        fee_maker_pct=0.02,
        fee_taker_pct=0.055,
        slippage_buffer_pct=0.10,
    )

    # Объем с учетом комиссий должен быть меньше либо равен валовому
    assert q1_net <= q1_gross
    assert q2_net <= q2_gross
    # Полный убыток с учетом комиссий и худшего проскальзывания не должен превышать $2.0
    assert (loss1_net + loss2_net) <= 2.0


def test_min_notional_skips_trade_without_inflating_risk(mock_client):
    """
    Защита minNotional:
    Если расчетный объем ордера 1 не дотягивает до minNotional ($5),
    бот НЕ раздувает лот выше риска, а возвращает нули (пропускает сделку).
    """
    # Монета с ценой $10. При риске $0.05 на ордер и SL $8 (дистанция $2),
    # расчетный объем = 0.05 / 2 = 0.025 монеты ($0.25 notional < $5 minNotional).
    q1, q2, l1, l2 = mock_client.calc_dual_grid_order_sizes(
        p_entry1=10.0,
        p_entry2=9.0,
        p_sl=8.0,
        total_risk_usd=0.10,
        symbol="SUIUSDT",
    )
    assert q1 == 0.0
    assert q2 == 0.0
    assert l1 == 0.0
    assert l2 == 0.0


def test_min_net_rr_filter_logic():
    """
    Проверка фильтра качества сетапов min_net_rr:
    Если чистый R:R с учетом комиссий ниже порога, сетап отсекается.
    """
    p_entry = 100.0
    p_tp = 101.0  # +1.0 USD
    p_sl = 98.0   # -2.0 USD
    fee_open = 0.0002
    fee_close = 0.00055

    net_reward = (p_tp - p_entry) - (p_entry * fee_open + p_tp * fee_close)
    worst_sl = p_sl * 0.999
    net_risk = (p_entry - worst_sl) + (p_entry * fee_open + worst_sl * fee_close)
    net_rr = net_reward / net_risk if net_risk > 0 else 0.0

    # net_reward ≈ 0.924, net_risk ≈ 2.174 -> net_rr ≈ 0.425
    min_net_rr_strict = 1.0
    min_net_rr_loose = 0.3

    assert net_rr < min_net_rr_strict
    assert net_rr >= min_net_rr_loose


def test_calc_residual_order_sizes_with_fees_not_exceeding_total_risk(mock_client):
    """
    Проверка п. 4:
    current_risk включает distance-to-worst-stop + avg_entry * fee_maker + worst_stop * fee_taker.
    Суммарный residual risk (current_risk + loss2 + loss3) с комиссиями не превышает total_risk_usd
    после округления вниз.
    """
    total_risk = 2.0
    pos_sz = 0.05
    avg_p = 100.0
    e2 = 95.0
    e3 = 90.0
    sl = 85.0
    fee_maker = 0.02
    fee_taker = 0.055
    slip = 0.10

    q2, q3, cur_risk, loss2, loss3 = mock_client.calc_residual_order_sizes(
        current_pos_size=pos_sz,
        current_pos_avg_price=avg_p,
        p_entry2=e2,
        p_entry3=e3,
        p_sl=sl,
        total_risk_usd=total_risk,
        symbol="SUIUSDT",
        is_long=True,
        fee_maker_pct=fee_maker,
        fee_taker_pct=fee_taker,
        slippage_buffer_pct=slip,
    )

    worst_sl = sl * (1.0 - slip / 100.0)
    expected_unit_loss_curr = abs(avg_p - worst_sl) + avg_p * (fee_maker / 100.0) + worst_sl * (fee_taker / 100.0)
    expected_cur_risk = pos_sz * expected_unit_loss_curr
    assert cur_risk == pytest.approx(expected_cur_risk, rel=1e-5)

    tot_risk = cur_risk + loss2 + loss3
    assert tot_risk <= total_risk + 1e-6


def test_calc_residual_order_sizes_backward_compatibility_zero_fees(mock_client):
    """
    Проверка п. 4:
    Обратная совместимость: если комиссии и проскальзывание равны нулю,
    current_risk равен прежней формуле pos_sz * abs(avg_p - sl).
    """
    pos_sz = 10.0
    avg_p = 50.0
    sl = 40.0
    q2, q3, cur_risk, loss2, loss3 = mock_client.calc_residual_order_sizes(
        current_pos_size=pos_sz,
        current_pos_avg_price=avg_p,
        p_entry2=45.0,
        p_entry3=42.0,
        p_sl=sl,
        total_risk_usd=2.0,
        symbol="SUIUSDT",
        fee_maker_pct=0.0,
        fee_taker_pct=0.0,
        slippage_buffer_pct=0.0,
    )
    assert cur_risk == pytest.approx(pos_sz * abs(avg_p - sl), rel=1e-5)



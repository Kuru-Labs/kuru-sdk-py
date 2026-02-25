from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kuru_sdk_py.executor.orders_executor import (
    BatchOrderRequest,
    OrdersExecutor,
    round_price_down,
    round_price_up,
)
from kuru_sdk_py.manager.order import Order, OrderSide, OrderType


def _make_executor() -> OrdersExecutor:
    executor = OrdersExecutor.__new__(OrdersExecutor)
    executor.market_config = SimpleNamespace(
        price_precision=100,
        size_precision=1000,
        tick_size=5,
        margin_contract_address="0x0000000000000000000000000000000000000002",
        base_token="0x0000000000000000000000000000000000000003",
        quote_token="0x0000000000000000000000000000000000000004",
        orderbook_implementation="0x0000000000000000000000000000000000000005",
        margin_account_implementation="0x0000000000000000000000000000000000000006",
    )
    executor.user_address = "0x0000000000000000000000000000000000000001"
    executor.order_book_address = "0x0000000000000000000000000000000000000007"
    executor._send_transaction = AsyncMock(return_value="0xtxhash")
    return executor


def test_round_price_down():
    assert round_price_down(123, 5) == 120
    assert round_price_down(125, 5) == 125


def test_round_price_up():
    assert round_price_up(123, 5) == 125
    assert round_price_up(125, 5) == 125


@pytest.mark.asyncio
async def test_place_order_converts_decimal_and_rounds_prices(monkeypatch):
    executor = _make_executor()
    captured_payload = {}
    function_call = object()

    def batch_update_mm(payload):
        captured_payload.update(payload)
        return function_call

    executor.contract = SimpleNamespace(
        functions=SimpleNamespace(batchUpdateMM=batch_update_mm)
    )

    monkeypatch.setattr(
        "kuru_sdk_py.transaction.access_list.build_access_list_for_cancel_and_place",
        lambda **kwargs: [],
    )

    txhash = await executor.place_order(
        buy_cloids=[b"buy"],
        sell_cloids=[b"sell"],
        cancel_cloids=[],
        buy_prices=[Decimal("1.23")],
        buy_sizes=[Decimal("0.4")],
        sell_prices=[Decimal("1.27")],
        sell_sizes=[Decimal("0.6")],
        order_ids_to_cancel=[],
        orders_to_cancel_metadata=[],
        post_only=True,
        price_rounding="default",
    )

    assert txhash == "0xtxhash"
    assert captured_payload["buyPrices"] == [120]
    assert captured_payload["sellPrices"] == [130]
    assert captured_payload["buySizes"] == [400]
    assert captured_payload["sellSizes"] == [600]
    executor._send_transaction.assert_awaited_once_with(function_call, access_list=[])


@pytest.mark.asyncio
async def test_place_order_validates_input_lengths():
    executor = _make_executor()
    executor.contract = SimpleNamespace(
        functions=SimpleNamespace(batchUpdateMM=lambda payload: object())
    )

    with pytest.raises(ValueError, match="buy_prices arrays must have same length"):
        await executor.place_order(
            buy_cloids=[b"buy1", b"buy2"],
            sell_cloids=[],
            cancel_cloids=[],
            buy_prices=[Decimal("1.0")],
            buy_sizes=[Decimal("1.0"), Decimal("2.0")],
            sell_prices=[],
            sell_sizes=[],
            order_ids_to_cancel=[],
            orders_to_cancel_metadata=[],
            post_only=True,
        )


def test_batch_order_request_from_orders_sorts_and_groups():
    orders = [
        Order(
            cloid="buy-low",
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            price=Decimal("1.00"),
            size=Decimal("1.0"),
        ),
        Order(
            cloid="buy-high",
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            price=Decimal("1.50"),
            size=Decimal("1.0"),
        ),
        Order(
            cloid="sell-low",
            order_type=OrderType.LIMIT,
            side=OrderSide.SELL,
            price=Decimal("2.00"),
            size=Decimal("1.0"),
        ),
        Order(
            cloid="sell-high",
            order_type=OrderType.LIMIT,
            side=OrderSide.SELL,
            price=Decimal("2.50"),
            size=Decimal("1.0"),
        ),
    ]

    orders_manager = SimpleNamespace(
        get_kuru_order_id=lambda cloid: None,
        cloid_to_order={},
    )
    market_config = SimpleNamespace(price_precision=100)
    request = BatchOrderRequest.from_orders(
        orders=orders,
        orders_manager=orders_manager,
        market_config=market_config,
        post_only=True,
        price_rounding="default",
    )

    assert [order.cloid for order in request.buy_orders] == ["buy-high", "buy-low"]
    assert [order.cloid for order in request.sell_orders] == ["sell-low", "sell-high"]
    assert request.post_only is True

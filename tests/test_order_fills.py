import pytest
from decimal import Decimal
from kuru_sdk_py.manager.order import Order, OrderType, OrderSide, OrderStatus
from kuru_sdk_py.manager.events import TradeEvent


def test_new_order_empty_fills():
    """New orders should have empty filled_sizes list"""
    order = Order(
        cloid="test-1",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("10")
    )
    assert order.filled_sizes == []
    assert order.total_filled_size == Decimal(0)


def test_single_partial_fill():
    """Single partial fill should append to filled_sizes"""
    order = Order(
        cloid="test-2",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("10")
    )
    trade_event = TradeEvent(
        order_id=1,
        maker_address="0x123",
        is_buy=True,
        price=100,
        updated_size=Decimal("7"),
        taker_address="0x456",
        tx_origin="0x789",
        filled_size=Decimal("3"),
        txhash="0xabc"
    )
    order.update_order_on_trade(trade_event)

    assert order.filled_sizes == [Decimal("3")]
    assert order.total_filled_size == Decimal("3")
    assert order.size == Decimal("7")
    assert order.status == OrderStatus.ORDER_PARTIALLY_FILLED


def test_multiple_partial_fills():
    """Multiple partial fills should accumulate in filled_sizes"""
    order = Order(
        cloid="test-3",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("10")
    )

    # First fill: 3.0
    trade1 = TradeEvent(
        order_id=1, maker_address="0x123", is_buy=True,
        price=100, updated_size=Decimal("7"), taker_address="0x456",
        tx_origin="0x789", filled_size=Decimal("3"), txhash="0xabc"
    )
    order.update_order_on_trade(trade1)

    # Second fill: 2.0
    trade2 = TradeEvent(
        order_id=1, maker_address="0x123", is_buy=True,
        price=100, updated_size=Decimal("5"), taker_address="0x456",
        tx_origin="0x789", filled_size=Decimal("2"), txhash="0xdef"
    )
    order.update_order_on_trade(trade2)

    assert order.filled_sizes == [Decimal("3"), Decimal("2")]
    assert order.total_filled_size == Decimal("5")
    assert order.size == Decimal("5")
    assert order.status == OrderStatus.ORDER_PARTIALLY_FILLED


def test_full_fill_single_trade():
    """Full fill in single trade should record the fill"""
    order = Order(
        cloid="test-4",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("10")
    )
    trade_event = TradeEvent(
        order_id=1, maker_address="0x123", is_buy=True,
        price=100, updated_size=Decimal("0"), taker_address="0x456",
        tx_origin="0x789", filled_size=Decimal("10"), txhash="0xabc"
    )
    order.update_order_on_trade(trade_event)

    assert order.filled_sizes == [Decimal("10")]
    assert order.total_filled_size == Decimal("10")
    assert order.size == Decimal(0)
    assert order.status == OrderStatus.ORDER_FULLY_FILLED


def test_full_fill_after_partials():
    """Full fill after partials should record all fills"""
    order = Order(
        cloid="test-5",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("10")
    )

    # Partial fill: 6.0
    trade1 = TradeEvent(
        order_id=1, maker_address="0x123", is_buy=True,
        price=100, updated_size=Decimal("4"), taker_address="0x456",
        tx_origin="0x789", filled_size=Decimal("6"), txhash="0xabc"
    )
    order.update_order_on_trade(trade1)

    # Final fill: 4.0
    trade2 = TradeEvent(
        order_id=1, maker_address="0x123", is_buy=True,
        price=100, updated_size=Decimal("0"), taker_address="0x456",
        tx_origin="0x789", filled_size=Decimal("4"), txhash="0xdef"
    )
    order.update_order_on_trade(trade2)

    assert order.filled_sizes == [Decimal("6"), Decimal("4")]
    assert order.total_filled_size == Decimal("10")
    assert order.size == Decimal(0)
    assert order.status == OrderStatus.ORDER_FULLY_FILLED


def test_list_independence():
    """Each order should have independent filled_sizes list"""
    order1 = Order(
        cloid="test-7a",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("10")
    )
    order2 = Order(
        cloid="test-7b",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("10")
    )

    trade = TradeEvent(
        order_id=1, maker_address="0x123", is_buy=True,
        price=100, updated_size=Decimal("7"), taker_address="0x456",
        tx_origin="0x789", filled_size=Decimal("3"), txhash="0xabc"
    )
    order1.update_order_on_trade(trade)

    assert order1.filled_sizes == [Decimal("3")]
    assert order2.filled_sizes == []  # Should be independent


def test_float_inputs_auto_coerced():
    """Float inputs should be auto-coerced to Decimal via __post_init__"""
    order = Order(
        cloid="test-coerce",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=100.0,
        size=10.0
    )
    assert isinstance(order.price, Decimal)
    assert isinstance(order.size, Decimal)
    assert order.price == Decimal("100.0")
    assert order.size == Decimal("10.0")

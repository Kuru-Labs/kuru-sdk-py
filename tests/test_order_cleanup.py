"""Tests for terminal-order cleanup in OrdersManager."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from kuru_sdk_py.manager.events import TradeEvent, OrdersCanceledEvent
from kuru_sdk_py.manager.order import Order, OrderType, OrderSide, OrderStatus
from kuru_sdk_py.manager.orders_manager import OrdersManager, SentOrders
from kuru_sdk_py.utils.async_mem_cache import AsyncMemCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_manager() -> OrdersManager:
    """Create an OrdersManager with mocked dependencies (no RPC needed)."""
    instance = OrdersManager()
    instance.rpc_url = "http://localhost:8545"
    instance.w3 = AsyncMock()
    instance.w3.is_connected = AsyncMock(return_value=True)
    instance._connected = True

    instance.pending_transactions = AsyncMemCache(
        ttl=15.0, on_expire=instance.on_transaction_timeout
    )
    instance.trade_events_cache = AsyncMemCache(ttl=5.0)
    instance.processed_orders_queue = asyncio.Queue()

    instance._reconciliation_interval = 999.0
    instance._reconciliation_threshold = 999.0

    return instance


def _make_order(
    cloid: str,
    *,
    side: OrderSide = OrderSide.BUY,
    price: float = 100.0,
    size: float = 1.0,
    txhash: str | None = "0xabc123",
    kuru_order_id: int | None = None,
    status: OrderStatus = OrderStatus.ORDER_SENT,
) -> Order:
    order = Order(
        cloid=cloid,
        order_type=OrderType.LIMIT,
        side=side,
        price=price,
        size=size,
        status=status,
        txhash=txhash,
    )
    if kuru_order_id is not None:
        order.kuru_order_id = kuru_order_id
    return order


def _register_order(manager: OrdersManager, order: Order) -> None:
    """Register an order in manager's tracking dicts."""
    manager.cloid_to_order[order.cloid] = order
    if order.kuru_order_id is not None:
        manager.kuru_order_id_to_cloid[order.kuru_order_id] = order.cloid


def _register_batch(
    manager: OrdersManager,
    txhash: str,
    buy_orders: list[Order],
    sell_orders: list[Order] | None = None,
    cancel_orders: list[Order] | None = None,
) -> None:
    """Register a batch of orders under a txhash."""
    manager.txhash_to_sent_orders[txhash] = SentOrders(
        buy_orders=buy_orders,
        sell_orders=sell_orders or [],
        cancel_orders=cancel_orders or [],
    )


# ---------------------------------------------------------------------------
# Tests: terminal states remove order from tracking dicts
# ---------------------------------------------------------------------------


class TestTerminalStatusCleanup:
    """Terminal-status orders are removed from memory after queue.put()."""

    @pytest.mark.asyncio
    async def test_cancelled_order_removed(self):
        manager = _create_manager()
        order = _make_order("o1", kuru_order_id=1001, txhash=None)
        _register_order(manager, order)

        order.update_status(OrderStatus.ORDER_CANCELLED)
        await manager._finalize_order_update(order)

        assert "o1" not in manager.cloid_to_order
        assert 1001 not in manager.kuru_order_id_to_cloid

    @pytest.mark.asyncio
    async def test_fully_filled_order_removed(self):
        manager = _create_manager()
        order = _make_order("o2", kuru_order_id=1002, txhash=None)
        _register_order(manager, order)

        order.update_status(OrderStatus.ORDER_FULLY_FILLED)
        await manager._finalize_order_update(order)

        assert "o2" not in manager.cloid_to_order
        assert 1002 not in manager.kuru_order_id_to_cloid

    @pytest.mark.asyncio
    async def test_timeout_order_removed(self):
        manager = _create_manager()
        order = _make_order("o3", kuru_order_id=1003, txhash=None)
        _register_order(manager, order)

        order.update_status(OrderStatus.ORDER_TIMEOUT)
        await manager._finalize_order_update(order)

        assert "o3" not in manager.cloid_to_order
        assert 1003 not in manager.kuru_order_id_to_cloid

    @pytest.mark.asyncio
    async def test_failed_order_removed(self):
        manager = _create_manager()
        order = _make_order("o4", kuru_order_id=1004, txhash=None)
        _register_order(manager, order)

        order.update_status(OrderStatus.ORDER_FAILED)
        await manager._finalize_order_update(order)

        assert "o4" not in manager.cloid_to_order
        assert 1004 not in manager.kuru_order_id_to_cloid

    @pytest.mark.asyncio
    async def test_fully_filled_via_trade_event(self):
        """ORDER_FULLY_FILLED triggered by on_trade removes the order."""
        manager = _create_manager()
        order = _make_order("o5", kuru_order_id=2001, txhash=None)
        _register_order(manager, order)

        trade = TradeEvent(
            order_id=2001,
            maker_address="0xmaker",
            is_buy=True,
            price=100,
            updated_size=Decimal(0),   # fully filled
            taker_address="0xtaker",
            tx_origin="0xorigin",
            filled_size=Decimal("1.0"),
        )
        await manager.on_trade(trade)

        assert "o5" not in manager.cloid_to_order
        assert 2001 not in manager.kuru_order_id_to_cloid


# ---------------------------------------------------------------------------
# Tests: non-terminal states keep order in memory
# ---------------------------------------------------------------------------


class TestNonTerminalStatusRetained:
    """Non-terminal orders must stay in memory."""

    @pytest.mark.asyncio
    async def test_partially_filled_order_retained(self):
        manager = _create_manager()
        order = _make_order("o6", kuru_order_id=3001, txhash=None)
        _register_order(manager, order)

        order.update_status(OrderStatus.ORDER_PARTIALLY_FILLED)
        await manager._finalize_order_update(order)

        assert "o6" in manager.cloid_to_order
        assert 3001 in manager.kuru_order_id_to_cloid

    @pytest.mark.asyncio
    async def test_placed_order_retained(self):
        manager = _create_manager()
        order = _make_order("o7", kuru_order_id=3002, txhash=None)
        _register_order(manager, order)

        order.update_status(OrderStatus.ORDER_PLACED)
        await manager._finalize_order_update(order)

        assert "o7" in manager.cloid_to_order

    @pytest.mark.asyncio
    async def test_partially_filled_via_trade_event_retained(self):
        """ORDER_PARTIALLY_FILLED (trade with leftover) keeps the order alive."""
        manager = _create_manager()
        order = _make_order("o8", kuru_order_id=4001, txhash=None)
        _register_order(manager, order)

        trade = TradeEvent(
            order_id=4001,
            maker_address="0xmaker",
            is_buy=True,
            price=100,
            updated_size=Decimal("0.5"),   # partially filled
            taker_address="0xtaker",
            tx_origin="0xorigin",
            filled_size=Decimal("0.5"),
        )
        await manager.on_trade(trade)

        assert "o8" in manager.cloid_to_order
        assert 4001 in manager.kuru_order_id_to_cloid


# ---------------------------------------------------------------------------
# Tests: consumer queue receives final order state before cleanup
# ---------------------------------------------------------------------------


class TestQueueReceivesOrderBeforeCleanup:
    """Order object must be on the queue even after it's removed from dicts."""

    @pytest.mark.asyncio
    async def test_queue_receives_cancelled_order(self):
        manager = _create_manager()
        order = _make_order("o9", kuru_order_id=5001, txhash=None)
        _register_order(manager, order)

        order.update_status(OrderStatus.ORDER_CANCELLED)
        await manager._finalize_order_update(order)

        queued = manager.processed_orders_queue.get_nowait()
        assert queued.cloid == "o9"
        assert queued.status == OrderStatus.ORDER_CANCELLED

    @pytest.mark.asyncio
    async def test_queue_receives_fully_filled_order(self):
        manager = _create_manager()
        order = _make_order("o10", kuru_order_id=5002, txhash=None)
        _register_order(manager, order)

        order.update_status(OrderStatus.ORDER_FULLY_FILLED)
        await manager._finalize_order_update(order)

        queued = manager.processed_orders_queue.get_nowait()
        assert queued.cloid == "o10"
        assert queued.status == OrderStatus.ORDER_FULLY_FILLED
        # Object is still accessible via the queue reference
        assert queued is order


# ---------------------------------------------------------------------------
# Tests: batch (txhash) dict cleanup
# ---------------------------------------------------------------------------


class TestBatchCleanup:
    """txhash_to_sent_orders and txhash_to_orders_created are cleaned up only
    when every order in the batch has reached a terminal state."""

    @pytest.mark.asyncio
    async def test_batch_cleaned_when_all_orders_terminal(self):
        manager = _create_manager()
        txhash = "0xbatch1"

        o1 = _make_order("b1", txhash=txhash)
        o2 = _make_order("b2", txhash=txhash)
        _register_order(manager, o1)
        _register_order(manager, o2)
        _register_batch(manager, txhash, buy_orders=[o1, o2])

        # Finalize first order — batch not yet fully terminal
        o1.update_status(OrderStatus.ORDER_CANCELLED)
        await manager._finalize_order_update(o1)
        assert txhash in manager.txhash_to_sent_orders

        # Finalize second order — batch now complete
        o2.update_status(OrderStatus.ORDER_CANCELLED)
        await manager._finalize_order_update(o2)
        assert txhash not in manager.txhash_to_sent_orders
        assert txhash not in manager.txhash_to_orders_created

    @pytest.mark.asyncio
    async def test_batch_not_cleaned_while_orders_remain(self):
        manager = _create_manager()
        txhash = "0xbatch2"

        o1 = _make_order("c1", txhash=txhash)
        o2 = _make_order("c2", txhash=txhash)
        _register_order(manager, o1)
        _register_order(manager, o2)
        _register_batch(manager, txhash, buy_orders=[o1, o2])

        # Only finalize one order
        o1.update_status(OrderStatus.ORDER_FAILED)
        await manager._finalize_order_update(o1)

        # Batch entry must still be present (o2 is still active)
        assert txhash in manager.txhash_to_sent_orders

    @pytest.mark.asyncio
    async def test_batch_with_mixed_sides_cleaned_together(self):
        manager = _create_manager()
        txhash = "0xbatch3"

        buy = _make_order("d_buy", side=OrderSide.BUY, txhash=txhash)
        sell = _make_order("d_sell", side=OrderSide.SELL, txhash=txhash)
        cancel = _make_order("d_cancel", txhash=txhash)
        _register_order(manager, buy)
        _register_order(manager, sell)
        _register_order(manager, cancel)
        _register_batch(
            manager, txhash,
            buy_orders=[buy],
            sell_orders=[sell],
            cancel_orders=[cancel],
        )

        buy.update_status(OrderStatus.ORDER_FULLY_FILLED)
        await manager._finalize_order_update(buy)
        assert txhash in manager.txhash_to_sent_orders

        sell.update_status(OrderStatus.ORDER_FULLY_FILLED)
        await manager._finalize_order_update(sell)
        assert txhash in manager.txhash_to_sent_orders

        cancel.update_status(OrderStatus.ORDER_CANCELLED)
        await manager._finalize_order_update(cancel)
        assert txhash not in manager.txhash_to_sent_orders

    @pytest.mark.asyncio
    async def test_cancelled_via_event_triggers_batch_cleanup(self):
        """on_orders_cancelled path also triggers cleanup for the last order."""
        manager = _create_manager()
        txhash = "0xbatch4"

        order = _make_order("e1", kuru_order_id=9001, txhash=txhash)
        _register_order(manager, order)
        _register_batch(manager, txhash, buy_orders=[order])

        event = OrdersCanceledEvent(order_ids=[9001], owner="0xowner")
        await manager.on_orders_cancelled(event)

        assert "e1" not in manager.cloid_to_order
        assert txhash not in manager.txhash_to_sent_orders

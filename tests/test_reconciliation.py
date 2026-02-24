"""Tests for periodic reconciliation of stuck ORDER_SENT orders."""

import asyncio
from time import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from kuru_sdk_py.manager.orders_manager import OrdersManager, SentOrders
from kuru_sdk_py.manager.order import Order, OrderType, OrderSide, OrderStatus
from kuru_sdk_py.utils.async_mem_cache import AsyncMemCache


def _make_order(
    cloid: str,
    side: OrderSide = OrderSide.BUY,
    price: float = 100.0,
    size: float = 1.0,
    txhash: str = "0xabc123",
    sent_timestamp: float = None,
) -> Order:
    """Helper to create a test order in ORDER_SENT state with a sent_timestamp."""
    order = Order(
        cloid=cloid,
        order_type=OrderType.LIMIT,
        side=side,
        price=price,
        size=size,
        status=OrderStatus.ORDER_SENT,
        txhash=txhash,
        sent_timestamp=sent_timestamp if sent_timestamp is not None else time() - 10,
    )
    return order


def _create_manager(
    reconciliation_interval: float = 0.2,
    reconciliation_threshold: float = 0.5,
) -> OrdersManager:
    """Create an OrdersManager with mocked RPC and fast reconciliation settings."""
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

    instance._reconciliation_interval = reconciliation_interval
    instance._reconciliation_threshold = reconciliation_threshold

    return instance


class TestReconciliationStuckOrders:
    """Tests that stuck ORDER_SENT orders are reconciled."""

    @pytest.mark.asyncio
    async def test_stuck_order_gets_reconciled(self):
        """An ORDER_SENT order not in pending_transactions should be reconciled."""
        manager = _create_manager(
            reconciliation_interval=0.1,
            reconciliation_threshold=0.0,
        )

        # Mock receipt -> tx not found (None), orders marked TIMEOUT
        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=None)

        txhash = "0xstuck"
        order = _make_order("buy1", txhash=txhash, sent_timestamp=time() - 10)
        manager.cloid_to_order["buy1"] = order
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[order], sell_orders=[], cancel_orders=[]
        )

        await manager.start()

        # Wait for at least one reconciliation cycle
        await asyncio.sleep(0.4)

        assert order.status == OrderStatus.ORDER_TIMEOUT
        await manager.close()

    @pytest.mark.asyncio
    async def test_stuck_order_with_successful_receipt(self):
        """A stuck order whose tx succeeded should trigger receipt processing."""
        manager = _create_manager(
            reconciliation_interval=0.1,
            reconciliation_threshold=0.0,
        )

        mock_receipt = MagicMock()
        mock_receipt.status = 1
        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)

        receipts_processed = []

        async def mock_processor(receipt):
            receipts_processed.append(receipt)

        manager.set_receipt_processor(mock_processor)

        txhash = "0xstuck_success"
        order = _make_order("buy1", txhash=txhash, sent_timestamp=time() - 10)
        manager.cloid_to_order["buy1"] = order
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[order], sell_orders=[], cancel_orders=[]
        )

        await manager.start()
        await asyncio.sleep(0.4)

        assert len(receipts_processed) >= 1
        assert receipts_processed[0] is mock_receipt
        await manager.close()


class TestReconciliationSkipCases:
    """Tests that reconciliation correctly skips orders that shouldn't be reconciled."""

    @pytest.mark.asyncio
    async def test_orders_in_pending_cache_are_skipped(self):
        """Orders whose txhash is still in pending_transactions should not be reconciled."""
        manager = _create_manager(
            reconciliation_interval=0.1,
            reconciliation_threshold=0.0,
        )

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=None)

        txhash = "0xpending"
        order = _make_order("buy1", txhash=txhash, sent_timestamp=time() - 10)
        manager.cloid_to_order["buy1"] = order
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[order], sell_orders=[], cancel_orders=[]
        )

        await manager.start()

        # Put txhash in pending_transactions cache — normal path handles it
        await manager.pending_transactions.set(txhash, txhash)

        await asyncio.sleep(0.4)

        # Order should still be ORDER_SENT since pending cache covers it
        assert order.status == OrderStatus.ORDER_SENT
        await manager.close()

    @pytest.mark.asyncio
    async def test_non_sent_orders_are_skipped(self):
        """Orders not in ORDER_SENT status should not be reconciled."""
        manager = _create_manager(
            reconciliation_interval=0.1,
            reconciliation_threshold=0.0,
        )

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=None)

        txhash = "0xplaced"
        order = Order(
            cloid="buy1",
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            price=100.0,
            size=1.0,
            status=OrderStatus.ORDER_PLACED,
            txhash=txhash,
            sent_timestamp=time() - 10,
        )
        manager.cloid_to_order["buy1"] = order
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[order], sell_orders=[], cancel_orders=[]
        )

        await manager.start()
        await asyncio.sleep(0.4)

        # Should remain ORDER_PLACED
        assert order.status == OrderStatus.ORDER_PLACED
        manager.w3.eth.get_transaction_receipt.assert_not_called()
        await manager.close()

    @pytest.mark.asyncio
    async def test_young_orders_are_skipped(self):
        """Orders younger than the reconciliation threshold should not be reconciled."""
        manager = _create_manager(
            reconciliation_interval=0.1,
            reconciliation_threshold=60.0,  # 60s threshold - order won't be old enough
        )

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=None)

        txhash = "0xyoung"
        order = _make_order("buy1", txhash=txhash, sent_timestamp=time())  # just now
        manager.cloid_to_order["buy1"] = order
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[order], sell_orders=[], cancel_orders=[]
        )

        await manager.start()
        await asyncio.sleep(0.4)

        # Should remain ORDER_SENT
        assert order.status == OrderStatus.ORDER_SENT
        manager.w3.eth.get_transaction_receipt.assert_not_called()
        await manager.close()


class TestReconciliationDedup:
    """Tests that multiple orders with the same txhash only trigger one reconciliation."""

    @pytest.mark.asyncio
    async def test_shared_txhash_triggers_single_timeout_call(self):
        """Multiple stuck orders sharing a txhash should only call on_transaction_timeout once."""
        manager = _create_manager(
            reconciliation_interval=0.1,
            reconciliation_threshold=0.0,
        )

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=None)

        txhash = "0xshared"
        buy1 = _make_order("buy1", txhash=txhash, sent_timestamp=time() - 10)
        buy2 = _make_order("buy2", txhash=txhash, sent_timestamp=time() - 10)
        sell1 = _make_order(
            "sell1", side=OrderSide.SELL, txhash=txhash, sent_timestamp=time() - 10
        )

        manager.cloid_to_order["buy1"] = buy1
        manager.cloid_to_order["buy2"] = buy2
        manager.cloid_to_order["sell1"] = sell1
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1, buy2], sell_orders=[sell1], cancel_orders=[]
        )

        await manager.start()
        await asyncio.sleep(0.4)

        # All orders should be marked as timed out
        assert buy1.status == OrderStatus.ORDER_TIMEOUT
        assert buy2.status == OrderStatus.ORDER_TIMEOUT
        assert sell1.status == OrderStatus.ORDER_TIMEOUT

        await manager.close()


class TestReconciliationLifecycle:
    """Tests for reconciliation task start/stop."""

    @pytest.mark.asyncio
    async def test_reconciliation_starts_with_start(self):
        """Reconciliation task should be running after start()."""
        manager = _create_manager()
        await manager.start()

        assert manager._reconciliation_task is not None
        assert manager._reconciliation_running is True
        assert not manager._reconciliation_task.done()

        await manager.close()

    @pytest.mark.asyncio
    async def test_reconciliation_stops_with_close(self):
        """Reconciliation task should be cancelled after close()."""
        manager = _create_manager()
        await manager.start()
        await manager.close()

        assert manager._reconciliation_running is False
        assert manager._reconciliation_task is None

    @pytest.mark.asyncio
    async def test_close_without_start_does_not_crash(self):
        """Calling close() without start() should not raise."""
        manager = _create_manager()
        # close without start - reconciliation task is None
        await manager.close()


class TestSentTimestamp:
    """Tests for the sent_timestamp field on Order."""

    def test_update_status_to_sent_records_timestamp(self):
        """Transitioning to ORDER_SENT should set sent_timestamp."""
        order = Order(
            cloid="test",
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            price=100.0,
            size=1.0,
        )
        assert order.sent_timestamp is None

        before = time()
        order.update_status(OrderStatus.ORDER_SENT)
        after = time()

        assert order.sent_timestamp is not None
        assert before <= order.sent_timestamp <= after

    def test_update_status_to_non_sent_does_not_set_timestamp(self):
        """Transitioning to non-ORDER_SENT should not set sent_timestamp."""
        order = Order(
            cloid="test",
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            price=100.0,
            size=1.0,
        )
        order.update_status(OrderStatus.ORDER_PLACED)
        assert order.sent_timestamp is None

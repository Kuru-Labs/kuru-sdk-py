"""Tests for transaction timeout recovery and receipt fallback."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from kuru_sdk_py.manager.orders_manager import OrdersManager, SentOrders
from kuru_sdk_py.manager.order import Order, OrderType, OrderSide, OrderStatus
from kuru_sdk_py.manager.events import (
    OrderCreatedEvent,
    BatchUpdateMMEvent,
)
from kuru_sdk_py.utils.async_mem_cache import AsyncMemCache


def _make_order(cloid: str, side: OrderSide = OrderSide.BUY, price: float = 100.0, size: float = 1.0) -> Order:
    """Helper to create a test order."""
    return Order(
        cloid=cloid,
        order_type=OrderType.LIMIT,
        side=side,
        price=price,
        size=size,
        status=OrderStatus.ORDER_SENT,
    )


def _make_cancel_order(cloid: str) -> Order:
    """Helper to create a test cancel order."""
    return Order(
        cloid=cloid,
        order_type=OrderType.CANCEL,
        status=OrderStatus.ORDER_SENT,
    )


def _create_manager() -> OrdersManager:
    """Create an OrdersManager with mocked RPC connection (no async needed)."""
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

    return instance


class TestCacheLifecycle:
    """Tests for cache monitor start/stop."""

    @pytest.mark.asyncio
    async def test_start_starts_cache_monitors(self):
        """Cache monitors should be running after start()."""
        manager = _create_manager()
        await manager.start()

        assert manager.pending_transactions._running is True
        assert manager.trade_events_cache._running is True

        await manager.close()

    @pytest.mark.asyncio
    async def test_close_stops_cache_monitors(self):
        """Cache monitors should be stopped after close()."""
        manager = _create_manager()
        await manager.start()
        await manager.close()

        assert manager.pending_transactions._running is False
        assert manager.trade_events_cache._running is False


class TestTransactionTimeoutSuccessReceipt:
    """Tests for timeout with successful (status=1) receipt."""

    @pytest.mark.asyncio
    async def test_successful_receipt_calls_processor(self):
        """Timeout on confirmed tx should call _receipt_processor."""
        manager = _create_manager()
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        mock_receipt.get = MagicMock(return_value=[])

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)

        processor_called_with = []

        async def mock_processor(receipt):
            processor_called_with.append(receipt)

        manager.set_receipt_processor(mock_processor)

        txhash = "0xabc123"
        buy = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy], sell_orders=[], cancel_orders=[]
        )

        await manager.on_transaction_timeout(txhash, txhash)

        assert len(processor_called_with) == 1
        assert processor_called_with[0] is mock_receipt

    @pytest.mark.asyncio
    async def test_successful_receipt_without_processor_logs_warning(self):
        """Timeout on confirmed tx without processor should not crash."""
        manager = _create_manager()
        mock_receipt = MagicMock()
        mock_receipt.status = 1

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)

        txhash = "0xabc123"
        buy = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy], sell_orders=[], cancel_orders=[]
        )

        # Should not raise
        await manager.on_transaction_timeout(txhash, txhash)


class TestTransactionTimeoutFailedReceipt:
    """Tests for timeout with failed (status!=1) receipt."""

    @pytest.mark.asyncio
    async def test_failed_receipt_marks_orders_failed(self):
        """Timeout on reverted tx should mark all orders ORDER_FAILED."""
        manager = _create_manager()
        mock_receipt = MagicMock()
        mock_receipt.status = 0

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)
        manager._get_revert_reason = AsyncMock(return_value="InsufficientMargin()")

        txhash = "0xfailed"
        buy1 = _make_order("buy1")
        sell1 = _make_order("sell1", side=OrderSide.SELL)
        cancel1 = _make_cancel_order("cancel1")

        manager.cloid_to_order["buy1"] = buy1
        manager.cloid_to_order["sell1"] = sell1
        manager.cloid_to_order["cancel1"] = cancel1

        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[sell1], cancel_orders=[cancel1]
        )

        await manager.on_transaction_timeout(txhash, txhash)

        assert buy1.status == OrderStatus.ORDER_FAILED
        assert sell1.status == OrderStatus.ORDER_FAILED
        assert cancel1.status == OrderStatus.ORDER_FAILED

        # All 3 orders should be in the queue
        queued = []
        while not manager.processed_orders_queue.empty():
            queued.append(manager.processed_orders_queue.get_nowait())
        assert len(queued) == 3

    @pytest.mark.asyncio
    async def test_failed_receipt_without_revert_reason(self):
        """Failed tx with no revert reason still marks orders ORDER_FAILED."""
        manager = _create_manager()
        mock_receipt = MagicMock()
        mock_receipt.status = 0

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)
        manager._get_revert_reason = AsyncMock(return_value=None)

        txhash = "0xfailed_no_reason"
        buy1 = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy1
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[], cancel_orders=[]
        )

        await manager.on_transaction_timeout(txhash, txhash)

        assert buy1.status == OrderStatus.ORDER_FAILED


class TestTransactionTimeoutNoReceipt:
    """Tests for timeout with no receipt (tx dropped/pending)."""

    @pytest.mark.asyncio
    async def test_no_receipt_marks_orders_timeout(self):
        """Timeout with no receipt should mark orders ORDER_TIMEOUT."""
        manager = _create_manager()
        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=None)

        txhash = "0xdropped"
        buy1 = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy1
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[], cancel_orders=[]
        )

        await manager.on_transaction_timeout(txhash, txhash)

        assert buy1.status == OrderStatus.ORDER_TIMEOUT

    @pytest.mark.asyncio
    async def test_receipt_fetch_exception_marks_orders_timeout(self):
        """RPC error during receipt fetch should mark orders ORDER_TIMEOUT."""
        manager = _create_manager()
        manager.w3.eth.get_transaction_receipt = AsyncMock(
            side_effect=Exception("RPC error")
        )

        txhash = "0xrpcfail"
        buy1 = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy1
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[], cancel_orders=[]
        )

        await manager.on_transaction_timeout(txhash, txhash)

        # When receipt fetch itself fails, receipt is None -> ORDER_TIMEOUT
        assert buy1.status == OrderStatus.ORDER_TIMEOUT


class TestCacheExpiryFiresCallback:
    """Tests that the cache monitor actually fires the on_expire callback after TTL."""

    @pytest.mark.asyncio
    async def test_expiry_fires_timeout_callback(self):
        """A pending tx that is NOT deleted should trigger on_transaction_timeout after TTL."""
        manager = _create_manager()

        # Replace pending_transactions with a very short TTL cache
        manager.pending_transactions = AsyncMemCache(
            ttl=0.3, on_expire=manager.on_transaction_timeout, check_interval=0.1
        )

        # Mock receipt fetch to return None (dropped tx)
        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=None)

        txhash = "0xexpiry_test"
        buy1 = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy1
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[], cancel_orders=[]
        )

        await manager.pending_transactions.start()
        await manager.trade_events_cache.start()

        # Add to pending cache — TTL starts now
        await manager.pending_transactions.set(txhash, txhash)

        # Wait for expiry + monitor check to fire
        await asyncio.sleep(0.6)

        # The callback should have marked the order as timed out
        assert buy1.status == OrderStatus.ORDER_TIMEOUT

        # Key should no longer be in the cache
        result = await manager.pending_transactions.get(txhash)
        assert result is None

        await manager.close()

    @pytest.mark.asyncio
    async def test_delete_before_expiry_prevents_callback(self):
        """Deleting a pending tx before TTL should NOT trigger on_transaction_timeout."""
        manager = _create_manager()

        manager.pending_transactions = AsyncMemCache(
            ttl=0.3, on_expire=manager.on_transaction_timeout, check_interval=0.1
        )

        # Mock receipt — should never be called
        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=None)

        txhash = "0xdelete_before_expiry"
        buy1 = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy1
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[], cancel_orders=[]
        )

        await manager.pending_transactions.start()
        await manager.trade_events_cache.start()

        await manager.pending_transactions.set(txhash, txhash)

        # Delete before TTL expires (simulating happy-path event arrival)
        await manager.pending_transactions.delete(txhash)

        # Wait past the original TTL
        await asyncio.sleep(0.6)

        # Order should still be in its original SENT status
        assert buy1.status == OrderStatus.ORDER_SENT

        # Receipt should never have been fetched
        manager.w3.eth.get_transaction_receipt.assert_not_called()

        await manager.close()


class TestBatchUpdateClearsPendingTx:
    """Tests for pending tx cache clearing on batch update."""

    @pytest.mark.asyncio
    async def test_batch_update_clears_pending_tx(self):
        """on_batch_update_mm should delete the txhash from pending_transactions."""
        manager = _create_manager()
        await manager.start()

        txhash = "0xbatch123"
        buy1 = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy1
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[], cancel_orders=[]
        )

        # Simulate adding to pending cache
        await manager.pending_transactions.set(txhash, txhash)

        # Create a batch update event
        event = BatchUpdateMMEvent(
            buy_cloids=["buy1"],
            sell_cloids=[],
            cancel_cloids=[],
            txhash=txhash,
        )

        # Also create the order created event
        order_created = OrderCreatedEvent(
            order_id=42,
            owner="0x1234567890",
            size=Decimal("1"),
            price=100,
            is_buy=True,
            txhash=txhash,
            log_index=0,
        )
        await manager.on_order_created(order_created)

        # Process batch update
        await manager.on_batch_update_mm(event)

        # Pending tx should be cleared
        result = await manager.pending_transactions.get(txhash)
        assert result is None

        await manager.close()


class TestMarkOrdersForTx:
    """Tests for _mark_orders_for_tx helper."""

    @pytest.mark.asyncio
    async def test_marks_all_order_types(self):
        """Should mark buy, sell, and cancel orders."""
        manager = _create_manager()
        txhash = "0xmark"
        buy1 = _make_order("buy1")
        sell1 = _make_order("sell1", side=OrderSide.SELL)
        cancel1 = _make_cancel_order("cancel1")

        manager.cloid_to_order["buy1"] = buy1
        manager.cloid_to_order["sell1"] = sell1
        manager.cloid_to_order["cancel1"] = cancel1

        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[sell1], cancel_orders=[cancel1]
        )

        await manager._mark_orders_for_tx(txhash, OrderStatus.ORDER_FAILED)

        assert buy1.status == OrderStatus.ORDER_FAILED
        assert sell1.status == OrderStatus.ORDER_FAILED
        assert cancel1.status == OrderStatus.ORDER_FAILED

    @pytest.mark.asyncio
    async def test_missing_txhash_is_noop(self):
        """Should not crash for unknown txhash."""
        manager = _create_manager()
        await manager._mark_orders_for_tx("0xunknown", OrderStatus.ORDER_TIMEOUT)
        # Should not raise


class TestReceiptProcessor:
    """Tests for set_receipt_processor and receipt log processing."""

    @pytest.mark.asyncio
    async def test_set_receipt_processor(self):
        """set_receipt_processor should store the callback."""
        manager = _create_manager()

        async def my_processor(receipt):
            pass

        manager.set_receipt_processor(my_processor)
        assert manager._receipt_processor is my_processor

    @pytest.mark.asyncio
    async def test_processor_error_is_caught(self):
        """Errors in receipt processor should be caught, not raised."""
        manager = _create_manager()
        mock_receipt = MagicMock()
        mock_receipt.status = 1

        manager.w3.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)

        async def failing_processor(receipt):
            raise RuntimeError("decode failed")

        manager.set_receipt_processor(failing_processor)

        txhash = "0xprocessfail"
        buy1 = _make_order("buy1")
        manager.cloid_to_order["buy1"] = buy1
        manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=[buy1], sell_orders=[], cancel_orders=[]
        )

        # Should not raise
        await manager.on_transaction_timeout(txhash, txhash)


class TestOrderFailedStatus:
    """Tests for the new ORDER_FAILED status."""

    def test_order_failed_enum_exists(self):
        """ORDER_FAILED should be a valid OrderStatus."""
        assert OrderStatus.ORDER_FAILED.value == "failed"

    def test_order_can_be_set_to_failed(self):
        """Orders should be settable to ORDER_FAILED."""
        order = _make_order("test")
        order.update_status(OrderStatus.ORDER_FAILED)
        assert order.status == OrderStatus.ORDER_FAILED

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional
from loguru import logger
from web3 import AsyncWeb3, AsyncHTTPProvider

from kuru_sdk_py.configs import ConnectionConfig, CacheConfig
from kuru_sdk_py.manager.order import Order
from kuru_sdk_py.manager.order import OrderStatus
from kuru_sdk_py.manager.events import (
    TradeEvent,
    OrdersCanceledEvent,
    OrderCreatedEvent,
    BatchUpdateMMEvent,
)
from kuru_sdk_py.utils.async_mem_cache import AsyncMemCache
from kuru_sdk_py.manager.order import OrderType
from kuru_sdk_py.manager.order import OrderSide
from kuru_sdk_py.utils.errors import decode_contract_error


@dataclass
class SentOrders:
    buy_orders: list[Order]
    sell_orders: list[Order]
    cancel_orders: list[Order]


@dataclass
class OrdersCreatedForTxhash:
    buy_orders: list[OrderCreatedEvent]
    sell_orders: list[OrderCreatedEvent]


class OrdersManager:
    """
    Manages order lifecycle, tracking, and event processing.

    Use the async `create()` factory method to instantiate this class,
    as it requires async initialization for RPC connection verification.

    Example:
        manager = await OrdersManager.create(rpc_url)
    """

    def __init__(self):
        """
        Private constructor - do not call directly.

        Use OrdersManager.create() instead.
        """
        self.rpc_url: str = ""
        self.w3: AsyncWeb3 = None
        self._connected = False

        # order mapping b/w cloid order and kuru_order_id
        self.cloid_to_order: dict[str, Order] = {}
        self.kuru_order_id_to_cloid: dict[int, str] = {}

        # txhas to sent orders to index kuru_order_id to cloid when we receive events
        self.txhash_to_sent_orders: dict[str, SentOrders] = {}
        self.txhash_to_orders_created: dict[
            str, OrdersCreatedForTxhash
        ] = {}  # txhash => OrdersCreatedForTxhash

        self.pending_transactions: AsyncMemCache = None
        self.trade_events_cache: AsyncMemCache = None
        self.processed_orders_queue: asyncio.Queue[Order] = None

        # Callback to process receipt logs when timeout fires on a confirmed tx
        self._receipt_processor: Optional[Callable] = None

    @classmethod
    async def create(
        cls,
        connection_config: ConnectionConfig,
        cache_config: CacheConfig,
    ) -> "OrdersManager":
        """
        Factory method to create and initialize OrdersManager.

        Args:
            connection_config: Connection configuration (RPC URLs)
            cache_config: Cache TTL configuration

        Returns:
            Initialized OrdersManager instance

        Raises:
            ConnectionError: If RPC connection fails
        """
        instance = cls()
        instance.rpc_url = connection_config.rpc_url
        instance.w3 = AsyncWeb3(AsyncHTTPProvider(connection_config.rpc_url))

        # Verify connection during initialization
        if not await instance.w3.is_connected():
            raise ConnectionError(
                f"Failed to connect to RPC at {connection_config.rpc_url}"
            )

        instance._connected = True
        logger.info(f"OrdersManager connected to RPC at {connection_config.rpc_url}")

        # Initialize async components with cache config
        instance.pending_transactions = AsyncMemCache(
            ttl=cache_config.pending_tx_ttl, on_expire=instance.on_transaction_timeout
        )
        instance.trade_events_cache = AsyncMemCache(ttl=cache_config.trade_events_ttl)
        instance.processed_orders_queue = asyncio.Queue()

        return instance

    def set_receipt_processor(self, processor: Callable) -> None:
        """Set the callback used to process receipt logs on timeout recovery."""
        self._receipt_processor = processor

    async def start(self) -> None:
        """Start background cache monitors for pending transactions and trade events."""
        await self.pending_transactions.start()
        await self.trade_events_cache.start()
        logger.debug("OrdersManager cache monitors started")

    async def on_transaction_timeout(self, txhash: str, value: Any) -> None:
        """Callback function to handle transaction timeout."""
        try:
            receipt = await self.w3.eth.get_transaction_receipt(txhash)
        except Exception as e:
            logger.error(f"Failed to fetch receipt for txhash {txhash}: {e}")
            receipt = None

        if receipt is None:
            # Transaction not found on-chain — likely dropped or still pending
            logger.warning(f"Transaction {txhash} not found on-chain, marking orders as timed out")
            await self._mark_orders_for_tx(txhash, OrderStatus.ORDER_TIMEOUT)
            return

        if receipt.status == 1:
            # Transaction succeeded but WebSocket events were missed — recover from receipt
            logger.info(f"Transaction {txhash} confirmed but events missed, recovering from receipt")
            if self._receipt_processor is not None:
                try:
                    await self._receipt_processor(receipt)
                except Exception as e:
                    logger.error(f"Error processing receipt logs for {txhash}: {e}")
            else:
                logger.warning(
                    f"Transaction {txhash} confirmed but no receipt processor set, "
                    "cannot recover missed events"
                )
        else:
            # Transaction reverted — mark orders as failed
            decoded_error = await self._get_revert_reason(txhash, receipt)
            sent = self.txhash_to_sent_orders.get(txhash)
            order_counts = (
                f"buy={len(sent.buy_orders)}, sell={len(sent.sell_orders)}, cancel={len(sent.cancel_orders)}"
                if sent else "unknown"
            )

            if decoded_error:
                logger.error(
                    f"Transaction {txhash} reverted: {decoded_error}\n"
                    f"  Failed orders: {order_counts}"
                )
            else:
                logger.error(
                    f"Transaction {txhash} failed (no revert reason available)\n"
                    f"  Failed orders: {order_counts}"
                )

            await self._mark_orders_for_tx(txhash, OrderStatus.ORDER_FAILED)

    async def _mark_orders_for_tx(self, txhash: str, status: OrderStatus) -> None:
        """Mark all orders associated with a txhash with the given status."""
        sent = self.txhash_to_sent_orders.get(txhash)
        if sent is None:
            logger.debug(f"No sent orders found for txhash {txhash}")
            return

        all_orders = sent.buy_orders + sent.sell_orders + sent.cancel_orders
        for order in all_orders:
            order.update_status(status)
            await self._finalize_order_update(order)

        logger.info(
            f"Marked {len(all_orders)} orders as {status.value} for txhash {txhash[:10]}..."
        )

    async def _get_revert_reason(self, txhash: str, receipt) -> str | None:
        """
        Extract revert reason from failed transaction.

        Uses eth_call to replay the transaction and capture revert data.

        Args:
            txhash: Transaction hash
            receipt: Transaction receipt

        Returns:
            Decoded error message or None if extraction failed
        """
        try:
            # Get original transaction
            tx = await self.w3.eth.get_transaction(txhash)

            # Replay transaction to get revert reason
            # This will raise an exception with the revert data
            try:
                await self.w3.eth.call(
                    {
                        "from": tx["from"],
                        "to": tx["to"],
                        "data": tx["input"],
                        "value": tx.get("value", 0),
                        "gas": tx.get("gas"),
                    },
                    receipt["blockNumber"] - 1,  # Call at block before tx
                )
            except Exception as call_error:
                # Exception contains revert data
                decoded = decode_contract_error(call_error)
                return decoded

        except Exception as e:
            logger.debug(f"Failed to extract revert reason for {txhash}: {e}")
            return None

        return None

    # async def on_order_expire(self, order_uid: str, order: Order) -> None:
    #     """Callback function to handle order expiration."""
    #     """Check the tx receipt from the rpc and update the order accordingly."""
    #     txhash = order.txhash
    #     if txhash is None:
    #         logger.error(f"Transaction hash is not set for order {order.cloid}")
    #         return
    #     receipt = await self.w3.eth.get_transaction_receipt(txhash)
    #     if receipt is None:
    #         logger.error(f"Transaction receipt is not found for order {order.cloid}")
    #         return
    #     if receipt.status == 1:
    #         order.update_status(OrderStatus.ORDER_PLACED)
    #     else:
    #         order.update_status(OrderStatus.ORDER_TIMEOUT)
    #         logger.info(f"Order {order.cloid} timed out")

    #     await self._finalize_order_update(order)

    async def _cache_trade_event_for_missing_order(
        self, kuru_order_id: int, trade_event: TradeEvent
    ) -> None:
        """Cache trade event when order is not yet recognized.

        Compares updated sizes and only caches if the new event has a smaller updated size.
        """
        # Add the trade event to the async memory cache.
        # If trade already exists, compare the updated size and only add the trade event if the updated size is lesser.
        existing_event = await self.trade_events_cache.get(kuru_order_id)
        if existing_event is None:
            await self.trade_events_cache.set(kuru_order_id, trade_event)
        else:
            if existing_event.updated_size < trade_event.updated_size:
                await self.trade_events_cache.set(kuru_order_id, trade_event)
            else:
                pass  # Trade event already cached with better data

    def _get_order_by_kuru_id(self, kuru_order_id: int) -> Order | None:
        """Retrieve order from kuru_order_id mapping.

        Returns None if order not found.
        """
        if kuru_order_id not in self.kuru_order_id_to_cloid:
            logger.debug(f"Order with kuru_order_id {kuru_order_id} not found")
            return None

        cloid = self.kuru_order_id_to_cloid[kuru_order_id]
        order = self.cloid_to_order.get(cloid)

        if order is None:
            logger.error(f"Order with cloid {cloid} not found")
            return None

        return order

    def get_kuru_order_id(self, cloid: str) -> int | None:
        """Get kuru_order_id from cloid.

        Returns None if the order hasn't been placed on-chain yet (no kuru_order_id assigned).
        """
        # First check if order exists
        order = self.cloid_to_order.get(cloid)
        if order is None:
            logger.debug(
                f"Order with cloid {cloid} not found in cloid_to_order mapping"
            )
            return None

        # Return the kuru_order_id (may be None if order not yet placed)
        return order.kuru_order_id

    async def _finalize_order_update(self, order: Order) -> None:
        """Finalize order update by creating unique ID, updating dictionaries, and queuing order."""
        self.cloid_to_order[order.cloid] = order
        if order.kuru_order_id is not None:
            self.kuru_order_id_to_cloid[order.kuru_order_id] = order.cloid

        # Send to queue
        await self.processed_orders_queue.put(order)

    async def on_order_created(self, order_created_event: OrderCreatedEvent) -> None:
        """Callback function to handle order creation."""

        txhash = order_created_event.txhash
        if self.txhash_to_orders_created.get(txhash) is None:
            self.txhash_to_orders_created[txhash] = OrdersCreatedForTxhash(
                buy_orders=[],
                sell_orders=[],
            )
        if order_created_event.is_buy:
            self.txhash_to_orders_created[txhash].buy_orders.append(order_created_event)
        else:
            self.txhash_to_orders_created[txhash].sell_orders.append(
                order_created_event
            )

    async def on_trade(self, trade_event: TradeEvent) -> None:
        """Callback function to handle trade events."""
        kuru_order_id = trade_event.order_id

        # Check if order exists
        if kuru_order_id not in self.kuru_order_id_to_cloid:
            logger.debug(
                f"Order with kuru_order_id {kuru_order_id} not found on receive trade event"
            )
            await self._cache_trade_event_for_missing_order(kuru_order_id, trade_event)
            return

        # Get order
        order = self._get_order_by_kuru_id(kuru_order_id)
        if order is None:
            logger.debug(
                f"Order with kuru_order_id {kuru_order_id} not found on receive trade event"
            )
            return

        # Update order from trade
        order.update_order_on_trade(trade_event)

        logger.info(
            f"Order {order.cloid} filled: order_id={kuru_order_id}, "
            f"filled={trade_event.filled_size}, remaining={trade_event.updated_size}"
        )

        # Finalize update
        await self._finalize_order_update(order)

    async def on_orders_cancelled(self, event: OrdersCanceledEvent) -> None:
        """Callback function to handle orders cancelled events."""
        for kuru_order_id in event.order_ids:
            order = self._get_order_by_kuru_id(kuru_order_id)
            if order is None:
                logger.debug(
                    f"Order with kuru_order_id {kuru_order_id} not found on receive orders cancelled event"
                )
                continue

            # Update status to cancelled
            order.update_status(OrderStatus.ORDER_CANCELLED)

            logger.info(f"Order {order.cloid} cancelled: order_id={kuru_order_id}")

            # Finalize update
            await self._finalize_order_update(order)

    async def _process_batch_orders(
        self,
        cloids: list[str],
        orders_created: list[OrderCreatedEvent],
        side_name: str,  # "buy" or "sell" for logging
    ) -> None:
        """
        Process batch of buy/sell orders, mapping cloids to OrderCreatedEvents.

        Args:
            cloids: List of client order IDs from BatchUpdateMMEvent
            orders_created: List of OrderCreatedEvent objects (already sorted by log_index)
            side_name: "buy" or "sell" for logging context
        """
        for index, cloid in enumerate(cloids):
            # Validate cloid exists
            if cloid not in self.cloid_to_order:
                logger.debug(
                    f"Cloid {cloid} (index {index}, {side_name}) not found in "
                    f"cloid_to_order mapping. Skipping."
                )
                continue

            order = self.cloid_to_order[cloid]

            # Case 1: Order has OrderCreatedEvent (placed on blockchain)
            if index < len(orders_created):
                order_created_event = orders_created[index]

                # Store blockchain order_id
                order.set_kuru_order_id(order_created_event.order_id)

                # Store original size for comparison
                original_size = order.size

                # Update order size to blockchain-confirmed size
                order.size = order_created_event.size

                # Determine status based on size comparison
                if order_created_event.size < original_size:
                    order.update_status(OrderStatus.ORDER_PARTIALLY_FILLED)
                    logger.info(
                        f"Order {cloid} ({side_name}) placed partially filled: "
                        f"order_id={order_created_event.order_id}, size={order_created_event.size}/{original_size}"
                    )
                else:
                    order.update_status(OrderStatus.ORDER_PLACED)
                    logger.info(
                        f"Order {cloid} ({side_name}) placed: "
                        f"order_id={order_created_event.order_id}, size={order_created_event.size}"
                    )

            # Case 2: No OrderCreatedEvent (immediately fully filled)
            else:
                order.update_status(OrderStatus.ORDER_FULLY_FILLED)
                order.size = 0

            # Finalize the update (updates mappings and queues order)
            await self._finalize_order_update(order)

    async def on_batch_update_mm(self, event: BatchUpdateMMEvent) -> None:
        """
        Callback function to handle batchUpdate events.

        Maps buy/sell cloids to their corresponding OrderCreatedEvents and handles
        immediately fulfilled orders.

        Args:
            event: The BatchUpdateMM event
        """
        buy_cloids = event.buy_cloids
        sell_cloids = event.sell_cloids
        cancel_cloids = event.cancel_cloids
        txhash = event.txhash

        logger.info(
            f"Processing BatchUpdateMM: {len(buy_cloids)} buys, "
            f"{len(sell_cloids)} sells, {len(cancel_cloids)} cancels, txhash={txhash[:10]}..."
        )

        # Validate txhash exists
        if self.txhash_to_sent_orders.get(txhash) is None:
            logger.error(f"Txhash {txhash} not found in txhash_to_sent_orders")
            return

        # Get OrderCreatedEvent objects for this txhash
        orders_created_for_txhash = self.txhash_to_orders_created.get(txhash)

        # If no orders were created, all orders were immediately filled
        if orders_created_for_txhash is None:
            orders_created_for_txhash = OrdersCreatedForTxhash(
                buy_orders=[],
                sell_orders=[],
            )

        # Sort by log_index to maintain blockchain order
        orders_created_for_txhash.buy_orders.sort(key=lambda x: x.log_index)
        orders_created_for_txhash.sell_orders.sort(key=lambda x: x.log_index)

        # Process buy orders
        await self._process_batch_orders(
            buy_cloids, orders_created_for_txhash.buy_orders, "buy"
        )

        # Process sell orders
        await self._process_batch_orders(
            sell_cloids, orders_created_for_txhash.sell_orders, "sell"
        )

        # Verify if the cloids are cancelled
        for cancel_cloid in cancel_cloids:
            order = self.cloid_to_order.get(cancel_cloid)
            if order is None:
                logger.warning(
                    f"Cloid {cancel_cloid} not found in cloid_to_order mapping"
                )
                continue

            if order.status != OrderStatus.ORDER_CANCELLED:
                logger.debug(
                    f"Cloid {cancel_cloid} is not cancelled, current order status is {order.status}"
                )
                continue

        # Clear pending transaction — events arrived successfully
        await self.pending_transactions.delete(txhash)

    async def close(self) -> None:
        """Stop cache monitors and close the HTTP provider session."""
        # Stop cache monitors
        try:
            await self.pending_transactions.stop()
        except Exception as e:
            logger.debug(f"Error stopping pending_transactions cache: {e}")
        try:
            await self.trade_events_cache.stop()
        except Exception as e:
            logger.debug(f"Error stopping trade_events_cache: {e}")

        try:
            if hasattr(self.w3.provider, "_session") and self.w3.provider._session:
                await self.w3.provider._session.close()
                logger.debug("OrdersManager HTTP provider session closed")
        except Exception as e:
            logger.debug(f"Error closing OrdersManager HTTP provider session: {e}")

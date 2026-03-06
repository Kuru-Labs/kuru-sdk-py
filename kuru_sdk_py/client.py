import asyncio
import signal
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Callable, Awaitable, Iterable
from loguru import logger

from kuru_sdk_py.configs import (
    MarketConfig,
    ConnectionConfig,
    WalletConfig,
    TransactionConfig,
    WebSocketConfig,
    OrderExecutionConfig,
    CacheConfig,
    ClientConfig,
    ConfigManager,
    MultiClientConfig,
    KuruMMConfig,  # Legacy - for backward compatibility
    KuruTopicsSignature,
)
from kuru_sdk_py.account_session import AccountSession
from kuru_sdk_py.manager.orders_manager import OrdersManager, SentOrders
from kuru_sdk_py.executor.orders_executor import OrdersExecutor, BatchOrderRequest
from kuru_sdk_py.feed.rpc_ws import RpcWebsocket, RpcEventRouter
from kuru_sdk_py.feed.orderbook_ws import KuruFrontendOrderbookClient, FrontendOrderbookUpdate
from kuru_sdk_py.user.user import User, AccountClient
from kuru_sdk_py.manager.order import Order, OrderStatus
from kuru_sdk_py.exceptions import KuruConfigError


class _QueueConsumer:
    """Generic queue consumer with callback invocation and graceful draining."""

    def __init__(
        self,
        name: str,
        queue: asyncio.Queue,
        callback_getter: Callable[[], Optional[Callable[[object], Awaitable[None]]]],
        shutdown_event: asyncio.Event,
    ) -> None:
        self._name = name
        self._queue = queue
        self._callback_getter = callback_getter
        self._shutdown_event = shutdown_event

    async def run(self) -> None:
        try:
            while True:
                if self._shutdown_event.is_set():
                    break

                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                callback = self._callback_getter()
                if callback is None:
                    continue

                try:
                    await callback(item)
                except Exception as e:
                    logger.error(
                        f"Error in {self._name} callback: {e}",
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Fatal error in {self._name} consumer: {e}", exc_info=True)

    async def drain_and_stop(self, task: Optional[asyncio.Task]) -> int:
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                pass

        callback = self._callback_getter()
        if callback is None:
            return 0

        remaining_count = 0
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                await callback(item)
                remaining_count += 1
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error(
                    f"Error processing remaining {self._name} item: {e}",
                    exc_info=True,
                )

        return remaining_count


@dataclass
class MarketOrderEvent:
    market_address: str
    order: Order


@dataclass
class MarketOrderbookEvent:
    market_address: str
    update: FrontendOrderbookUpdate


class KuruClient:
    """
    Unified client for managing Kuru market maker operations.

    This class initializes and manages core components:
    - OrdersManager: Manages order state and lifecycle
    - OrdersExecutor: Executes orders on-chain
    - RpcWebsocket: Listens to blockchain events via WebSocket
    - OrderbookWebsocket: (Optional) Streams real-time orderbook updates

    Usage:
        # Option 1: Manual start/stop
        client = await KuruClient.create(market_config, connection_config, wallet_config)
        await client.start()
        # ... use client ...
        await client.stop()

        # Option 2: Async context manager (recommended)
        async with await KuruClient.create(...) as client:
            await client.start()
            # ... use client ...
            pass

        # Option 3: With orderbook streaming
        async with await KuruClient.create(...) as client:
            client.set_orderbook_callback(my_orderbook_handler)
            await client.start()
            await client.subscribe_to_orderbook()
            # ... orderbook updates flow to callback ...
    """

    def __init__(self):
        """Internal constructor - use create() factory method instead."""
        raise NotImplementedError("Use KuruClient.create() async factory method")

    @classmethod
    async def create(
        cls,
        market_config: MarketConfig,
        connection_config: Optional[ConnectionConfig] = None,
        wallet_config: Optional[WalletConfig] = None,
        transaction_config: Optional[TransactionConfig] = None,
        websocket_config: Optional[WebSocketConfig] = None,
        order_execution_config: Optional[OrderExecutionConfig] = None,
        cache_config: Optional[CacheConfig] = None,
        # Legacy parameter for backward compatibility
        kuru_mm_config: Optional[KuruMMConfig] = None,
        account_session: Optional[AccountSession] = None,
        account_client: Optional[AccountClient] = None,
        event_router: Optional[RpcEventRouter] = None,
        manage_shared_resources: bool = True,
    ) -> "KuruClient":
        """
        Create and initialize KuruClient with all configurations.

        Args:
            market_config: Market-specific settings (addresses, decimals, etc.) - REQUIRED
            connection_config: Connection endpoints (RPC URLs, API URLs)
            wallet_config: Wallet configuration (private key, user address)
            transaction_config: Transaction behavior (timeouts, gas adjustments)
            websocket_config: WebSocket reconnection and heartbeat settings
            order_execution_config: Order execution defaults (post_only, auto_approve)
            cache_config: Cache TTL configuration
            kuru_mm_config: DEPRECATED - Legacy combined config for backward compatibility

        Returns:
            Initialized KuruClient instance

        Raises:
            ValueError: If required configs are missing
            ConnectionError: If RPC connection fails

        Examples:
            # New pattern (recommended)
            client = await KuruClient.create(
                market_config=market,
                connection_config=connection,
                wallet_config=wallet,
            )

            # With optional configs
            client = await KuruClient.create(
                market_config=market,
                connection_config=connection,
                wallet_config=wallet,
                transaction_config=TransactionConfig(timeout=300),
                order_execution_config=OrderExecutionConfig(post_only=False),
            )

            # Legacy pattern (still supported)
            client = await KuruClient.create(market_config, kuru_mm_config)
        """
        # Create instance without calling __init__
        self = cls.__new__(cls)

        # Handle backward compatibility with legacy kuru_mm_config
        if kuru_mm_config is not None:
            logger.warning(
                "Using deprecated kuru_mm_config parameter. "
                "Consider migrating to ConnectionConfig + WalletConfig for better security separation."
            )
            # Split legacy config into new configs
            if connection_config is None:
                connection_config = ConnectionConfig(
                    rpc_url=kuru_mm_config.rpc_url,
                    rpc_ws_url=kuru_mm_config.rpc_ws_url,
                    kuru_ws_url=kuru_mm_config.kuru_ws_url,
                    kuru_api_url=kuru_mm_config.kuru_api_url,
                )
            if wallet_config is None:
                wallet_config = WalletConfig(
                    private_key=kuru_mm_config.private_key,
                    user_address=kuru_mm_config.user_address,
                )

        # Validate required configs
        if connection_config is None:
            raise KuruConfigError(
                "connection_config is required. "
                "Use ConfigManager.load_connection_config() to create one."
            )
        if wallet_config is None:
            raise KuruConfigError(
                "wallet_config is required. "
                "Use ConfigManager.load_wallet_config() to create one."
            )

        # Use defaults for optional configs
        if transaction_config is None:
            transaction_config = TransactionConfig()
        if websocket_config is None:
            websocket_config = WebSocketConfig()
        if order_execution_config is None:
            order_execution_config = OrderExecutionConfig()
        if cache_config is None:
            cache_config = CacheConfig()

        # Store all configs
        self.market_config = market_config
        self.connection_config = connection_config
        self.wallet_config = wallet_config
        self.transaction_config = transaction_config
        self.websocket_config = websocket_config
        self.order_execution_config = order_execution_config
        self.cache_config = cache_config

        # Store legacy config for backward compatibility (if provided)
        self.kuru_mm_config = kuru_mm_config
        self._manage_shared_resources = manage_shared_resources
        self._shared_event_router = event_router

        if account_session is None:
            account_session = AccountSession(
                connection_config=connection_config,
                wallet_config=wallet_config,
                transaction_config=transaction_config,
            )
        self.account_session = account_session

        # Initialize User (manages user operations)
        self.user = User(
            market_config=market_config,
            connection_config=connection_config,
            wallet_config=wallet_config,
            transaction_config=transaction_config,
            order_execution_config=order_execution_config,
            account_session=account_session,
        )
        self.account = account_client or AccountClient(
            market_config=market_config,
            connection_config=connection_config,
            wallet_config=wallet_config,
            transaction_config=transaction_config,
            order_execution_config=order_execution_config,
            account_session=account_session,
        )

        # Initialize OrdersManager (manages order state) - now async
        self.orders_manager = await OrdersManager.create(
            connection_config=connection_config,
            cache_config=cache_config,
        )

        # Initialize OrdersExecutor (executes orders on-chain)
        self.executor = OrdersExecutor(
            market_config=market_config,
            connection_config=connection_config,
            wallet_config=wallet_config,
            transaction_config=transaction_config,
            order_execution_config=order_execution_config,
            account_session=account_session,
        )

        # Shutdown event for graceful shutdown on signals
        self._shutdown_event = asyncio.Event()

        # Task reference for log processing
        self._log_processing_task: Optional[asyncio.Task] = None

        # Order callback consumer
        self._order_callback: Optional[Callable[[Order], Awaitable[None]]] = None
        self._shared_order_callback: Optional[Callable[[Order], Awaitable[None]]] = None
        self._order_consumer = _QueueConsumer(
            name="order",
            queue=self.orders_manager.processed_orders_queue,
            callback_getter=lambda: self._dispatch_order_update,
            shutdown_event=self._shutdown_event,
        )
        self._order_consumer_task: Optional[asyncio.Task] = None

        # Orderbook websocket consumer
        self._orderbook_ws_client: Optional[KuruFrontendOrderbookClient] = None
        self._orderbook_update_queue: Optional[
            asyncio.Queue[FrontendOrderbookUpdate]
        ] = None
        self._orderbook_callback: Optional[
            Callable[[FrontendOrderbookUpdate], Awaitable[None]]
        ] = None
        self._shared_orderbook_callback: Optional[
            Callable[[FrontendOrderbookUpdate], Awaitable[None]]
        ] = None
        self._orderbook_consumer: Optional[_QueueConsumer] = None
        self._orderbook_consumer_task: Optional[asyncio.Task] = None

        if event_router is not None:
            self.websocket = event_router
            self.websocket.register_market(
                market_config=market_config,
                orders_manager=self.orders_manager,
            )
        else:
            self.websocket = RpcWebsocket(
                connection_config=connection_config,
                market_config=market_config,
                wallet_config=wallet_config,
                websocket_config=websocket_config,
                orders_manager=self.orders_manager,
                shutdown_event=self._shutdown_event,
            )
            self.websocket.set_on_reconnect(self._on_rpc_ws_reconnected)
            self.websocket.set_on_disconnect(self._on_rpc_ws_disconnected)

        self.orders_manager.set_receipt_processor(self.websocket.process_receipt_logs)

        return self

    @classmethod
    async def from_config(cls, config: ClientConfig) -> "KuruClient":
        """Create a client from a single ClientConfig bundle."""
        (
            market_config,
            connection_config,
            wallet_config,
            transaction_config,
            websocket_config,
            order_execution_config,
        ) = config.to_configs()

        return await cls.create(
            market_config=market_config,
            connection_config=connection_config,
            wallet_config=wallet_config,
            transaction_config=transaction_config,
            websocket_config=websocket_config,
            order_execution_config=order_execution_config,
        )

    def set_order_callback(
        self, callback: Optional[Callable[[Order], Awaitable[None]]]
    ) -> None:
        """
        Set callback function to automatically process orders from the queue.

        The callback will be called for each order that is placed, filled, or cancelled.
        The consumer task runs in the background during the client lifecycle.

        Args:
            callback: Async function that receives an Order object.
                      Set to None to disable the callback.

        Example:
            async def my_order_handler(order: Order):
                logger.info(f"Order {order.cloid} status: {order.status}")

            client.set_order_callback(my_order_handler)
            await client.start()

        Note:
            - If callback raises an exception, it will be logged and processing continues
            - Direct queue access (client.orders_manager.processed_orders_queue) remains available
            - Callback can be set before or after calling start()
        """
        self._order_callback = callback

        # If client is already started and callback is set, start consumer
        if (
            callback is not None
            and self._log_processing_task is not None
            and not self._log_processing_task.done()
        ):
            if self._order_consumer_task is None or self._order_consumer_task.done():
                self._order_consumer_task = asyncio.create_task(
                    self._order_consumer.run()
                )

    async def _dispatch_order_update(self, order: Order) -> None:
        for callback in (self._order_callback, self._shared_order_callback):
            if callback is None:
                continue
            await callback(order)

    def set_shared_order_callback(
        self, callback: Optional[Callable[[Order], Awaitable[None]]]
    ) -> None:
        self._shared_order_callback = callback
        if (
            callback is not None
            and self._log_processing_task is not None
            and not self._log_processing_task.done()
        ):
            if self._order_consumer_task is None or self._order_consumer_task.done():
                self._order_consumer_task = asyncio.create_task(
                    self._order_consumer.run()
                )

    def set_orderbook_callback(
        self, callback: Optional[Callable[[FrontendOrderbookUpdate], Awaitable[None]]]
    ) -> None:
        """
        Set callback function to automatically process orderbook updates from the queue.

        The callback will be called for each orderbook update (snapshot or incremental).
        The consumer task runs in the background after subscribe_to_orderbook() is called.

        Args:
            callback: Async function that receives a FrontendOrderbookUpdate object.
                      Set to None to disable the callback.

        Example:
            async def my_orderbook_handler(update: FrontendOrderbookUpdate):
                if update.b and update.a:  # Has bids and asks
                    best_bid = update.b[0][0]
                    best_ask = update.a[0][0]
                    logger.info(f"Spread: {best_ask - best_bid}")

            client.set_orderbook_callback(my_orderbook_handler)
            await client.subscribe_to_orderbook()

        Note:
            - If callback raises an exception, it will be logged and processing continues
            - Callback can be set before or after calling subscribe_to_orderbook()
        """
        self._orderbook_callback = callback

        # If client is already subscribed and callback is set, start consumer
        if (
            callback is not None
            and self._orderbook_ws_client is not None
            and self._orderbook_ws_client.is_connected()
            and self._orderbook_consumer is not None
        ):
            if (
                self._orderbook_consumer_task is None
                or self._orderbook_consumer_task.done()
            ):
                self._orderbook_consumer_task = asyncio.create_task(
                    self._orderbook_consumer.run()
                )

    async def _dispatch_orderbook_update(self, update: FrontendOrderbookUpdate) -> None:
        for callback in (self._orderbook_callback, self._shared_orderbook_callback):
            if callback is None:
                continue
            await callback(update)

    def set_shared_orderbook_callback(
        self,
        callback: Optional[Callable[[FrontendOrderbookUpdate], Awaitable[None]]],
    ) -> None:
        self._shared_orderbook_callback = callback
        if (
            callback is not None
            and self._orderbook_ws_client is not None
            and self._orderbook_ws_client.is_connected()
            and self._orderbook_consumer is not None
        ):
            if (
                self._orderbook_consumer_task is None
                or self._orderbook_consumer_task.done()
            ):
                self._orderbook_consumer_task = asyncio.create_task(
                    self._orderbook_consumer.run()
                )

    async def start(self) -> None:
        """
        Connect to websocket, subscribe to logs, and start processing.

        This method:
        1. Sets up signal handlers for graceful shutdown (SIGINT, SIGTERM)
        2. Authorizes MM Entrypoint
        3. Connects to the WebSocket
        4. Subscribes to contract events
        5. Starts processing logs in the background

        Note: OrdersManager is already connected during initialization via KuruClient.create()
        """
        if self._manage_shared_resources:
            if await self.account.has_mm_entrypoint_authorization():
                logger.info(
                    f"EIP-7702 authorization already active for user {self.account.user_address} "
                    f"to MM Entrypoint {self.account.mm_entrypoint_address}; skipping authorization tx"
                )
            else:
                logger.info(
                    f"Authorizing MM Entrypoint for user {self.account.user_address} "
                    f"with MM Entrypoint {self.account.mm_entrypoint_address}"
                )
                await self.account.eip_7702_auth()

            try:
                await self.account_session.start_gas_price_worker()
            except Exception as e:
                logger.warning(f"Failed to start shared gas price worker: {e}")

        await self.orders_manager.start()

        if self._manage_shared_resources:
            await self.websocket.connect()
            await self.websocket.create_log_subscription(KuruTopicsSignature)
            self._log_processing_task = asyncio.create_task(
                self.websocket.process_subscription_logs()
            )
        else:
            self._log_processing_task = asyncio.create_task(
                self._wait_for_shared_shutdown()
            )

        # Start order consumer if callback is set
        if self._order_callback is not None or self._shared_order_callback is not None:
            self._order_consumer_task = asyncio.create_task(self._order_consumer.run())
            logger.info("Order consumer task started")

    async def _wait_for_shared_shutdown(self) -> None:
        await self._shutdown_event.wait()

    async def place_orders(
        self,
        orders: list[Order],
        post_only: Optional[bool] = None,
        price_rounding: Optional[str] = "default",
        gas_price: Optional[int] = None,
    ) -> str:
        """
        Place orders on the market.

        Args:
            orders: List of orders to place
            post_only: Whether orders should be post-only.
                      If None, uses order_execution_config.post_only default.
            price_rounding: Tick-size rounding mode for prices.
                           "default" = round down for buys, up for sells.
                           "down" = round down for all.
                           "up" = round up for all.
                           "none" = no rounding (preserves truncation behavior).

        Returns:
            Transaction hash as hex string
        """
        # Use config default if not specified
        if post_only is None:
            post_only = self.order_execution_config.post_only

        t_start = time.perf_counter()

        t0 = time.perf_counter()
        request = BatchOrderRequest.from_orders(
            orders=orders,
            orders_manager=self.orders_manager,
            market_config=self.market_config,
            post_only=post_only,
            price_rounding=price_rounding,
        )
        dt_build_batch = time.perf_counter() - t0

        logger.info(
            f"Placing batch order: {len(request.buy_orders)} buy orders, "
            f"{len(request.sell_orders)} sell orders, {len(request.cancel_orders)} cancel orders"
        )

        # Register all orders BEFORE sending transaction
        orders_to_register = request.buy_orders + request.sell_orders
        for order in orders_to_register:
            order.update_status(OrderStatus.ORDER_SENT)
            self.orders_manager.cloid_to_order[order.cloid] = order

        t0 = time.perf_counter()
        txhash = await self.executor.place_batch(request, gas_price=gas_price)
        dt_executor = time.perf_counter() - t0

        logger.info(
            f"place_orders_timings | orders={len(orders)} "
            f"build_batch={dt_build_batch*1000:.1f}ms "
            f"executor_place={dt_executor*1000:.1f}ms "
            f"total={(time.perf_counter()-t_start)*1000:.1f}ms"
        )

        all_orders = orders_to_register + request.cancel_orders

        # Set txhash on all orders and register txhash mapping
        for order in all_orders:
            order.set_txhash(txhash)

        # Register sent orders by txhash
        self.orders_manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=request.buy_orders,
            sell_orders=request.sell_orders,
            cancel_orders=request.cancel_orders,
        )

        # Add to pending transactions cache
        await self.orders_manager.pending_transactions.set(txhash, txhash)

        return txhash

    async def place_market_buy(
        self,
        quote_amount: Decimal,
        min_amount_out: Decimal,
        is_margin: bool = True,
        is_fill_or_kill: bool = False,
        gas_price: Optional[int] = None,
    ) -> str:
        """
        Place a market buy order.

        This function buys base tokens by spending quote tokens at the best available market price.
        The order executes immediately.

        Args:
            quote_amount: Amount of quote tokens to spend
            min_amount_out: Minimum base tokens to receive (slippage protection)
            is_margin: Whether to use margin account (default: True)
            is_fill_or_kill: Execute fully or revert (default: False)

        Returns:
            Transaction hash as hex string
        """
        txhash = await self.executor.place_market_buy(
            quote_amount, min_amount_out, is_margin, is_fill_or_kill, gas_price=gas_price
        )

        # Add to pending transactions cache
        await self.orders_manager.pending_transactions.set(txhash, txhash)

        return txhash

    async def place_market_sell(
        self,
        size: Decimal,
        min_amount_out: Decimal,
        is_margin: bool = True,
        is_fill_or_kill: bool = False,
        gas_price: Optional[int] = None,
    ) -> str:
        """
        Place a market sell order.

        This function sells base tokens to receive quote tokens at the best available market price.
        The order executes immediately.

        Args:
            size: Amount of base tokens to sell
            min_amount_out: Minimum quote tokens to receive (slippage protection)
            is_margin: Whether to use margin account (default: True)
            is_fill_or_kill: Execute fully or revert (default: False)

        Returns:
            Transaction hash as hex string
        """
        txhash = await self.executor.place_market_sell(
            size, min_amount_out, is_margin, is_fill_or_kill, gas_price=gas_price
        )

        # Add to pending transactions cache
        await self.orders_manager.pending_transactions.set(txhash, txhash)

        return txhash

    async def cancel_all_active_orders_for_market(
        self, use_access_list: Optional[bool] = None, gas_price: Optional[int] = None
    ) -> None:
        """
        Cancel all active orders for the market.
        Loops until all orders are cancelled by checking the API every 3 seconds.

        Args:
            use_access_list: If True, build transaction with EIP-2930 access list for gas optimization.
                            If False, build transaction without access list.
                            If None, uses order_execution_config.use_access_list default.
        """
        # Use config default if not specified
        if use_access_list is None:
            use_access_list = self.order_execution_config.use_access_list

        logger.info(
            f"Cancelling all active orders for market {self.user.market_address}"
        )

        while True:
            # Fetch active orders from API
            orders = self.user.get_active_orders()

            # Check if there are any active orders
            if not orders:
                logger.info("No more active orders to cancel")
                break

            # Extract order data - format depends on use_access_list parameter
            orders_to_cancel = []
            for order in orders:
                order_id = order["orderid"]

                if use_access_list:
                    # Include metadata for access list optimization
                    price = int(
                        order["price"]
                    )  # Price already in precision units (from API)
                    is_buy = order["isbuy"]  # Boolean from API
                    orders_to_cancel.append((order_id, price, is_buy))
                else:
                    # Just order IDs - no access list
                    orders_to_cancel.append(order_id)

            # Log message depends on whether access list is used
            access_list_msg = (
                "with access list" if use_access_list else "without access list"
            )
            order_ids = (
                [order_id for order_id, _, _ in orders_to_cancel]
                if use_access_list
                else orders_to_cancel
            )
            logger.info(
                f"Cancelling {len(orders_to_cancel)} active orders {access_list_msg}: {order_ids}"
            )

            # Cancel orders - executor will detect format and handle appropriately
            txhash = await self.executor.cancel_orders_with_kuru_order_ids(
                orders_to_cancel, gas_price=gas_price
            )
            logger.success(
                f"Cancelled {len(orders_to_cancel)} orders with txhash: {txhash}"
            )

            # Sleep for 3 seconds before checking again
            await asyncio.sleep(3)

    async def subscribe_to_orderbook(self) -> None:
        """
        Subscribe to real-time orderbook updates via WebSocket.

        This method:
        1. Creates the orderbook WebSocket client (if not already created)
        2. Connects to the WebSocket server
        3. Subscribes to the market's orderbook feed
        4. Starts the consumer task (if callback is set)

        Note: Must call start() first to initialize the client.

        Raises:
            RuntimeError: If client not started or already subscribed
            ConnectionError: If WebSocket connection fails

        Example:
            client = await KuruClient.create(...)
            client.set_orderbook_callback(my_callback)
            await client.start()
            await client.subscribe_to_orderbook()
        """
        # Validate client is started
        if self._manage_shared_resources:
            if self._log_processing_task is None or self._log_processing_task.done():
                raise RuntimeError(
                    "Client must be started before subscribing to orderbook. Call start() first."
                )
        elif not self.websocket.is_connected:
            raise RuntimeError(
                "Shared router must be started before subscribing to orderbook. Call start() on the parent client first."
            )

        # Check if already subscribed
        if (
            self._orderbook_ws_client is not None
            and self._orderbook_ws_client.is_connected()
        ):
            logger.debug("Already subscribed to orderbook")
            return

        # Create queue if needed
        if self._orderbook_update_queue is None:
            self._orderbook_update_queue = asyncio.Queue()
        if self._orderbook_consumer is None:
            self._orderbook_consumer = _QueueConsumer(
                name="orderbook",
                queue=self._orderbook_update_queue,
                callback_getter=lambda: self._dispatch_orderbook_update,
                shutdown_event=self._shutdown_event,
            )

        # Create client if needed
        if self._orderbook_ws_client is None:
            self._orderbook_ws_client = KuruFrontendOrderbookClient(
                ws_url=self.connection_config.kuru_ws_url,
                market_address=self.market_config.market_address,
                update_queue=self._orderbook_update_queue,
                size_precision=self.market_config.size_precision,
                websocket_config=self.websocket_config,
                on_error=self._handle_orderbook_error,
            )

        # Connect and subscribe (connect() automatically subscribes internally)
        await self._orderbook_ws_client.connect()

        # Start consumer task if callback is set
        if (
            (self._orderbook_callback is not None or self._shared_orderbook_callback is not None)
            and self._orderbook_consumer is not None
        ):
            self._orderbook_consumer_task = asyncio.create_task(
                self._orderbook_consumer.run()
            )

    async def stop(self, signal_num: Optional[int] = None) -> None:
        """
        Gracefully shutdown the client.

        This method:
        1. Cancels the log processing task
        2. Cancels the order consumer task (if running)
        3. Processes any remaining orders
        4. Disconnects from the WebSocket

        Args:
            signal_num: Optional signal number if triggered by a signal
        """
        if signal_num:
            signal_name = signal.Signals(signal_num).name
            logger.info(f"Stopping client due to signal {signal_name} ({signal_num})")
        else:
            logger.info("Stopping client...")

        self._shutdown_event.set()
        # Cancel log processing task if running
        if (
            self._log_processing_task is not None
            and not self._log_processing_task.done()
        ):
            self._log_processing_task.cancel()
            try:
                await asyncio.wait_for(self._log_processing_task, timeout=3.0)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                pass

        remaining_orders = await self._order_consumer.drain_and_stop(
            self._order_consumer_task
        )
        self._order_consumer_task = None
        if remaining_orders > 0:
            logger.info(f"Processed {remaining_orders} remaining orders")

        if self._orderbook_consumer is not None:
            remaining_orderbook_updates = await self._orderbook_consumer.drain_and_stop(
                self._orderbook_consumer_task
            )
            self._orderbook_consumer_task = None
            if remaining_orderbook_updates > 0:
                logger.info(
                    f"Processed {remaining_orderbook_updates} remaining orderbook updates"
                )

        # Close orderbook websocket if connected
        if self._orderbook_ws_client is not None:
            try:
                await self._orderbook_ws_client.close()
            except Exception:
                pass

        if self._manage_shared_resources:
            await self.websocket.disconnect()

        try:
            await self.user.close()
        except Exception:
            pass

        try:
            await self.orders_manager.close()
        except Exception:
            pass

        if self._manage_shared_resources:
            try:
                await self.executor.stop_gas_price_worker()
            except Exception:
                pass

            try:
                await self.account.close()
            except Exception:
                pass

            try:
                await self.account_session.close()
            except Exception:
                pass

        logger.info("Client stopped")

    def _setup_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Setup signal handlers for graceful shutdown on SIGINT and SIGTERM.

        Args:
            loop: The asyncio event loop to register handlers with
        """
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: self._signal_handler(s, loop))

    def _signal_handler(self, sig_num: int, loop: asyncio.AbstractEventLoop) -> None:
        """
        Handle SIGINT and SIGTERM signals for graceful shutdown.

        Args:
            sig_num: The signal number received (e.g., signal.SIGINT)
            loop: The asyncio event loop
        """
        signal_name = signal.Signals(sig_num).name
        logger.info(
            f"Received signal {signal_name} ({sig_num}), initiating graceful shutdown..."
        )

        self._shutdown_event.set()

        loop.call_soon(self._schedule_stop, sig_num)

    def _schedule_stop(self, sig_num: int) -> None:
        """Schedule stop() to run in the event loop."""
        try:
            asyncio.create_task(self.stop(sig_num))
        except RuntimeError:
            pass

    def _handle_orderbook_error(self, error: Exception) -> None:
        """Handle errors from orderbook websocket client."""
        logger.error(f"Orderbook WebSocket error: {error}")
        # KuruFrontendOrderbookClient handles reconnection automatically

    def _on_rpc_ws_reconnected(self) -> None:
        """Called when the RPC WebSocket reconnects after a disconnection."""
        logger.info("RPC WebSocket reconnected — event stream resumed")

    def _on_rpc_ws_disconnected(self) -> None:
        """Called when the RPC WebSocket connection is lost (before reconnection attempt)."""
        logger.warning("RPC WebSocket disconnected — attempting reconnection")

    def is_healthy(self) -> bool:
        """
        Check if the client's RPC WebSocket connection is healthy.

        Returns:
            True if the RPC WS is connected and the log processing task is running.
        """
        ws_connected = self.websocket.is_connected

        task_alive = (
            self._log_processing_task is not None
            and not self._log_processing_task.done()
        )

        return ws_connected and task_alive

    async def __aenter__(self) -> "KuruClient":
        """
        Async context manager entry.

        Note: With factory pattern, client is already initialized by KuruClient.create()
        This method simply returns self.

        Returns:
            The KuruClient instance
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Async context manager exit - automatically stops the client.

        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred
        """
        await self.stop()


MarketClient = KuruClient


class MultiMarketClient:
    """Account-scoped client that manages multiple market-scoped clients."""

    def __init__(self):
        raise NotImplementedError("Use MultiMarketClient.create() async factory method")

    @classmethod
    async def create(
        cls,
        markets: Iterable[MarketConfig | str],
        connection_config: Optional[ConnectionConfig] = None,
        wallet_config: Optional[WalletConfig] = None,
        transaction_config: Optional[TransactionConfig] = None,
        websocket_config: Optional[WebSocketConfig] = None,
        order_execution_config: Optional[OrderExecutionConfig] = None,
        cache_config: Optional[CacheConfig] = None,
    ) -> "MultiMarketClient":
        self = cls.__new__(cls)

        if connection_config is None:
            raise KuruConfigError(
                "connection_config is required. "
                "Use ConfigManager.load_connection_config() to create one."
            )
        if wallet_config is None:
            raise KuruConfigError(
                "wallet_config is required. "
                "Use ConfigManager.load_wallet_config() to create one."
            )

        if transaction_config is None:
            transaction_config = TransactionConfig()
        if websocket_config is None:
            websocket_config = WebSocketConfig()
        if order_execution_config is None:
            order_execution_config = OrderExecutionConfig()
        if cache_config is None:
            cache_config = CacheConfig()

        self.connection_config = connection_config
        self.wallet_config = wallet_config
        self.transaction_config = transaction_config
        self.websocket_config = websocket_config
        self.order_execution_config = order_execution_config
        self.cache_config = cache_config
        self._shutdown_event = asyncio.Event()
        self._log_processing_task: Optional[asyncio.Task] = None
        self._started = False
        self._order_callback: Optional[
            Callable[[MarketOrderEvent], Awaitable[None]]
        ] = None
        self._orderbook_callback: Optional[
            Callable[[MarketOrderbookEvent], Awaitable[None]]
        ] = None

        market_configs = self._resolve_market_configs(markets, connection_config.rpc_url)
        if not market_configs:
            raise KuruConfigError("At least one market is required.")

        self._validate_shared_market_scope(market_configs)

        self.account_session = AccountSession(
            connection_config=connection_config,
            wallet_config=wallet_config,
            transaction_config=transaction_config,
        )
        self.account = AccountClient(
            market_config=market_configs[0],
            connection_config=connection_config,
            wallet_config=wallet_config,
            transaction_config=transaction_config,
            order_execution_config=order_execution_config,
            account_session=self.account_session,
        )
        self.event_router = self._new_event_router()
        self._markets: dict[str, MarketClient] = {}

        for market_config in market_configs:
            await self._create_market_client(market_config)

        return self

    @classmethod
    async def from_config(cls, config: MultiClientConfig) -> "MultiMarketClient":
        (
            market_configs,
            connection_config,
            wallet_config,
            transaction_config,
            websocket_config,
            order_execution_config,
        ) = config.to_configs()

        return await cls.create(
            markets=market_configs,
            connection_config=connection_config,
            wallet_config=wallet_config,
            transaction_config=transaction_config,
            websocket_config=websocket_config,
            order_execution_config=order_execution_config,
        )

    @staticmethod
    def _resolve_market_configs(
        markets: Iterable[MarketConfig | str],
        rpc_url: str,
    ) -> list[MarketConfig]:
        resolved: list[MarketConfig] = []
        for market in markets:
            if isinstance(market, MarketConfig):
                resolved.append(market)
            else:
                resolved.append(
                    ConfigManager.load_market_config(
                        market_address=market,
                        fetch_from_chain=True,
                        rpc_url=rpc_url,
                        auto_env=False,
                    )
                )
        return resolved

    @staticmethod
    def _validate_shared_market_scope(market_configs: list[MarketConfig]) -> None:
        first = market_configs[0]
        for market_config in market_configs[1:]:
            if (
                market_config.margin_contract_address
                != first.margin_contract_address
                or market_config.mm_entrypoint_address != first.mm_entrypoint_address
            ):
                raise KuruConfigError(
                    "All markets in a MultiMarketClient must share the same "
                    "margin contract and MM entrypoint."
                )

    def _new_event_router(self) -> RpcEventRouter:
        router = RpcEventRouter(
            connection_config=self.connection_config,
            wallet_config=self.wallet_config,
            websocket_config=self.websocket_config,
            shutdown_event=self._shutdown_event,
        )
        router.set_on_reconnect(self._on_rpc_ws_reconnected)
        router.set_on_disconnect(self._on_rpc_ws_disconnected)
        return router

    async def _create_market_client(self, market_config: MarketConfig) -> MarketClient:
        market = await KuruClient.create(
            market_config=market_config,
            connection_config=self.connection_config,
            wallet_config=self.wallet_config,
            transaction_config=self.transaction_config,
            websocket_config=self.websocket_config,
            order_execution_config=self.order_execution_config,
            cache_config=self.cache_config,
            account_session=self.account_session,
            account_client=self.account,
            event_router=self.event_router,
            manage_shared_resources=False,
        )
        market.set_shared_order_callback(
            lambda order, market_address=market_config.market_address: self._on_market_order(
                market_address, order
            )
        )
        market.set_shared_orderbook_callback(
            lambda update, market_address=market_config.market_address: self._on_market_orderbook(
                market_address, update
            )
        )
        self._markets[market_config.market_address] = market
        return market

    async def _on_market_order(self, market_address: str, order: Order) -> None:
        if self._order_callback is None:
            return
        await self._order_callback(
            MarketOrderEvent(market_address=market_address, order=order)
        )

    async def _on_market_orderbook(
        self,
        market_address: str,
        update: FrontendOrderbookUpdate,
    ) -> None:
        if self._orderbook_callback is None:
            return
        await self._orderbook_callback(
            MarketOrderbookEvent(market_address=market_address, update=update)
        )

    def set_order_callback(
        self,
        callback: Optional[Callable[[MarketOrderEvent], Awaitable[None]]],
    ) -> None:
        self._order_callback = callback

    def set_orderbook_callback(
        self,
        callback: Optional[Callable[[MarketOrderbookEvent], Awaitable[None]]],
    ) -> None:
        self._orderbook_callback = callback

    def market(self, market_address: str) -> MarketClient:
        normalized = market_address.lower()
        for existing_address, market_client in self._markets.items():
            if existing_address.lower() == normalized:
                return market_client
        raise KeyError(f"Market {market_address} is not registered")

    def list_markets(self) -> list[str]:
        return list(self._markets.keys())

    async def add_market(self, market: MarketConfig | str) -> MarketClient:
        market_config = self._resolve_market_configs([market], self.connection_config.rpc_url)[0]
        self._validate_shared_market_scope([self.account.market_config, market_config])
        existing = None
        for address, market_client in self._markets.items():
            if address.lower() == market_config.market_address.lower():
                existing = market_client
                break
        if existing is not None:
            return existing

        market_client = await self._create_market_client(market_config)
        if self._started:
            await market_client.start()
            await self._restart_router()
        return market_client

    async def remove_market(self, market_address: str) -> None:
        market_client = self.market(market_address)
        self.event_router.unregister_market(market_client.market_config.market_address)
        self._markets.pop(market_client.market_config.market_address, None)
        if self._started:
            await market_client.stop()
            if self._markets:
                await self._restart_router()
            else:
                if self._log_processing_task is not None:
                    self._log_processing_task.cancel()
                    try:
                        await self._log_processing_task
                    except asyncio.CancelledError:
                        pass
                    self._log_processing_task = None
                await self.event_router.disconnect()

    async def _restart_router(self) -> None:
        if self._log_processing_task is not None:
            self._log_processing_task.cancel()
            try:
                await self._log_processing_task
            except asyncio.CancelledError:
                pass
            self._log_processing_task = None

        try:
            await self.event_router.disconnect()
        except Exception:
            pass

        self.event_router = self._new_event_router()
        for market_client in self._markets.values():
            self.event_router.register_market(
                market_config=market_client.market_config,
                orders_manager=market_client.orders_manager,
            )
            market_client.websocket = self.event_router
            market_client.orders_manager.set_receipt_processor(
                self.event_router.process_receipt_logs
            )

        await self.event_router.connect()
        await self.event_router.create_log_subscription(KuruTopicsSignature)
        self._log_processing_task = asyncio.create_task(
            self.event_router.process_subscription_logs()
        )

    async def start(self) -> None:
        if await self.account.has_mm_entrypoint_authorization():
            logger.info(
                f"EIP-7702 authorization already active for user {self.account.user_address} "
                f"to MM Entrypoint {self.account.mm_entrypoint_address}; skipping authorization tx"
            )
        else:
            await self.account.eip_7702_auth()

        await self.account_session.start_gas_price_worker()

        for market in self._markets.values():
            await market.start()

        await self.event_router.connect()
        await self.event_router.create_log_subscription(KuruTopicsSignature)
        self._log_processing_task = asyncio.create_task(
            self.event_router.process_subscription_logs()
        )
        self._started = True

    async def stop(self) -> None:
        self._shutdown_event.set()

        if self._log_processing_task is not None:
            self._log_processing_task.cancel()
            try:
                await self._log_processing_task
            except asyncio.CancelledError:
                pass
            self._log_processing_task = None

        for market in list(self._markets.values()):
            await market.stop()

        try:
            await self.event_router.disconnect()
        except Exception:
            pass

        try:
            await self.account.close()
        except Exception:
            pass

        try:
            await self.account_session.close()
        except Exception:
            pass

        self._started = False

    def _on_rpc_ws_reconnected(self) -> None:
        logger.info("Shared RPC WebSocket reconnected - event stream resumed")

    def _on_rpc_ws_disconnected(self) -> None:
        logger.warning("Shared RPC WebSocket disconnected - attempting reconnection")

    async def __aenter__(self) -> "MultiMarketClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

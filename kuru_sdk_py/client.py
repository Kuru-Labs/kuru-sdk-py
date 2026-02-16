import asyncio
import signal
from typing import Optional, Callable, Awaitable
from loguru import logger

from kuru_sdk_py.configs import (
    MarketConfig,
    ConnectionConfig,
    WalletConfig,
    TransactionConfig,
    WebSocketConfig,
    OrderExecutionConfig,
    CacheConfig,
    KuruMMConfig,  # Legacy - for backward compatibility
    KuruTopicsSignature,
)
from kuru_sdk_py.manager.orders_manager import OrdersManager, SentOrders
from kuru_sdk_py.executor.orders_executor import OrdersExecutor
from kuru_sdk_py.feed.rpc_ws import RpcWebsocket
from kuru_sdk_py.feed.orderbook_ws import KuruFrontendOrderbookClient, FrontendOrderbookUpdate
from kuru_sdk_py.user.user import User
from kuru_sdk_py.manager.order import Order, OrderSide, OrderType, OrderStatus
from kuru_sdk_py.utils.utils import string_to_bytes32


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
            raise ValueError(
                "connection_config is required. "
                "Use ConfigManager.load_connection_config() to create one."
            )
        if wallet_config is None:
            raise ValueError(
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

        # Initialize User (manages user operations)
        self.user = User(
            market_config=market_config,
            connection_config=connection_config,
            wallet_config=wallet_config,
            transaction_config=transaction_config,
            order_execution_config=order_execution_config,
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
        )

        # Shutdown event for graceful shutdown on signals
        self._shutdown_event = asyncio.Event()

        # Task reference for log processing
        self._log_processing_task: Optional[asyncio.Task] = None

        # Order callback consumer
        self._order_callback: Optional[Callable[[Order], Awaitable[None]]] = None
        self._order_consumer_task: Optional[asyncio.Task] = None

        # Orderbook websocket consumer
        self._orderbook_ws_client: Optional[KuruFrontendOrderbookClient] = None
        self._orderbook_update_queue: Optional[
            asyncio.Queue[FrontendOrderbookUpdate]
        ] = None
        self._orderbook_callback: Optional[
            Callable[[FrontendOrderbookUpdate], Awaitable[None]]
        ] = None
        self._orderbook_consumer_task: Optional[asyncio.Task] = None

        # Initialize RpcWebsocket (listens to blockchain events)
        self.websocket = RpcWebsocket(
            connection_config=connection_config,
            market_config=market_config,
            wallet_config=wallet_config,
            websocket_config=websocket_config,
            orders_manager=self.orders_manager,
            shutdown_event=self._shutdown_event,
        )

        # Wire reconnection callbacks
        self.websocket.set_on_reconnect(self._on_rpc_ws_reconnected)
        self.websocket.set_on_disconnect(self._on_rpc_ws_disconnected)

        return self

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
                self._order_consumer_task = asyncio.create_task(self._consume_orders())

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
        ):
            if (
                self._orderbook_consumer_task is None
                or self._orderbook_consumer_task.done()
            ):
                self._orderbook_consumer_task = asyncio.create_task(
                    self._consume_orderbook_updates()
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
        # Authorize MM Entrypoint
        logger.info(
            f"Authorizing MM Entrypoint for user {self.user.user_address} with MM Entrypoint {self.user.mm_entrypoint_address}"
        )
        await self.user.eip_7702_auth()

        # Connect to websocket
        await self.websocket.connect()

        # Subscribe to logs
        await self.websocket.create_log_subscription(KuruTopicsSignature)

        # Start processing logs in background task
        self._log_processing_task = asyncio.create_task(
            self.websocket.process_subscription_logs()
        )

        # Start order consumer if callback is set
        if self._order_callback is not None:
            self._order_consumer_task = asyncio.create_task(self._consume_orders())
            logger.info("Order consumer task started")

    async def place_orders(
        self,
        orders: list[Order],
        post_only: Optional[bool] = None,
        price_rounding: Optional[str] = "default",
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

        # split the orders list into buy orders, sell orders and cancel orders
        buy_orders = [order for order in orders if order.side == OrderSide.BUY]
        sell_orders = [order for order in orders if order.side == OrderSide.SELL]
        cancel_orders = [
            order for order in orders if order.order_type == OrderType.CANCEL
        ]

        logger.info(
            f"Placing batch order: {len(buy_orders)} buy orders, "
            f"{len(sell_orders)} sell orders, {len(cancel_orders)} cancel orders"
        )

        # sort the buy orders by price in descending order
        buy_orders.sort(key=lambda x: x.price, reverse=True)
        # sort the sell orders by price in ascending order
        sell_orders.sort(key=lambda x: x.price)

        # Register all orders BEFORE sending transaction
        orders_to_register = buy_orders + sell_orders
        for order in orders_to_register:
            order.update_status(OrderStatus.ORDER_SENT)
            self.orders_manager.cloid_to_order[order.cloid] = order

        buy_cloids = [string_to_bytes32(order.cloid) for order in buy_orders]
        buy_prices = [order.price for order in buy_orders]
        buy_sizes = [order.size for order in buy_orders]

        sell_cloids = [string_to_bytes32(order.cloid) for order in sell_orders]
        sell_prices = [order.price for order in sell_orders]
        sell_sizes = [order.size for order in sell_orders]

        cancel_cloids = [string_to_bytes32(order.cloid) for order in cancel_orders]

        kuru_order_ids_to_cancel = []
        orders_to_cancel_metadata = []  # For access list optimization

        for cancel_order in cancel_orders:
            # Get kuru order ID (existing logic)
            kuru_order_id = self.orders_manager.get_kuru_order_id(cancel_order.cloid)
            if kuru_order_id is None:
                logger.error(
                    f"Kuru order ID not found for cancel order {cancel_order.cloid}"
                )
                continue

            kuru_order_ids_to_cancel.append(kuru_order_id)

            # Get original order to extract price and side for access list
            original_order = self.orders_manager.cloid_to_order.get(cancel_order.cloid)
            if (
                original_order
                and original_order.price is not None
                and original_order.side is not None
            ):
                # Convert price to integer using market precision
                price_int = int(
                    original_order.price * self.market_config.price_precision
                )
                is_buy = original_order.side == OrderSide.BUY

                orders_to_cancel_metadata.append((kuru_order_id, price_int, is_buy))
                logger.debug(
                    f"Added cancel metadata for {cancel_order.cloid}: "
                    f"order_id={kuru_order_id}, price={price_int}, is_buy={is_buy}"
                )
            else:
                logger.warning(
                    f"Could not get price/side for cancel order {cancel_order.cloid}, "
                    "access list will be less optimal"
                )

        txhash = await self.executor.place_order(
            buy_cloids,
            sell_cloids,
            cancel_cloids,
            buy_prices,
            buy_sizes,
            sell_prices,
            sell_sizes,
            kuru_order_ids_to_cancel,
            orders_to_cancel_metadata,  # Pass metadata for access list
            post_only=post_only,
            price_rounding=price_rounding,
        )

        all_orders = orders_to_register + cancel_orders

        # Set txhash on all orders and register txhash mapping
        for order in all_orders:
            order.set_txhash(txhash)

        # Register sent orders by txhash
        self.orders_manager.txhash_to_sent_orders[txhash] = SentOrders(
            buy_orders=buy_orders, sell_orders=sell_orders, cancel_orders=cancel_orders
        )

        # Add to pending transactions cache
        await self.orders_manager.pending_transactions.set(txhash, txhash)

        return txhash

    async def place_market_buy(
        self,
        quote_amount: float,
        min_amount_out: float,
        is_margin: bool = True,
        is_fill_or_kill: bool = False,
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
            quote_amount, min_amount_out, is_margin, is_fill_or_kill
        )

        # Add to pending transactions cache
        await self.orders_manager.pending_transactions.set(txhash, txhash)

        return txhash

    async def place_market_sell(
        self,
        size: float,
        min_amount_out: float,
        is_margin: bool = True,
        is_fill_or_kill: bool = False,
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
            size, min_amount_out, is_margin, is_fill_or_kill
        )

        # Add to pending transactions cache
        await self.orders_manager.pending_transactions.set(txhash, txhash)

        return txhash

    async def cancel_all_active_orders_for_market(
        self, use_access_list: Optional[bool] = None
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
                orders_to_cancel
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
        if self._log_processing_task is None or self._log_processing_task.done():
            raise RuntimeError(
                "Client must be started before subscribing to orderbook. Call start() first."
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
        if self._orderbook_callback is not None:
            self._orderbook_consumer_task = asyncio.create_task(
                self._consume_orderbook_updates()
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

        # Cancel order consumer task if running
        if (
            self._order_consumer_task is not None
            and not self._order_consumer_task.done()
        ):
            self._order_consumer_task.cancel()
            try:
                await asyncio.wait_for(self._order_consumer_task, timeout=3.0)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                pass

            # Process any remaining orders in queue before shutdown
            if self._order_callback is not None:
                logger.info("Processing remaining orders before shutdown...")
                remaining_count = 0
                while not self.orders_manager.processed_orders_queue.empty():
                    try:
                        order = self.orders_manager.processed_orders_queue.get_nowait()
                        await self._order_callback(order)
                        remaining_count += 1
                    except asyncio.QueueEmpty:
                        break
                    except Exception as e:
                        logger.error(f"Error processing remaining order: {e}")

                if remaining_count > 0:
                    logger.info(f"Processed {remaining_count} remaining orders")

        # Cancel orderbook consumer task if running
        if (
            self._orderbook_consumer_task is not None
            and not self._orderbook_consumer_task.done()
        ):
            self._orderbook_consumer_task.cancel()
            try:
                await asyncio.wait_for(self._orderbook_consumer_task, timeout=3.0)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                pass

            # Process any remaining orderbook updates in queue before shutdown
            if (
                self._orderbook_callback is not None
                and self._orderbook_update_queue is not None
            ):
                logger.info("Processing remaining orderbook updates before shutdown...")
                remaining_count = 0
                while not self._orderbook_update_queue.empty():
                    try:
                        update = self._orderbook_update_queue.get_nowait()
                        await self._orderbook_callback(update)
                        remaining_count += 1
                    except asyncio.QueueEmpty:
                        break
                    except Exception as e:
                        logger.error(
                            f"Error processing remaining orderbook update: {e}"
                        )

                if remaining_count > 0:
                    logger.info(
                        f"Processed {remaining_count} remaining orderbook updates"
                    )

        # Close orderbook websocket if connected
        if self._orderbook_ws_client is not None:
            try:
                await self._orderbook_ws_client.close()
            except Exception:
                pass

        # Disconnect websocket
        await self.websocket.disconnect()

        # Close all HTTP provider sessions
        try:
            await self.user.close()
        except Exception:
            pass

        try:
            await self.orders_manager.close()
        except Exception:
            pass

        try:
            await self.executor.close()
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

    async def _consume_orders(self) -> None:
        """
        Background task to consume orders from the queue and invoke callback.

        Runs continuously while client is active and callback is set.
        Handles exceptions from user callbacks gracefully.
        """
        try:
            while True:
                # Check for shutdown signal
                if self._shutdown_event.is_set():
                    break

                # Get order from queue with timeout to check shutdown periodically
                try:
                    order = await asyncio.wait_for(
                        self.orders_manager.processed_orders_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # No orders, continue loop to check shutdown
                    continue

                # Skip if callback was cleared
                if self._order_callback is None:
                    continue

                # Invoke callback
                try:
                    await self._order_callback(order)
                except Exception as e:
                    logger.error(
                        f"Error in order callback for order {order.cloid}: {e}",
                        exc_info=True,
                    )
                    # Continue processing - don't let one error stop the consumer

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Fatal error in order consumer: {e}", exc_info=True)

    async def _consume_orderbook_updates(self) -> None:
        """
        Background task to consume orderbook updates from the queue and invoke callback.

        Runs continuously while client is active and callback is set.
        Handles exceptions from user callbacks gracefully.
        """
        try:
            while True:
                # Check for shutdown signal
                if self._shutdown_event.is_set():
                    break

                # Get update from queue with timeout to check shutdown periodically
                try:
                    update = await asyncio.wait_for(
                        self._orderbook_update_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # No updates, continue loop to check shutdown
                    continue

                # Skip if callback was cleared
                if self._orderbook_callback is None:
                    continue

                # Invoke callback
                try:
                    await self._orderbook_callback(update)
                except Exception as e:
                    logger.error(
                        f"Error in orderbook callback: {e}",
                        exc_info=True,
                    )
                    # Continue processing - don't let one error stop the consumer

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Fatal error in orderbook consumer: {e}", exc_info=True)

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

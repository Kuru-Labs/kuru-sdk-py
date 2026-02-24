import asyncio
from decimal import Decimal
from typing import Callable, Optional
from loguru import logger
from web3 import AsyncWeb3, Web3
from web3.providers.persistent import WebSocketProvider

from kuru_sdk_py.utils import load_abi
from kuru_sdk_py.utils.utils import normalize_hex, bytes32_to_string
from kuru_sdk_py.utils.ws_utils import (
    calculate_backoff_delay,
    format_reconnect_attempts,
    parse_hex_or_int,
    BoundedDedupSet,
)
from kuru_sdk_py.manager.orders_manager import OrdersManager
from kuru_sdk_py.manager.events import (
    OrderCreatedEvent,
    OrdersCanceledEvent,
    TradeEvent,
    BatchUpdateMMEvent,
)
from kuru_sdk_py.configs import (
    KuruTopicsSignature,
    MarketConfig,
    ConnectionConfig,
    WalletConfig,
    WebSocketConfig,
)


class RpcWebsocket:
    def __init__(
        self,
        connection_config: ConnectionConfig,
        market_config: MarketConfig,
        wallet_config: WalletConfig,
        websocket_config: WebSocketConfig,
        orders_manager: OrdersManager,
        shutdown_event: Optional[asyncio.Event] = None,
    ):
        """
        Initialize the RpcWebsocket class.

        Args:
            connection_config: Connection configuration (RPC URLs)
            market_config: Market configuration (contract addresses, precision)
            wallet_config: Wallet configuration (user address)
            websocket_config: WebSocket behavior configuration
            orders_manager: The orders manager to handle the orders
            shutdown_event: Optional asyncio.Event for graceful shutdown
        """
        # Store configs
        self.connection_config = connection_config
        self.market_config = market_config
        self.wallet_config = wallet_config
        self.websocket_config = websocket_config

        # Extract commonly used values
        self.rpc_url = connection_config.rpc_ws_url
        # Keep checksummed addresses for contract creation (web3.py requirement)
        self.orderbook_address = market_config.market_address
        self.mm_entrypoint_address = market_config.mm_entrypoint_address
        self.user_address = wallet_config.user_address

        # Store lowercase versions for event filtering (case-insensitive comparison)
        self.orderbook_address_lower = self.orderbook_address.lower()
        self.mm_entrypoint_address_lower = self.mm_entrypoint_address.lower()
        self.user_address_lower = self.user_address.lower()

        # Load ABIs once (reused across reconnections)
        self._orderbook_abi = load_abi("orderbook")
        self._mm_entrypoint_abi = load_abi("mm_entrypoint")

        # Initialize Web3 with WebSocket provider
        self.w3 = AsyncWeb3(WebSocketProvider(self.rpc_url))

        # Create contract instances for both contracts (requires checksummed addresses)
        self.orderbook_contract = self.w3.eth.contract(
            address=self.orderbook_address, abi=self._orderbook_abi
        )

        self.mm_entrypoint_contract = self.w3.eth.contract(
            address=self.mm_entrypoint_address, abi=self._mm_entrypoint_abi
        )

        # HTTP Web3 instance for gap recovery (stable, survives WS reconnections)
        self._http_w3 = Web3(Web3.HTTPProvider(connection_config.rpc_url))

        self.subscription = None
        self._connected = False
        self._closing = False
        self._shutdown_event = shutdown_event

        # Topic hash mapping for all contracts
        self.events_to_topic_hashes = {}
        self._kuru_topics: Optional[dict[str, str]] = None
        self._subscription_type: Optional[str] = None
        self.orders_manager = orders_manager

        self.size_precision = market_config.size_precision
        self.price_precision = market_config.price_precision

        # Reconnection config
        self._max_reconnect_attempts = websocket_config.rpc_ws_max_reconnect_attempts
        self._reconnect_delay = websocket_config.rpc_ws_reconnect_delay
        self._max_reconnect_delay = websocket_config.rpc_ws_max_reconnect_delay
        self._reconnect_count = 0

        # Gap recovery config
        self._gap_recovery_block_buffer = websocket_config.gap_recovery_block_buffer
        self._gap_recovery_max_block_range = (
            websocket_config.gap_recovery_max_block_range
        )

        # Block tracking for gap recovery
        self._last_seen_block: Optional[int] = None

        # Event deduplication (txhash:logIndex)
        self._dedup = BoundedDedupSet(max_size=10_000)

        # Stale connection detection timeout
        self._receive_timeout = (
            websocket_config.heartbeat_interval + websocket_config.heartbeat_timeout
        )

        # Reconnection callbacks
        self._on_reconnect_callback: Optional[Callable] = None
        self._on_disconnect_callback: Optional[Callable] = None

    def set_on_reconnect(self, callback: Optional[Callable]) -> None:
        """Set callback invoked after successful reconnection."""
        self._on_reconnect_callback = callback

    def set_on_disconnect(self, callback: Optional[Callable]) -> None:
        """Set callback invoked when connection is lost (before reconnection attempt)."""
        self._on_disconnect_callback = callback

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to the RPC WebSocket."""
        return self._connected

    async def connect(self) -> None:
        """Connect to the WebSocket provider."""
        await self.w3.provider.connect()
        self._connected = True
        self._reconnect_count = 0
        logger.debug("RpcWebsocket connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket provider."""
        self._closing = True
        self._connected = False

        try:
            await asyncio.wait_for(self.w3.provider.disconnect(), timeout=5.0)
            logger.debug("RpcWebsocket disconnected successfully")
        except asyncio.TimeoutError:
            logger.warning("WebSocket disconnect timed out after 5s, forcing close")
        except Exception as e:
            logger.warning(f"Error during disconnect (may already be closed): {e}")

    async def create_log_subscription(
        self,
        kuru_topics: dict[str, str],
        subscription_type: Optional[str] = None,
    ) -> None:
        """
        Create a subscription to contract logs for specified event topics from multiple contracts.

        Args:
            kuru_topics: Dict mapping topic names to event signatures.
                        e.g., {"OrderCreated": "OrderCreated(address,uint256,bool)"}
            subscription_type: RPC subscription type to use (defaults to
                               websocket_config.rpc_logs_subscription).
                               For Monad, some RPCs support non-finalized streams like "monadLogs".

        Returns:
            The web3 subscription object
        """
        # Store for reconnection
        self._kuru_topics = kuru_topics

        subscription_type = (
            subscription_type or self.websocket_config.rpc_logs_subscription
        ).strip()
        if not subscription_type:
            raise ValueError("subscription_type cannot be empty")

        self._subscription_type = subscription_type

        await self._subscribe(kuru_topics, subscription_type)

    async def _subscribe(
        self,
        kuru_topics: dict[str, str],
        subscription_type: str,
    ) -> None:
        """Internal subscription logic, shared between initial connect and reconnect."""
        # Compute topic hashes for all Kuru events
        topic_hashes = []
        events_to_topic_hashes = {}
        for name, signature in kuru_topics.items():
            topic_hash = Web3.keccak(text=signature).hex()
            topic_hashes.append(topic_hash)
            events_to_topic_hashes[name] = topic_hash

        self.events_to_topic_hashes = events_to_topic_hashes

        # Subscribe to logs from BOTH contracts with filter
        # Note: Passing list of addresses to subscribe to multiple contracts
        params = {
            "address": [self.orderbook_address, self.user_address],
            "topics": [topic_hashes],
        }
        try:
            subscription = await self.w3.eth.subscribe(subscription_type, params)
        except Exception as e:
            msg = (
                f"Failed to create RPC WS subscription type={subscription_type!r}. "
                f"If you're trying to stream non-finalized Monad events, verify your RPC supports "
                f"that subscription type (e.g. 'monadLogs'). Original error: {e}"
            )
            logger.error(msg)
            raise RuntimeError(msg) from e

        self.subscription = subscription

        logger.info(
            f"Created subscription type={subscription_type!r} for {len(kuru_topics)} Kuru events"
        )

    async def _handle_log(self, log: dict) -> None:
        """
        Extract log metadata and route to appropriate contract handler.

        Tracks the last seen block number for gap recovery and deduplicates
        events by txhash:logIndex.

        Args:
            log: The log object from the subscription
        """
        if not log:
            return

        # Track block number for gap recovery
        block_number = parse_hex_or_int(log.get("blockNumber"))
        if block_number is not None:
            if self._last_seen_block is None or block_number > self._last_seen_block:
                self._last_seen_block = block_number

        # Deduplicate by txhash:logIndex
        txhash_raw = log.get("transactionHash")
        log_index_raw = log.get("logIndex")
        if txhash_raw is not None and log_index_raw is not None:
            dedup_key = f"{normalize_hex(txhash_raw)}:{parse_hex_or_int(log_index_raw)}"
            if not self._dedup.check_and_add(dedup_key):
                logger.debug(f"Skipping duplicate event: {dedup_key}")
                return

        topics = log.get("topics")
        topic0 = normalize_hex(topics[0]) if topics and len(topics) > 0 else None
        txhash = normalize_hex(log.get("transactionHash"))
        log_address = log.get("address").lower() if log.get("address") else None

        # Route to appropriate contract handler based on log address
        # Compare using lowercase versions for case-insensitive matching
        if log_address == self.orderbook_address_lower:
            await self._process_orderbook_log(log, topic0, txhash)
        elif log_address == self.user_address_lower:
            await self._batch_update_mm_log(log, topic0, txhash)
        elif log_address is not None:
            logger.trace(f"Log from unknown contract address: {log_address}")

    async def process_receipt_logs(self, receipt) -> None:
        """
        Process logs from a transaction receipt through the existing event pipeline.

        Used as a fallback when WebSocket events are missed — feeds receipt logs
        through _handle_log which handles deduplication, contract decoding, and
        OrdersManager event dispatch.

        Args:
            receipt: Transaction receipt (from web3 eth.get_transaction_receipt)
        """
        logs = receipt.get("logs", [])
        if not logs:
            logger.debug(f"No logs in receipt for tx {receipt.get('transactionHash', 'unknown')}")
            return

        processed = 0
        for log in logs:
            # Convert AttributeDict to regular dict for _handle_log
            log_dict = dict(log)
            # Convert nested AttributeDict fields (e.g., topics)
            if hasattr(log_dict.get("topics", []), "items"):
                log_dict["topics"] = list(log_dict["topics"])
            await self._handle_log(log_dict)
            processed += 1

        logger.info(
            f"Receipt fallback: processed {processed} logs for tx "
            f"{str(receipt.get('transactionHash', 'unknown'))[:10]}..."
        )

    async def process_subscription_logs(self) -> None:
        """
        Process logs from WebSocket subscription with automatic reconnection.

        This is a long-lived loop that survives connection drops. When the inner
        receive loop breaks (timeout / StopAsyncIteration / error), it triggers
        reconnection and resumes.
        """
        if self.subscription is None:
            logger.error(
                "Subscription is not created. Subscribe to the orderbook events first."
            )
            return

        logger.info(f"Starting log processor for subscription: {self.subscription}")

        while not self._closing:
            # Check shutdown signal if present
            if self._shutdown_event and self._shutdown_event.is_set():
                logger.info("Shutdown signal received, stopping log processor...")
                break

            try:
                if not hasattr(self.w3, "socket") or self.w3.socket is None:
                    logger.error(
                        "AsyncWeb3.socket is not available. Ensure connect() was called."
                    )
                    if not await self._reconnect():
                        break
                    continue

                subscription_iterator = self.w3.socket.process_subscriptions()

                while self._connected and not self._closing:
                    # Check shutdown signal if present
                    if self._shutdown_event and self._shutdown_event.is_set():
                        logger.info(
                            "Shutdown signal received, stopping log processor..."
                        )
                        return

                    try:
                        # Wait for next message with timeout for stale connection detection
                        response = await asyncio.wait_for(
                            subscription_iterator.__anext__(),
                            timeout=self._receive_timeout,
                        )

                        # Process the response
                        result = response.get("result")
                        if result:
                            await self._handle_log(result)

                    except asyncio.TimeoutError:
                        logger.info(
                            f"No message received in {self._receive_timeout:.0f}s — "
                            "connection may be stale, triggering reconnection"
                        )
                        break

                    except StopAsyncIteration:
                        logger.debug("WebSocket subscription ended")
                        break

                    except asyncio.CancelledError:
                        logger.info("Log processor cancelled")
                        raise

                    except Exception as e:
                        logger.error(f"Error processing subscription message: {e}")
                        break

            except asyncio.CancelledError:
                logger.info("Log processor cancelled")
                raise

            except Exception as e:
                logger.error(f"Error in subscription loop: {e}", exc_info=True)

            # If we got here, the inner loop broke — attempt reconnection
            if not self._closing:
                if self._on_disconnect_callback:
                    try:
                        self._on_disconnect_callback()
                    except Exception as e:
                        logger.error(f"Error in disconnect callback: {e}")

                if not await self._reconnect():
                    logger.error(
                        "Reconnection permanently failed, log processor stopping"
                    )
                    break

        logger.info("Log processor stopped")

    async def _reconnect(self) -> bool:
        """
        Attempt to reconnect with exponential backoff and jitter.

        Creates a fresh AsyncWeb3 + WebSocketProvider instance, recreates
        contract objects, resubscribes, and runs gap recovery.

        Returns:
            True if reconnection succeeded, False if max attempts exhausted.
        """
        while not self._closing:
            # Check if max attempts exceeded (0 = unlimited)
            if (
                self._max_reconnect_attempts > 0
                and self._reconnect_count >= self._max_reconnect_attempts
            ):
                logger.error(
                    f"Max RPC WS reconnection attempts ({self._max_reconnect_attempts}) reached"
                )
                return False

            self._reconnect_count += 1
            self._connected = False

            # Calculate exponential backoff with jitter
            delay = calculate_backoff_delay(
                self._reconnect_count - 1,
                self._reconnect_delay,
                self._max_reconnect_delay,
            )
            attempts_msg = format_reconnect_attempts(
                self._reconnect_count, self._max_reconnect_attempts
            )
            logger.info(f"RPC WS reconnection attempt {attempts_msg} in {delay:.2f}s")

            await asyncio.sleep(delay)

            if self._closing:
                return False

            # Disconnect old provider
            try:
                await asyncio.wait_for(self.w3.provider.disconnect(), timeout=5.0)
            except Exception:
                pass  # Old provider may already be dead

            # Record the block before reconnecting for gap recovery
            gap_from_block = self._last_seen_block

            try:
                # Create fresh Web3 + WebSocketProvider
                self.w3 = AsyncWeb3(WebSocketProvider(self.rpc_url))

                # Recreate contract objects on the new Web3 instance
                self.orderbook_contract = self.w3.eth.contract(
                    address=self.orderbook_address, abi=self._orderbook_abi
                )
                self.mm_entrypoint_contract = self.w3.eth.contract(
                    address=self.mm_entrypoint_address, abi=self._mm_entrypoint_abi
                )

                # Connect
                await self.w3.provider.connect()
                self._connected = True
                self._reconnect_count = 0

                logger.info("RPC WS reconnected successfully")

                # Resubscribe
                if self._kuru_topics and self._subscription_type:
                    await self._subscribe(self._kuru_topics, self._subscription_type)

                # Gap recovery
                if gap_from_block is not None:
                    await self._recover_missed_events(gap_from_block)

                # Invoke reconnect callback
                if self._on_reconnect_callback:
                    try:
                        self._on_reconnect_callback()
                    except Exception as e:
                        logger.error(f"Error in reconnect callback: {e}")

                return True

            except Exception as e:
                logger.error(f"RPC WS reconnection failed: {e}")
                # Loop will retry with next backoff
                continue

        return False

    async def _recover_missed_events(self, from_block: int) -> None:
        """
        Fetch missed events via eth_getLogs (HTTP) and process them.

        Uses the HTTP Web3 instance for stability. Processes in chunks
        of gap_recovery_max_block_range blocks. Events are deduplicated
        by _handle_log via txhash:logIndex.

        Args:
            from_block: The last block number seen before disconnection.
        """
        try:
            current_block = self._http_w3.eth.block_number
        except Exception as e:
            logger.error(f"Gap recovery: failed to get current block number: {e}")
            return

        # Apply buffer (look back extra blocks for safety)
        start_block = max(0, from_block - self._gap_recovery_block_buffer)
        end_block = current_block

        if start_block >= end_block:
            logger.debug("Gap recovery: no blocks to recover")
            return

        total_blocks = end_block - start_block
        logger.info(
            f"Gap recovery: fetching events from block {start_block} to {end_block} "
            f"({total_blocks} blocks)"
        )

        # Build topic filter (same as subscription)
        topic_hashes = list(self.events_to_topic_hashes.values())

        # Process in chunks
        chunk_start = start_block
        total_events = 0

        while chunk_start <= end_block:
            chunk_end = min(
                chunk_start + self._gap_recovery_max_block_range - 1, end_block
            )

            try:
                logs = self._http_w3.eth.get_logs(
                    {
                        "fromBlock": chunk_start,
                        "toBlock": chunk_end,
                        "address": [self.orderbook_address, self.user_address],
                        "topics": [topic_hashes],
                    }
                )

                for log in logs:
                    # Convert AttributeDict to regular dict for _handle_log
                    log_dict = dict(log)
                    await self._handle_log(log_dict)
                    total_events += 1

            except Exception as e:
                logger.error(
                    f"Gap recovery: error fetching logs for blocks {chunk_start}-{chunk_end}: {e}"
                )
                # Continue with next chunk — partial recovery is better than none

            chunk_start = chunk_end + 1

        logger.info(f"Gap recovery: processed {total_events} events")

    async def _process_orderbook_log(self, log, topic0: str, txhash: str) -> None:
        """Process logs from orderbook contract."""
        if topic0 == self.events_to_topic_hashes.get("OrderCreated"):
            decoded = self.orderbook_contract.events.OrderCreated().process_log(log)
            args = decoded["args"]

            if args["owner"].lower() != self.user_address_lower:
                return

            event = OrderCreatedEvent(
                order_id=args["orderId"],
                owner=args["owner"],
                size=Decimal(args["size"]) / Decimal(self.size_precision),
                price=args["price"],
                is_buy=args["isBuy"],
                txhash=txhash,
                log_index=log["logIndex"],
            )
            await self.orders_manager.on_order_created(event)

        elif topic0 == self.events_to_topic_hashes.get("OrdersCanceled"):
            decoded = self.orderbook_contract.events.OrdersCanceled().process_log(log)
            args = decoded["args"]

            if args["owner"].lower() != self.user_address_lower:
                return

            event = OrdersCanceledEvent(
                order_ids=list(args["orderId"]),
                owner=args["owner"],
                txhash=txhash,
            )
            await self.orders_manager.on_orders_cancelled(event)

        elif topic0 == self.events_to_topic_hashes.get("Trade"):
            decoded = self.orderbook_contract.events.Trade().process_log(log)
            args = decoded["args"]

            maker = args["makerAddress"].lower()
            taker = args["takerAddress"].lower()
            user = self.user_address_lower

            if maker != user and taker != user:
                return

            event = TradeEvent(
                order_id=args["orderId"],
                maker_address=args["makerAddress"],
                is_buy=args["isBuy"],
                price=args["price"],
                updated_size=Decimal(args["updatedSize"]) / Decimal(self.size_precision),
                taker_address=args["takerAddress"],
                tx_origin=args["txOrigin"],
                filled_size=Decimal(args["filledSize"]) / Decimal(self.size_precision),
                txhash=txhash,
            )
            await self.orders_manager.on_trade(event)

        else:
            logger.trace(f"Unknown orderbook topic: {topic0}")

    async def _batch_update_mm_log(self, log, topic0: str, txhash: str) -> None:
        """Process logs from MM entrypoint contract."""
        if topic0 == self.events_to_topic_hashes.get("batchUpdate"):
            decoded = self.mm_entrypoint_contract.events.batchUpdate().process_log(log)
            args = decoded["args"]

            # Extract cloid arrays (bytes32[])
            buy_cloids = [
                bytes32_to_string(cloid) if isinstance(cloid, bytes) else cloid
                for cloid in args["buyCloids"]
            ]
            sell_cloids = [
                bytes32_to_string(cloid) if isinstance(cloid, bytes) else cloid
                for cloid in args["sellCloids"]
            ]
            cancel_cloids = [
                bytes32_to_string(cloid) if isinstance(cloid, bytes) else cloid
                for cloid in args["cancelCloids"]
            ]

            event = BatchUpdateMMEvent(
                buy_cloids=buy_cloids,
                sell_cloids=sell_cloids,
                cancel_cloids=cancel_cloids,
                txhash=txhash,
            )
            await self.orders_manager.on_batch_update_mm(event)

        else:
            logger.trace(f"Unknown MM entrypoint topic: {topic0}")

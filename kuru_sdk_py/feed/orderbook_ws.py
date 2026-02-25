import asyncio
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, List, Optional, Tuple

import websockets.asyncio.client
import websockets.exceptions
from loguru import logger

from kuru_sdk_py.configs import WebSocketConfig
from kuru_sdk_py.utils.ws_utils import calculate_backoff_delay, format_reconnect_attempts


# === WEBSOCKET FORMAT CONSTANTS ===

# WebSocket Frontend Orderbook Format Constants
# The frontend orderbook WebSocket uses the following format:
# - Prices: Always in 10^18 format (universal across all markets)
# - Sizes: In the market's size_precision format (varies by market)
WEBSOCKET_PRICE_PRECISION = (
    1_000_000_000_000_000_000  # 10^18 - prices always in wei-like format
)
WEBSOCKET_PRICE_PRECISION_DECIMAL = Decimal(WEBSOCKET_PRICE_PRECISION)


# === DATACLASSES ===


@dataclass
class SubscriptionRequest:
    """Subscription request to server."""

    type: str  # "subscribe"
    channel: str  # "frontendOrderbook"
    market: str  # Market address (lowercase)


@dataclass
class MarketParams:
    """Market configuration parameters."""

    price_precision: int
    size_precision: int
    base_asset: str
    base_asset_decimals: int
    quote_asset: str
    quote_asset_decimals: int
    tick_size: int
    min_size: int
    max_size: int
    taker_fee_bps: int
    maker_fee_bps: int


@dataclass
class VaultParams:
    """Vault liquidity parameters."""

    vault_best_bid: int
    bid_partially_filled_size: int
    vault_best_ask: int
    ask_partially_filled_size: int
    vault_bid_order_size: int
    vault_ask_order_size: int
    spread: int


@dataclass
class SubscriptionResponse:
    """Response to subscription request."""

    type: str  # "subscribed"
    status: str  # "success" | "pending" | "error"
    message: Optional[str] = None
    data: Optional[dict] = None  # FrontendOrderbookData as dict


@dataclass
class FrontendEvent:
    """Individual orderbook event.

    Prices (p) and sizes (s) are pre-normalized to human-readable floats.
    """

    e: str  # Event type (e.g., "Trade", "OrderCreated", etc.)
    ts: int  # Timestamp
    mad: str  # Market address
    th: Optional[str] = None  # Transaction hash
    p: Optional[Decimal] = None  # Price (human-readable Decimal)
    s: Optional[Decimal] = None  # Size (human-readable Decimal)
    ib: Optional[bool] = None  # Is buy
    t: Optional[str] = None  # Taker address
    m: Optional[str] = None  # Maker address


@dataclass
class FrontendOrderbookUpdate:
    """Incremental orderbook update.

    Prices and sizes in bids/asks are pre-normalized to human-readable floats.
    """

    events: List[FrontendEvent]
    b: Optional[List[Tuple[Decimal, Decimal]]] = (
        None  # Bids: [(price, size), ...]
    )
    a: Optional[List[Tuple[Decimal, Decimal]]] = (
        None  # Asks: [(price, size), ...]
    )
    v: Optional[VaultParams] = None  # Updated vault params


# === MAIN CLIENT CLASS ===


class KuruFrontendOrderbookClient:
    """
    WebSocket client for Kuru frontend orderbook data.

    Features:
    - Automatic reconnection with exponential backoff
    - Built-in heartbeat monitoring
    - BigInt parsing for large numbers
    - Type-safe dataclass structures
    - Async context manager support
    - Queue-based orderbook updates

    Args:
        ws_url: WebSocket server URL (ws:// or wss://)
        market_address: Market contract address
        update_queue: asyncio.Queue to receive orderbook updates
        websocket_config: WebSocket behavior configuration
        on_error: Optional callback for errors
    """

    def __init__(
        self,
        ws_url: str,
        market_address: str,
        update_queue: asyncio.Queue[FrontendOrderbookUpdate],
        size_precision: int = 1,
        websocket_config: Optional[WebSocketConfig] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """
        Initialize the KuruFrontendOrderbookClient.

        Args:
            ws_url: WebSocket server URL
            market_address: Market contract address
            update_queue: Queue to receive orderbook updates
            size_precision: Market's size precision divisor for normalizing sizes
            websocket_config: WebSocket behavior configuration.
                            If None, uses default WebSocketConfig()
            on_error: Optional callback for errors

        Raises:
            ValueError: If any validation fails
        """
        # Use default config if not provided
        if websocket_config is None:
            websocket_config = WebSocketConfig()

        # Store config
        self.websocket_config = websocket_config

        # Validation
        if not ws_url:
            raise ValueError("ws_url cannot be empty")
        if not ws_url.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")
        if not market_address:
            raise ValueError("market_address cannot be empty")
        if not isinstance(update_queue, asyncio.Queue):
            raise ValueError("update_queue must be an asyncio.Queue")

        # Connection parameters
        self._ws_url = ws_url
        self._market_address = market_address.lower()  # Normalize to lowercase
        self._update_queue = update_queue
        self._size_precision = size_precision
        self._on_error = on_error

        # Get reconnection parameters from config
        self._max_reconnect_attempts = websocket_config.max_reconnect_attempts
        self._reconnect_delay = websocket_config.reconnect_delay

        # Connection state
        self._websocket: Optional[websockets.asyncio.client.ClientConnection] = None
        self._connected = False
        self._closing = False
        self._subscribed = False
        self._initial_snapshot_received = False

        # Reconnection state
        self._reconnect_count = 0
        self._current_reconnect_delay = self._reconnect_delay
        self._reconnect_task: Optional[asyncio.Task] = None

        # Background tasks
        self._message_loop_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # Heartbeat configuration from config
        self._heartbeat_interval = websocket_config.heartbeat_interval
        self._heartbeat_timeout = websocket_config.heartbeat_timeout

        # Thread safety
        self._lock = asyncio.Lock()

    @staticmethod
    def format_websocket_price(raw_price: int) -> Decimal:
        """
        Convert WebSocket price to human-readable Decimal.

        Note: Queue data is now pre-normalized. This method is only needed
        if you are working with raw WebSocket data outside the client.

        Args:
            raw_price: Raw price from WebSocket (in 10^18 format)

        Returns:
            Human-readable price as Decimal

        Example:
            241470000000000000000 -> Decimal('241.47')
        """
        return Decimal(raw_price) / WEBSOCKET_PRICE_PRECISION_DECIMAL

    @staticmethod
    def format_websocket_size(raw_size: int, size_precision: int) -> Decimal:
        """
        Convert WebSocket size to human-readable Decimal.

        Note: Queue data is now pre-normalized. This method is only needed
        if you are working with raw WebSocket data outside the client.

        Args:
            raw_size: Raw size from WebSocket (in size_precision format)
            size_precision: Size precision from MarketConfig

        Returns:
            Size as Decimal

        Example:
            format_websocket_size(100000000000, 10000000000) -> Decimal('10')
        """
        if size_precision == 0:
            return Decimal(raw_size)
        return Decimal(raw_size) / Decimal(size_precision)

    async def connect(self) -> None:
        """
        Establish WebSocket connection and start message processing.

        This method:
        1. Connects to the WebSocket server
        2. Starts the message processing loop
        3. Starts the heartbeat monitor
        4. Subscribes to the market orderbook

        Raises:
            RuntimeError: If already connected or closing
            ConnectionError: If connection fails
        """
        async with self._lock:
            await self._connect_unlocked()

    async def _connect_unlocked(self) -> None:
        """Connect without lock re-entry.

        Caller must hold ``self._lock``.
        """
        if self._connected:
            logger.debug("Already connected")
            return

        if self._closing:
            raise RuntimeError("Cannot connect while closing")

        try:
            logger.info(f"Connecting to {self._ws_url}")

            # Use websockets.asyncio.client.connect with built-in ping/pong
            self._websocket = await websockets.asyncio.client.connect(
                self._ws_url,
                ping_interval=self._heartbeat_interval,
                ping_timeout=self._heartbeat_timeout,
                open_timeout=10.0,
                close_timeout=10.0,
                max_size=10 * 1024 * 1024,  # 10MB max message size
            )

            self._connected = True
            self._reconnect_count = 0  # Reset on successful connection

            logger.info("WebSocket connected successfully")

            # Start background tasks
            self._message_loop_task = asyncio.create_task(self._message_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

            # Subscribe to market
            await self.subscribe()

        except websockets.exceptions.InvalidURI as e:
            self._connected = False
            self._websocket = None
            logger.error(f"Invalid WebSocket URI: {e}")
            raise ValueError(f"Invalid WebSocket URI: {self._ws_url}") from e
        except OSError as e:
            self._connected = False
            self._websocket = None
            logger.error(f"Network error: {e}")
            raise ConnectionError(f"Failed to connect to {self._ws_url}") from e
        except Exception as e:
            self._connected = False
            self._websocket = None
            logger.error(f"Connection failed: {e}")
            raise

    async def _start_reconnect_task(self) -> None:
        """Start a single reconnect loop if one is not already running."""
        async with self._lock:
            if self._connected:
                return
            if self._closing:
                return
            if self._reconnect_task is not None and not self._reconnect_task.done():
                return
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def subscribe(self) -> None:
        """
        Subscribe to the market's frontend orderbook.

        Sends subscription request and waits for confirmation.

        Raises:
            RuntimeError: If not connected
        """
        if not self._connected or self._websocket is None:
            raise RuntimeError("Cannot subscribe - not connected")

        if self._subscribed:
            logger.debug("Already subscribed")
            return

        # Build subscription request
        request_dict = {
            "type": "subscribe",
            "channel": "frontendOrderbook",
            "market": self._market_address,
        }

        try:
            await self._websocket.send(json.dumps(request_dict))
        except Exception as e:
            logger.error(f"Failed to send subscription request: {e}")
            raise

    async def close(self) -> None:
        """
        Close the WebSocket connection and cleanup resources.

        This method:
        1. Sets closing flag to prevent reconnection
        2. Cancels background tasks
        3. Closes WebSocket connection
        4. Resets state
        """
        reconnect_task: Optional[asyncio.Task] = None
        async with self._lock:
            if self._closing:
                return

            self._closing = True
            reconnect_task = self._reconnect_task
            self._reconnect_task = None
            logger.info("Closing WebSocket connection")

        await self._cleanup_connection()

        if reconnect_task is not None and not reconnect_task.done():
            reconnect_task.cancel()
            try:
                await reconnect_task
            except asyncio.CancelledError:
                pass

        logger.info("WebSocket closed successfully")

    def is_connected(self) -> bool:
        """
        Check if currently connected to WebSocket.

        Returns:
            True if connected, False otherwise
        """
        return self._connected and self._websocket is not None

    async def _cleanup_connection(self) -> None:
        """Clean up connection resources."""
        current_task = asyncio.current_task()
        # Cancel background tasks
        if self._message_loop_task:
            self._message_loop_task.cancel()
            if self._message_loop_task is not current_task:
                try:
                    await self._message_loop_task
                except asyncio.CancelledError:
                    pass
            self._message_loop_task = None

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            if self._heartbeat_task is not current_task:
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
            self._heartbeat_task = None

        # Close WebSocket
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

        self._connected = False
        self._subscribed = False
        self._initial_snapshot_received = False

    async def _reconnect(self) -> None:
        """
        Attempt to reconnect with exponential backoff.

        Backoff formula: delay = base_delay * (2 ^ attempt) + jitter
        Max delay capped at 60 seconds.
        """
        try:
            while True:
                async with self._lock:
                    if self._closing:
                        logger.info("Not reconnecting - client is closing")
                        return

                    if (
                        self._max_reconnect_attempts > 0
                        and self._reconnect_count >= self._max_reconnect_attempts
                    ):
                        error_msg = (
                            f"Max reconnection attempts ({self._max_reconnect_attempts}) reached"
                        )
                        logger.error(error_msg)
                        await self._invoke_error_callback(ConnectionError(error_msg))
                        return

                    # Calculate exponential backoff with jitter
                    self._current_reconnect_delay = calculate_backoff_delay(
                        self._reconnect_count, self._reconnect_delay, 60.0
                    )
                    self._reconnect_count += 1
                    attempts_msg = format_reconnect_attempts(
                        self._reconnect_count, self._max_reconnect_attempts
                    )
                    delay = self._current_reconnect_delay

                logger.info(f"Reconnection attempt {attempts_msg} in {delay:.2f}s")
                await asyncio.sleep(delay)

                async with self._lock:
                    if self._closing:
                        return

                try:
                    await self._cleanup_connection()
                    await self.connect()
                    logger.info("Reconnection successful")
                    return
                except Exception as e:
                    logger.error(f"Reconnection failed: {e}")
                    continue
        finally:
            async with self._lock:
                if self._reconnect_task is asyncio.current_task():
                    self._reconnect_task = None

    async def _message_loop(self) -> None:
        """
        Main message processing loop.

        Continuously receives and processes messages from WebSocket.
        Handles connection errors and triggers reconnection.
        """
        logger.info("Message loop started")
        connection_lost_detected = False

        try:
            while self._connected and not self._closing:
                if self._websocket is None:
                    logger.debug("WebSocket is None in message loop")
                    connection_lost_detected = True
                    break

                try:
                    # Receive message with timeout
                    message = await asyncio.wait_for(
                        self._websocket.recv(),
                        timeout=self._heartbeat_interval
                        + self._heartbeat_timeout
                        + 5.0,
                    )

                    # Process message
                    self._handle_message(message)

                except asyncio.TimeoutError:
                    logger.info("Message receive timeout - connection may be stale")
                    connection_lost_detected = True
                    break

                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"WebSocket connection closed: {e}")
                    connection_lost_detected = True
                    break

                except Exception as e:
                    logger.error(f"Error receiving message: {e}")
                    await self._invoke_error_callback(e)
                    # Continue loop - don't break on parse errors

        except asyncio.CancelledError:
            pass

        except Exception as e:
            logger.error(f"Fatal error in message loop: {e}")
            await self._invoke_error_callback(e)
            connection_lost_detected = True
        finally:
            if connection_lost_detected and not self._closing:
                await self._handle_connection_loss()

    def _handle_message(self, message: str) -> None:
        """
        Parse and route incoming message to appropriate handler.

        Args:
            message: Raw JSON message string
        """
        try:
            data = json.loads(message)

            # Detect message type
            msg_type = data.get("type")

            if msg_type == "subscribed":
                # Subscription response
                self._handle_subscription_response(data)

            elif msg_type == "snapshot" or (
                msg_type is None and "b" in data and "a" in data
            ):
                # Initial snapshot or update with full orderbook
                # Check if this is first snapshot
                if not self._initial_snapshot_received:
                    self._initial_snapshot_received = True
                    asyncio.create_task(self._handle_initial_snapshot(data))
                else:
                    # It's an update with full orderbook state
                    asyncio.create_task(self._handle_orderbook_update(data))

            elif "events" in data:
                # Incremental update with events
                asyncio.create_task(self._handle_orderbook_update(data))

            else:
                logger.trace(
                    f"Unknown message type: {msg_type}, data keys: {list(data.keys())}"
                )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON message: {e}")
            asyncio.create_task(
                self._invoke_error_callback(ValueError(f"Invalid JSON: {e}"))
            )

        except Exception as e:
            logger.error(f"Error handling message: {e}")
            asyncio.create_task(self._invoke_error_callback(e))

    def _handle_subscription_response(self, data: dict) -> None:
        """
        Handle subscription response message.

        Args:
            data: Parsed JSON message
        """
        try:
            response = SubscriptionResponse(
                type=data.get("type"),
                status=data.get("status"),
                message=data.get("message"),
                data=data.get("data"),
            )

            if response.status == "success":
                self._subscribed = True

                # If initial data provided, process it as snapshot
                if response.data:
                    asyncio.create_task(self._handle_initial_snapshot(response.data))

            elif response.status == "pending":
                pass  # Wait for subsequent message

            else:
                error_msg = f"Subscription failed: {response.message}"
                logger.error(error_msg)
                asyncio.create_task(self._invoke_error_callback(ValueError(error_msg)))

        except Exception as e:
            logger.error(f"Error handling subscription response: {e}")
            asyncio.create_task(self._invoke_error_callback(e))

    async def _handle_initial_snapshot(self, data: dict) -> None:
        """
        Handle initial orderbook snapshot.

        Args:
            data: FrontendOrderbookData dict
        """
        try:
            # Parse bids and asks, normalizing to human-readable Decimals
            size_precision_decimal = Decimal(self._size_precision)
            bids = [
                (
                    Decimal(self._parse_big_int(bid[0])) / WEBSOCKET_PRICE_PRECISION_DECIMAL,
                    Decimal(self._parse_big_int(bid[1])) / size_precision_decimal,
                )
                for bid in data.get("b", [])
            ]
            asks = [
                (
                    Decimal(self._parse_big_int(ask[0])) / WEBSOCKET_PRICE_PRECISION_DECIMAL,
                    Decimal(self._parse_big_int(ask[1])) / size_precision_decimal,
                )
                for ask in data.get("a", [])
            ]

            # Parse vault params if present
            vault_params = None
            if "vp" in data and data["vp"]:
                vault_params = self._parse_vault_params(data["vp"])

            # Create update with empty events for initial snapshot
            update = FrontendOrderbookUpdate(
                events=[],
                b=bids,
                a=asks,
                v=vault_params,
            )

            # Put update on queue
            await self._update_queue.put(update)

        except Exception as e:
            logger.error(f"Error handling initial snapshot: {e}")
            asyncio.create_task(self._invoke_error_callback(e))

    async def _handle_orderbook_update(self, data: dict) -> None:
        """
        Handle incremental orderbook update.

        Args:
            data: FrontendOrderbookUpdate dict
        """
        try:
            # Parse events
            events = []
            if "events" in data:
                for event_data in data["events"]:
                    events.append(self._parse_frontend_event(event_data))

            # Parse optional fields, normalizing to human-readable Decimals
            size_precision_decimal = Decimal(self._size_precision)
            bids = None
            if "b" in data and data["b"]:
                bids = [
                    (
                        Decimal(self._parse_big_int(bid[0])) / WEBSOCKET_PRICE_PRECISION_DECIMAL,
                        Decimal(self._parse_big_int(bid[1])) / size_precision_decimal,
                    )
                    for bid in data["b"]
                ]

            asks = None
            if "a" in data and data["a"]:
                asks = [
                    (
                        Decimal(self._parse_big_int(ask[0])) / WEBSOCKET_PRICE_PRECISION_DECIMAL,
                        Decimal(self._parse_big_int(ask[1])) / size_precision_decimal,
                    )
                    for ask in data["a"]
                ]

            vault_params = None
            if "v" in data and data["v"]:
                vault_params = self._parse_vault_params(data["v"])

            # Create update
            update = FrontendOrderbookUpdate(
                events=events,
                b=bids,
                a=asks,
                v=vault_params,
            )

            # Put update on queue
            await self._update_queue.put(update)

        except Exception as e:
            logger.error(f"Error handling orderbook update: {e}")
            asyncio.create_task(self._invoke_error_callback(e))

    def _parse_big_int(self, value: Any) -> int:
        """
        Parse BigInt value from various formats to Python int.

        Handles:
        - int: Return as-is
        - str: Parse as decimal or hexadecimal integer (supports '0x' prefix)
        - None: Return 0
        - Other: Attempt int() conversion

        Args:
            value: Value to parse

        Returns:
            Python int

        Raises:
            ValueError: If value cannot be parsed as integer
        """
        if value is None:
            return 0

        if isinstance(value, int):
            return value

        if isinstance(value, str):
            try:
                # Remove any whitespace
                value_stripped = value.strip()

                # Check if it's a hexadecimal string (starts with '0x')
                if value_stripped.startswith("0x") or value_stripped.startswith("0X"):
                    return int(value_stripped, 16)
                else:
                    # Parse as decimal
                    return int(value_stripped)
            except ValueError as e:
                logger.error(f"Failed to parse string as int: '{value}'")
                raise ValueError(f"Invalid integer string: {value}") from e

        # Attempt generic int() conversion
        try:
            return int(value)
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to convert {type(value)} to int: {value}")
            raise ValueError(f"Cannot convert {type(value).__name__} to int") from e

    def _parse_market_params(self, data: dict) -> MarketParams:
        """
        Convert dict to MarketParams dataclass.

        Args:
            data: Dict with market params

        Returns:
            MarketParams dataclass
        """
        return MarketParams(
            price_precision=self._parse_big_int(data.get("price_precision", 0)),
            size_precision=self._parse_big_int(data.get("size_precision", 0)),
            base_asset=data.get("base_asset", ""),
            base_asset_decimals=self._parse_big_int(data.get("base_asset_decimals", 0)),
            quote_asset=data.get("quote_asset", ""),
            quote_asset_decimals=self._parse_big_int(
                data.get("quote_asset_decimals", 0)
            ),
            tick_size=self._parse_big_int(data.get("tick_size", 0)),
            min_size=self._parse_big_int(data.get("min_size", 0)),
            max_size=self._parse_big_int(data.get("max_size", 0)),
            taker_fee_bps=self._parse_big_int(data.get("taker_fee_bps", 0)),
            maker_fee_bps=self._parse_big_int(data.get("maker_fee_bps", 0)),
        )

    def _parse_vault_params(self, data: dict) -> VaultParams:
        """
        Convert dict to VaultParams dataclass.

        Args:
            data: Dict with vault params

        Returns:
            VaultParams dataclass
        """
        return VaultParams(
            vault_best_bid=self._parse_big_int(data.get("vault_best_bid", 0)),
            bid_partially_filled_size=self._parse_big_int(
                data.get("bid_partially_filled_size", 0)
            ),
            vault_best_ask=self._parse_big_int(data.get("vault_best_ask", 0)),
            ask_partially_filled_size=self._parse_big_int(
                data.get("ask_partially_filled_size", 0)
            ),
            vault_bid_order_size=self._parse_big_int(
                data.get("vault_bid_order_size", 0)
            ),
            vault_ask_order_size=self._parse_big_int(
                data.get("vault_ask_order_size", 0)
            ),
            spread=self._parse_big_int(data.get("spread", 0)),
        )

    def _parse_frontend_event(self, data: dict) -> FrontendEvent:
        """
        Convert dict to FrontendEvent dataclass.

        Args:
            data: Dict with event data

        Returns:
            FrontendEvent dataclass
        """
        return FrontendEvent(
            e=data["e"],  # Required
            ts=self._parse_big_int(data["ts"]),  # Required - handle hex timestamps
            mad=data["mad"],  # Required
            th=data.get("th"),
            p=(
                Decimal(self._parse_big_int(data["p"])) / WEBSOCKET_PRICE_PRECISION_DECIMAL
                if "p" in data and data["p"] is not None
                else None
            ),
            s=(
                Decimal(self._parse_big_int(data["s"])) / Decimal(self._size_precision)
                if "s" in data and data["s"] is not None
                else None
            ),
            ib=data.get("ib"),
            t=data.get("t"),
            m=data.get("m"),
        )

    async def _heartbeat_monitor(self) -> None:
        """
        Monitor connection health using websockets' built-in ping/pong.

        This monitor checks if the connection is alive by relying on
        websockets library's automatic ping/pong mechanism. If the
        connection becomes stale, trigger reconnection.
        """
        while self._connected and not self._closing:
            try:
                await asyncio.sleep(self._heartbeat_interval)

                # Check if websocket is still open
                if self._websocket is None:
                    asyncio.create_task(self._handle_connection_loss())
                    break

                # websockets library handles ping/pong automatically
                # If connection is stale, it will raise an exception
                # We just need to catch it in the message loop

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat monitor error: {e}")
                await self._invoke_error_callback(e)
                asyncio.create_task(self._handle_connection_loss())
                break

    async def _handle_connection_loss(self) -> None:
        """
        Handle detected connection loss.

        This method:
        1. Logs the connection loss
        2. Cleans up current connection
        3. Initiates reconnection
        """
        if self._closing:
            return

        async with self._lock:
            self._connected = False

        await self._invoke_error_callback(ConnectionError("Connection lost"))

        # Initiate reconnection
        await self._start_reconnect_task()

    async def _invoke_error_callback(self, error: Exception) -> None:
        """
        Invoke the on_error callback if provided.

        Args:
            error: Exception to pass to callback
        """
        if self._on_error is None:
            return

        try:
            if asyncio.iscoroutinefunction(self._on_error):
                await self._on_error(error)
            else:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._on_error, error)

        except Exception as e:
            logger.error(f"Error in on_error callback: {e}", exc_info=True)

    async def __aenter__(self) -> "KuruFrontendOrderbookClient":
        """
        Async context manager entry.

        Automatically connects when entering the context.

        Returns:
            Self for use in the context
        """
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[Exception],
        exc_tb: Optional[Any],
    ) -> bool:
        """
        Async context manager exit.

        Automatically closes connection when exiting the context.

        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred

        Returns:
            False to propagate any exceptions
        """
        await self.close()
        return False  # Propagate exceptions

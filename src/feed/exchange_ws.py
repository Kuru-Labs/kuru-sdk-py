import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple, Union

import websockets.asyncio.client
import websockets.exceptions
from loguru import logger

from src.configs import WebSocketConfig
from src.utils.ws_utils import calculate_backoff_delay, format_reconnect_attempts


# === DATACLASSES ===


@dataclass
class DepthUpdate:
    """
    Standard Binance-compatible depth update.

    Attributes:
        e: Event type ("depthUpdate")
        E: Event time (milliseconds since epoch)
        s: Symbol (market address)
        U: First update ID in event
        u: Final update ID in event
        b: Bids as list of [price, size] pairs (U256 strings)
        a: Asks as list of [price, size] pairs (U256 strings)
    """

    e: str  # Event type: "depthUpdate"
    E: int  # Event time (milliseconds)
    s: str  # Symbol (market address)
    U: int  # First update ID
    u: int  # Final update ID
    b: List[Tuple[str, str]]  # Bids: [(price, size), ...]
    a: List[Tuple[str, str]]  # Asks: [(price, size), ...]


@dataclass
class MonadDepthUpdate:
    """
    Monad-enhanced depth update with blockchain state.

    Extends the standard depth update with Monad-specific state tracking,
    including block number, block hash, and commitment state.

    Attributes:
        e: Event type ("monadDepthUpdate")
        E: Event time (milliseconds since epoch)
        s: Symbol (market address)
        state: Blockchain state ("committed" | "proposed" | "finalized")
        blockNumber: Block number where this update occurred
        blockId: Block hash (hex string with 0x prefix)
        U: First update ID in event
        u: Final update ID in event
        b: Bids as list of [price, size] pairs (U256 strings)
        a: Asks as list of [price, size] pairs (U256 strings)
    """

    e: str  # Event type: "monadDepthUpdate"
    E: int  # Event time
    s: str  # Symbol
    state: str  # "committed" | "proposed" | "finalized"
    blockNumber: int
    blockId: str  # Block hash
    U: int
    u: int
    b: List[Tuple[str, str]]
    a: List[Tuple[str, str]]


# === MAIN CLIENT CLASS ===


class ExchangeWebsocketClient:
    """
    WebSocket client for Exchange orderbook data (Binance-compatible format).

    This client connects to the exchange WebSocket feed which provides:
    - Real-time L2 orderbook updates (depth)
    - Binary WebSocket messages (JSON serialized to binary)
    - Binance-style subscription protocol
    - U256 string representations for prices/sizes (scaled to 10^18)
    - Optional Monad-specific updates with blockchain state

    Features:
    - Automatic reconnection with exponential backoff
    - Built-in heartbeat monitoring
    - Binary message parsing (JSON over binary WebSocket)
    - Type-safe dataclass structures
    - Async context manager support
    - Queue-based update delivery

    Args:
        ws_url: Exchange WebSocket server URL (ws:// or wss://)
        market_address: Market contract address
        update_queue: asyncio.Queue to receive depth updates
        websocket_config: WebSocket behavior configuration
        on_error: Optional callback for errors (sync or async)

    Example:
        ```python
        update_queue = asyncio.Queue()
        client = ExchangeWebsocketClient(
            ws_url="wss://exchange.kuru.io/",
            market_address="0x065C9d28E428A0db40191a54d33d5b7c71a9C394",
            update_queue=update_queue,
        )

        async with client:
            while True:
                update = await update_queue.get()
                if update.b:
                    best_bid = ExchangeWebsocketClient.format_price(update.b[0][0])
                    print(f"Best bid: {best_bid}")
        ```
    """

    def __init__(
        self,
        ws_url: str,
        market_address: str,
        update_queue: asyncio.Queue[Union[DepthUpdate, MonadDepthUpdate]],
        websocket_config: Optional[WebSocketConfig] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """
        Initialize the ExchangeWebsocketClient.

        Args:
            ws_url: Exchange WebSocket server URL
            market_address: Market contract address
            update_queue: Queue to receive orderbook updates
            websocket_config: WebSocket behavior configuration.
                            If None, uses default WebSocketConfig()
            on_error: Optional callback for errors (can be sync or async)

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
        self._on_error = on_error

        # Get reconnection parameters from config
        self._max_reconnect_attempts = websocket_config.max_reconnect_attempts
        self._reconnect_delay = websocket_config.reconnect_delay

        # Connection state
        self._websocket: Optional[websockets.asyncio.client.ClientConnection] = None
        self._connected = False
        self._closing = False
        self._subscribed = False

        # Reconnection state
        self._reconnect_count = 0
        self._current_reconnect_delay = self._reconnect_delay

        # Background tasks
        self._message_loop_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # Heartbeat configuration from config
        self._heartbeat_interval = websocket_config.heartbeat_interval
        self._heartbeat_timeout = websocket_config.heartbeat_timeout

        # Thread safety
        self._lock = asyncio.Lock()

        logger.info(
            f"Initialized ExchangeWebsocketClient for market {self._market_address}"
        )

    @staticmethod
    def format_price(price_str: str) -> float:
        """
        Convert U256 price string to human-readable decimal.

        The exchange WebSocket sends prices as U256 strings scaled to 10^18.

        Args:
            price_str: Price as U256 string (e.g., "241470000000000000000")

        Returns:
            Human-readable price as float (e.g., 241.47)

        Example:
            >>> ExchangeWebsocketClient.format_price("241470000000000000000")
            241.47
        """
        return int(price_str) / 1_000_000_000_000_000_000

    @staticmethod
    def format_size(size_str: str) -> float:
        """
        Convert U256 size string to human-readable decimal.

        The exchange WebSocket sends sizes as U256 strings scaled to 10^18.

        Args:
            size_str: Size as U256 string (e.g., "10000000000000000000")

        Returns:
            Human-readable size as float (e.g., 10.0)

        Example:
            >>> ExchangeWebsocketClient.format_size("10000000000000000000")
            10.0
        """
        return int(size_str) / 1_000_000_000_000_000_000

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
            if self._connected:
                logger.warning("Already connected")
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

    async def subscribe(self) -> None:
        """
        Subscribe to the market's orderbook depth stream.

        Sends a Binance-compatible subscription request:
        {"method": "SUBSCRIBE", "params": ["<market>@depth"], "id": 1}

        Raises:
            RuntimeError: If not connected
        """
        if not self._connected or self._websocket is None:
            raise RuntimeError("Cannot subscribe - not connected")

        if self._subscribed:
            logger.warning("Already subscribed")
            return

        # Build Binance-style subscription request
        channel = f"{self._market_address}@depth"
        request_dict = {"method": "SUBSCRIBE", "params": [channel], "id": 1}

        logger.info(f"Subscribing to channel: {channel}")

        try:
            # Send as text message (subscription message is text, not binary)
            await self._websocket.send(json.dumps(request_dict))
            logger.debug(f"Subscription request sent: {request_dict}")
            self._subscribed = True
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
        async with self._lock:
            if self._closing:
                return

            self._closing = True
            logger.info("Closing WebSocket connection")

            await self._cleanup_connection()

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
        # Cancel background tasks
        if self._message_loop_task:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
            self._message_loop_task = None

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Close WebSocket
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception as e:
                logger.warning(f"Error closing websocket: {e}")
            self._websocket = None

        self._connected = False
        self._subscribed = False

    async def _reconnect(self) -> None:
        """
        Attempt to reconnect with exponential backoff.

        Backoff formula: delay = base_delay * (2 ^ attempt) + jitter
        Max delay capped at 60 seconds.
        """
        async with self._lock:
            if self._closing:
                logger.info("Not reconnecting - client is closing")
                return

            if self._max_reconnect_attempts > 0 and self._reconnect_count >= self._max_reconnect_attempts:
                error_msg = f"Max reconnection attempts ({self._max_reconnect_attempts}) reached"
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
            logger.info(
                f"Reconnection attempt {attempts_msg} "
                f"in {self._current_reconnect_delay:.2f}s"
            )

            await asyncio.sleep(self._current_reconnect_delay)

            try:
                await self._cleanup_connection()
                await self.connect()
                logger.info("Reconnection successful")
            except Exception as e:
                logger.error(f"Reconnection failed: {e}")
                # Schedule another reconnection attempt
                asyncio.create_task(self._reconnect())

    async def _message_loop(self) -> None:
        """
        Main message processing loop.

        Continuously receives and processes binary messages from WebSocket.
        Handles connection errors and triggers reconnection.
        """
        logger.info("Message loop started")

        try:
            while self._connected and not self._closing:
                if self._websocket is None:
                    logger.warning("WebSocket is None in message loop")
                    break

                try:
                    # Receive message with timeout
                    message = await asyncio.wait_for(
                        self._websocket.recv(),
                        timeout=self._heartbeat_interval
                        + self._heartbeat_timeout
                        + 5.0,
                    )

                    # Process message (binary or text)
                    await self._handle_message(message)

                except asyncio.TimeoutError:
                    logger.warning("Message receive timeout - connection may be stale")
                    break

                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"WebSocket connection closed: {e}")
                    break

                except Exception as e:
                    logger.error(f"Error receiving message: {e}")
                    await self._invoke_error_callback(e)
                    # Continue loop - don't break on parse errors

        except asyncio.CancelledError:
            logger.debug("Message loop cancelled")

        except Exception as e:
            logger.error(f"Fatal error in message loop: {e}")
            await self._invoke_error_callback(e)

        finally:
            logger.info("Message loop ended")

            # If we exited due to connection issue (not intentional close), reconnect
            if not self._closing:
                asyncio.create_task(self._handle_connection_loss())

    async def _handle_message(self, message: Union[str, bytes]) -> None:
        """
        Parse and route incoming message to appropriate handler.

        The exchange WebSocket sends binary messages (JSON serialized to bytes).
        Subscription acknowledgments may be text.

        Args:
            message: Raw message (binary JSON or text)
        """
        try:
            # Decode binary message to JSON string
            if isinstance(message, bytes):
                json_str = message.decode("utf-8")
            else:
                json_str = message

            data = json.loads(json_str)

            # Log raw message for debugging (first message only)
            if not hasattr(self, '_first_message_logged'):
                logger.debug(f"First message structure: {list(data.keys())}")
                logger.debug(f"Full message: {data}")
                self._first_message_logged = True

            # Detect message type
            if "result" in data and data.get("id") == 1:
                # Subscription acknowledgment
                await self._handle_subscription_ack(data)

            elif "e" in data:
                event_type = data["e"]

                if event_type == "depthUpdate":
                    await self._handle_depth_update(data)

                elif event_type == "monadDepthUpdate":
                    await self._handle_monad_depth_update(data)

                else:
                    logger.warning(f"Unknown event type: {event_type}")

            else:
                logger.debug(
                    f"Unknown message format, keys: {list(data.keys())}"
                )

        except UnicodeDecodeError as e:
            logger.error(f"Failed to decode binary message: {e}")
            await self._invoke_error_callback(ValueError(f"Invalid UTF-8: {e}"))

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON message: {e}")
            await self._invoke_error_callback(ValueError(f"Invalid JSON: {e}"))

        except Exception as e:
            logger.error(f"Error handling message: {e}")
            await self._invoke_error_callback(e)

    async def _handle_subscription_ack(self, data: dict) -> None:
        """
        Handle subscription acknowledgment message.

        Expected format: {"result": null, "id": 1}

        Args:
            data: Parsed JSON message
        """
        try:
            result = data.get("result")
            msg_id = data.get("id")

            logger.info(
                f"Subscription acknowledgment received: id={msg_id}, result={result}"
            )

            if result is None:
                logger.info("Subscription confirmed by server")
            else:
                logger.warning(f"Unexpected subscription result: {result}")

        except Exception as e:
            logger.error(f"Error handling subscription acknowledgment: {e}")
            await self._invoke_error_callback(e)

    async def _handle_depth_update(self, data: dict) -> None:
        """
        Handle standard depth update message.

        Expected format:
        {
            "e": "depthUpdate",
            "E": 1234567890,  # Optional - may not be present
            "s": "0x...",
            "U": 100,
            "u": 150,
            "b": [["241.47", "10.5"], ...],
            "a": [["242.15", "8.3"], ...]
        }

        Args:
            data: Parsed JSON message
        """
        try:
            # Use current time in milliseconds if E is not provided
            import time
            event_time = int(data.get("E", int(time.time() * 1000)))

            update = DepthUpdate(
                e=data["e"],
                E=event_time,
                s=data.get("s", self._market_address),  # Default to subscribed market
                U=int(data.get("U", 0)),  # Default to 0 if not provided
                u=int(data.get("u", 0)),  # Default to 0 if not provided
                b=[(str(bid[0]), str(bid[1])) for bid in data.get("b", [])],
                a=[(str(ask[0]), str(ask[1])) for ask in data.get("a", [])],
            )

            # Put update on queue
            await self._update_queue.put(update)

        except KeyError as e:
            logger.error(f"Missing required field in depth update: {e}")
            logger.error(f"Available fields: {list(data.keys())}")
            logger.error(f"Full message: {data}")
            await self._invoke_error_callback(
                ValueError(f"Invalid depth update: missing field {e}")
            )

        except Exception as e:
            logger.error(f"Error handling depth update: {e}")
            logger.error(f"Message data: {data}")
            await self._invoke_error_callback(e)

    async def _handle_monad_depth_update(self, data: dict) -> None:
        """
        Handle Monad-enhanced depth update message.

        Expected format:
        {
            "e": "monadDepthUpdate",
            "E": 1234567890,  # Optional
            "s": "0x...",
            "state": "committed",
            "blockNumber": 12345,
            "blockId": "0xabc...",
            "U": 100,
            "u": 150,
            "b": [["241.47", "10.5"], ...],
            "a": [["242.15", "8.3"], ...]
        }

        Args:
            data: Parsed JSON message
        """
        try:
            # Use current time in milliseconds if E is not provided
            import time
            event_time = int(data.get("E", int(time.time() * 1000)))

            update = MonadDepthUpdate(
                e=data["e"],
                E=event_time,
                s=data.get("s", self._market_address),  # Default to subscribed market
                state=data.get("state", "unknown"),
                blockNumber=int(data.get("blockNumber", 0)),
                blockId=data.get("blockId", "0x0"),
                U=int(data.get("U", 0)),
                u=int(data.get("u", 0)),
                b=[(str(bid[0]), str(bid[1])) for bid in data.get("b", [])],
                a=[(str(ask[0]), str(ask[1])) for ask in data.get("a", [])],
            )

            # Put update on queue
            await self._update_queue.put(update)

        except KeyError as e:
            logger.error(f"Missing required field in monad depth update: {e}")
            logger.error(f"Available fields: {list(data.keys())}")
            logger.error(f"Full message: {data}")
            await self._invoke_error_callback(
                ValueError(f"Invalid monad depth update: missing field {e}")
            )

        except Exception as e:
            logger.error(f"Error handling monad depth update: {e}")
            logger.error(f"Message data: {data}")
            await self._invoke_error_callback(e)

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
                    logger.warning("WebSocket is None in heartbeat monitor")
                    asyncio.create_task(self._handle_connection_loss())
                    break

                # websockets library handles ping/pong automatically
                # If connection is stale, it will raise an exception
                # We just need to catch it in the message loop

            except asyncio.CancelledError:
                logger.debug("Heartbeat monitor cancelled")
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

        logger.warning("Connection loss detected")

        async with self._lock:
            self._connected = False

        await self._invoke_error_callback(ConnectionError("Connection lost"))

        # Initiate reconnection
        asyncio.create_task(self._reconnect())

    async def _invoke_error_callback(self, error: Exception) -> None:
        """
        Invoke the on_error callback if provided.

        Supports both sync and async callbacks.

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

    async def __aenter__(self) -> "ExchangeWebsocketClient":
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

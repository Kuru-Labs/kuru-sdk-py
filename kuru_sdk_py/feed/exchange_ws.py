import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, List, Optional, Tuple, Union

import websockets.asyncio.client
from loguru import logger

from kuru_sdk_py.configs import MarketConfig, WebSocketConfig
from kuru_sdk_py.feed.base_ws import BaseWebSocketClient
from kuru_sdk_py.utils.ws_utils import calculate_backoff_delay, format_reconnect_attempts

PRICE_PRECISION_DECIMAL = Decimal(1_000_000_000_000_000_000)

# === DATACLASSES ===


@dataclass
class DepthUpdate:
    """
    Standard Binance-compatible depth update.

    Prices and sizes are pre-normalized to human-readable floats.

    Attributes:
        e: Event type ("depthUpdate")
        E: Event time (milliseconds since epoch)
        s: Symbol (market address)
        U: First update ID in event
        u: Final update ID in event
        b: Bids as list of (price, size) float pairs
        a: Asks as list of (price, size) float pairs
    """

    e: str  # Event type: "depthUpdate"
    E: int  # Event time (milliseconds)
    s: str  # Symbol (market address)
    U: int  # First update ID
    u: int  # Final update ID
    b: List[Tuple[Decimal, Decimal]]  # Bids: [(price, size), ...]
    a: List[Tuple[Decimal, Decimal]]  # Asks: [(price, size), ...]


@dataclass
class MonadDepthUpdate:
    """
    Monad-enhanced depth update with blockchain state.

    Prices and sizes are pre-normalized to human-readable floats.

    Attributes:
        e: Event type ("monadDepthUpdate")
        E: Event time (milliseconds since epoch)
        s: Symbol (market address)
        state: Blockchain state ("committed" | "proposed" | "finalized")
        blockNumber: Block number where this update occurred
        blockId: Block hash (hex string with 0x prefix)
        U: First update ID in event
        u: Final update ID in event
        b: Bids as list of (price, size) float pairs
        a: Asks as list of (price, size) float pairs
    """

    e: str  # Event type: "monadDepthUpdate"
    E: int  # Event time
    s: str  # Symbol
    state: str  # "committed" | "proposed" | "finalized"
    blockNumber: int
    blockId: str  # Block hash
    U: int
    u: int
    b: List[Tuple[Decimal, Decimal]]
    a: List[Tuple[Decimal, Decimal]]


# === MAIN CLIENT CLASS ===


class ExchangeWebsocketClient(BaseWebSocketClient):
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
        market_config: MarketConfig containing market address and size precision
        update_queue: asyncio.Queue to receive depth updates
        websocket_config: WebSocket behavior configuration
        on_error: Optional callback for errors (sync or async)

    Example:
        ```python
        update_queue = asyncio.Queue()
        client = ExchangeWebsocketClient(
            ws_url="wss://exchange.kuru.io/",
            market_config=market_config,
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
        market_config: MarketConfig,
        update_queue: asyncio.Queue[Union[DepthUpdate, MonadDepthUpdate]],
        websocket_config: Optional[WebSocketConfig] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """
        Initialize the ExchangeWebsocketClient.

        Args:
            ws_url: Exchange WebSocket server URL
            market_config: MarketConfig containing market address and size precision
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

        # Validation
        if not market_config.market_address:
            raise ValueError("market_address cannot be empty")
        if not isinstance(update_queue, asyncio.Queue):
            raise ValueError("update_queue must be an asyncio.Queue")

        self._market_config = market_config
        super().__init__(
            ws_url=ws_url,
            websocket_config=websocket_config,
            on_error=on_error,
            client_label="ExchangeWebsocketClient",
            max_reconnect_attempts=websocket_config.max_reconnect_attempts,
            reconnect_delay=websocket_config.reconnect_delay,
            max_reconnect_delay=60.0,
            websocket_connect=websockets.asyncio.client.connect,
            backoff_fn=calculate_backoff_delay,
            format_attempts_fn=format_reconnect_attempts,
        )

        self._market_address = market_config.market_address.lower()  # Normalize to lowercase
        self._update_queue = update_queue
        self._market_depth = websocket_config.exchange_market_depth

        logger.info(
            f"Initialized ExchangeWebsocketClient for market {self._market_address}"
        )

    @staticmethod
    def format_price(price_str: str) -> Decimal:
        """
        Convert U256 price string to human-readable Decimal.

        Note: Queue data is now pre-normalized. This method is only needed
        if you are working with raw WebSocket data outside the client.

        Args:
            price_str: Price as U256 string (e.g., "241470000000000000000")

        Returns:
            Human-readable price as Decimal (e.g., Decimal('241.47'))

        Example:
            >>> ExchangeWebsocketClient.format_price("241470000000000000000")
            Decimal('241.47')
        """
        return Decimal(int(price_str)) / PRICE_PRECISION_DECIMAL

    @staticmethod
    def format_size(size_str: str, size_precision: int) -> Decimal:
        """
        Convert U256 size string to human-readable Decimal.

        Note: Queue data is now pre-normalized. This method is only needed
        if you are working with raw WebSocket data outside the client.

        Args:
            size_str: Size as U256 string (e.g., "10000000000")
            size_precision: Market's size precision divisor (from MarketConfig)

        Returns:
            Human-readable size as Decimal (e.g., Decimal('1'))

        Example:
            >>> ExchangeWebsocketClient.format_size("10000000000", 10000000000)
            Decimal('1')
        """
        return Decimal(int(size_str)) / Decimal(size_precision)

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
        channel = f"{self._market_address}@{self._market_depth}"
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
                    logger.trace(f"Unknown event type: {event_type}")

            else:
                logger.trace(
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

            size_precision_decimal = Decimal(self._market_config.size_precision)
            update = DepthUpdate(
                e=data["e"],
                E=event_time,
                s=data.get("s", self._market_address),  # Default to subscribed market
                U=int(data.get("U", 0)),  # Default to 0 if not provided
                u=int(data.get("u", 0)),  # Default to 0 if not provided
                b=[
                    (Decimal(int(bid[0])) / PRICE_PRECISION_DECIMAL, Decimal(int(bid[1])) / size_precision_decimal)
                    for bid in data.get("b", [])
                ],
                a=[
                    (Decimal(int(ask[0])) / PRICE_PRECISION_DECIMAL, Decimal(int(ask[1])) / size_precision_decimal)
                    for ask in data.get("a", [])
                ],
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

            size_precision_decimal = Decimal(self._market_config.size_precision)
            update = MonadDepthUpdate(
                e=data["e"],
                E=event_time,
                s=data.get("s", self._market_address),  # Default to subscribed market
                state=data.get("state", "unknown"),
                blockNumber=int(data.get("blockNumber", 0)),
                blockId=data.get("blockId", "0x0"),
                U=int(data.get("U", 0)),
                u=int(data.get("u", 0)),
                b=[
                    (Decimal(int(bid[0])) / PRICE_PRECISION_DECIMAL, Decimal(int(bid[1])) / size_precision_decimal)
                    for bid in data.get("b", [])
                ],
                a=[
                    (Decimal(int(ask[0])) / PRICE_PRECISION_DECIMAL, Decimal(int(ask[1])) / size_precision_decimal)
                    for ask in data.get("a", [])
                ],
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

    async def _on_cleanup(self) -> None:
        """No additional subclass cleanup required."""
        return None

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional

import websockets.asyncio.client
import websockets.exceptions
from loguru import logger

from kuru_sdk_py.configs import WebSocketConfig
from kuru_sdk_py.exceptions import KuruConnectionError, KuruWebSocketError
from kuru_sdk_py.utils.ws_utils import calculate_backoff_delay, format_reconnect_attempts


ErrorCallback = Callable[[Exception], Optional[Awaitable[None]]]
ConnectFn = Callable[..., Awaitable[websockets.asyncio.client.ClientConnection]]
BackoffFn = Callable[[int, float, float], float]
AttemptsFn = Callable[[int, int], str]


class BaseWebSocketClient(ABC):
    """Shared WebSocket lifecycle behavior for Kuru streaming clients."""

    def __init__(
        self,
        ws_url: str,
        websocket_config: WebSocketConfig,
        on_error: Optional[ErrorCallback] = None,
        *,
        client_label: str = "WebSocketClient",
        max_reconnect_attempts: Optional[int] = None,
        reconnect_delay: Optional[float] = None,
        max_reconnect_delay: float = 60.0,
        websocket_connect: Optional[ConnectFn] = None,
        backoff_fn: Optional[BackoffFn] = None,
        format_attempts_fn: Optional[AttemptsFn] = None,
    ) -> None:
        if not ws_url:
            raise ValueError("ws_url cannot be empty")
        if not ws_url.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")

        self.websocket_config = websocket_config
        self._ws_url = ws_url
        self._on_error = on_error
        self._client_label = client_label

        self._max_reconnect_attempts = (
            max_reconnect_attempts
            if max_reconnect_attempts is not None
            else websocket_config.max_reconnect_attempts
        )
        self._reconnect_delay = (
            reconnect_delay
            if reconnect_delay is not None
            else websocket_config.reconnect_delay
        )
        self._max_reconnect_delay = max_reconnect_delay

        self._heartbeat_interval = websocket_config.heartbeat_interval
        self._heartbeat_timeout = websocket_config.heartbeat_timeout

        self._websocket_connect = websocket_connect or websockets.asyncio.client.connect
        self._calculate_backoff_delay = backoff_fn or calculate_backoff_delay
        self._format_reconnect_attempts = (
            format_attempts_fn or format_reconnect_attempts
        )

        self._websocket: Optional[websockets.asyncio.client.ClientConnection] = None
        self._connected = False
        self._closing = False
        self._subscribed = False

        self._reconnect_count = 0
        self._current_reconnect_delay = self._reconnect_delay
        self._reconnect_task: Optional[asyncio.Task] = None

        self._message_loop_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        self._lock = asyncio.Lock()

    @abstractmethod
    async def subscribe(self) -> None:
        """Subscribe to the target stream after a successful connection."""

    @abstractmethod
    async def _handle_message(self, message: Any) -> None:
        """Parse and route incoming WebSocket messages."""

    async def _on_cleanup(self) -> None:
        """Hook for subclass-specific cleanup state resets."""
        return None

    async def connect(self) -> None:
        """Establish WebSocket connection and start background tasks."""
        async with self._lock:
            await self._connect_unlocked()

    async def _connect_unlocked(self) -> None:
        if self._connected:
            logger.debug(f"{self._client_label}: already connected")
            return

        if self._closing:
            raise RuntimeError("Cannot connect while closing")

        try:
            logger.info(f"{self._client_label}: connecting to {self._ws_url}")
            self._websocket = await self._websocket_connect(
                self._ws_url,
                ping_interval=self._heartbeat_interval,
                ping_timeout=self._heartbeat_timeout,
                open_timeout=10.0,
                close_timeout=10.0,
                max_size=10 * 1024 * 1024,
            )

            self._connected = True
            self._reconnect_count = 0

            self._message_loop_task = asyncio.create_task(self._message_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

            await self.subscribe()
            logger.info(f"{self._client_label}: connected")

        except websockets.exceptions.InvalidURI as e:
            self._connected = False
            self._websocket = None
            raise KuruWebSocketError(f"Invalid WebSocket URI: {self._ws_url}") from e
        except OSError as e:
            self._connected = False
            self._websocket = None
            raise KuruConnectionError(f"Failed to connect to {self._ws_url}") from e
        except Exception:
            self._connected = False
            self._websocket = None
            raise

    async def _start_reconnect_task(self) -> None:
        async with self._lock:
            if self._connected or self._closing:
                return
            if self._reconnect_task is not None and not self._reconnect_task.done():
                return
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def close(self) -> None:
        """Close connection, cancel tasks, and disable reconnection."""
        reconnect_task: Optional[asyncio.Task] = None
        async with self._lock:
            if self._closing:
                return
            self._closing = True
            reconnect_task = self._reconnect_task
            self._reconnect_task = None

        await self._cleanup_connection()

        if reconnect_task is not None and not reconnect_task.done():
            reconnect_task.cancel()
            try:
                await reconnect_task
            except asyncio.CancelledError:
                pass

    def is_connected(self) -> bool:
        return self._connected and self._websocket is not None

    async def _cleanup_connection(self) -> None:
        current_task = asyncio.current_task()

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

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

        self._connected = False
        self._subscribed = False
        await self._on_cleanup()

    async def _reconnect(self) -> None:
        try:
            while True:
                async with self._lock:
                    if self._closing:
                        return

                    if (
                        self._max_reconnect_attempts > 0
                        and self._reconnect_count >= self._max_reconnect_attempts
                    ):
                        error = KuruConnectionError(
                            f"Max reconnection attempts ({self._max_reconnect_attempts}) reached"
                        )
                        await self._invoke_error_callback(error)
                        return

                    self._current_reconnect_delay = self._calculate_backoff_delay(
                        self._reconnect_count,
                        self._reconnect_delay,
                        self._max_reconnect_delay,
                    )
                    self._reconnect_count += 1
                    attempts_msg = self._format_reconnect_attempts(
                        self._reconnect_count, self._max_reconnect_attempts
                    )
                    delay = self._current_reconnect_delay

                logger.info(
                    f"{self._client_label}: reconnection attempt {attempts_msg} in {delay:.2f}s"
                )
                await asyncio.sleep(delay)

                async with self._lock:
                    if self._closing:
                        return

                try:
                    await self._cleanup_connection()
                    await self.connect()
                    logger.info(f"{self._client_label}: reconnection successful")
                    return
                except Exception as e:
                    logger.error(f"{self._client_label}: reconnection failed: {e}")
                    continue
        finally:
            async with self._lock:
                if self._reconnect_task is asyncio.current_task():
                    self._reconnect_task = None

    async def _message_loop(self) -> None:
        connection_lost_detected = False
        try:
            while self._connected and not self._closing:
                if self._websocket is None:
                    connection_lost_detected = True
                    break

                try:
                    message = await asyncio.wait_for(
                        self._websocket.recv(),
                        timeout=self._heartbeat_interval
                        + self._heartbeat_timeout
                        + 5.0,
                    )
                    await self._handle_message(message)
                except asyncio.TimeoutError:
                    connection_lost_detected = True
                    break
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"{self._client_label}: websocket closed: {e}")
                    connection_lost_detected = True
                    break
                except Exception as e:
                    logger.error(f"{self._client_label}: message loop error: {e}")
                    await self._invoke_error_callback(e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"{self._client_label}: fatal loop error: {e}")
            await self._invoke_error_callback(e)
            connection_lost_detected = True
        finally:
            if connection_lost_detected and not self._closing:
                await self._handle_connection_loss()

    async def _heartbeat_monitor(self) -> None:
        while self._connected and not self._closing:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if self._websocket is None:
                    await self._handle_connection_loss()
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"{self._client_label}: heartbeat error: {e}")
                await self._invoke_error_callback(e)
                await self._handle_connection_loss()
                break

    async def _handle_connection_loss(self) -> None:
        if self._closing:
            return

        async with self._lock:
            self._connected = False

        await self._invoke_error_callback(KuruConnectionError("Connection lost"))
        await self._start_reconnect_task()

    async def _invoke_error_callback(self, error: Exception) -> None:
        if self._on_error is None:
            return

        try:
            if asyncio.iscoroutinefunction(self._on_error):
                await self._on_error(error)
            else:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._on_error, error)
        except Exception as callback_error:
            logger.error(
                f"{self._client_label}: error callback failed: {callback_error}",
                exc_info=True,
            )

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[Exception],
        exc_tb: Optional[Any],
    ) -> bool:
        await self.close()
        return False

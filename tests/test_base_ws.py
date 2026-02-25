import asyncio
from unittest.mock import AsyncMock

import pytest

from kuru_sdk_py.configs import WebSocketConfig
from kuru_sdk_py.feed.base_ws import BaseWebSocketClient


class _StubWebSocket:
    def __init__(self, recv_mock=None):
        self._recv_mock = recv_mock
        self.closed = False
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        if self._recv_mock is not None:
            return await self._recv_mock()
        await asyncio.sleep(3600)

    async def close(self):
        self.closed = True


class _DummyClient(BaseWebSocketClient):
    def __init__(self, connect_fn):
        self.subscribe_calls = 0
        self.handled_messages = []
        self.cleanup_calls = 0
        super().__init__(
            ws_url="ws://example.local/ws",
            websocket_config=WebSocketConfig(
                heartbeat_interval=0.01,
                heartbeat_timeout=0.01,
                reconnect_delay=0.01,
                max_reconnect_attempts=2,
            ),
            websocket_connect=connect_fn,
            backoff_fn=lambda *args, **kwargs: 0.0,
            format_attempts_fn=lambda count, max_count: f"{count}/{max_count}",
            client_label="DummyClient",
        )

    async def subscribe(self) -> None:
        self.subscribe_calls += 1
        self._subscribed = True

    async def _handle_message(self, message):
        self.handled_messages.append(message)

    async def _on_cleanup(self) -> None:
        self.cleanup_calls += 1


@pytest.mark.asyncio
async def test_connect_and_close_lifecycle():
    ws = _StubWebSocket()

    async def connect_fn(*args, **kwargs):
        return ws

    client = _DummyClient(connect_fn=connect_fn)
    await client.connect()
    assert client.is_connected()
    assert client.subscribe_calls == 1

    await client.close()
    assert not client.is_connected()
    assert ws.closed is True
    assert client.cleanup_calls >= 1


@pytest.mark.asyncio
async def test_reconnect_succeeds_with_backoff_zero():
    ws = _StubWebSocket()

    async def connect_fn(*args, **kwargs):
        return ws

    client = _DummyClient(connect_fn=connect_fn)
    await client._reconnect()

    assert client.is_connected()
    await client.close()


@pytest.mark.asyncio
async def test_message_loop_timeout_triggers_connection_loss():
    recv_mock = AsyncMock(side_effect=asyncio.TimeoutError)
    ws = _StubWebSocket(recv_mock=recv_mock)

    async def connect_fn(*args, **kwargs):
        return ws

    client = _DummyClient(connect_fn=connect_fn)
    client._connected = True
    client._websocket = ws
    client._handle_connection_loss = AsyncMock()

    await client._message_loop()
    client._handle_connection_loss.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_monitor_triggers_connection_loss_when_socket_missing():
    async def connect_fn(*args, **kwargs):
        return _StubWebSocket()

    client = _DummyClient(connect_fn=connect_fn)
    client._connected = True
    client._websocket = None
    client._handle_connection_loss = AsyncMock()

    await asyncio.wait_for(client._heartbeat_monitor(), timeout=0.1)
    client._handle_connection_loss.assert_awaited_once()

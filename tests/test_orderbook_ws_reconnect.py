"""Regression tests for orderbook websocket reconnect behavior."""

import asyncio
import sys
import types
from contextlib import suppress
from typing import Optional
from unittest.mock import AsyncMock

import pytest

# Compatibility shim for environments where websockets.asyncio is unavailable.
try:
    import websockets.asyncio.client  # type: ignore
except ModuleNotFoundError:
    import websockets

    asyncio_module = types.ModuleType("websockets.asyncio")
    client_module = types.ModuleType("websockets.asyncio.client")

    async def _unpatched_connect(*args, **kwargs):  # pragma: no cover - patched in tests
        raise RuntimeError("connect() must be monkeypatched in tests")

    client_module.connect = _unpatched_connect
    asyncio_module.client = client_module
    websockets.asyncio = asyncio_module  # type: ignore[attr-defined]
    sys.modules["websockets.asyncio"] = asyncio_module
    sys.modules["websockets.asyncio.client"] = client_module

import kuru_sdk_py.feed.orderbook_ws as orderbook_ws_module
from kuru_sdk_py.configs import WebSocketConfig
from kuru_sdk_py.feed.orderbook_ws import KuruFrontendOrderbookClient


class _StubWebSocket:
    def __init__(self, recv_mock: Optional[AsyncMock] = None):
        self._recv_mock = recv_mock
        self.sent_messages: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent_messages.append(message)

    async def recv(self):
        if self._recv_mock is not None:
            return await self._recv_mock()
        await asyncio.sleep(3600)

    async def close(self) -> None:
        self.closed = True


def _make_client() -> KuruFrontendOrderbookClient:
    return KuruFrontendOrderbookClient(
        ws_url="ws://example.local/ws",
        market_address="0x0000000000000000000000000000000000000001",
        update_queue=asyncio.Queue(),
        websocket_config=WebSocketConfig(
            reconnect_delay=0.01,
            heartbeat_interval=0.01,
            heartbeat_timeout=0.01,
            max_reconnect_attempts=3,
        ),
    )


@pytest.mark.asyncio
async def test_reconnect_does_not_deadlock_when_connect_is_invoked(monkeypatch):
    """Reconnect should complete without lock re-entry deadlock."""
    ws = _StubWebSocket()

    async def fake_connect(*args, **kwargs):
        return ws

    monkeypatch.setattr(
        orderbook_ws_module.websockets.asyncio.client, "connect", fake_connect
    )
    monkeypatch.setattr(
        orderbook_ws_module, "calculate_backoff_delay", lambda *args, **kwargs: 0.0
    )

    client = _make_client()

    await asyncio.wait_for(client._reconnect(), timeout=0.5)
    assert client.is_connected()

    await client.close()


@pytest.mark.asyncio
async def test_message_loop_timeout_triggers_connection_loss_handler():
    """Timeout exits in message loop should trigger connection-loss handling."""
    recv_mock = AsyncMock(side_effect=asyncio.TimeoutError)
    ws = _StubWebSocket(recv_mock=recv_mock)

    client = _make_client()
    client._connected = True
    client._websocket = ws
    client._handle_connection_loss = AsyncMock()

    await client._message_loop()

    client._handle_connection_loss.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_reconnect_when_closing_true():
    """Connection-loss handler should be a no-op while closing."""
    client = _make_client()
    client._closing = True
    client._invoke_error_callback = AsyncMock()

    await client._handle_connection_loss()

    assert client._reconnect_task is None
    client._invoke_error_callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_flight_reconnect_guard(monkeypatch):
    """Multiple loss signals should create only one reconnect task."""
    started = 0
    gate = asyncio.Event()

    async def slow_reconnect():
        nonlocal started
        started += 1
        await gate.wait()

    client = _make_client()
    client._invoke_error_callback = AsyncMock()
    monkeypatch.setattr(client, "_reconnect", slow_reconnect)

    await asyncio.gather(
        client._handle_connection_loss(),
        client._handle_connection_loss(),
        client._handle_connection_loss(),
    )
    await asyncio.sleep(0)

    assert started == 1
    assert client._reconnect_task is not None

    client._reconnect_task.cancel()
    with suppress(asyncio.CancelledError):
        await client._reconnect_task

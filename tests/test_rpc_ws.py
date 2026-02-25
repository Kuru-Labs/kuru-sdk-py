from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kuru_sdk_py.feed.rpc_ws import RpcWebsocket
from kuru_sdk_py.utils.ws_utils import BoundedDedupSet


def _make_rpc_ws_for_log_tests() -> RpcWebsocket:
    ws = RpcWebsocket.__new__(RpcWebsocket)
    ws._last_seen_block = None
    ws._dedup = BoundedDedupSet(max_size=1000)
    ws.orderbook_address_lower = "0x0000000000000000000000000000000000000001"
    ws.user_address_lower = "0x0000000000000000000000000000000000000002"
    ws._process_orderbook_log = AsyncMock()
    ws._batch_update_mm_log = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_handle_log_routes_and_deduplicates():
    ws = _make_rpc_ws_for_log_tests()
    log = {
        "blockNumber": "0x10",
        "transactionHash": "0xabc",
        "logIndex": "0x1",
        "topics": ["0xtopic"],
        "address": ws.orderbook_address_lower,
    }

    await ws._handle_log(log)
    await ws._handle_log(log)

    ws._process_orderbook_log.assert_awaited_once()
    ws._batch_update_mm_log.assert_not_awaited()
    assert ws._last_seen_block == 16


@pytest.mark.asyncio
async def test_process_orderbook_log_parses_order_created_event():
    ws = RpcWebsocket.__new__(RpcWebsocket)
    ws.events_to_topic_hashes = {
        "OrderCreated": "0xcreated",
        "OrdersCanceled": "0xcanceled",
        "Trade": "0xtrade",
    }
    ws.user_address_lower = "0xabc"
    ws.size_precision = 100
    ws.orderbook_contract = MagicMock()
    ws.orders_manager = MagicMock()
    ws.orders_manager.on_order_created = AsyncMock()

    ws.orderbook_contract.events.OrderCreated.return_value.process_log.return_value = {
        "args": {
            "orderId": 7,
            "owner": "0xAbC",
            "size": 250,
            "price": 1234,
            "isBuy": True,
        }
    }

    await ws._process_orderbook_log({"logIndex": 5}, "0xcreated", "0xtx")

    ws.orders_manager.on_order_created.assert_awaited_once()
    event = ws.orders_manager.on_order_created.await_args.args[0]
    assert event.order_id == 7
    assert event.price == 1234
    assert event.size == Decimal("2.5")
    assert event.txhash == "0xtx"


@pytest.mark.asyncio
async def test_gap_recovery_uses_expected_block_chunks():
    ws = RpcWebsocket.__new__(RpcWebsocket)
    ws._gap_recovery_block_buffer = 5
    ws._gap_recovery_max_block_range = 20
    ws.events_to_topic_hashes = {"OrderCreated": "0xtopic"}
    ws.orderbook_address = "0x0000000000000000000000000000000000000001"
    ws.user_address = "0x0000000000000000000000000000000000000002"
    ws._handle_log = AsyncMock()

    mock_eth = MagicMock()
    mock_eth.block_number = 150
    mock_eth.get_logs = MagicMock(side_effect=[[], [], []])
    ws._http_w3 = SimpleNamespace(eth=mock_eth)

    await ws._recover_missed_events(from_block=100)

    calls = [call.args[0] for call in mock_eth.get_logs.call_args_list]
    assert [c["fromBlock"] for c in calls] == [95, 115, 135]
    assert [c["toBlock"] for c in calls] == [114, 134, 150]

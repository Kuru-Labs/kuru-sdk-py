import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kuru_sdk_py.account_session import AccountSession
from kuru_sdk_py.client import MultiMarketClient, MarketOrderEvent, MarketOrderbookEvent
from kuru_sdk_py.configs import ConnectionConfig, MarketConfig, WalletConfig
from kuru_sdk_py.feed.orderbook_ws import FrontendOrderbookUpdate
from kuru_sdk_py.feed.rpc_ws import RpcEventRouter, _MarketRoute
from kuru_sdk_py.manager.order import Order, OrderSide, OrderType
from kuru_sdk_py.utils.ws_utils import BoundedDedupSet


def _make_market_config(address: str, symbol: str) -> MarketConfig:
    return MarketConfig(
        market_address=address,
        base_token="0x0000000000000000000000000000000000000000",
        quote_token="0x754704Bc059F8C67012fEd69BC8A327a5aafb603",
        market_symbol=symbol,
        mm_entrypoint_address="0xA9d8269ad1Bd6e2a02BD8996a338Dc5C16aef440",
        margin_contract_address="0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5",
        base_token_decimals=18,
        quote_token_decimals=6,
        price_precision=100000000,
        size_precision=10000000000,
        base_symbol="MON",
        quote_symbol="USDC",
        orderbook_implementation="0xea2Cc8769Fb04Ff1893Ed11cf517b7F040C823CD",
        margin_account_implementation="0x57cF97FE1FAC7D78B07e7e0761410cb2e91F0ca7",
        tick_size=100,
    )


class _FakeMarketClient:
    def __init__(self, market_config: MarketConfig):
        self.market_config = market_config
        self.orders_manager = SimpleNamespace(
            cloid_to_order={},
            txhash_to_sent_orders={},
            set_receipt_processor=lambda processor: None,
        )
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.shared_order_callback = None
        self.shared_orderbook_callback = None
        self.websocket = None

    def set_shared_order_callback(self, callback):
        self.shared_order_callback = callback

    def set_shared_orderbook_callback(self, callback):
        self.shared_orderbook_callback = callback


@pytest.mark.asyncio
async def test_multi_market_client_uses_shared_session_and_callbacks(monkeypatch):
    created_clients: list[_FakeMarketClient] = []

    async def fake_create(
        cls,
        market_config,
        connection_config=None,
        wallet_config=None,
        transaction_config=None,
        websocket_config=None,
        order_execution_config=None,
        cache_config=None,
        kuru_mm_config=None,
        account_session=None,
        account_client=None,
        event_router=None,
        manage_shared_resources=True,
    ):
        client = _FakeMarketClient(market_config)
        client.account_session = account_session
        client.account_client = account_client
        client.websocket = event_router
        client.manage_shared_resources = manage_shared_resources
        created_clients.append(client)
        return client

    start_gas_worker = AsyncMock()
    connect_router = AsyncMock()
    subscribe_router = AsyncMock()
    disconnect_router = AsyncMock()

    async def noop_process_logs(self):
        return None

    monkeypatch.setattr("kuru_sdk_py.client.KuruClient.create", classmethod(fake_create))
    monkeypatch.setattr(AccountSession, "start_gas_price_worker", start_gas_worker)
    monkeypatch.setattr(AccountSession, "close", AsyncMock())
    monkeypatch.setattr(
        "kuru_sdk_py.client.AccountClient.has_mm_entrypoint_authorization",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "kuru_sdk_py.client.AccountClient.eip_7702_auth",
        AsyncMock(),
    )
    monkeypatch.setattr(RpcEventRouter, "connect", connect_router)
    monkeypatch.setattr(RpcEventRouter, "create_log_subscription", subscribe_router)
    monkeypatch.setattr(RpcEventRouter, "disconnect", disconnect_router)
    monkeypatch.setattr(RpcEventRouter, "process_subscription_logs", noop_process_logs)

    multi = await MultiMarketClient.create(
        markets=[
            _make_market_config("0x065C9d28E428A0db40191a54d33d5b7c71a9C394", "MON-USDC"),
            _make_market_config("0x6eB96A614E49b0dAc69F48E799C5C825AF9B33fA", "MON-USDT"),
        ],
        connection_config=ConnectionConfig(),
        wallet_config=WalletConfig(
            private_key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        ),
    )

    assert len(created_clients) == 2
    assert created_clients[0].account_session is created_clients[1].account_session
    assert created_clients[0].account_client is created_clients[1].account_client
    assert created_clients[0].websocket is created_clients[1].websocket
    assert created_clients[0].manage_shared_resources is False
    assert len(multi.list_markets()) == 2

    created_clients[0].orders_manager.cloid_to_order["same"] = "market-a"
    created_clients[1].orders_manager.cloid_to_order["same"] = "market-b"
    assert created_clients[0].orders_manager.cloid_to_order["same"] == "market-a"
    assert created_clients[1].orders_manager.cloid_to_order["same"] == "market-b"

    order_callback = AsyncMock()
    orderbook_callback = AsyncMock()
    multi.set_order_callback(order_callback)
    multi.set_orderbook_callback(orderbook_callback)

    sample_order = Order(
        cloid="order-1",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=1.0,
        size=1.0,
    )
    sample_update = FrontendOrderbookUpdate(events=[], b=[], a=[])

    await created_clients[0].shared_order_callback(sample_order)
    await created_clients[0].shared_orderbook_callback(sample_update)

    assert isinstance(order_callback.await_args.args[0], MarketOrderEvent)
    assert order_callback.await_args.args[0].market_address == created_clients[0].market_config.market_address
    assert isinstance(orderbook_callback.await_args.args[0], MarketOrderbookEvent)
    assert orderbook_callback.await_args.args[0].market_address == created_clients[0].market_config.market_address

    await multi.start()
    start_gas_worker.assert_awaited_once()
    connect_router.assert_awaited_once()
    subscribe_router.assert_awaited_once()
    created_clients[0].start.assert_awaited_once()
    created_clients[1].start.assert_awaited_once()


@pytest.mark.asyncio
async def test_rpc_event_router_routes_orderbook_logs_to_matching_market():
    router = RpcEventRouter.__new__(RpcEventRouter)
    router._last_seen_block = None
    router._dedup = BoundedDedupSet(max_size=1000)
    router.user_address_lower = "0x0000000000000000000000000000000000000002"
    router._process_orderbook_log = AsyncMock()
    router._batch_update_mm_log = AsyncMock()
    route_a = _MarketRoute(
        market_config=_make_market_config(
            "0x0000000000000000000000000000000000000001", "A"
        ),
        orders_manager=MagicMock(),
        orderbook_contract=MagicMock(),
    )
    route_b = _MarketRoute(
        market_config=_make_market_config(
            "0x0000000000000000000000000000000000000003", "B"
        ),
        orders_manager=MagicMock(),
        orderbook_contract=MagicMock(),
    )
    router._market_routes = {
        route_a.market_config.market_address.lower(): route_a,
        route_b.market_config.market_address.lower(): route_b,
    }

    log = {
        "blockNumber": "0x10",
        "transactionHash": "0xabc",
        "logIndex": "0x1",
        "topics": ["0xtopic"],
        "address": route_b.market_config.market_address,
    }

    await router._handle_log(log)

    router._process_orderbook_log.assert_awaited_once_with(
        route_b,
        log,
        "0xtopic",
        "0xabc",
    )
    router._batch_update_mm_log.assert_not_awaited()
    assert router._last_seen_block == 16

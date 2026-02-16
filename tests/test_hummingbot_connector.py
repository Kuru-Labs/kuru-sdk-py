"""
Unit tests for the Hummingbot Kuru connector.

Tests fill computation, balance calculation, order state mapping,
trading rules, and WebSocket formatting. Tests that require the
hummingbot package are skipped if it's not installed.
"""

import asyncio
import time
from decimal import Decimal
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.configs import MarketConfig
from src.manager.order import Order as SdkOrder
from src.manager.order import OrderSide as SdkOrderSide
from src.manager.order import OrderStatus as SdkOrderStatus
from src.manager.order import OrderType as SdkOrderType
from src.feed.orderbook_ws import (
    FrontendEvent,
    FrontendOrderbookUpdate,
    KuruFrontendOrderbookClient,
)

from hummingbot_connector.kuru.kuru_auth import KuruAuth
from hummingbot_connector.kuru.kuru_utils import (
    get_market_config,
    trading_pair_from_market_config,
)
import hummingbot_connector.kuru.kuru_constants as CONSTANTS

# Check if hummingbot is available for integration tests
try:
    from hummingbot.core.data_type.in_flight_order import OrderState
    HAS_HUMMINGBOT = True
except ImportError:
    HAS_HUMMINGBOT = False


# ============================================================================
# Test Fixtures
# ============================================================================


def make_market_config(**overrides) -> MarketConfig:
    """Create a test MarketConfig with sensible defaults."""
    defaults = {
        "market_address": "0x065C9d28E428A0db40191a54d33d5b7c71a9C394",
        "base_token": "0x0000000000000000000000000000000000000000",
        "quote_token": "0x754704Bc059F8C67012fEd69BC8A327a5aafb603",
        "market_symbol": "MON-USDC",
        "mm_entrypoint_address": "0xA9d8269ad1Bd6e2a02BD8996a338Dc5C16aef440",
        "margin_contract_address": "0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5",
        "base_token_decimals": 18,
        "quote_token_decimals": 6,
        "price_precision": 100000000,
        "size_precision": 10000000000,
        "base_symbol": "MON",
        "quote_symbol": "USDC",
        "orderbook_implementation": "0xea2Cc8769Fb04Ff1893Ed11cf517b7F040C823CD",
        "margin_account_implementation": "0x57cF97FE1FAC7D78B07e7e0761410cb2e91F0ca7",
        "tick_size": 100,
    }
    defaults.update(overrides)
    return MarketConfig(**defaults)


# ============================================================================
# KuruAuth Tests
# ============================================================================


class TestKuruAuth:
    """Tests for wallet-based authentication."""

    def test_auth_derives_address(self):
        """KuruAuth should derive wallet address from private key."""
        # Hardhat account #0
        test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        auth = KuruAuth(test_key)
        assert auth.address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
        assert auth.private_key == test_key

    def test_wallet_config(self):
        """get_wallet_config should return a valid WalletConfig."""
        test_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        auth = KuruAuth(test_key)
        wc = auth.get_wallet_config()
        assert wc.private_key == test_key


# ============================================================================
# Config & Utils Tests
# ============================================================================


class TestKuruUtils:
    """Tests for config utilities."""

    def test_known_market_lookup(self):
        """Known market addresses should return cached MarketConfig."""
        mc = get_market_config("0x065C9d28E428A0db40191a54d33d5b7c71a9C394")
        assert mc.market_symbol == "MON-USDC"
        assert mc.price_precision == 100000000
        assert mc.size_precision == 10000000000
        assert mc.tick_size == 100

    def test_known_market_case_insensitive(self):
        """Known market lookup should be case-insensitive."""
        mc = get_market_config("0x065c9d28e428a0db40191a54d33d5b7c71a9c394")
        assert mc.market_symbol == "MON-USDC"

    def test_trading_pair_from_config(self):
        """Trading pair should be derived from market_symbol."""
        mc = make_market_config(market_symbol="ETH-USDC")
        assert trading_pair_from_market_config(mc) == "ETH-USDC"


# ============================================================================
# Order State Mapping Tests
# ============================================================================


@pytest.mark.skipif(not HAS_HUMMINGBOT, reason="hummingbot not installed")
class TestOrderStateMapping:
    """Tests for SDK OrderStatus -> Hummingbot OrderState mapping."""

    def test_all_statuses_mapped(self):
        """Every SDK OrderStatus should have a mapping."""
        from hummingbot_connector.kuru.kuru_exchange import KuruExchange

        for sdk_status in SdkOrderStatus:
            assert sdk_status in KuruExchange._SDK_STATUS_MAP, (
                f"Missing mapping for {sdk_status}"
            )

    def test_status_mapping_values(self):
        """Verify specific status mappings match the plan."""
        from hummingbot_connector.kuru.kuru_exchange import KuruExchange

        m = KuruExchange._SDK_STATUS_MAP
        assert m[SdkOrderStatus.ORDER_CREATED] == OrderState.PENDING_CREATE
        assert m[SdkOrderStatus.ORDER_SENT] == OrderState.PENDING_CREATE
        assert m[SdkOrderStatus.ORDER_PLACED] == OrderState.OPEN
        assert m[SdkOrderStatus.ORDER_PARTIALLY_FILLED] == OrderState.PARTIALLY_FILLED
        assert m[SdkOrderStatus.ORDER_FULLY_FILLED] == OrderState.FILLED
        assert m[SdkOrderStatus.ORDER_CANCELLED] == OrderState.CANCELED
        assert m[SdkOrderStatus.ORDER_TIMEOUT] == OrderState.FAILED


# ============================================================================
# Fill Computation Tests
# ============================================================================


class TestFillComputation:
    """Tests for the fill tracking logic used by _process_fills."""

    def test_new_fills_detected(self):
        """Should detect fills beyond what's already reported."""
        sdk_order = SdkOrder(
            cloid="test-buy-123",
            order_type=SdkOrderType.LIMIT,
            side=SdkOrderSide.BUY,
            price=10.0,
            size=30.0,
        )
        sdk_order.filled_sizes = [5.0, 3.0, 2.0]

        already_reported = 1
        new_fills = sdk_order.filled_sizes[already_reported:]
        assert new_fills == [3.0, 2.0]
        assert len(new_fills) == 2

    def test_no_new_fills(self):
        """Should return empty when all fills are already reported."""
        sdk_order = SdkOrder(
            cloid="test-sell-456",
            order_type=SdkOrderType.LIMIT,
            side=SdkOrderSide.SELL,
            price=20.0,
            size=10.0,
        )
        sdk_order.filled_sizes = [5.0, 5.0]

        already_reported = 2
        new_fills = sdk_order.filled_sizes[already_reported:]
        assert new_fills == []

    def test_fill_trade_id_uniqueness(self):
        """Trade IDs should be unique per fill index."""
        cloid = "test-order-789"
        fills = [1.0, 2.0, 3.0]

        trade_ids = [f"{cloid}_{i}" for i in range(len(fills))]
        assert len(trade_ids) == len(set(trade_ids))
        assert trade_ids == [
            "test-order-789_0",
            "test-order-789_1",
            "test-order-789_2",
        ]

    def test_fill_quote_amount(self):
        """Fill quote amount should be price * base_amount."""
        fill_price = Decimal("10.50")
        fill_base = Decimal("3.0")
        fill_quote = fill_price * fill_base
        assert fill_quote == Decimal("31.50")

    def test_incremental_fill_reporting(self):
        """Simulates the incremental fill reporting pattern."""
        sdk_order = SdkOrder(
            cloid="test-incremental",
            order_type=SdkOrderType.LIMIT,
            side=SdkOrderSide.BUY,
            price=100.0,
            size=10.0,
        )

        # First callback: 1 fill
        sdk_order.filled_sizes = [3.0]
        reported = 0
        new = sdk_order.filled_sizes[reported:]
        assert new == [3.0]
        reported += len(new)

        # Second callback: 2 fills total
        sdk_order.filled_sizes = [3.0, 4.0]
        new = sdk_order.filled_sizes[reported:]
        assert new == [4.0]
        reported += len(new)

        # Third callback: 3 fills total (fully filled)
        sdk_order.filled_sizes = [3.0, 4.0, 3.0]
        new = sdk_order.filled_sizes[reported:]
        assert new == [3.0]
        reported += len(new)

        assert reported == 3
        assert sum(sdk_order.filled_sizes) == 10.0


# ============================================================================
# Balance Calculation Tests
# ============================================================================


class TestBalanceCalculation:
    """Tests for margin balance and locked amount computation."""

    def test_balance_conversion_from_wei(self):
        """Raw margin balances should convert correctly using token decimals."""
        mc = make_market_config()

        base_wei = 5_000_000_000_000_000_000  # 5e18 = 5 MON
        quote_wei = 1_000_000  # 1e6 = 1 USDC

        base_balance = Decimal(str(base_wei)) / Decimal(10 ** mc.base_token_decimals)
        quote_balance = Decimal(str(quote_wei)) / Decimal(
            10 ** mc.quote_token_decimals
        )

        assert base_balance == Decimal("5")
        assert quote_balance == Decimal("1")

    def test_locked_amounts_buy_orders(self):
        """Buy orders should lock quote tokens (price * remaining_size)."""
        order_price = Decimal("5.0")
        order_amount = Decimal("10.0")
        executed = Decimal("3.0")
        remaining = order_amount - executed

        locked_quote = order_price * remaining
        assert locked_quote == Decimal("35.0")

    def test_locked_amounts_sell_orders(self):
        """Sell orders should lock base tokens (remaining_size)."""
        order_amount = Decimal("10.0")
        executed = Decimal("4.0")
        remaining = order_amount - executed

        locked_base = remaining
        assert locked_base == Decimal("6.0")

    def test_available_balance(self):
        """Available = total - locked, floored at 0."""
        total_quote = Decimal("100")
        locked_quote = Decimal("35")
        available = max(Decimal("0"), total_quote - locked_quote)
        assert available == Decimal("65")

    def test_available_balance_floor_at_zero(self):
        """Available balance should not go negative."""
        total = Decimal("10")
        locked = Decimal("15")
        available = max(Decimal("0"), total - locked)
        assert available == Decimal("0")

    def test_multiple_orders_lock_amounts(self):
        """Multiple active orders should accumulate locked amounts."""
        orders = [
            {"trade_type": "BUY", "price": Decimal("10"), "amount": Decimal("5"), "executed": Decimal("0")},
            {"trade_type": "BUY", "price": Decimal("9.5"), "amount": Decimal("3"), "executed": Decimal("1")},
            {"trade_type": "SELL", "amount": Decimal("8"), "executed": Decimal("2")},
        ]

        locked_base = Decimal("0")
        locked_quote = Decimal("0")
        for o in orders:
            remaining = o["amount"] - o["executed"]
            if o["trade_type"] == "BUY":
                locked_quote += o["price"] * remaining
            else:
                locked_base += remaining

        # Buy 1: 10 * 5 = 50, Buy 2: 9.5 * 2 = 19, Sell: 6 base
        assert locked_quote == Decimal("69.0")
        assert locked_base == Decimal("6")


# ============================================================================
# Trading Rules Tests
# ============================================================================


class TestTradingRules:
    """Tests for trading rule computation from MarketConfig."""

    def test_min_price_increment(self):
        """min_price_increment = tick_size / price_precision."""
        mc = make_market_config(tick_size=100, price_precision=100000000)
        min_price_increment = Decimal(str(mc.tick_size)) / Decimal(
            str(mc.price_precision)
        )
        assert min_price_increment == Decimal("0.000001")

    def test_min_base_amount_increment(self):
        """min_base_amount_increment = 1 / size_precision."""
        mc = make_market_config(size_precision=10000000000)
        min_base_amount = Decimal("1") / Decimal(str(mc.size_precision))
        assert min_base_amount == Decimal("0.0000000001")

    def test_different_market_precisions(self):
        """Rules should adapt to different market configurations."""
        mc = make_market_config(
            tick_size=1,
            price_precision=1000000,
            size_precision=1000000000000000000,
        )
        min_price = Decimal(str(mc.tick_size)) / Decimal(str(mc.price_precision))
        min_base = Decimal("1") / Decimal(str(mc.size_precision))
        assert min_price == Decimal("0.000001")
        assert min_base == Decimal("1E-18")


# ============================================================================
# WebSocket Price Formatting Tests
# ============================================================================


class TestWebSocketFormatting:
    """Tests for frontend orderbook WebSocket price/size formatting."""

    def test_format_websocket_price(self):
        """WS prices in 10^18 format should convert to readable decimals."""
        raw = 241_470_000_000_000_000_000
        price = KuruFrontendOrderbookClient.format_websocket_price(raw)
        assert abs(price - 241.47) < 1e-10

    def test_format_websocket_size(self):
        """WS sizes should convert using market size_precision."""
        raw = 100_000_000_000
        size = KuruFrontendOrderbookClient.format_websocket_size(raw, 10_000_000_000)
        assert abs(size - 10.0) < 1e-10

    def test_format_zero_price(self):
        assert KuruFrontendOrderbookClient.format_websocket_price(0) == 0.0

    def test_format_zero_size(self):
        assert (
            KuruFrontendOrderbookClient.format_websocket_size(0, 10_000_000_000) == 0.0
        )

    def test_small_price(self):
        """Very small prices should still convert correctly."""
        raw = 1_000_000_000_000_000  # 0.001
        price = KuruFrontendOrderbookClient.format_websocket_price(raw)
        assert abs(price - 0.001) < 1e-15


# ============================================================================
# Orderbook Update Conversion Tests
# ============================================================================


class TestOrderbookUpdateConversion:
    """Tests for converting FrontendOrderbookUpdate to orderbook data."""

    def test_bids_asks_conversion(self):
        """FrontendOrderbookUpdate bids/asks are pre-normalized floats."""
        update = FrontendOrderbookUpdate(
            events=[],
            b=[
                (10.0, 5.0),
                (9.5, 3.0),
            ],
            a=[
                (10.5, 2.0),
                (11.0, 4.0),
            ],
        )

        # Values are already human-readable floats
        assert len(update.b) == 2
        assert abs(update.b[0][0] - 10.0) < 1e-10
        assert abs(update.b[0][1] - 5.0) < 1e-10
        assert abs(update.b[1][0] - 9.5) < 1e-10

        assert len(update.a) == 2
        assert abs(update.a[0][0] - 10.5) < 1e-10
        assert abs(update.a[1][0] - 11.0) < 1e-10

    def test_trade_event_extraction(self):
        """Trade events should be identifiable by event type 'Trade'."""
        events = [
            FrontendEvent(
                e="Trade",
                ts=1000,
                mad="0xabc",
                p=10.0,
                s=0.5,
                ib=True,
                t="0xtaker",
                m="0xmaker",
            ),
            FrontendEvent(
                e="OrderCreated",
                ts=1001,
                mad="0xabc",
                p=11.0,
                s=0.3,
                ib=False,
            ),
        ]

        trade_events = [e for e in events if e.e == "Trade"]
        assert len(trade_events) == 1
        assert abs(trade_events[0].p - 10.0) < 1e-10

    def test_empty_orderbook_update(self):
        """Updates with no bids/asks should be handled gracefully."""
        update = FrontendOrderbookUpdate(events=[], b=None, a=None)
        assert update.b is None
        assert update.a is None
        assert update.events == []

    def test_snapshot_with_events(self):
        """Snapshot updates can include events alongside bids/asks."""
        update = FrontendOrderbookUpdate(
            events=[
                FrontendEvent(e="Trade", ts=1000, mad="0xabc", p=100.0, s=50.0),
            ],
            b=[(10.0, 5.0)],
            a=[(11.0, 3.0)],
        )
        assert len(update.events) == 1
        assert len(update.b) == 1
        assert len(update.a) == 1


# ============================================================================
# Constants Tests
# ============================================================================


class TestConstants:
    """Tests for connector constants."""

    def test_exchange_name(self):
        assert CONSTANTS.EXCHANGE_NAME == "kuru"

    def test_known_markets_not_empty(self):
        assert len(CONSTANTS.KNOWN_MARKETS) > 0

    def test_known_market_has_required_fields(self):
        for addr, config in CONSTANTS.KNOWN_MARKETS.items():
            assert "market_address" in config
            assert "market_symbol" in config
            assert "price_precision" in config
            assert "size_precision" in config
            assert "tick_size" in config
            assert "base_symbol" in config
            assert "quote_symbol" in config

    def test_fee_defaults(self):
        assert CONSTANTS.DEFAULT_MAKER_FEE_BPS == 0
        assert CONSTANTS.DEFAULT_TAKER_FEE_BPS == 10

    def test_client_order_id_prefix(self):
        assert CONSTANTS.CLIENT_ORDER_ID_PREFIX == "kuru"

    def test_max_order_id_len(self):
        assert CONSTANTS.MAX_ORDER_ID_LEN == 64

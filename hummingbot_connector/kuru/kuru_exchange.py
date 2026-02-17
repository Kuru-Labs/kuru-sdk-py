"""
Kuru Exchange connector for Hummingbot.

Wraps the kuru-mm-py SDK to integrate Kuru's on-chain CLOB DEX
with Hummingbot's trading strategies (e.g., pure market making).

Key design: SDK order callbacks are bridged to Hummingbot's order
tracker via an asyncio.Queue, ensuring thread-safe event processing.
"""

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import (
    InFlightOrder,
    OrderState,
    OrderUpdate,
    TradeUpdate,
)
from hummingbot.core.data_type.order_book_tracker_data_source import (
    OrderBookTrackerDataSource,
)
from hummingbot.core.data_type.trade_fee import (
    AddedToCostTradeFee,
    TokenAmount,
    TradeFeeBase,
)
from hummingbot.core.data_type.user_stream_tracker_data_source import (
    UserStreamTrackerDataSource,
)
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.core.web_assistant.web_assistants_factory import (
    WebAssistantsFactory,
)

from kuru_sdk_py.client import KuruClient
from kuru_sdk_py.configs import (
    ConfigManager,
    ConnectionConfig,
    MarketConfig,
    WalletConfig,
)
from kuru_sdk_py.feed.orderbook_ws import (
    FrontendOrderbookUpdate,
)
from kuru_sdk_py.manager.order import Order as SdkOrder
from kuru_sdk_py.manager.order import OrderSide as SdkOrderSide
from kuru_sdk_py.manager.order import OrderStatus as SdkOrderStatus
from kuru_sdk_py.manager.order import OrderType as SdkOrderType

from hummingbot_connector.kuru import kuru_constants as CONSTANTS
from hummingbot_connector.kuru.kuru_api_order_book_data_source import (
    KuruAPIOrderBookDataSource,
)
from hummingbot_connector.kuru.kuru_api_user_stream_data_source import (
    KuruAPIUserStreamDataSource,
)
from hummingbot_connector.kuru.kuru_auth import KuruAuth
from hummingbot_connector.kuru.kuru_utils import get_market_config

logger = logging.getLogger(__name__)

s_decimal_NaN = Decimal("nan")


class KuruExchange(ExchangePyBase):
    """
    Hummingbot exchange connector for Kuru on-chain CLOB DEX.

    Wraps the Kuru SDK (KuruClient) and bridges its async callback-based
    event system with Hummingbot's order tracker and strategy framework.

    Order flow:
        Strategy -> _place_order() -> KuruClient.place_orders() -> on-chain TX
        on-chain event -> SDK callback -> _sdk_order_event_queue
        -> _user_stream_event_listener() -> _order_tracker updates -> Strategy

    Balance model:
        Uses margin account balances (not wallet balances). Orders lock
        margin: buy orders lock quote, sell orders lock base.
    """

    # ----------------------------------------------------------------
    # Class-level config
    # ----------------------------------------------------------------

    web_utils = None  # Not using REST API throttler

    # SDK Order Status -> Hummingbot OrderState mapping
    _SDK_STATUS_MAP = {
        SdkOrderStatus.ORDER_CREATED: OrderState.PENDING_CREATE,
        SdkOrderStatus.ORDER_SENT: OrderState.PENDING_CREATE,
        SdkOrderStatus.ORDER_PLACED: OrderState.OPEN,
        SdkOrderStatus.ORDER_PARTIALLY_FILLED: OrderState.PARTIALLY_FILLED,
        SdkOrderStatus.ORDER_FULLY_FILLED: OrderState.FILLED,
        SdkOrderStatus.ORDER_CANCELLED: OrderState.CANCELED,
        SdkOrderStatus.ORDER_TIMEOUT: OrderState.FAILED,
        SdkOrderStatus.ORDER_FAILED: OrderState.FAILED,
    }

    def __init__(
        self,
        private_key: str,
        market_address: str,
        trading_pairs: List[str],
        trading_required: bool = True,
        domain: str = CONSTANTS.DEFAULT_DOMAIN,
        rpc_url: Optional[str] = None,
        rpc_ws_url: Optional[str] = None,
        kuru_ws_url: Optional[str] = None,
        kuru_api_url: Optional[str] = None,
    ):
        # Store all custom fields BEFORE super().__init__()
        self._private_key = private_key
        self._market_address = market_address
        self._domain_str = domain
        self._trading_required = trading_required
        self._trading_pairs_list = trading_pairs

        # Optional endpoint overrides
        self._rpc_url = rpc_url
        self._rpc_ws_url = rpc_ws_url
        self._kuru_ws_url = kuru_ws_url
        self._kuru_api_url = kuru_api_url

        # Auth
        self._kuru_auth = KuruAuth(private_key)

        # SDK components (created in start_network)
        self._client: Optional[KuruClient] = None
        self._market_config: Optional[MarketConfig] = None

        # Shared queues for SDK -> Hummingbot bridge
        self._sdk_order_event_queue: asyncio.Queue[SdkOrder] = asyncio.Queue()
        self._sdk_orderbook_queue: asyncio.Queue[FrontendOrderbookUpdate] = (
            asyncio.Queue()
        )

        # Last traded price cache (updated from orderbook WS events)
        self._last_traded_prices: Dict[str, float] = {}

        # Track SDK start task
        self._sdk_start_task: Optional[asyncio.Task] = None

        super().__init__()

    # ----------------------------------------------------------------
    # Abstract properties
    # ----------------------------------------------------------------

    @property
    def name(self) -> str:
        return CONSTANTS.EXCHANGE_NAME

    @property
    def authenticator(self):
        return self._kuru_auth

    @property
    def rate_limits_rules(self) -> List[RateLimit]:
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self) -> str:
        return self._domain_str

    @property
    def client_order_id_max_length(self) -> int:
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self) -> str:
        return CONSTANTS.CLIENT_ORDER_ID_PREFIX

    @property
    def trading_rules_request_path(self) -> str:
        return ""  # Not used - rules come from SDK MarketConfig

    @property
    def trading_pairs_request_path(self) -> str:
        return ""  # Not used

    @property
    def check_network_request_path(self) -> str:
        return ""  # Not used - we override check_network()

    @property
    def trading_pairs(self) -> List[str]:
        return self._trading_pairs_list

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        # Cancel is an on-chain TX - confirmed asynchronously via callback
        return False

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    # ----------------------------------------------------------------
    # Public accessors for data sources
    # ----------------------------------------------------------------

    @property
    def sdk_orderbook_queue(self) -> asyncio.Queue:
        """Queue of FrontendOrderbookUpdate for the orderbook data source."""
        return self._sdk_orderbook_queue

    @property
    def last_traded_prices(self) -> Dict[str, float]:
        return self._last_traded_prices

    @property
    def size_precision(self) -> int:
        """Market's size_precision for WS size conversion."""
        if self._market_config:
            return self._market_config.size_precision
        return 1

    # ----------------------------------------------------------------
    # Supported order types
    # ----------------------------------------------------------------

    def supported_order_types(self) -> List[OrderType]:
        return [OrderType.LIMIT, OrderType.LIMIT_MAKER]

    # ----------------------------------------------------------------
    # Lifecycle: start / stop / check_network
    # ----------------------------------------------------------------

    async def start_network(self):
        await super().start_network()
        self._sdk_start_task = asyncio.ensure_future(self._start_sdk())

    async def stop_network(self):
        if self._sdk_start_task is not None:
            self._sdk_start_task.cancel()
            self._sdk_start_task = None

        if self._client is not None:
            try:
                await self._client.stop()
            except Exception:
                logger.exception("Error stopping KuruClient")
            self._client = None

        await super().stop_network()

    async def check_network(self) -> NetworkStatus:
        try:
            if self._client is None:
                return NetworkStatus.NOT_CONNECTED
            if not self._client.is_healthy():
                return NetworkStatus.NOT_CONNECTED
            return NetworkStatus.CONNECTED
        except Exception:
            return NetworkStatus.NOT_CONNECTED

    async def _start_sdk(self):
        """Initialize the Kuru SDK client and connect to all services."""
        try:
            # Load market config (checks known markets, falls back to chain)
            self._market_config = get_market_config(
                self._market_address,
                rpc_url=self._rpc_url,
            )
            logger.info(
                f"Loaded market config: {self._market_config.market_symbol} "
                f"(price_precision={self._market_config.price_precision}, "
                f"size_precision={self._market_config.size_precision}, "
                f"tick_size={self._market_config.tick_size})"
            )

            # Build SDK configs
            wallet_config = self._kuru_auth.get_wallet_config()
            connection_config = ConnectionConfig(
                rpc_url=self._rpc_url or CONSTANTS.DEFAULT_RPC_URL,
                rpc_ws_url=self._rpc_ws_url or CONSTANTS.DEFAULT_RPC_WS_URL,
                kuru_ws_url=self._kuru_ws_url or CONSTANTS.DEFAULT_KURU_WS_URL,
                kuru_api_url=self._kuru_api_url or CONSTANTS.DEFAULT_KURU_API_URL,
            )

            # Create SDK client
            self._client = await KuruClient.create(
                market_config=self._market_config,
                connection_config=connection_config,
                wallet_config=wallet_config,
            )

            # Register callbacks BEFORE start
            self._client.set_order_callback(self._on_sdk_order_event)
            self._client.set_orderbook_callback(self._on_sdk_orderbook_event)

            # Start client (EIP-7702 auth, RPC WebSocket, event processing)
            await self._client.start()

            # Subscribe to orderbook WebSocket
            await self._client.subscribe_to_orderbook()

            # Build initial trading rules
            await self._update_trading_rules()

            logger.info("Kuru SDK started successfully")

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to start Kuru SDK")
            raise

    # ----------------------------------------------------------------
    # SDK callbacks
    # ----------------------------------------------------------------

    async def _on_sdk_order_event(self, sdk_order: SdkOrder):
        """SDK order callback -> push to bridge queue."""
        await self._sdk_order_event_queue.put(sdk_order)

    async def _on_sdk_orderbook_event(self, update: FrontendOrderbookUpdate):
        """SDK orderbook callback -> update last traded price + push to queue."""
        # Extract last traded price from trade events (prices are pre-normalized)
        for event in update.events:
            if event.e == "Trade" and event.p is not None:
                for pair in self._trading_pairs_list:
                    self._last_traded_prices[pair] = event.p

        await self._sdk_orderbook_queue.put(update)

    # ----------------------------------------------------------------
    # User stream event listener (SDK callback bridge)
    # ----------------------------------------------------------------

    async def _user_stream_event_listener(self):
        """
        Consume SDK order events from the bridge queue and map them
        to Hummingbot OrderUpdate / TradeUpdate for the order tracker.
        """
        while True:
            try:
                sdk_order: SdkOrder = await self._sdk_order_event_queue.get()
                await self._process_sdk_order_event(sdk_order)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in SDK order event listener")
                await asyncio.sleep(1.0)

    async def _process_sdk_order_event(self, sdk_order: SdkOrder):
        """Process a single SDK Order event."""
        client_order_id = sdk_order.cloid

        # Skip cancel-type orders (cancel confirmations come via original order)
        if sdk_order.order_type == SdkOrderType.CANCEL:
            return

        # Find the tracked order in Hummingbot
        tracked_order = self._order_tracker.fetch_order(
            client_order_id=client_order_id
        )
        if tracked_order is None:
            return

        status = sdk_order.status

        if status == SdkOrderStatus.ORDER_PLACED:
            # Order confirmed on-chain - update to real exchange_order_id
            exchange_order_id = (
                str(sdk_order.kuru_order_id)
                if sdk_order.kuru_order_id is not None
                else None
            )
            self._order_tracker.process_order_update(
                OrderUpdate(
                    trading_pair=tracked_order.trading_pair,
                    update_timestamp=time.time(),
                    new_state=OrderState.OPEN,
                    client_order_id=client_order_id,
                    exchange_order_id=exchange_order_id,
                )
            )

        elif status in (
            SdkOrderStatus.ORDER_PARTIALLY_FILLED,
            SdkOrderStatus.ORDER_FULLY_FILLED,
        ):
            # Process any new fills
            self._process_fills(sdk_order, tracked_order)

            new_state = (
                OrderState.PARTIALLY_FILLED
                if status == SdkOrderStatus.ORDER_PARTIALLY_FILLED
                else OrderState.FILLED
            )
            self._order_tracker.process_order_update(
                OrderUpdate(
                    trading_pair=tracked_order.trading_pair,
                    update_timestamp=time.time(),
                    new_state=new_state,
                    client_order_id=client_order_id,
                    exchange_order_id=tracked_order.exchange_order_id,
                )
            )

        elif status == SdkOrderStatus.ORDER_CANCELLED:
            self._order_tracker.process_order_update(
                OrderUpdate(
                    trading_pair=tracked_order.trading_pair,
                    update_timestamp=time.time(),
                    new_state=OrderState.CANCELED,
                    client_order_id=client_order_id,
                    exchange_order_id=tracked_order.exchange_order_id,
                )
            )

        elif status == SdkOrderStatus.ORDER_TIMEOUT:
            self._order_tracker.process_order_update(
                OrderUpdate(
                    trading_pair=tracked_order.trading_pair,
                    update_timestamp=time.time(),
                    new_state=OrderState.FAILED,
                    client_order_id=client_order_id,
                    exchange_order_id=tracked_order.exchange_order_id,
                )
            )

    def _process_fills(self, sdk_order: SdkOrder, tracked_order: InFlightOrder):
        """
        Emit TradeUpdate for each new fill in the SDK order.

        Compares the number of fills already reported to Hummingbot
        (len(tracked_order.order_fills)) against the SDK's fill list
        (sdk_order.filled_sizes) and reports only new ones.
        """
        already_reported = len(tracked_order.order_fills)
        all_fills = sdk_order.filled_sizes

        exchange_order_id = (
            str(sdk_order.kuru_order_id)
            if sdk_order.kuru_order_id is not None
            else tracked_order.exchange_order_id
        )

        for i in range(already_reported, len(all_fills)):
            fill_size = Decimal(str(all_fills[i]))
            # Maker fills execute at the limit price
            fill_price = tracked_order.price if tracked_order.price else Decimal("0")
            fill_quote = fill_price * fill_size

            fee = AddedToCostTradeFee(
                percent=Decimal("0"),  # Maker fee is 0%
            )

            trade_update = TradeUpdate(
                trade_id=f"{sdk_order.cloid}_{i}",
                client_order_id=sdk_order.cloid,
                exchange_order_id=exchange_order_id,
                trading_pair=tracked_order.trading_pair,
                fill_timestamp=time.time(),
                fill_price=fill_price,
                fill_base_amount=fill_size,
                fill_quote_amount=fill_quote,
                fee=fee,
                is_taker=False,
            )
            self._order_tracker.process_trade_update(trade_update)

    # ----------------------------------------------------------------
    # Order placement
    # ----------------------------------------------------------------

    async def _place_order(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        **kwargs,
    ) -> Tuple[str, float]:
        """
        Place a single limit order on Kuru via the SDK.

        Returns (txhash, timestamp). The txhash serves as a temporary
        exchange_order_id until the real kuru_order_id arrives via callback.
        """
        if self._client is None:
            raise RuntimeError("KuruClient not initialized - call start_network first")

        side = SdkOrderSide.BUY if trade_type == TradeType.BUY else SdkOrderSide.SELL

        sdk_order = SdkOrder(
            cloid=order_id,
            order_type=SdkOrderType.LIMIT,
            side=side,
            price=float(price),
            size=float(amount),
            post_only=True,
        )

        txhash = await self._client.place_orders([sdk_order])
        return txhash, time.time()

    # ----------------------------------------------------------------
    # Order cancellation
    # ----------------------------------------------------------------

    async def _place_cancel(
        self, order_id: str, tracked_order: InFlightOrder
    ):
        """
        Cancel an order on Kuru via the SDK.

        Returns True if cancel TX was submitted, False if the order
        hasn't been confirmed on-chain yet (kuru_order_id unknown).
        """
        if self._client is None:
            logger.warning("Cannot cancel: KuruClient not initialized")
            return False

        kuru_order_id = self._client.orders_manager.get_kuru_order_id(order_id)
        if kuru_order_id is None:
            logger.warning(
                f"Cannot cancel {order_id}: kuru_order_id not yet available "
                "(order may not be confirmed on-chain yet)"
            )
            return False

        cancel_order = SdkOrder(
            cloid=order_id,
            order_type=SdkOrderType.CANCEL,
            order_ids_to_cancel=[kuru_order_id],
        )

        try:
            await self._client.place_orders([cancel_order])
            return True
        except Exception:
            logger.exception(f"Failed to cancel order {order_id}")
            return False

    # ----------------------------------------------------------------
    # Balance management
    # ----------------------------------------------------------------

    async def _update_balances(self):
        """
        Fetch margin account balances and compute available balances.

        Available = margin balance - locked in open orders.
        Buy orders lock quote tokens, sell orders lock base tokens.
        """
        if self._client is None or self._market_config is None:
            return

        try:
            base_margin_wei, quote_margin_wei = (
                await self._client.user.get_margin_balances()
            )
        except Exception:
            logger.exception("Failed to fetch margin balances")
            return

        mc = self._market_config
        base_symbol = mc.base_symbol
        quote_symbol = mc.quote_symbol

        base_total = Decimal(str(base_margin_wei)) / Decimal(
            10 ** mc.base_token_decimals
        )
        quote_total = Decimal(str(quote_margin_wei)) / Decimal(
            10 ** mc.quote_token_decimals
        )

        # Compute locked amounts from active orders
        locked_base = Decimal("0")
        locked_quote = Decimal("0")
        for order in self._order_tracker.active_orders.values():
            remaining = order.amount - order.executed_amount_base
            if remaining <= Decimal("0"):
                continue
            if order.trade_type == TradeType.BUY:
                order_price = order.price if order.price else Decimal("0")
                locked_quote += order_price * remaining
            else:
                locked_base += remaining

        self._account_balances[base_symbol] = base_total
        self._account_balances[quote_symbol] = quote_total
        self._account_available_balances[base_symbol] = max(
            Decimal("0"), base_total - locked_base
        )
        self._account_available_balances[quote_symbol] = max(
            Decimal("0"), quote_total - locked_quote
        )

    # ----------------------------------------------------------------
    # Trading rules
    # ----------------------------------------------------------------

    async def _update_trading_rules(self):
        """Build trading rules from the SDK's MarketConfig."""
        if self._market_config is None:
            return

        mc = self._market_config
        trading_pair = self._trading_pairs_list[0]

        min_price_increment = Decimal(str(mc.tick_size)) / Decimal(
            str(mc.price_precision)
        )
        min_base_amount_increment = Decimal("1") / Decimal(str(mc.size_precision))

        rule = TradingRule(
            trading_pair=trading_pair,
            min_price_increment=min_price_increment,
            min_base_amount_increment=min_base_amount_increment,
            supports_limit_orders=True,
            supports_market_orders=False,
            buy_order_collateral_token=mc.quote_symbol,
            sell_order_collateral_token=mc.base_symbol,
        )
        self._trading_rules.clear()
        self._trading_rules[trading_pair] = rule

    async def _format_trading_rules(
        self, exchange_info_dict: Dict[str, Any]
    ) -> List[TradingRule]:
        """
        Parse trading rules from exchange info.

        Called by the base class polling loop. We override
        _update_trading_rules to build rules from MarketConfig
        directly, so this is a passthrough.
        """
        # If market config is available, build from it
        if self._market_config is not None:
            await self._update_trading_rules()
        return list(self._trading_rules.values())

    # ----------------------------------------------------------------
    # Fees
    # ----------------------------------------------------------------

    def _get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = s_decimal_NaN,
        is_maker: Optional[bool] = None,
    ) -> AddedToCostTradeFee:
        """
        Return trade fee estimate.

        Kuru maker orders (post-only) have 0% fee.
        Taker orders have ~10 bps fee (for estimation).
        """
        is_maker = is_maker or order_type in (OrderType.LIMIT, OrderType.LIMIT_MAKER)
        if is_maker:
            return AddedToCostTradeFee(percent=Decimal("0"))
        else:
            return AddedToCostTradeFee(
                percent=Decimal(str(CONSTANTS.DEFAULT_TAKER_FEE_BPS)) / Decimal("10000")
            )

    async def _update_trading_fees(self):
        """No dynamic fee updates needed for on-chain DEX."""
        pass

    # ----------------------------------------------------------------
    # Last traded price
    # ----------------------------------------------------------------

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        """Return last traded price from orderbook WS events."""
        return self._last_traded_prices.get(trading_pair, 0.0)

    # ----------------------------------------------------------------
    # Order status queries (REST fallback)
    # ----------------------------------------------------------------

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        """
        Query current order status from the SDK's order manager.

        This is a fallback for when events are missed. The primary
        order tracking is via SDK callbacks.
        """
        client_order_id = tracked_order.client_order_id
        sdk_order = self._client.orders_manager.cloid_to_order.get(
            client_order_id
        ) if self._client else None

        if sdk_order is None:
            # Order not found in SDK - might have been cleaned up
            return OrderUpdate(
                trading_pair=tracked_order.trading_pair,
                update_timestamp=time.time(),
                new_state=tracked_order.current_state,
                client_order_id=client_order_id,
                exchange_order_id=tracked_order.exchange_order_id,
            )

        new_state = self._SDK_STATUS_MAP.get(
            sdk_order.status, tracked_order.current_state
        )
        exchange_order_id = (
            str(sdk_order.kuru_order_id)
            if sdk_order.kuru_order_id is not None
            else tracked_order.exchange_order_id
        )

        return OrderUpdate(
            trading_pair=tracked_order.trading_pair,
            update_timestamp=time.time(),
            new_state=new_state,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
        )

    async def _all_trade_updates_for_order(
        self, order: InFlightOrder
    ) -> List[TradeUpdate]:
        """
        Return all trade fills for an order from the SDK.

        Fallback for missed fill events.
        """
        if self._client is None:
            return []

        sdk_order = self._client.orders_manager.cloid_to_order.get(
            order.client_order_id
        )
        if sdk_order is None:
            return []

        exchange_order_id = (
            str(sdk_order.kuru_order_id)
            if sdk_order.kuru_order_id is not None
            else order.exchange_order_id
        )

        trade_updates = []
        for i, fill_size in enumerate(sdk_order.filled_sizes):
            fill_amount = Decimal(str(fill_size))
            fill_price = order.price if order.price else Decimal("0")

            trade_updates.append(
                TradeUpdate(
                    trade_id=f"{sdk_order.cloid}_{i}",
                    client_order_id=sdk_order.cloid,
                    exchange_order_id=exchange_order_id,
                    trading_pair=order.trading_pair,
                    fill_timestamp=time.time(),
                    fill_price=fill_price,
                    fill_base_amount=fill_amount,
                    fill_quote_amount=fill_price * fill_amount,
                    fee=AddedToCostTradeFee(percent=Decimal("0")),
                    is_taker=False,
                )
            )
        return trade_updates

    # ----------------------------------------------------------------
    # Network check override
    # ----------------------------------------------------------------

    async def _make_network_check_request(self):
        """Override to check SDK health instead of REST ping."""
        if self._client is None:
            raise ConnectionError("KuruClient not initialized")
        if not self._client.is_healthy():
            raise ConnectionError("KuruClient is not healthy")

    async def _make_trading_rules_request(self) -> Any:
        """Override to return market config data instead of REST call."""
        if self._market_config is not None:
            return {"market_config": self._market_config}
        return {}

    async def _make_trading_pairs_request(self) -> Any:
        """Override to return configured trading pairs."""
        return {"pairs": self._trading_pairs_list}

    # ----------------------------------------------------------------
    # Error classification
    # ----------------------------------------------------------------

    def _is_request_exception_related_to_time_synchronizer(
        self, request_exception: Exception
    ) -> bool:
        # DEX has no server time sync issues
        return False

    def _is_order_not_found_during_status_update_error(
        self, status_update_exception: Exception
    ) -> bool:
        return "not found" in str(status_update_exception).lower()

    def _is_order_not_found_during_cancelation_error(
        self, cancelation_exception: Exception
    ) -> bool:
        return "not found" in str(cancelation_exception).lower()

    # ----------------------------------------------------------------
    # Factory methods for data sources
    # ----------------------------------------------------------------

    def _create_web_assistants_factory(self) -> Optional[WebAssistantsFactory]:
        """Not used - SDK handles all network communication."""
        return None

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        return KuruAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs_list,
            connector=self,
        )

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        return KuruAPIUserStreamDataSource(connector=self)

    # ----------------------------------------------------------------
    # Trading pair symbol mapping
    # ----------------------------------------------------------------

    def _initialize_trading_pair_symbols_from_exchange_info(
        self, exchange_info: Dict[str, Any]
    ):
        """
        Build trading pair symbol map.

        For Kuru, the trading pair is derived from MarketConfig.market_symbol
        (e.g., "MON-USDC") and maps 1:1 with the Hummingbot trading pair.
        """
        from bidict import bidict

        mapping = bidict()
        for pair in self._trading_pairs_list:
            mapping[pair] = pair  # exchange symbol == hummingbot symbol
        self._set_trading_pair_symbol_map(mapping)

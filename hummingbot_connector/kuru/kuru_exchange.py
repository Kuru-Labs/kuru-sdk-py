"""Kuru DEX exchange connector for Hummingbot.

Implements ExchangePyBase by delegating to KuruClient for all on-chain
operations: order placement, cancellation, balance queries, and event handling.
"""

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.cancellation_result import CancellationResult
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.core.utils.tracking_nonce import get_tracking_nonce

from src.client import KuruClient
from src.configs import (
    ConfigManager,
    ConnectionConfig,
    MarketConfig,
    WalletConfig,
)
from src.manager.order import Order as KuruOrder, OrderSide as KuruOrderSide, OrderType as KuruOrderType, OrderStatus as KuruOrderStatus

from hummingbot_connector.kuru.kuru_auth import KuruAuth
from hummingbot_connector.kuru.kuru_api_order_book_data_source import KuruAPIOrderBookDataSource
from hummingbot_connector.kuru.kuru_api_user_stream_data_source import KuruAPIUserStreamDataSource
from hummingbot_connector.kuru.kuru_constants import (
    EXCHANGE_NAME,
    DEFAULT_KURU_API_URL,
    DEFAULT_KURU_WS_URL,
    DEFAULT_RPC_URL,
    DEFAULT_RPC_WS_URL,
    KNOWN_MARKETS,
)
from hummingbot_connector.kuru.kuru_utils import (
    trading_pair_to_market_address,
    trading_pair_to_market_info,
)

logger = logging.getLogger(__name__)

# Kuru OrderStatus -> Hummingbot OrderState mapping
KURU_TO_HB_ORDER_STATE = {
    KuruOrderStatus.ORDER_CREATED: OrderState.PENDING_CREATE,
    KuruOrderStatus.ORDER_SENT: OrderState.PENDING_CREATE,
    KuruOrderStatus.ORDER_PLACED: OrderState.OPEN,
    KuruOrderStatus.ORDER_PARTIALLY_FILLED: OrderState.PARTIALLY_FILLED,
    KuruOrderStatus.ORDER_FULLY_FILLED: OrderState.FILLED,
    KuruOrderStatus.ORDER_CANCELLED: OrderState.CANCELED,
    KuruOrderStatus.ORDER_TIMEOUT: OrderState.FAILED,
}


class KuruExchange(ExchangePyBase):
    """Hummingbot CLOB DEX connector for Kuru on Monad."""

    web_utils = None  # Not using centralized REST gateway

    def __init__(
        self,
        private_key: str,
        trading_pairs: List[str],
        trading_required: bool = True,
        rpc_url: Optional[str] = None,
        rpc_ws_url: Optional[str] = None,
        kuru_ws_url: Optional[str] = None,
        kuru_api_url: Optional[str] = None,
    ):
        self._auth = KuruAuth(private_key)
        self._trading_pairs = trading_pairs
        self._trading_required = trading_required

        # Connection params (use defaults if not provided)
        self._rpc_url = rpc_url or DEFAULT_RPC_URL
        self._rpc_ws_url = rpc_ws_url or DEFAULT_RPC_WS_URL
        self._kuru_ws_url = kuru_ws_url or DEFAULT_KURU_WS_URL
        self._kuru_api_url = kuru_api_url or DEFAULT_KURU_API_URL

        # SDK client (created async in start_network)
        self._kuru_client: Optional[KuruClient] = None
        self._market_config: Optional[MarketConfig] = None
        self._market_info: Optional[dict] = None

        super().__init__()

    # ----------------------------------------------------------------
    # ExchangePyBase abstract property implementations
    # ----------------------------------------------------------------

    @property
    def authenticator(self):
        return self._auth

    @property
    def name(self) -> str:
        return EXCHANGE_NAME

    @property
    def rate_limits_rules(self) -> List:
        return []  # On-chain connector, no API rate limits

    @property
    def domain(self) -> str:
        return ""

    @property
    def client_order_id_max_length(self) -> int:
        return 32  # bytes32 on-chain

    @property
    def client_order_id_prefix(self) -> str:
        return "kuru"

    @property
    def trading_rules(self) -> Dict[str, TradingRule]:
        return self._trading_rules

    @property
    def trading_pairs(self) -> List[str]:
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return False  # Cancel is async (blockchain tx)

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    @property
    def check_network_request_path(self):
        return ""

    @property
    def trading_pairs_request_path(self):
        return ""

    @property
    def status_dict(self) -> Dict[str, bool]:
        sd = super().status_dict
        sd["kuru_client_initialized"] = self._kuru_client is not None
        return sd

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------

    async def start_network(self):
        """Initialize KuruClient and connect to the blockchain."""
        await super().start_network()

        trading_pair = self._trading_pairs[0]
        self._market_info = trading_pair_to_market_info(trading_pair)
        if self._market_info is None:
            raise ValueError(
                f"Unknown trading pair '{trading_pair}'. "
                f"Known pairs: {list(KNOWN_MARKETS.keys())}"
            )

        market_address = self._market_info["market_address"]

        # Build SDK configs
        connection_config = ConnectionConfig(
            rpc_url=self._rpc_url,
            rpc_ws_url=self._rpc_ws_url,
            kuru_ws_url=self._kuru_ws_url,
            kuru_api_url=self._kuru_api_url,
        )

        wallet_config = self._auth.get_wallet_config()

        # Load full MarketConfig from known data + chain defaults
        self._market_config = MarketConfig(
            market_address=market_address,
            base_token=self._market_info["base_token"],
            quote_token=self._market_info["quote_token"],
            market_symbol=self._market_info["market_symbol"],
            mm_entrypoint_address="0xA9d8269ad1Bd6e2a02BD8996a338Dc5C16aef440",
            margin_contract_address="0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5",
            base_token_decimals=self._market_info["base_token_decimals"],
            quote_token_decimals=self._market_info["quote_token_decimals"],
            price_precision=self._market_info["price_precision"],
            size_precision=self._market_info["size_precision"],
            base_symbol=self._market_info["base_symbol"],
            quote_symbol=self._market_info["quote_symbol"],
            orderbook_implementation="0xea2Cc8769Fb04Ff1893Ed11cf517b7F040C823CD",
            margin_account_implementation="0x57cF97FE1FAC7D78B07e7e0761410cb2e91F0ca7",
        )

        # Create and start the SDK client
        self._kuru_client = await KuruClient.create(
            market_config=self._market_config,
            connection_config=connection_config,
            wallet_config=wallet_config,
        )

        # Register our callback for order status updates
        self._kuru_client.set_order_callback(self._on_kuru_order_update)

        await self._kuru_client.start()

        # Populate trading rules
        self._trading_rules = self._format_trading_rules()

        logger.info(
            f"Kuru client started for {trading_pair} "
            f"(market: {market_address}, wallet: {self._auth.wallet_address})"
        )

    async def stop_network(self):
        """Stop the SDK client (cancels orders, closes connections)."""
        if self._kuru_client is not None:
            try:
                await self._kuru_client.stop()
            except Exception:
                logger.exception("Error stopping Kuru client")
            self._kuru_client = None
        await super().stop_network()

    async def check_network(self) -> NetworkStatus:
        """Check if the SDK client is connected and operational."""
        if self._kuru_client is None:
            return NetworkStatus.NOT_CONNECTED
        try:
            # Quick balance check to verify connectivity
            await self._kuru_client.user.get_margin_balances()
            return NetworkStatus.CONNECTED
        except Exception:
            return NetworkStatus.NOT_CONNECTED

    # ----------------------------------------------------------------
    # Data source factories
    # ----------------------------------------------------------------

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        trading_pair = self._trading_pairs[0]
        market_info = trading_pair_to_market_info(trading_pair)
        return KuruAPIOrderBookDataSource(
            trading_pair=trading_pair,
            market_address=market_info["market_address"],
            size_precision=market_info["size_precision"],
            connector=self,
            ws_url=self._kuru_ws_url,
        )

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        return KuruAPIUserStreamDataSource()

    # ----------------------------------------------------------------
    # Order management
    # ----------------------------------------------------------------

    def supported_order_types(self) -> List[OrderType]:
        return [OrderType.LIMIT, OrderType.LIMIT_MAKER]

    def buy(
        self, trading_pair: str, amount: Decimal, order_type: OrderType, price: Decimal, **kwargs
    ) -> str:
        order_id = self._create_client_order_id(TradeType.BUY, trading_pair)
        safe_ensure_future(self._create_order(
            trade_type=TradeType.BUY,
            order_id=order_id,
            trading_pair=trading_pair,
            amount=amount,
            order_type=order_type,
            price=price,
        ))
        return order_id

    def sell(
        self, trading_pair: str, amount: Decimal, order_type: OrderType, price: Decimal, **kwargs
    ) -> str:
        order_id = self._create_client_order_id(TradeType.SELL, trading_pair)
        safe_ensure_future(self._create_order(
            trade_type=TradeType.SELL,
            order_id=order_id,
            trading_pair=trading_pair,
            amount=amount,
            order_type=order_type,
            price=price,
        ))
        return order_id

    def _create_client_order_id(self, trade_type: TradeType, trading_pair: str) -> str:
        nonce = get_tracking_nonce()
        side = "B" if trade_type == TradeType.BUY else "S"
        return f"{self.client_order_id_prefix}{side}{nonce}"

    async def _create_order(
        self,
        trade_type: TradeType,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        **kwargs,
    ):
        """Create and track an order, then submit it to Kuru."""
        try:
            # Start tracking in Hummingbot
            self.start_tracking_order(
                order_id=order_id,
                exchange_order_id=None,
                trading_pair=trading_pair,
                trade_type=trade_type,
                price=price,
                amount=amount,
                order_type=order_type,
            )

            # Build Kuru order
            kuru_side = KuruOrderSide.BUY if trade_type == TradeType.BUY else KuruOrderSide.SELL
            post_only = order_type in (OrderType.LIMIT_MAKER, OrderType.LIMIT)

            kuru_order = KuruOrder(
                cloid=order_id,
                order_type=KuruOrderType.LIMIT,
                side=kuru_side,
                price=float(price),
                size=float(amount),
                post_only=post_only,
            )

            # Submit to blockchain via SDK
            tx_hash = await self._kuru_client.place_orders([kuru_order])

            # Update tracked order with initial info
            order_update = OrderUpdate(
                client_order_id=order_id,
                exchange_order_id=tx_hash,  # Temporary; real kuru_order_id comes via callback
                trading_pair=trading_pair,
                update_timestamp=time.time(),
                new_state=OrderState.PENDING_CREATE,
            )
            self._order_tracker.process_order_update(order_update)

            logger.info(
                f"Submitted {trade_type.name} order {order_id}: "
                f"{amount} @ {price} (tx: {tx_hash})"
            )

        except Exception as e:
            logger.exception(f"Error placing order {order_id}: {e}")
            self.stop_tracking_order(order_id)
            order_update = OrderUpdate(
                client_order_id=order_id,
                trading_pair=trading_pair,
                update_timestamp=time.time(),
                new_state=OrderState.FAILED,
            )
            self._order_tracker.process_order_update(order_update)

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
        """ExchangePyBase hook for placing an order. Returns (exchange_order_id, timestamp)."""
        kuru_side = KuruOrderSide.BUY if trade_type == TradeType.BUY else KuruOrderSide.SELL
        post_only = order_type in (OrderType.LIMIT_MAKER, OrderType.LIMIT)

        kuru_order = KuruOrder(
            cloid=order_id,
            order_type=KuruOrderType.LIMIT,
            side=kuru_side,
            price=float(price),
            size=float(amount),
            post_only=post_only,
        )

        tx_hash = await self._kuru_client.place_orders([kuru_order])
        return tx_hash, time.time()

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        """Cancel an order by its on-chain kuru_order_id."""
        if tracked_order.exchange_order_id is None:
            logger.warning(f"Cannot cancel {order_id}: no exchange_order_id yet")
            return False

        # Look up the kuru order ID from the orders manager
        kuru_order_id = None
        if self._kuru_client is not None:
            kuru_order_id = self._kuru_client.orders_manager.get_kuru_order_id(order_id)

        if kuru_order_id is None:
            # Fall back to exchange_order_id if it was set to the kuru_order_id
            try:
                kuru_order_id = int(tracked_order.exchange_order_id)
            except (ValueError, TypeError):
                logger.warning(f"Cannot cancel {order_id}: unable to resolve kuru_order_id")
                return False

        cancel_order = KuruOrder(
            cloid=f"cancel_{order_id}",
            order_type=KuruOrderType.CANCEL,
            order_ids_to_cancel=[kuru_order_id],
        )

        try:
            await self._kuru_client.place_orders([cancel_order])
            return True
        except Exception as e:
            logger.exception(f"Error cancelling order {order_id}: {e}")
            return False

    async def cancel_all(self, timeout_seconds: float) -> List[CancellationResult]:
        """Cancel all active orders on the market."""
        results = []
        if self._kuru_client is not None:
            try:
                await self._kuru_client.cancel_all_active_orders_for_market()
                for order_id, tracked_order in self._order_tracker.active_orders.items():
                    results.append(CancellationResult(order_id, True))
            except Exception as e:
                logger.exception(f"Error cancelling all orders: {e}")
                for order_id in self._order_tracker.active_orders:
                    results.append(CancellationResult(order_id, False))
        return results

    # ----------------------------------------------------------------
    # SDK callback: order status updates
    # ----------------------------------------------------------------

    async def _on_kuru_order_update(self, kuru_order: KuruOrder):
        """Callback invoked by KuruClient when an order's status changes.

        Maps Kuru order events to Hummingbot OrderUpdate/TradeUpdate.
        """
        client_order_id = kuru_order.cloid

        # Skip cancel-type orders
        if kuru_order.order_type == KuruOrderType.CANCEL:
            return

        tracked_order = self._order_tracker.fetch_tracked_order(client_order_id)
        if tracked_order is None:
            logger.debug(f"Received update for untracked order: {client_order_id}")
            return

        trading_pair = tracked_order.trading_pair
        new_state = KURU_TO_HB_ORDER_STATE.get(kuru_order.status)
        if new_state is None:
            return

        # Set the real exchange_order_id from kuru_order_id
        exchange_order_id = tracked_order.exchange_order_id
        if kuru_order.kuru_order_id is not None:
            exchange_order_id = str(kuru_order.kuru_order_id)

        # Emit OrderUpdate
        order_update = OrderUpdate(
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            trading_pair=trading_pair,
            update_timestamp=kuru_order.timestamp,
            new_state=new_state,
        )
        self._order_tracker.process_order_update(order_update)

        # Emit TradeUpdate on fills
        if new_state in (OrderState.PARTIALLY_FILLED, OrderState.FILLED):
            fill_amount = self._compute_fill_amount(kuru_order, tracked_order)
            if fill_amount > Decimal("0"):
                fee = self.get_fee(
                    tracked_order.base_asset,
                    tracked_order.quote_asset,
                    tracked_order.order_type,
                    tracked_order.trade_type,
                    fill_amount,
                    tracked_order.price,
                )
                trade_update = TradeUpdate(
                    trade_id=f"{client_order_id}_{int(time.time() * 1e6)}",
                    client_order_id=client_order_id,
                    exchange_order_id=exchange_order_id,
                    trading_pair=trading_pair,
                    fee=fee,
                    fill_base_amount=fill_amount,
                    fill_quote_amount=fill_amount * tracked_order.price,
                    fill_price=tracked_order.price,
                    fill_timestamp=kuru_order.timestamp,
                )
                self._order_tracker.process_trade_update(trade_update)

    def _compute_fill_amount(self, kuru_order: KuruOrder, tracked_order: InFlightOrder) -> Decimal:
        """Compute incremental fill amount from order size change."""
        original_size = tracked_order.amount
        if kuru_order.size is not None:
            remaining = Decimal(str(kuru_order.size))
            filled_so_far = original_size - remaining
            already_filled = tracked_order.executed_amount_base
            incremental = filled_so_far - already_filled
            return max(incremental, Decimal("0"))
        return Decimal("0")

    # ----------------------------------------------------------------
    # Balances
    # ----------------------------------------------------------------

    async def _update_balances(self):
        """Fetch margin balances from the Kuru SDK."""
        if self._kuru_client is None:
            return

        try:
            base_wei, quote_wei = await self._kuru_client.user.get_margin_balances()

            base_decimals = self._market_info["base_token_decimals"]
            quote_decimals = self._market_info["quote_token_decimals"]

            base_balance = Decimal(str(base_wei)) / Decimal(10 ** base_decimals)
            quote_balance = Decimal(str(quote_wei)) / Decimal(10 ** quote_decimals)

            base_symbol = self._market_info["base_symbol"]
            quote_symbol = self._market_info["quote_symbol"]

            self._account_balances[base_symbol] = base_balance
            self._account_balances[quote_symbol] = quote_balance
            self._account_available_balances[base_symbol] = base_balance
            self._account_available_balances[quote_symbol] = quote_balance

        except Exception:
            logger.exception("Error updating Kuru balances")

    # ----------------------------------------------------------------
    # Trading rules
    # ----------------------------------------------------------------

    def _format_trading_rules(self) -> Dict[str, TradingRule]:
        """Build trading rules from the market config."""
        rules = {}
        if self._market_config is not None:
            trading_pair = self._trading_pairs[0]
            price_precision = self._market_config.price_precision
            size_precision = self._market_config.size_precision

            min_price_increment = Decimal("1") / Decimal(str(price_precision))
            min_base_increment = Decimal("1") / Decimal(str(size_precision))

            rules[trading_pair] = TradingRule(
                trading_pair=trading_pair,
                min_price_increment=min_price_increment,
                min_base_amount_increment=min_base_increment,
                min_order_size=min_base_increment,
                supports_limit_orders=True,
                supports_market_orders=False,  # Kuru SDK supports them, but start with limit only
                buy_order_collateral_token=self._market_info["quote_symbol"],
                sell_order_collateral_token=self._market_info["base_symbol"],
            )
        return rules

    async def _update_trading_rules(self):
        """Refresh trading rules (no-op for now; rules are static from market config)."""
        self._trading_rules = self._format_trading_rules()

    async def _format_trading_rules_from_exchange_info(self, exchange_info: Any) -> List[TradingRule]:
        """Not used -- rules come from MarketConfig."""
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
        price: Decimal = Decimal("0"),
        is_maker: Optional[bool] = None,
    ) -> AddedToCostTradeFee:
        """Return fee estimate. Kuru maker fee is 0; taker fee is small."""
        if is_maker is None:
            is_maker = order_type in (OrderType.LIMIT_MAKER, OrderType.LIMIT)

        if is_maker:
            return AddedToCostTradeFee(
                percent=Decimal("0"),
                flat_fees=[],
            )
        else:
            # Default taker fee estimate (actual fee depends on market params)
            return AddedToCostTradeFee(
                percent=Decimal("0.001"),  # 10 bps default taker
                flat_fees=[],
            )

    # ----------------------------------------------------------------
    # REST polling fallbacks (required by ExchangePyBase)
    # ----------------------------------------------------------------

    async def _update_order_status(self):
        """Poll for order status updates.

        Primary updates come via the SDK callback. This is a fallback
        to catch any missed events.
        """
        if self._kuru_client is None:
            return

        for order_id, tracked_order in self._order_tracker.active_orders.items():
            kuru_order = self._kuru_client.orders_manager.cloid_to_order.get(order_id)
            if kuru_order is not None:
                await self._on_kuru_order_update(kuru_order)

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        """Return trade updates for a specific order (used during recovery)."""
        return []

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        """Request current status of a tracked order."""
        if self._kuru_client is not None:
            kuru_order = self._kuru_client.orders_manager.cloid_to_order.get(
                tracked_order.client_order_id
            )
            if kuru_order is not None:
                new_state = KURU_TO_HB_ORDER_STATE.get(
                    kuru_order.status, OrderState.PENDING_CREATE
                )
                exchange_order_id = tracked_order.exchange_order_id
                if kuru_order.kuru_order_id is not None:
                    exchange_order_id = str(kuru_order.kuru_order_id)
                return OrderUpdate(
                    client_order_id=tracked_order.client_order_id,
                    exchange_order_id=exchange_order_id,
                    trading_pair=tracked_order.trading_pair,
                    update_timestamp=time.time(),
                    new_state=new_state,
                )

        return OrderUpdate(
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=tracked_order.exchange_order_id,
            trading_pair=tracked_order.trading_pair,
            update_timestamp=time.time(),
            new_state=tracked_order.current_state,
        )

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        """Return the last traded price. Falls back to 0 if unavailable."""
        # Could parse from orderbook WS events; for now return 0
        return 0.0


def safe_ensure_future(coro):
    """Schedule a coroutine without awaiting it."""
    asyncio.ensure_future(coro)

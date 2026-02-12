from loguru import logger
from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.contract import AsyncContract
from web3 import Web3

from typing import Optional

from src.configs import (
    MarketConfig,
    ConnectionConfig,
    WalletConfig,
    TransactionConfig,
    OrderExecutionConfig,
)
from src.manager.order import Order, OrderType, OrderStatus, OrderSide
from src.utils import load_abi
from src.transaction.transaction import AsyncTransactionSenderMixin
from src.transaction.access_list import build_access_list_for_cancel_only, build_access_list_for_cancel_and_place


def round_price_down(price_int: int, tick_size: int) -> int:
    """Round price down to nearest tick (for buys)."""
    return (price_int // tick_size) * tick_size


def round_price_up(price_int: int, tick_size: int) -> int:
    """Round price up to nearest tick (for sells)."""
    return ((price_int + tick_size - 1) // tick_size) * tick_size


class OrdersExecutor(AsyncTransactionSenderMixin):
    """
    OrdersExecutor wraps the MM Entrypoint smart contract and provides
    a method to place batch orders on-chain.
    """

    def __init__(
        self,
        market_config: MarketConfig,
        connection_config: ConnectionConfig,
        wallet_config: WalletConfig,
        transaction_config: TransactionConfig,
        order_execution_config: OrderExecutionConfig,
    ):
        """
        Initialize the OrdersExecutor with MM entrypoint contract.

        Note: This constructor does not verify the RPC connection.
        Use the async `create()` factory method for initialization with connection verification.

        Args:
            market_config: Market configuration (token addresses, decimals, contract addresses)
            connection_config: Connection configuration (RPC URLs, API URLs)
            wallet_config: Wallet configuration (private key, user address)
            transaction_config: Transaction behavior configuration
            order_execution_config: Order execution defaults
        """
        # Store all configs
        self.market_config = market_config
        self.connection_config = connection_config
        self.wallet_config = wallet_config
        self.transaction_config = transaction_config
        self.order_execution_config = order_execution_config

        # Extract commonly used values from configs
        self.mm_entrypoint_address = Web3.to_checksum_address(market_config.mm_entrypoint_address)
        self.order_book_address = Web3.to_checksum_address(market_config.market_address)
        self.user_address = Web3.to_checksum_address(wallet_config.user_address)

        # Initialize AsyncWeb3
        self.w3 = AsyncWeb3(AsyncHTTPProvider(connection_config.rpc_url))

        # Create account from private key for signing transactions
        self.account = self.w3.eth.account.from_key(wallet_config.private_key)

        # Load MM Entrypoint ABI and create contract instance
        # With EIP-7702, we call the user's EOA address (which has MM Entrypoint code delegated)
        mm_entrypoint_abi = load_abi("mm_entrypoint")
        self.contract: AsyncContract = self.w3.eth.contract(
            address=self.user_address, abi=mm_entrypoint_abi
        )

        self.orderbook_contract: AsyncContract = self.w3.eth.contract(
            address=self.order_book_address, abi=load_abi("orderbook")
        )

        # Keep reference to original contract address for logging
        self.contract_address = self.mm_entrypoint_address

        self._connected = False

    @classmethod
    async def create(
        cls,
        mm_entrypoint_address: str,
        config: KuruMMConfig,
        order_book_address: str,
        market_config: MarketConfig,
    ) -> "OrdersExecutor":
        """
        Factory method to create and initialize OrdersExecutor with connection verification.

        Args:
            mm_entrypoint_address: The address of the MM Entrypoint smart contract
            config: KuruMMConfig containing RPC URL and private key
            order_book_address: The address of the Order Book (market address)
            market_config: MarketConfig containing precision and market metadata

        Returns:
            Initialized OrdersExecutor instance

        Raises:
            ConnectionError: If RPC connection fails
        """
        instance = cls(mm_entrypoint_address, config, order_book_address, market_config)

        # Verify connection
        if not await instance.w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC at {config.rpc_url}")

        instance._connected = True
        logger.info(f"Connected to RPC at {config.rpc_url}")
        logger.info(
            f"OrdersExecutor initialized for MM Entrypoint contract {instance.contract_address}"
        )
        logger.info(f"User address: {instance.user_address}")

        return instance

    # _send_transaction is inherited from AsyncTransactionSenderMixin

    async def place_order(
        self,
        buy_cloids: list[bytes],
        sell_cloids: list[bytes],
        cancel_cloids: list[bytes],
        buy_prices: list[float],
        buy_sizes: list[float],
        sell_prices: list[float],
        sell_sizes: list[float],
        order_ids_to_cancel: list[int],
        orders_to_cancel_metadata: list[tuple[int, int, bool]],
        post_only: bool,
        price_rounding: Optional[str] = "default",
    ) -> str:
        """
        Place a batch order using the MM Entrypoint contract's batchUpdateMM function
        with EIP-2930 access list optimization.

        This function allows creating buy orders, sell orders, and canceling existing orders
        in a single transaction, with gas optimization through access lists.

        Args:
            buy_cloids: List of client order IDs for buy orders (bytes32 values as bytes)
            sell_cloids: List of client order IDs for sell orders (bytes32 values as bytes)
            cancel_cloids: List of client order IDs for cancel orders (bytes32 values as bytes)
            buy_prices: List of buy order prices as floats (will be converted using price_precision)
            buy_sizes: List of buy order sizes as floats (will be converted using size_precision)
            sell_prices: List of sell order prices as floats (will be converted using price_precision)
            sell_sizes: List of sell order sizes as floats (will be converted using size_precision)
            order_ids_to_cancel: List of order IDs to cancel (uint40)
            orders_to_cancel_metadata: List of (order_id, price, is_buy) tuples for access list
            post_only: Whether orders should be post-only

        Returns:
            Transaction hash as hex string

        Raises:
            ValueError: If input arrays are invalid or mismatched

        Note:
            Float values are converted to integers by multiplying with precision and truncating.
            Example: price=0.5 with price_precision=10000000 becomes int(5000000.0) = 5000000
        """
        # Validate input arrays
        if len(buy_cloids) != len(buy_prices):
            raise ValueError(
                f"Buy cloids and buy_prices arrays must have same length: "
                f"{len(buy_cloids)} != {len(buy_prices)}"
            )

        if len(buy_cloids) != len(buy_sizes):
            raise ValueError(
                f"Buy cloids and buy_sizes arrays must have same length: "
                f"{len(buy_cloids)} != {len(buy_sizes)}"
            )

        if len(sell_cloids) != len(sell_prices):
            raise ValueError(
                f"Sell cloids and sell_prices arrays must have same length: "
                f"{len(sell_cloids)} != {len(sell_prices)}"
            )

        if len(sell_cloids) != len(sell_sizes):
            raise ValueError(
                f"Sell cloids and sell_sizes arrays must have same length: "
                f"{len(sell_cloids)} != {len(sell_sizes)}"
            )

        # Convert float prices/sizes to integers using precision multipliers
        # Using int() for truncation (round down)
        buy_prices_int = [int(price * self.market_config.price_precision) for price in buy_prices]
        buy_sizes_int = [int(size * self.market_config.size_precision) for size in buy_sizes]
        sell_prices_int = [int(price * self.market_config.price_precision) for price in sell_prices]
        sell_sizes_int = [int(size * self.market_config.size_precision) for size in sell_sizes]

        # Apply tick-size rounding to prices
        tick_size = self.market_config.tick_size
        if price_rounding != "none" and tick_size > 1:
            if price_rounding == "default":
                buy_prices_int = [round_price_down(p, tick_size) for p in buy_prices_int]
                sell_prices_int = [round_price_up(p, tick_size) for p in sell_prices_int]
            elif price_rounding == "down":
                buy_prices_int = [round_price_down(p, tick_size) for p in buy_prices_int]
                sell_prices_int = [round_price_down(p, tick_size) for p in sell_prices_int]
            elif price_rounding == "up":
                buy_prices_int = [round_price_up(p, tick_size) for p in buy_prices_int]
                sell_prices_int = [round_price_up(p, tick_size) for p in sell_prices_int]

        # Log the operation
        logger.info(
            f"Placing batch order: {len(buy_cloids)} buy CLOIDs, "
            f"{len(sell_cloids)} sell CLOIDs, {len(cancel_cloids)} cancel CLOIDs, "
            f"{len(buy_prices)} buys, {len(sell_prices)} sells, "
            f"{len(order_ids_to_cancel)} cancels, post_only={post_only}"
        )

        logger.debug(f"Buy cloids: {buy_cloids}")
        logger.debug(f"Sell cloids: {sell_cloids}")
        logger.debug(f"Cancel cloids: {cancel_cloids}")
        logger.debug(f"Buy prices: {buy_prices_int}")
        logger.debug(f"Buy sizes: {buy_sizes_int}")
        logger.debug(f"Sell prices: {sell_prices_int}")
        logger.debug(f"Sell sizes: {sell_sizes_int}")
        logger.debug(f"Order ids to cancel: {order_ids_to_cancel}")
        logger.debug(f"Post only: {post_only}")


        # Call batchUpdateMM function
        function_call = self.contract.functions.batchUpdateMM(
            self.order_book_address,
            buy_cloids,
            sell_cloids,
            cancel_cloids,
            buy_prices_int,
            buy_sizes_int,
            sell_prices_int,
            sell_sizes_int,
            order_ids_to_cancel,
            post_only,
        )

        # Build access list if we have any operations
        access_list = None
        if buy_prices or sell_prices or order_ids_to_cancel:
            from src.transaction.access_list import build_access_list_for_cancel_and_place

            # Prepare buy/sell orders as (price, size) tuples
            buy_orders_for_access_list = list(zip(buy_prices_int, buy_sizes_int))
            sell_orders_for_access_list = list(zip(sell_prices_int, sell_sizes_int))

            access_list = build_access_list_for_cancel_and_place(
                user_address=self.user_address,
                orderbook_address=self.order_book_address,
                margin_account_address=self.market_config.margin_contract_address,
                base_token_address=self.market_config.base_token,
                quote_token_address=self.market_config.quote_token,
                orderbook_implementation=self.market_config.orderbook_implementation,
                margin_account_implementation=self.market_config.margin_account_implementation,
                orders_to_cancel=orders_to_cancel_metadata,
                buy_orders=buy_orders_for_access_list,
                sell_orders=sell_orders_for_access_list,
            )
            logger.debug(
                f"Built access list for place_order: "
                f"{len(orders_to_cancel_metadata)} cancels, "
                f"{len(buy_prices)} buys, {len(sell_prices)} sells"
            )

        txhash = await self._send_transaction(function_call, access_list=access_list)

        return txhash

    async def cancel_orders_with_kuru_order_ids(
        self, kuru_order_ids: list[int] | list[tuple[int, int, bool]]
    ) -> str:
        """
        Cancel orders with Kuru order IDs using batchCancelOrders.
        Automatically builds and uses EIP-2930 access list when order metadata is provided.

        Args:
            kuru_order_ids: Either:
                - list[int]: Just order IDs (backward compatible, no access list)
                - list[tuple[int, int, bool]]: Tuples of (order_id, price, is_buy) for access list

        Returns:
            Transaction hash as hex string
        """
        logger.success(f"Using account: {self.account.address}")

        # Determine if we have metadata for access list
        has_metadata = len(kuru_order_ids) > 0 and isinstance(kuru_order_ids[0], tuple)

        # Extract just the order IDs for the contract call
        if has_metadata:
            order_ids = [order_id for order_id, price, is_buy in kuru_order_ids]
            orders_to_cancel_metadata = kuru_order_ids  # Full tuples
        else:
            order_ids = kuru_order_ids  # Already just IDs
            orders_to_cancel_metadata = []

        # Build contract function call
        function_call = self.orderbook_contract.functions.batchCancelOrders(order_ids)

        # Build access list if we have metadata
        access_list = None
        if has_metadata:

            access_list = build_access_list_for_cancel_only(
                user_address=self.user_address,
                orderbook_address=self.order_book_address,
                margin_account_address=self.market_config.margin_contract_address,
                base_token_address=self.market_config.base_token,
                quote_token_address=self.market_config.quote_token,
                orderbook_implementation=self.market_config.orderbook_implementation,
                margin_account_implementation=self.market_config.margin_account_implementation,
                orders_to_cancel=orders_to_cancel_metadata,
            )
            logger.debug(f"Built access list for {len(order_ids)} order cancellations")
        else:
            logger.warning(
                "Canceling orders without access list - consider passing order metadata "
                "(order_id, price, is_buy) tuples for gas savings"
            )

        logger.success(f"Sending transaction with access list: {access_list}")

        # Send transaction (with or without access list)
        txhash = await self._send_transaction(function_call, access_list=access_list)

        # Wait for confirmation
        await self._wait_for_transaction_receipt(txhash)

        return txhash

    async def place_market_buy(
        self,
        quote_amount: float,
        min_amount_out: float,
        is_margin: bool = True,
        is_fill_or_kill: bool = False,
    ) -> str:
        """
        Place a market buy order using the orderbook contract's placeAndExecuteMarketBuy function.

        This function buys base tokens by spending quote tokens at the best available market price.
        The order executes immediately.

        Args:
            quote_amount: Amount of quote tokens to spend as float (will be converted using quote_token_decimals)
            min_amount_out: Minimum base tokens to receive as float (will be converted using base_token_decimals)
            is_margin: Whether to use margin account (default: True)
            is_fill_or_kill: Execute fully or revert (default: False)

        Returns:
            Transaction hash as hex string

        Note:
            Float values are converted to integers:
            - quote_amount uses 10^quote_token_decimals
            - min_amount_out uses 10^base_token_decimals
        """
        # Convert float amounts to integers using token decimals
        quote_amount_int = int(quote_amount * (10 ** self.market_config.quote_token_decimals))
        min_amount_out_int = int(min_amount_out * (10 ** self.market_config.base_token_decimals))

        # Log the operation
        logger.info(
            f"Placing market buy order: quote_amount={quote_amount} ({quote_amount_int}), "
            f"min_amount_out={min_amount_out} ({min_amount_out_int}), "
            f"is_margin={is_margin}, is_fill_or_kill={is_fill_or_kill}"
        )

        # Call placeAndExecuteMarketBuy function
        function_call = self.orderbook_contract.functions.placeAndExecuteMarketBuy(
            quote_amount_int,
            min_amount_out_int,
            is_margin,
            is_fill_or_kill,
        )

        txhash = await self._send_transaction(function_call)

        return txhash

    async def place_market_sell(
        self,
        size: float,
        min_amount_out: float,
        is_margin: bool = True,
        is_fill_or_kill: bool = False,
    ) -> str:
        """
        Place a market sell order using the orderbook contract's placeAndExecuteMarketSell function.

        This function sells base tokens to receive quote tokens at the best available market price.
        The order executes immediately.

        Args:
            size: Amount of base tokens to sell as float (will be converted using size_precision)
            min_amount_out: Minimum quote tokens to receive as float (will be converted using quote_token_decimals)
            is_margin: Whether to use margin account (default: True)
            is_fill_or_kill: Execute fully or revert (default: False)

        Returns:
            Transaction hash as hex string

        Note:
            Float values are converted to integers:
            - size uses size_precision
            - min_amount_out uses 10^quote_token_decimals
        """
        # Convert float amounts to integers
        size_int = int(size * self.market_config.size_precision)
        min_amount_out_int = int(min_amount_out * (10 ** self.market_config.quote_token_decimals))

        # Log the operation
        logger.info(
            f"Placing market sell order: size={size} ({size_int}), "
            f"min_amount_out={min_amount_out} ({min_amount_out_int}), "
            f"is_margin={is_margin}, is_fill_or_kill={is_fill_or_kill}"
        )

        # Call placeAndExecuteMarketSell function
        function_call = self.orderbook_contract.functions.placeAndExecuteMarketSell(
            size_int,
            min_amount_out_int,
            is_margin,
            is_fill_or_kill,
        )

        txhash = await self._send_transaction(function_call)

        return txhash

    async def close(self) -> None:
        """Close the HTTP provider session."""
        try:
            if hasattr(self.w3.provider, '_session') and self.w3.provider._session:
                await self.w3.provider._session.close()
                logger.debug("OrdersExecutor HTTP provider session closed")
        except Exception as e:
            logger.debug(f"Error closing OrdersExecutor HTTP provider session: {e}")
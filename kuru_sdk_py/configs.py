from dataclasses import dataclass
from typing import Optional
from loguru import logger
from web3 import Web3, Account

from kuru_sdk_py.utils import load_abi, is_native_token
from kuru_sdk_py.utils.validation import validate_ethereum_address, validate_private_key
from kuru_sdk_py.config_defaults import (
    DEFAULT_MM_ENTRYPOINT_ADDRESS,
    DEFAULT_MARGIN_CONTRACT_ADDRESS,
    DEFAULT_ORDERBOOK_IMPLEMENTATION,
    DEFAULT_MARGIN_ACCOUNT_IMPLEMENTATION,
    DEFAULT_RPC_URL,
    DEFAULT_RPC_WS_URL,
    DEFAULT_KURU_WS_URL,
    DEFAULT_KURU_API_URL,
    DEFAULT_EXCHANGE_WS_URL,
    DEFAULT_TRANSACTION_TIMEOUT,
    DEFAULT_POLL_LATENCY,
    DEFAULT_GAS_ADJUSTMENT_PER_SLOT,
    DEFAULT_GAS_BUFFER_MULTIPLIER,
    DEFAULT_GAS_BUFFER,
    DEFAULT_MAX_RECONNECT_ATTEMPTS,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_HEARTBEAT_TIMEOUT,
    DEFAULT_RPC_LOGS_SUBSCRIPTION,
    DEFAULT_RPC_WS_MAX_RECONNECT_ATTEMPTS,
    DEFAULT_RPC_WS_RECONNECT_DELAY,
    DEFAULT_RPC_WS_MAX_RECONNECT_DELAY,
    DEFAULT_GAP_RECOVERY_BLOCK_BUFFER,
    DEFAULT_GAP_RECOVERY_MAX_BLOCK_RANGE,
    DEFAULT_POST_ONLY,
    DEFAULT_AUTO_APPROVE,
    DEFAULT_USE_ACCESS_LIST,
    DEFAULT_EXCHANGE_MARKET_DEPTH,
    DEFAULT_PENDING_TX_TTL,
    DEFAULT_TRADE_EVENTS_TTL,
    DEFAULT_CACHE_CHECK_INTERVAL,
    DEFAULT_RECONCILIATION_INTERVAL,
    DEFAULT_RECONCILIATION_THRESHOLD,
)

# ============================================================================
# NEW CONFIG CLASSES (Refactored - Tier 1: Essential)
# ============================================================================

@dataclass
class ConnectionConfig:
    """
    Connection and API endpoint configuration.

    This config contains only connection URLs (no sensitive data like private keys).
    Safe to pass around and log.

    Attributes:
        rpc_url: HTTP RPC endpoint for blockchain interactions
        rpc_ws_url: WebSocket RPC endpoint for real-time blockchain events
        kuru_ws_url: Kuru WebSocket API for orderbook streaming
        kuru_api_url: Kuru REST API for market data
        exchange_ws_url: Exchange WebSocket for orderbook streaming (Binance-compatible format)
    """
    rpc_url: str = DEFAULT_RPC_URL
    rpc_ws_url: str = DEFAULT_RPC_WS_URL
    kuru_ws_url: str = DEFAULT_KURU_WS_URL
    kuru_api_url: str = DEFAULT_KURU_API_URL
    exchange_ws_url: str = DEFAULT_EXCHANGE_WS_URL


@dataclass
class WalletConfig:
    """
    Wallet configuration with private key and derived address.

    This config contains sensitive data (private key) and should be handled
    carefully. Never log or expose this config.

    Attributes:
        private_key: Private key for signing transactions (required)
        user_address: Ethereum address derived from private key
    """
    private_key: str
    user_address: Optional[str] = None

    def __post_init__(self):
        """Validate private key and derive user address"""
        # Validate private key format
        self.private_key = validate_private_key(self.private_key)

        # Derive user address if not provided
        if not self.user_address:
            account = Account.from_key(self.private_key)
            self.user_address = account.address

        # Checksum user address
        self.user_address = Web3.to_checksum_address(self.user_address)


@dataclass
class TransactionConfig:
    """
    Transaction confirmation and gas configuration.

    Controls how transactions are sent, confirmed, and gas is calculated.

    Attributes:
        timeout: Seconds to wait for transaction confirmation
        poll_latency: Seconds to wait after confirmation for RPC sync
        gas_adjustment_per_slot: Gas to subtract per access list storage slot
        gas_buffer_multiplier: Safety buffer multiplier for gas estimates (e.g., 1.1 = 10% extra)
        gas_buffer: Fixed gas buffer added after access-list slot subtraction to prevent negative gas
    """
    timeout: int = DEFAULT_TRANSACTION_TIMEOUT
    poll_latency: float = DEFAULT_POLL_LATENCY
    gas_adjustment_per_slot: int = DEFAULT_GAS_ADJUSTMENT_PER_SLOT
    gas_buffer_multiplier: float = DEFAULT_GAS_BUFFER_MULTIPLIER
    gas_buffer: int = DEFAULT_GAS_BUFFER


@dataclass
class WebSocketConfig:
    """
    WebSocket connection behavior configuration.

    Controls reconnection, heartbeat, and connection parameters.

    Attributes:
        max_reconnect_attempts: Maximum reconnection attempts for orderbook/exchange WS (0=unlimited)
        reconnect_delay: Base delay for exponential backoff reconnection (seconds)
        heartbeat_interval: Seconds between ping messages
        heartbeat_timeout: Seconds to wait for pong response
        rpc_logs_subscription: RPC `eth_subscribe` type for log streaming
        rpc_ws_max_reconnect_attempts: Maximum reconnection attempts for RPC WS (0=unlimited)
        rpc_ws_reconnect_delay: Base delay for RPC WS exponential backoff (seconds)
        rpc_ws_max_reconnect_delay: Maximum delay cap for RPC WS backoff (seconds)
        gap_recovery_block_buffer: Extra blocks to look back during gap recovery
        gap_recovery_max_block_range: Maximum blocks per eth_getLogs call in gap recovery
    """
    max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS
    reconnect_delay: float = DEFAULT_RECONNECT_DELAY
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL
    heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT
    exchange_market_depth: str = DEFAULT_EXCHANGE_MARKET_DEPTH
    rpc_logs_subscription: str = DEFAULT_RPC_LOGS_SUBSCRIPTION
    rpc_ws_max_reconnect_attempts: int = DEFAULT_RPC_WS_MAX_RECONNECT_ATTEMPTS
    rpc_ws_reconnect_delay: float = DEFAULT_RPC_WS_RECONNECT_DELAY
    rpc_ws_max_reconnect_delay: float = DEFAULT_RPC_WS_MAX_RECONNECT_DELAY
    gap_recovery_block_buffer: int = DEFAULT_GAP_RECOVERY_BLOCK_BUFFER
    gap_recovery_max_block_range: int = DEFAULT_GAP_RECOVERY_MAX_BLOCK_RANGE


@dataclass
class OrderExecutionConfig:
    """
    Order execution behavior defaults.

    Controls default behavior for order placement and token approvals.
    These can be overridden per-method call.

    Attributes:
        post_only: Only place maker (limit) orders by default
        auto_approve: Automatically approve tokens when depositing
        use_access_list: Use EIP-2930 access lists for gas optimization
    """
    post_only: bool = DEFAULT_POST_ONLY
    auto_approve: bool = DEFAULT_AUTO_APPROVE
    use_access_list: bool = DEFAULT_USE_ACCESS_LIST


@dataclass
class CacheConfig:
    """
    Cache TTL configuration for order tracking.

    Controls cache expiration for internal caches.

    Attributes:
        pending_tx_ttl: Time before triggering timeout callback (seconds)
        trade_events_ttl: Time to cache orphaned trade events (seconds)
        check_interval: How often to check for expired cache keys (seconds)
        reconciliation_interval: How often to scan for stuck ORDER_SENT orders (seconds)
        reconciliation_threshold: Age before an ORDER_SENT order is reconciled (seconds)
    """
    pending_tx_ttl: float = DEFAULT_PENDING_TX_TTL
    trade_events_ttl: float = DEFAULT_TRADE_EVENTS_TTL
    check_interval: float = DEFAULT_CACHE_CHECK_INTERVAL
    reconciliation_interval: float = DEFAULT_RECONCILIATION_INTERVAL
    reconciliation_threshold: float = DEFAULT_RECONCILIATION_THRESHOLD


# ============================================================================
# LEGACY/EXISTING CONFIG CLASSES
# ============================================================================

@dataclass
class MarketConfig:
    """
    Market-specific configuration.

    Contains all market-specific parameters including token addresses,
    decimals, precision, and contract addresses.

    Note: This class is unchanged from the original implementation for
    backward compatibility.
    """
    market_address: str
    base_token: str
    quote_token: str
    market_symbol: str
    mm_entrypoint_address: str
    margin_contract_address: str
    base_token_decimals: int
    quote_token_decimals: int
    price_precision: int
    size_precision: int
    base_symbol: str
    quote_symbol: str
    orderbook_implementation: str  # Orderbook implementation contract address
    margin_account_implementation: str  # Margin account implementation contract address
    tick_size: int = 1  # Minimum price increment (from on-chain getMarketParams, index 6)

    def __post_init__(self):
        """Validate and normalize all Ethereum addresses"""
        # Validate and checksum all addresses
        self.market_address = validate_ethereum_address(self.market_address, "market_address")
        self.base_token = validate_ethereum_address(self.base_token, "base_token")
        self.quote_token = validate_ethereum_address(self.quote_token, "quote_token")
        self.mm_entrypoint_address = validate_ethereum_address(self.mm_entrypoint_address, "mm_entrypoint_address")
        self.margin_contract_address = validate_ethereum_address(self.margin_contract_address, "margin_contract_address")
        self.orderbook_implementation = validate_ethereum_address(self.orderbook_implementation, "orderbook_implementation")
        self.margin_account_implementation = validate_ethereum_address(self.margin_account_implementation, "margin_account_implementation")


@dataclass
class KuruMMConfig:
    """
    Legacy config class combining connection and wallet configuration.

    DEPRECATED: This class mixes connection settings with sensitive private key data.
    For new code, use ConnectionConfig + WalletConfig instead.

    This class is maintained for backward compatibility only.
    """
    rpc_url: str
    rpc_ws_url: str
    kuru_ws_url: str
    kuru_api_url: str
    private_key: str
    user_address: str


# Example market configs
# Note: For production use, it's recommended to use ConfigManager.load_market_config()
# with fetch_from_chain=True to automatically fetch current market parameters

# MON_AUSD_MARKET - commented out due to placeholder addresses
# Use ConfigManager.load_market_config(market_address="0x...", fetch_from_chain=True) instead
# MON_AUSD_MARKET = MarketConfig(
#     market_address="0x...",
#     base_token="0x...",
#     quote_token="0x...",
#     market_symbol="MON-AUSD",
#     mm_entrypoint_address="0xA9d8269ad1Bd6e2a02BD8996a338Dc5C16aef440",
#     margin_contract_address="0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5",
#     base_token_decimals=18,
#     quote_token_decimals=18,
#     price_precision=6,
#     size_precision=6,
#     base_symbol="MON",
#     quote_symbol="AUSD",
#     orderbook_implementation="0xea2Cc8769Fb04Ff1893Ed11cf517b7F040C823CD",
#     margin_account_implementation="0x57cF97FE1FAC7D78B07e7e0761410cb2e91F0ca7",
# )

MON_USDC_MARKET = MarketConfig(
    market_address="0x065C9d28E428A0db40191a54d33d5b7c71a9C394",
    base_token="0x0000000000000000000000000000000000000000",
    quote_token="0x754704Bc059F8C67012fEd69BC8A327a5aafb603",
    market_symbol="MON-USDC",
    mm_entrypoint_address=DEFAULT_MM_ENTRYPOINT_ADDRESS,
    margin_contract_address=DEFAULT_MARGIN_CONTRACT_ADDRESS,
    base_token_decimals=18,
    quote_token_decimals=6,
    price_precision=100000000,
    size_precision=10000000000,
    base_symbol="MON",
    quote_symbol="USDC",
    orderbook_implementation=DEFAULT_ORDERBOOK_IMPLEMENTATION,
    margin_account_implementation=DEFAULT_MARGIN_ACCOUNT_IMPLEMENTATION,
    tick_size=100,
)

# WBTC_AUSD_MARKET - commented out due to placeholder addresses
# Use ConfigManager.load_market_config(market_address="0xed1448a8f1859970B2C96D184938690353E88330", fetch_from_chain=True) instead
# WBTC_AUSD_MARKET = MarketConfig(
#     market_address="0xed1448a8f1859970B2C96D184938690353E88330",
#     base_token="0x...",
#     quote_token="0x...",
#     market_symbol="BTC-AUSD",
#     mm_entrypoint_address="0xA9d8269ad1Bd6e2a02BD8996a338Dc5C16aef440",
#     margin_contract_address="0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5",
#     base_token_decimals=8,
#     quote_token_decimals=6,
#     price_precision=2,
#     size_precision=8,
#     base_symbol="BTC",
#     quote_symbol="USDC",
#     orderbook_implementation="0xea2Cc8769Fb04Ff1893Ed11cf517b7F040C823CD",
#     margin_account_implementation="0x57cF97FE1FAC7D78B07e7e0761410cb2e91F0ca7",
# )

MARKETS = {
    # "MON-AUSD": MON_AUSD_MARKET,  # Commented out - use ConfigManager instead
    "MON-USDC": MON_USDC_MARKET,
    # "BTC-AUSD": WBTC_AUSD_MARKET,  # Commented out - use ConfigManager instead
}


KuruTopicsSignature = {
    # Orderbook events
    "OrderCreated": "OrderCreated(uint40,address,uint96,uint32,bool)",
    "OrdersCanceled": "OrdersCanceled(uint40[],address)",
    "OrderCanceled": "OrderCanceled(uint40,address,uint32,uint96,bool)",
    "Trade": "Trade(uint40,address,bool,uint256,uint96,address,address,uint96)",
    # MM Entrypoint events
    "batchUpdate": "batchUpdate(bytes32[],bytes32[],bytes32[])",
}


# ============================================================================
# CONFIG MANAGER - Flexible Config Loading with Layered Overrides
# ============================================================================

class ConfigManager:
    """
    Manages configuration creation with validation and environment variable support.

    Provides a clean interface for loading all config types with layered override system:
    1. Defaults from config_defaults.py
    2. Environment variables (if auto_env=True)
    3. Explicit function arguments (highest priority)

    Example:
        # Load wallet from env var
        wallet = ConfigManager.load_wallet_config()

        # Load connection with custom RPC
        connection = ConfigManager.load_connection_config(
            rpc_url="https://my-rpc.com"
        )

        # Load market from chain
        market = ConfigManager.load_market_config(
            market_address="0x...",
            fetch_from_chain=True
        )
    """

    # ========================================================================
    # ESSENTIAL CONFIGS (Tier 1)
    # ========================================================================

    @staticmethod
    def load_wallet_config(
        private_key: Optional[str] = None,
        auto_env: bool = True,
    ) -> WalletConfig:
        """
        Load wallet config with validation.

        Priority: explicit arg > environment var

        Args:
            private_key: Private key for signing transactions (required)
            auto_env: Automatically load from PRIVATE_KEY env var if not provided

        Returns:
            Validated WalletConfig instance

        Raises:
            ValueError: If private_key is not provided and not found in environment

        Example:
            # From environment variable
            wallet = ConfigManager.load_wallet_config()

            # Explicit private key
            wallet = ConfigManager.load_wallet_config(
                private_key="0x...",
                auto_env=False
            )
        """
        import os
        from kuru_sdk_py.config_defaults import ENV_PRIVATE_KEY

        # Load from environment if auto_env enabled
        if auto_env and private_key is None:
            private_key = os.getenv(ENV_PRIVATE_KEY)

        # Validate required field
        if not private_key:
            raise ValueError(
                "private_key is required. "
                f"Provide it as argument or set {ENV_PRIVATE_KEY} environment variable."
            )

        return WalletConfig(private_key=private_key)

    @staticmethod
    def load_connection_config(
        rpc_url: Optional[str] = None,
        rpc_ws_url: Optional[str] = None,
        kuru_ws_url: Optional[str] = None,
        kuru_api_url: Optional[str] = None,
        exchange_ws_url: Optional[str] = None,
        auto_env: bool = True,
    ) -> ConnectionConfig:
        """
        Load connection config with defaults.

        Priority: explicit args > environment vars > defaults

        Args:
            rpc_url: HTTP RPC endpoint URL
            rpc_ws_url: WebSocket RPC endpoint URL
            kuru_ws_url: Kuru WebSocket API URL
            kuru_api_url: Kuru REST API URL
            exchange_ws_url: Exchange WebSocket URL (Binance-compatible format)
            auto_env: Automatically load from environment variables

        Returns:
            ConnectionConfig instance

        Example:
            # All defaults
            connection = ConfigManager.load_connection_config()

            # Custom RPC
            connection = ConfigManager.load_connection_config(
                rpc_url="https://premium-rpc.com"
            )
        """
        import os
        from kuru_sdk_py.config_defaults import (
            ENV_RPC_URL, ENV_RPC_WS_URL, ENV_KURU_WS_URL, ENV_KURU_API_URL, ENV_EXCHANGE_WS_URL
        )

        # Layer 1: Defaults (from dataclass defaults)
        config_dict = {}

        # Layer 2: Environment variables
        if auto_env:
            if env_rpc := os.getenv(ENV_RPC_URL):
                config_dict["rpc_url"] = env_rpc
            if env_ws := os.getenv(ENV_RPC_WS_URL):
                config_dict["rpc_ws_url"] = env_ws
            if env_kuru_ws := os.getenv(ENV_KURU_WS_URL):
                config_dict["kuru_ws_url"] = env_kuru_ws
            if env_api := os.getenv(ENV_KURU_API_URL):
                config_dict["kuru_api_url"] = env_api
            if env_exchange_ws := os.getenv(ENV_EXCHANGE_WS_URL):
                config_dict["exchange_ws_url"] = env_exchange_ws

        # Layer 3: Explicit arguments (highest priority)
        if rpc_url is not None:
            config_dict["rpc_url"] = rpc_url
        if rpc_ws_url is not None:
            config_dict["rpc_ws_url"] = rpc_ws_url
        if kuru_ws_url is not None:
            config_dict["kuru_ws_url"] = kuru_ws_url
        if kuru_api_url is not None:
            config_dict["kuru_api_url"] = kuru_api_url
        if exchange_ws_url is not None:
            config_dict["exchange_ws_url"] = exchange_ws_url

        # Warn if using public endpoints
        final_rpc = config_dict.get("rpc_url", DEFAULT_RPC_URL)
        if final_rpc == DEFAULT_RPC_URL:
            logger.warning(
                "Using default public RPC endpoint. Consider using a custom RPC for production. "
                "You may experience rate limiting."
            )

        return ConnectionConfig(**config_dict)

    @staticmethod
    def load_market_config(
        market_address: Optional[str] = None,
        fetch_from_chain: bool = False,
        rpc_url: Optional[str] = None,
        mm_entrypoint_address: Optional[str] = None,
        margin_contract_address: Optional[str] = None,
        orderbook_implementation: Optional[str] = None,
        margin_account_implementation: Optional[str] = None,
        auto_env: bool = True,
        **kwargs
    ) -> MarketConfig:
        """
        Load market config with validation.

        Priority: explicit args > fetch from chain > env vars > defaults

        Args:
            market_address: Market contract address (required)
            fetch_from_chain: Fetch market parameters from blockchain
            rpc_url: RPC URL for chain fetching (defaults to public RPC)
            mm_entrypoint_address: MM Entrypoint contract address
            margin_contract_address: Margin Account contract address
            orderbook_implementation: Orderbook implementation address
            margin_account_implementation: Margin Account implementation address
            auto_env: Automatically load from environment variables
            **kwargs: Additional MarketConfig fields for direct construction

        Returns:
            MarketConfig instance

        Raises:
            ValueError: If market_address is not provided
            ConnectionError: If fetch_from_chain=True and RPC is unreachable

        Example:
            # Fetch from chain
            market = ConfigManager.load_market_config(
                market_address="0x065C9d28E428A0db40191a54d33d5b7c71a9C394",
                fetch_from_chain=True
            )

            # From environment
            market = ConfigManager.load_market_config(
                market_address=os.getenv("MARKET_ADDRESS"),
                fetch_from_chain=True
            )
        """
        import os
        from kuru_sdk_py.config_defaults import (
            ENV_MARKET_ADDRESS, ENV_MM_ENTRYPOINT_ADDRESS, ENV_MARGIN_CONTRACT_ADDRESS
        )

        # Load market_address from env if needed
        if auto_env and market_address is None:
            market_address = os.getenv(ENV_MARKET_ADDRESS)

        if not market_address:
            raise ValueError(
                "market_address is required. "
                f"Provide it as argument or set {ENV_MARKET_ADDRESS} environment variable."
            )

        # If fetching from chain, delegate to existing function
        if fetch_from_chain:
            return market_config_from_market_address(
                market_address=market_address,
                rpc_url=rpc_url or DEFAULT_RPC_URL,
                mm_entrypoint_address=mm_entrypoint_address or DEFAULT_MM_ENTRYPOINT_ADDRESS,
                margin_contract_address=margin_contract_address or DEFAULT_MARGIN_CONTRACT_ADDRESS,
                orderbook_implementation=orderbook_implementation or DEFAULT_ORDERBOOK_IMPLEMENTATION,
                margin_account_implementation=margin_account_implementation or DEFAULT_MARGIN_ACCOUNT_IMPLEMENTATION,
            )

        # Otherwise, construct directly from provided kwargs
        # (requires all MarketConfig fields)
        if not kwargs:
            raise ValueError(
                "Either set fetch_from_chain=True or provide all MarketConfig fields"
            )

        # Merge in any overrides
        config_dict = {"market_address": market_address}
        if mm_entrypoint_address:
            config_dict["mm_entrypoint_address"] = mm_entrypoint_address
        if margin_contract_address:
            config_dict["margin_contract_address"] = margin_contract_address
        if orderbook_implementation:
            config_dict["orderbook_implementation"] = orderbook_implementation
        if margin_account_implementation:
            config_dict["margin_account_implementation"] = margin_account_implementation

        config_dict.update(kwargs)

        return MarketConfig(**config_dict)

    @staticmethod
    def load_transaction_config(
        timeout: Optional[int] = None,
        poll_latency: Optional[float] = None,
        gas_adjustment_per_slot: Optional[int] = None,
        gas_buffer_multiplier: Optional[float] = None,
        gas_buffer: Optional[int] = None,
        auto_env: bool = True,
    ) -> TransactionConfig:
        """
        Load transaction config with defaults.

        Priority: explicit args > environment vars > defaults

        Args:
            timeout: Seconds to wait for transaction confirmation
            poll_latency: Seconds to wait after confirmation for RPC sync
            gas_adjustment_per_slot: Gas to subtract per access list slot
            gas_buffer_multiplier: Safety buffer multiplier for gas estimates
            gas_buffer: Fixed gas buffer added after access-list slot subtraction
            auto_env: Automatically load from environment variables

        Returns:
            TransactionConfig instance

        Example:
            # Slow network configuration
            tx_config = ConfigManager.load_transaction_config(
                timeout=300,  # 5 minutes
                poll_latency=1.0  # 1 second RPC sync
            )
        """
        import os
        from kuru_sdk_py.config_defaults import (
            ENV_TRANSACTION_TIMEOUT, ENV_POLL_LATENCY,
            ENV_GAS_ADJUSTMENT_PER_SLOT, ENV_GAS_BUFFER_MULTIPLIER,
            ENV_GAS_BUFFER,
        )

        config_dict = {}

        # Load from environment
        if auto_env:
            if env_timeout := os.getenv(ENV_TRANSACTION_TIMEOUT):
                config_dict["timeout"] = int(env_timeout)
            if env_latency := os.getenv(ENV_POLL_LATENCY):
                config_dict["poll_latency"] = float(env_latency)
            if env_gas_adj := os.getenv(ENV_GAS_ADJUSTMENT_PER_SLOT):
                config_dict["gas_adjustment_per_slot"] = int(env_gas_adj)
            if env_gas_buf := os.getenv(ENV_GAS_BUFFER_MULTIPLIER):
                config_dict["gas_buffer_multiplier"] = float(env_gas_buf)
            if env_gas_buf_fixed := os.getenv(ENV_GAS_BUFFER):
                config_dict["gas_buffer"] = int(env_gas_buf_fixed)

        # Explicit arguments override
        if timeout is not None:
            config_dict["timeout"] = timeout
        if poll_latency is not None:
            config_dict["poll_latency"] = poll_latency
        if gas_adjustment_per_slot is not None:
            config_dict["gas_adjustment_per_slot"] = gas_adjustment_per_slot
        if gas_buffer_multiplier is not None:
            config_dict["gas_buffer_multiplier"] = gas_buffer_multiplier
        if gas_buffer is not None:
            config_dict["gas_buffer"] = gas_buffer

        return TransactionConfig(**config_dict)

    @staticmethod
    def load_websocket_config(
        max_reconnect_attempts: Optional[int] = None,
        reconnect_delay: Optional[float] = None,
        heartbeat_interval: Optional[float] = None,
        heartbeat_timeout: Optional[float] = None,
        exchange_market_depth: Optional[str] = None,
        rpc_logs_subscription: Optional[str] = None,
        rpc_ws_max_reconnect_attempts: Optional[int] = None,
        rpc_ws_reconnect_delay: Optional[float] = None,
        rpc_ws_max_reconnect_delay: Optional[float] = None,
        gap_recovery_block_buffer: Optional[int] = None,
        gap_recovery_max_block_range: Optional[int] = None,
        auto_env: bool = True,
    ) -> WebSocketConfig:
        """
        Load WebSocket config with defaults.

        Priority: explicit args > environment vars > defaults

        Args:
            max_reconnect_attempts: Maximum reconnection attempts for orderbook/exchange WS
            reconnect_delay: Base delay for exponential backoff (seconds)
            heartbeat_interval: Seconds between ping messages
            heartbeat_timeout: Seconds to wait for pong response
            rpc_logs_subscription: RPC logs subscription type (e.g., "logs", "monadLogs")
            rpc_ws_max_reconnect_attempts: Max reconnection attempts for RPC WS (0=unlimited)
            rpc_ws_reconnect_delay: Base delay for RPC WS backoff (seconds)
            rpc_ws_max_reconnect_delay: Max delay cap for RPC WS backoff (seconds)
            gap_recovery_block_buffer: Extra blocks to look back during gap recovery
            gap_recovery_max_block_range: Max blocks per eth_getLogs call in gap recovery
            auto_env: Automatically load from environment variables

        Returns:
            WebSocketConfig instance

        Example:
            # Aggressive reconnection
            ws_config = ConfigManager.load_websocket_config(
                max_reconnect_attempts=3,
                reconnect_delay=0.5
            )
        """
        import os
        from kuru_sdk_py.config_defaults import (
            ENV_MAX_RECONNECT_ATTEMPTS, ENV_RECONNECT_DELAY,
            ENV_HEARTBEAT_INTERVAL, ENV_HEARTBEAT_TIMEOUT,
            ENV_EXCHANGE_MARKET_DEPTH,
            ENV_RPC_LOGS_SUBSCRIPTION,
            ENV_RPC_WS_MAX_RECONNECT_ATTEMPTS, ENV_RPC_WS_RECONNECT_DELAY,
            ENV_RPC_WS_MAX_RECONNECT_DELAY,
            ENV_GAP_RECOVERY_BLOCK_BUFFER, ENV_GAP_RECOVERY_MAX_BLOCK_RANGE,
        )

        config_dict = {}

        # Load from environment
        if auto_env:
            if env_attempts := os.getenv(ENV_MAX_RECONNECT_ATTEMPTS):
                config_dict["max_reconnect_attempts"] = int(env_attempts)
            if env_delay := os.getenv(ENV_RECONNECT_DELAY):
                config_dict["reconnect_delay"] = float(env_delay)
            if env_interval := os.getenv(ENV_HEARTBEAT_INTERVAL):
                config_dict["heartbeat_interval"] = float(env_interval)
            if env_timeout := os.getenv(ENV_HEARTBEAT_TIMEOUT):
                config_dict["heartbeat_timeout"] = float(env_timeout)
            if env_depth := os.getenv(ENV_EXCHANGE_MARKET_DEPTH):
                config_dict["exchange_market_depth"] = env_depth
            if env_sub := os.getenv(ENV_RPC_LOGS_SUBSCRIPTION):
                env_sub = env_sub.strip()
                if not env_sub:
                    raise ValueError(
                        f"{ENV_RPC_LOGS_SUBSCRIPTION} cannot be empty when set."
                    )
                config_dict["rpc_logs_subscription"] = env_sub
            if env_rpc_attempts := os.getenv(ENV_RPC_WS_MAX_RECONNECT_ATTEMPTS):
                config_dict["rpc_ws_max_reconnect_attempts"] = int(env_rpc_attempts)
            if env_rpc_delay := os.getenv(ENV_RPC_WS_RECONNECT_DELAY):
                config_dict["rpc_ws_reconnect_delay"] = float(env_rpc_delay)
            if env_rpc_max_delay := os.getenv(ENV_RPC_WS_MAX_RECONNECT_DELAY):
                config_dict["rpc_ws_max_reconnect_delay"] = float(env_rpc_max_delay)
            if env_gap_buffer := os.getenv(ENV_GAP_RECOVERY_BLOCK_BUFFER):
                config_dict["gap_recovery_block_buffer"] = int(env_gap_buffer)
            if env_gap_range := os.getenv(ENV_GAP_RECOVERY_MAX_BLOCK_RANGE):
                config_dict["gap_recovery_max_block_range"] = int(env_gap_range)

        # Explicit arguments override
        if max_reconnect_attempts is not None:
            config_dict["max_reconnect_attempts"] = max_reconnect_attempts
        if reconnect_delay is not None:
            config_dict["reconnect_delay"] = reconnect_delay
        if heartbeat_interval is not None:
            config_dict["heartbeat_interval"] = heartbeat_interval
        if heartbeat_timeout is not None:
            config_dict["heartbeat_timeout"] = heartbeat_timeout
        if exchange_market_depth is not None:
            config_dict["exchange_market_depth"] = exchange_market_depth
        if rpc_logs_subscription is not None:
            rpc_logs_subscription = rpc_logs_subscription.strip()
            if not rpc_logs_subscription:
                raise ValueError("rpc_logs_subscription cannot be empty when provided.")
            config_dict["rpc_logs_subscription"] = rpc_logs_subscription
        if rpc_ws_max_reconnect_attempts is not None:
            config_dict["rpc_ws_max_reconnect_attempts"] = rpc_ws_max_reconnect_attempts
        if rpc_ws_reconnect_delay is not None:
            config_dict["rpc_ws_reconnect_delay"] = rpc_ws_reconnect_delay
        if rpc_ws_max_reconnect_delay is not None:
            config_dict["rpc_ws_max_reconnect_delay"] = rpc_ws_max_reconnect_delay
        if gap_recovery_block_buffer is not None:
            config_dict["gap_recovery_block_buffer"] = gap_recovery_block_buffer
        if gap_recovery_max_block_range is not None:
            config_dict["gap_recovery_max_block_range"] = gap_recovery_max_block_range

        return WebSocketConfig(**config_dict)

    @staticmethod
    def load_order_execution_config(
        post_only: Optional[bool] = None,
        auto_approve: Optional[bool] = None,
        use_access_list: Optional[bool] = None,
        auto_env: bool = True,
    ) -> OrderExecutionConfig:
        """
        Load order execution config with defaults.

        Priority: explicit args > environment vars > defaults

        Args:
            post_only: Only place maker (limit) orders
            auto_approve: Automatically approve tokens when depositing
            use_access_list: Use EIP-2930 access lists for gas optimization
            auto_env: Automatically load from environment variables

        Returns:
            OrderExecutionConfig instance

        Example:
            # Allow taker orders
            exec_config = ConfigManager.load_order_execution_config(
                post_only=False
            )
        """
        import os
        from kuru_sdk_py.config_defaults import ENV_POST_ONLY, ENV_AUTO_APPROVE, ENV_USE_ACCESS_LIST
        from kuru_sdk_py.utils.validation import validate_boolean_env

        config_dict = {}

        # Load from environment
        if auto_env:
            if env_post := os.getenv(ENV_POST_ONLY):
                config_dict["post_only"] = validate_boolean_env(env_post, "post_only")
            if env_approve := os.getenv(ENV_AUTO_APPROVE):
                config_dict["auto_approve"] = validate_boolean_env(env_approve, "auto_approve")
            if env_access := os.getenv(ENV_USE_ACCESS_LIST):
                config_dict["use_access_list"] = validate_boolean_env(env_access, "use_access_list")

        # Explicit arguments override
        if post_only is not None:
            config_dict["post_only"] = post_only
        if auto_approve is not None:
            config_dict["auto_approve"] = auto_approve
        if use_access_list is not None:
            config_dict["use_access_list"] = use_access_list

        return OrderExecutionConfig(**config_dict)

    @staticmethod
    def load_cache_config(
        pending_tx_ttl: Optional[float] = None,
        trade_events_ttl: Optional[float] = None,
        check_interval: Optional[float] = None,
        reconciliation_interval: Optional[float] = None,
        reconciliation_threshold: Optional[float] = None,
        auto_env: bool = False,  # Advanced users only
    ) -> CacheConfig:
        """
        Load cache config with defaults.

        Priority: explicit args > environment vars > defaults

        Note: auto_env defaults to False as this is for advanced users.

        Args:
            pending_tx_ttl: Time before triggering timeout callback (seconds)
            trade_events_ttl: Time to cache orphaned trade events (seconds)
            check_interval: How often to check for expired keys (seconds)
            reconciliation_interval: How often to scan for stuck ORDER_SENT orders (seconds)
            reconciliation_threshold: Age before an ORDER_SENT order is reconciled (seconds)
            auto_env: Automatically load from environment variables

        Returns:
            CacheConfig instance

        Example:
            # Faster timeout detection
            cache_config = ConfigManager.load_cache_config(
                pending_tx_ttl=3.0,
                check_interval=0.5
            )
        """
        import os
        from kuru_sdk_py.config_defaults import (
            ENV_PENDING_TX_TTL, ENV_TRADE_EVENTS_TTL, ENV_CACHE_CHECK_INTERVAL,
            ENV_RECONCILIATION_INTERVAL, ENV_RECONCILIATION_THRESHOLD,
        )

        config_dict = {}

        # Load from environment (only if enabled)
        if auto_env:
            if env_tx_ttl := os.getenv(ENV_PENDING_TX_TTL):
                config_dict["pending_tx_ttl"] = float(env_tx_ttl)
            if env_events_ttl := os.getenv(ENV_TRADE_EVENTS_TTL):
                config_dict["trade_events_ttl"] = float(env_events_ttl)
            if env_interval := os.getenv(ENV_CACHE_CHECK_INTERVAL):
                config_dict["check_interval"] = float(env_interval)
            if env_recon_interval := os.getenv(ENV_RECONCILIATION_INTERVAL):
                config_dict["reconciliation_interval"] = float(env_recon_interval)
            if env_recon_threshold := os.getenv(ENV_RECONCILIATION_THRESHOLD):
                config_dict["reconciliation_threshold"] = float(env_recon_threshold)

        # Explicit arguments override
        if pending_tx_ttl is not None:
            config_dict["pending_tx_ttl"] = pending_tx_ttl
        if trade_events_ttl is not None:
            config_dict["trade_events_ttl"] = trade_events_ttl
        if check_interval is not None:
            config_dict["check_interval"] = check_interval
        if reconciliation_interval is not None:
            config_dict["reconciliation_interval"] = reconciliation_interval
        if reconciliation_threshold is not None:
            config_dict["reconciliation_threshold"] = reconciliation_threshold

        return CacheConfig(**config_dict)

    @staticmethod
    def load_all_configs(
        market_address: Optional[str] = None,
        fetch_from_chain: bool = True,
        auto_env: bool = True,
        **overrides
    ) -> dict:
        """
        Load all configs at once for convenience.

        Returns dict with keys matching KuruClient.create() parameter names:
        - wallet_config
        - connection_config
        - market_config
        - transaction_config
        - websocket_config
        - order_execution_config
        - cache_config

        Args:
            market_address: Market contract address
            fetch_from_chain: Fetch market parameters from blockchain
            auto_env: Automatically load from environment variables
            **overrides: Override specific config parameters

        Returns:
            Dictionary of all config objects

        Example:
            configs = ConfigManager.load_all_configs(
                market_address="0x...",
                fetch_from_chain=True
            )
            client = await KuruClient.create(**configs)
        """
        return {
            "wallet_config": ConfigManager.load_wallet_config(auto_env=auto_env),
            "connection_config": ConfigManager.load_connection_config(auto_env=auto_env),
            "market_config": ConfigManager.load_market_config(
                market_address=market_address,
                fetch_from_chain=fetch_from_chain,
                auto_env=auto_env
            ),
            "transaction_config": ConfigManager.load_transaction_config(auto_env=auto_env),
            "websocket_config": ConfigManager.load_websocket_config(auto_env=auto_env),
            "order_execution_config": ConfigManager.load_order_execution_config(auto_env=auto_env),
            "cache_config": ConfigManager.load_cache_config(auto_env=False),  # Advanced only
        }


class ConfigPresets:
    """
    Pre-configured setups for common scenarios.

    These presets provide sensible defaults for different use cases
    without requiring manual configuration of every parameter.

    Example:
        preset = ConfigPresets.conservative()
        client = await KuruClient.create(
            wallet_config=wallet,
            connection_config=connection,
            market_config=market,
            **preset
        )
    """

    @staticmethod
    def conservative() -> dict:
        """
        Conservative settings: longer timeouts, more retries.

        Best for:
        - Production environments
        - Unreliable networks
        - Critical operations that must not fail

        Returns:
            Dict with transaction_config and websocket_config
        """
        return {
            "transaction_config": TransactionConfig(
                timeout=180,  # 3 minutes
                poll_latency=1.0,  # 1 second RPC sync
            ),
            "websocket_config": WebSocketConfig(
                max_reconnect_attempts=10,  # More retries
                reconnect_delay=2.0,  # Longer base delay
            ),
        }

    @staticmethod
    def aggressive() -> dict:
        """
        Aggressive settings: shorter timeouts, fewer retries.

        Best for:
        - Fast networks
        - High-frequency strategies
        - Fail-fast scenarios

        Returns:
            Dict with transaction_config and websocket_config
        """
        return {
            "transaction_config": TransactionConfig(
                timeout=60,  # 1 minute
                poll_latency=0.1,  # 100ms RPC sync
            ),
            "websocket_config": WebSocketConfig(
                max_reconnect_attempts=3,  # Give up faster
                reconnect_delay=0.5,  # Try quickly
            ),
        }

    @staticmethod
    def testnet() -> dict:
        """
        Settings optimized for testnet (slower blocks).

        Best for:
        - Testnet deployments
        - Development environments
        - Networks with slower block times

        Returns:
            Dict with transaction_config
        """
        return {
            "transaction_config": TransactionConfig(
                timeout=300,  # 5 minutes
                poll_latency=2.0,  # 2 second RPC sync
            ),
        }


# ============================================================================
# LEGACY FUNCTIONS (Backward Compatibility)
# ============================================================================

def market_config_from_market_address(
    market_address: str,
    mm_entrypoint_address: str = DEFAULT_MM_ENTRYPOINT_ADDRESS,
    margin_contract_address: str = DEFAULT_MARGIN_CONTRACT_ADDRESS,
    rpc_url: str = DEFAULT_RPC_URL,
    margin_account_implementation: str = DEFAULT_MARGIN_ACCOUNT_IMPLEMENTATION,
    orderbook_implementation: str = DEFAULT_ORDERBOOK_IMPLEMENTATION,
) -> MarketConfig:
    try:
        orderbook_abi = load_abi("orderbook")
        erc20_abi = load_abi("erc20")

        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            logger.error(f"Failed to connect to RPC at {rpc_url}")
            raise ConnectionError(f"Failed to connect to RPC at {rpc_url}")

        # Convert all addresses to checksum format
        market_address = Web3.to_checksum_address(market_address)
        mm_entrypoint_address = Web3.to_checksum_address(mm_entrypoint_address)
        margin_contract_address = Web3.to_checksum_address(margin_contract_address)
        margin_account_implementation = Web3.to_checksum_address(margin_account_implementation)
        orderbook_implementation = Web3.to_checksum_address(orderbook_implementation)

        orderbook = w3.eth.contract(address=market_address, abi=orderbook_abi)
        params = orderbook.functions.getMarketParams().call()

        price_precision = int(params[0])
        size_precision = int(params[1])
        base_token = Web3.to_checksum_address(params[2])
        base_token_decimals = int(params[3])
        quote_token = Web3.to_checksum_address(params[4])
        quote_token_decimals = int(params[5])
        tick_size = int(params[6])

        if is_native_token(base_token):
            base_symbol = "MON"
        else:
            base_contract = w3.eth.contract(address=base_token, abi=erc20_abi)
            try:
                base_symbol = base_contract.functions.symbol().call()
            except Exception as e:
                logger.warning(f"Failed to fetch base token symbol: {e}")
                base_symbol = ""

        if is_native_token(quote_token):
            quote_symbol = "MON"
        else:
            quote_contract = w3.eth.contract(address=quote_token, abi=erc20_abi)
            try:
                quote_symbol = quote_contract.functions.symbol().call()
            except Exception as e:
                logger.warning(f"Failed to fetch quote token symbol: {e}")
                quote_symbol = ""

        market_symbol = f"{base_symbol}-{quote_symbol}"

        margin_account_implementation = margin_account_implementation
        orderbook_implementation = orderbook_implementation

        logger.info(f"Successfully fetched market config for {market_symbol}")

        return MarketConfig(
            market_address=market_address,
            base_token=base_token,
            quote_token=quote_token,
            market_symbol=market_symbol,
            mm_entrypoint_address=mm_entrypoint_address,
            margin_contract_address=margin_contract_address,
            base_token_decimals=base_token_decimals,
            quote_token_decimals=quote_token_decimals,
            price_precision=price_precision,
            size_precision=size_precision,
            base_symbol=base_symbol,
            quote_symbol=quote_symbol,
            orderbook_implementation=orderbook_implementation,
            margin_account_implementation=margin_account_implementation,
            tick_size=tick_size,
        )
    except Exception as e:
        logger.error(f"Failed to fetch market config: {e}")
        raise


def initialize_kuru_mm_config(
    private_key: str,
    rpc_url: str = DEFAULT_RPC_URL,
    rpc_ws_url: str = DEFAULT_RPC_WS_URL,
    kuru_ws_url: str = DEFAULT_KURU_WS_URL,
    kuru_api_url: str = DEFAULT_KURU_API_URL,
) -> KuruMMConfig:
    if not private_key:
        raise ValueError("private_key cannot be None or empty")

    account = Account.from_key(private_key)
    user_address = account.address
    logger.success(f"User address: {user_address}")

    logger.info(f"Initializing Kuru MM config")
    logger.info(f"User address: {user_address}")

    default_rpc = DEFAULT_RPC_URL

    if rpc_url == default_rpc:
        logger.warning(
            "Using default public RPC/Websocket endpoints. You may experience rate limiting. Consider using a custom RPC for production use."
            "For more information, see TODO: docs URL"
        )

    return KuruMMConfig(
        rpc_url=rpc_url,
        rpc_ws_url=rpc_ws_url,
        kuru_ws_url=kuru_ws_url,
        kuru_api_url=kuru_api_url,
        private_key=private_key,
        user_address=user_address,
    )

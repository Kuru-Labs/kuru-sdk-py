"""
Centralized configuration defaults for Kuru MM SDK

This module contains all default values used throughout the SDK. Having all
defaults in one place makes it easy to find, update, and maintain configuration
across the entire codebase.

Usage:
    from kuru_sdk_py.config_defaults import DEFAULT_RPC_URL, DEFAULT_TRANSACTION_TIMEOUT
"""

# ============================================================================
# CONTRACT ADDRESSES (Mainnet)
# ============================================================================
# These are the default contract addresses for the Kuru protocol on mainnet.
# Users can override these via MarketConfig if deploying to a different network
# or using different contract versions.

DEFAULT_MM_ENTRYPOINT_ADDRESS = "0xd63aD20884aFA6281969b2BD21A8708Eed546fAC"
"""Default MM Entrypoint contract address for order placement"""

DEFAULT_MARGIN_CONTRACT_ADDRESS = "0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5"
"""Default Margin Account contract address for managing user balances"""

DEFAULT_ORDERBOOK_IMPLEMENTATION = "0xea2Cc8769Fb04Ff1893Ed11cf517b7F040C823CD"
"""Default Orderbook implementation contract address"""

DEFAULT_MARGIN_ACCOUNT_IMPLEMENTATION = "0x57cF97FE1FAC7D78B07e7e0761410cb2e91F0ca7"
"""Default Margin Account implementation contract address"""

# ============================================================================
# RPC AND API ENDPOINTS
# ============================================================================
# Default public endpoints for Kuru network. Users should consider using their
# own RPC endpoints for production use to avoid rate limiting and ensure
# better reliability.

DEFAULT_RPC_URL = "https://rpc.monad.xyz"
"""Default HTTP RPC endpoint for blockchain interactions"""

DEFAULT_RPC_WS_URL = "wss://rpc.monad.xyz"
"""Default WebSocket RPC endpoint for real-time blockchain events"""

DEFAULT_KURU_WS_URL = "wss://ws.kuru.io/"
"""Default Kuru WebSocket API for orderbook streaming"""

DEFAULT_KURU_API_URL = "https://api.kuru.io/"
"""Default Kuru REST API for market data and account information"""

DEFAULT_EXCHANGE_WS_URL = "wss://rpc.staging.kuru.io/kuru-rpc-ws"
"""Default Exchange WebSocket for orderbook streaming (Binance-compatible format)"""

# ============================================================================
# TRANSACTION CONFIGURATION
# ============================================================================
# These defaults work well for typical network conditions but may need
# adjustment based on:
# - Network congestion (increase timeout during high traffic)
# - RPC provider speed (adjust poll_latency based on sync delay)
# - Chain characteristics (different chains have different block times)

DEFAULT_TRANSACTION_TIMEOUT = 120
"""
Seconds to wait for transaction confirmation (default: 2 minutes)

Increase this value if:
- Network is congested and transactions take longer to mine
- Using a slower RPC provider
- Running on a testnet with slower block times

Decrease this value if:
- Using a fast RPC provider with guaranteed inclusion
- Want to fail fast and retry with higher gas
"""

DEFAULT_POLL_LATENCY = 0.4
"""
Seconds to wait after confirmation before considering transaction final

This accounts for RPC sync delays - some RPC nodes may not immediately
reflect the latest state after a transaction is confirmed. The default
of 0.4s works for most providers.

Increase if you see:
- State reads returning stale data after transactions
- Nonce conflicts due to reading old account state

Decrease if:
- Using a premium RPC provider with instant sync
- Every millisecond matters for your strategy
"""

DEFAULT_GAS_ADJUSTMENT_PER_SLOT = 6500
"""
Gas units to subtract per storage slot in access list

EIP-2930 access lists pre-warm storage slots, reducing gas cost per slot.
The default of 6500 is based on standard EVM gas costs:
- Cold SLOAD: 2100 gas
- Warm SLOAD: 100 gas
- Access list entry: ~2400 gas per slot
- Net savings: ~6500 gas per slot

Adjust this if:
- RPC provider uses different gas calculation
- Testing shows different actual savings
"""

# If you are seeing "out of gas" or "gas too low" transaction failures, increase
# this multiplier by setting the KURU_GAS_BUFFER_MULTIPLIER environment variable
# to a higher value (e.g., 1.3 or 1.5).
DEFAULT_GAS_BUFFER_MULTIPLIER = 1.2
"""
Safety buffer multiplier for gas estimates (default: 20% extra)

Gas estimates from eth_estimateGas are not always accurate due to:
- State changes between estimation and execution
- Block gas limit variations
- Estimation edge cases

The 20% buffer provides a safety margin while not overpaying significantly.

Increase if you see:
- Frequent "out of gas" or "gas too low" errors
- Transactions failing due to insufficient gas

To increase without changing code, set the environment variable:
    KURU_GAS_BUFFER_MULTIPLIER=1.5  # or 2.0 for a larger buffer

Decrease if:
- Gas costs are critical for profitability
- You have very accurate custom gas estimation
"""

# ============================================================================
# WEBSOCKET CONFIGURATION
# ============================================================================
# WebSocket connection stability parameters. These defaults provide a good
# balance between reconnection attempts and network efficiency.

DEFAULT_MAX_RECONNECT_ATTEMPTS = 5
"""
Maximum number of reconnection attempts before giving up

After 5 failed reconnection attempts (with exponential backoff), the
connection is considered permanently failed and requires manual restart.

With base delay of 1.0s and exponential backoff, total retry time is:
1s + 2s + 4s + 8s + 16s = 31 seconds

Increase if:
- Network is unreliable but eventually recovers
- Can tolerate longer downtime before alerting

Decrease if:
- Want to fail fast and alert immediately
- Have external monitoring that will restart the process
"""

DEFAULT_RECONNECT_DELAY = 1.0
"""
Base delay in seconds for exponential backoff reconnection

Actual delay = base_delay * (2 ** attempt_number) + random(0, 1)

Example delays:
- Attempt 1: 1-2 seconds
- Attempt 2: 2-3 seconds
- Attempt 3: 4-5 seconds
- Attempt 4: 8-9 seconds
- Attempt 5: 16-17 seconds

Increase if:
- Network issues tend to last longer
- Want to reduce reconnection load on server

Decrease if:
- Network blips are brief
- Every second of downtime matters
"""

DEFAULT_HEARTBEAT_INTERVAL = 30.0
"""
Seconds between ping messages to keep connection alive

WebSocket connections can be silently dropped by intermediate proxies,
load balancers, or NAT gateways if idle for too long. Periodic pings
ensure the connection stays active and detect disconnections quickly.

Increase if:
- Bandwidth is constrained
- Connection is very stable

Decrease if:
- Need faster detection of connection drops
- Aggressive intermediate proxies
"""

DEFAULT_HEARTBEAT_TIMEOUT = 10.0
"""
Seconds to wait for pong response before considering connection dead

If no pong is received within this timeout, the connection is closed
and reconnection is attempted.

Increase if:
- Network has high latency
- Occasional timeouts are acceptable

Decrease if:
- Need fast failover
- Network is low-latency
"""

DEFAULT_MAX_RECONNECT_DELAY = 60.0
"""
Maximum delay in seconds for exponential backoff reconnection

Caps the exponential backoff to prevent excessively long waits between
reconnection attempts. With base delay of 1.0s, uncapped delays would
grow unbounded (1s, 2s, 4s, 8s, 16s, 32s, 64s, ...). The cap ensures
reconnection attempts happen at least every 60 seconds.
"""

DEFAULT_WS_OPEN_TIMEOUT = 10.0
"""Connection establishment timeout in seconds"""

DEFAULT_WS_CLOSE_TIMEOUT = 10.0
"""Connection close timeout in seconds"""

DEFAULT_MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB
"""
Maximum WebSocket message size in bytes

Large orderbook snapshots or batch updates can exceed default WebSocket
message size limits. 10MB should handle even very deep orderbooks.
"""

# ============================================================================
# RPC WEBSOCKET SUBSCRIPTION DEFAULTS
# ============================================================================

DEFAULT_RPC_LOGS_SUBSCRIPTION = "monadLogs"
"""
Default RPC WebSocket logs subscription type.

Most Ethereum-compatible nodes support `eth_subscribe` with type `logs`.
Monad nodes may additionally support non-finalized streams (e.g., `monadLogs`).
"""

# ============================================================================
# RPC WEBSOCKET RECONNECTION DEFAULTS
# ============================================================================
# These control the RPC WebSocket (event stream) reconnection behavior.
# Market makers should never silently stop receiving events, so defaults
# are tuned for unlimited reconnection with gap recovery.

DEFAULT_RPC_WS_MAX_RECONNECT_ATTEMPTS = 0
"""
Maximum reconnection attempts for the RPC WebSocket (0 = unlimited).

The RPC WebSocket carries critical order lifecycle events (OrderCreated,
Trade, Cancel, BatchUpdate). Losing this connection silently would cause
order state to become inconsistent.

Set to 0 (default) for unlimited retries — the SDK will keep trying
to reconnect forever with exponential backoff. Set to a positive integer
to cap reconnection attempts.
"""

DEFAULT_RPC_WS_RECONNECT_DELAY = 1.0
"""
Base delay in seconds for RPC WebSocket exponential backoff reconnection.

Actual delay = base_delay * (2 ** attempt_number) + random(0, 1),
capped at rpc_ws_max_reconnect_delay.
"""

DEFAULT_RPC_WS_MAX_RECONNECT_DELAY = 30.0
"""
Maximum delay in seconds for RPC WebSocket reconnection backoff.

Caps the exponential backoff so reconnection attempts happen at least
every 30 seconds, even after many failures.
"""

DEFAULT_GAP_RECOVERY_BLOCK_BUFFER = 5
"""
Extra blocks to look back during gap recovery.

When the RPC WebSocket reconnects, it fetches missed events via
eth_getLogs from (last_seen_block - buffer) to current_block.
The buffer provides overlap to handle reorgs and ensure no events
are missed. Duplicates are filtered by txhash:logIndex.
"""

DEFAULT_GAP_RECOVERY_MAX_BLOCK_RANGE = 2000
"""
Maximum number of blocks per eth_getLogs call during gap recovery.

Large block ranges may exceed RPC provider limits. Gap recovery
splits the range into chunks of this size. Each chunk is fetched
and processed independently.
"""

# ============================================================================
# ORDER EXECUTION DEFAULTS
# ============================================================================
# These behavioral defaults affect how orders are placed and executed.
# Users should set these based on their trading strategy.

DEFAULT_POST_ONLY = True
"""
Only place maker (limit) orders by default

When True:
- Orders are guaranteed to be makers (add liquidity)
- Orders will be cancelled if they would match immediately
- Earn maker fees/rebates

When False:
- Orders can take liquidity (match immediately)
- May pay taker fees
- Faster execution but less fee efficient

Most market makers should keep this True. Set to False for:
- Taking opportunities in other bots' orders
- Aggressive strategies that prioritize speed over fees
"""

DEFAULT_AUTO_APPROVE = True
"""
Automatically approve tokens when depositing

When True:
- First deposit will include token approval transaction
- Seamless user experience for new users
- Two transactions required for first deposit

When False:
- Users must manually approve tokens before depositing
- More control but requires extra step
- Useful for security-conscious users

Most users should keep this True for convenience.
"""

DEFAULT_USE_ACCESS_LIST = True
"""
Use EIP-2930 access lists for gas optimization

When True:
- Builds access list before transaction submission
- Pre-warms storage slots, reducing gas cost
- ~6500 gas savings per accessed storage slot
- Adds small overhead for access list generation

When False:
- No access list optimization
- Simpler transaction flow
- May pay more gas

Keep True unless:
- Access list generation is failing
- Testing shows no benefit on your specific RPC
- Simplicity is more important than gas savings
"""

# ============================================================================
# CACHE CONFIGURATION
# ============================================================================
# TTL values for internal caches that track pending transactions and events.

DEFAULT_PENDING_TX_TTL = 15.0
"""
Time-to-live (seconds) for pending transaction cache

Pending transactions are cached to track their confirmation status.
After this TTL expires without confirmation, the timeout callback is
triggered to recover order state from the transaction receipt.

Default of 15s accounts for Monad's ~1s block time plus WebSocket event
propagation delays. Configurable via KURU_PENDING_TX_TTL env var.

Increase if:
- Network is slow and transactions take longer
- False timeout alerts are occurring

Decrease if:
- Want faster timeout detection
- Network is fast and timeouts should be rare
"""

DEFAULT_TRADE_EVENTS_TTL = 5.0
"""
Time-to-live (seconds) for orphaned trade event cache

Trade events that arrive before their corresponding order creation
events are cached temporarily. This handles race conditions in event
processing.

Should generally match PENDING_TX_TTL.
"""

DEFAULT_CACHE_CHECK_INTERVAL = 1.0
"""
How often (seconds) to check for expired cache entries

The cache background task wakes up at this interval to check for
expired entries and trigger callbacks.

Lower values = faster timeout detection but more CPU usage
Higher values = less CPU usage but slower timeout detection
"""

# ============================================================================
# INTERNAL CONSTANTS (not user-configurable)
# ============================================================================
# These are protocol-level constants that should not be changed by users.
# They are defined here for reference and internal use.

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
"""
Zero address used to represent native token (ETH) in the protocol

This is a blockchain standard and cannot be changed.
"""

WEBSOCKET_PRICE_PRECISION = 1_000_000_000_000_000_000  # 10^18
"""
Universal price precision for WebSocket price feeds

All prices from the Kuru WebSocket API are normalized to 18 decimals
regardless of the market's actual price precision. This is a protocol
constant and cannot be changed.
"""

# Storage slot indices for Orderbook contract state
# These match the Solidity contract storage layout and must not be changed
ORDER_SLOT = 50  # mapping(uint40 => Order) s_orders
BUY_PP_SLOT = 51  # mapping(uint256 => PricePoint) s_buyPricePoints
SELL_PP_SLOT = 52  # mapping(uint256 => PricePoint) s_sellPricePoints
MARGIN_BALANCES_SLOT = 1  # mapping(bytes32 => uint256) balances
BUY_TREE_BASE_SLOT = 53  # TreeMath.TreeUint32 s_buyTree (level 0)
SELL_TREE_BASE_SLOT = 57  # TreeMath.TreeUint32 s_sellTree (level 0)
VERIFIED_MARKET_SLOT = 2  # mapping(address => bool) in MarginAccount

# ============================================================================
# ENVIRONMENT VARIABLE KEYS
# ============================================================================
# Standard environment variable names for configuration.
# Users can set these in .env files or environment.

# Wallet configuration
ENV_PRIVATE_KEY = "PRIVATE_KEY"
"""Environment variable for wallet private key (required)"""

# Connection configuration
ENV_RPC_URL = "RPC_URL"
"""Environment variable for HTTP RPC endpoint"""

ENV_RPC_WS_URL = "RPC_WS_URL"
"""Environment variable for WebSocket RPC endpoint"""

ENV_KURU_WS_URL = "KURU_WS_URL"
"""Environment variable for Kuru WebSocket API endpoint"""

ENV_KURU_API_URL = "KURU_API_URL"
"""Environment variable for Kuru REST API endpoint"""

ENV_EXCHANGE_WS_URL = "EXCHANGE_WS_URL"
"""Environment variable for Exchange WebSocket endpoint"""

# Market configuration
ENV_MARKET_ADDRESS = "MARKET_ADDRESS"
"""Environment variable for market contract address"""

ENV_MM_ENTRYPOINT_ADDRESS = "MM_ENTRYPOINT_ADDRESS"
"""Environment variable for MM Entrypoint contract address"""

ENV_MARGIN_CONTRACT_ADDRESS = "MARGIN_CONTRACT_ADDRESS"
"""Environment variable for Margin Account contract address"""

# Transaction configuration
ENV_TRANSACTION_TIMEOUT = "KURU_TRANSACTION_TIMEOUT"
"""Environment variable for transaction confirmation timeout (seconds)"""

ENV_POLL_LATENCY = "KURU_POLL_LATENCY"
"""Environment variable for RPC poll latency (seconds)"""

ENV_GAS_ADJUSTMENT_PER_SLOT = "KURU_GAS_ADJUSTMENT_PER_SLOT"
"""Environment variable for gas adjustment per access list slot"""

ENV_GAS_BUFFER_MULTIPLIER = "KURU_GAS_BUFFER_MULTIPLIER"
"""Environment variable for gas estimate safety buffer multiplier"""

# WebSocket configuration
ENV_MAX_RECONNECT_ATTEMPTS = "KURU_MAX_RECONNECT_ATTEMPTS"
"""Environment variable for maximum WebSocket reconnection attempts"""

ENV_RECONNECT_DELAY = "KURU_RECONNECT_DELAY"
"""Environment variable for base reconnection delay (seconds)"""

ENV_HEARTBEAT_INTERVAL = "KURU_HEARTBEAT_INTERVAL"
"""Environment variable for WebSocket heartbeat interval (seconds)"""

ENV_HEARTBEAT_TIMEOUT = "KURU_HEARTBEAT_TIMEOUT"
"""Environment variable for WebSocket heartbeat timeout (seconds)"""

ENV_RPC_LOGS_SUBSCRIPTION = "KURU_RPC_LOGS_SUBSCRIPTION"
"""
Environment variable for RPC WebSocket logs subscription type.

Examples:
- logs (default): finalized/committed stream (node-dependent)
- monadLogs: proposed (pre-commit) stream if supported by your RPC
"""

# RPC WebSocket reconnection configuration
ENV_RPC_WS_MAX_RECONNECT_ATTEMPTS = "KURU_RPC_WS_MAX_RECONNECT_ATTEMPTS"
"""Environment variable for RPC WebSocket max reconnection attempts (0=unlimited)"""

ENV_RPC_WS_RECONNECT_DELAY = "KURU_RPC_WS_RECONNECT_DELAY"
"""Environment variable for RPC WebSocket base reconnection delay (seconds)"""

ENV_RPC_WS_MAX_RECONNECT_DELAY = "KURU_RPC_WS_MAX_RECONNECT_DELAY"
"""Environment variable for RPC WebSocket max reconnection delay (seconds)"""

ENV_GAP_RECOVERY_BLOCK_BUFFER = "KURU_GAP_RECOVERY_BLOCK_BUFFER"
"""Environment variable for gap recovery extra block lookback buffer"""

ENV_GAP_RECOVERY_MAX_BLOCK_RANGE = "KURU_GAP_RECOVERY_MAX_BLOCK_RANGE"
"""Environment variable for max blocks per eth_getLogs call in gap recovery"""

# Order execution configuration
ENV_POST_ONLY = "KURU_POST_ONLY"
"""Environment variable for post-only order flag (true/false)"""

ENV_AUTO_APPROVE = "KURU_AUTO_APPROVE"
"""Environment variable for auto-approve tokens flag (true/false)"""

ENV_USE_ACCESS_LIST = "KURU_USE_ACCESS_LIST"
"""Environment variable for EIP-2930 access list flag (true/false)"""

# Logging configuration
ENV_LOG_LEVEL = "LOG_LEVEL"
"""
Environment variable for log level (default: INFO).

Valid values: TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL

Set to DEBUG or TRACE to see more verbose output, including unknown event notices.
"""

# Cache configuration
ENV_PENDING_TX_TTL = "KURU_PENDING_TX_TTL"
"""Environment variable for pending transaction cache TTL (seconds)"""

ENV_TRADE_EVENTS_TTL = "KURU_TRADE_EVENTS_TTL"
"""Environment variable for trade events cache TTL (seconds)"""

ENV_CACHE_CHECK_INTERVAL = "KURU_CACHE_CHECK_INTERVAL"
"""Environment variable for cache expiration check interval (seconds)"""

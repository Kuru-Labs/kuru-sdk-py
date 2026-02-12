"""Constants for the Kuru DEX Hummingbot connector."""

from enum import Enum


EXCHANGE_NAME = "kuru"
CONNECTOR_NAME = "kuru"

# Default endpoints (Monad mainnet)
DEFAULT_RPC_URL = "https://rpc.fullnode.kuru.io/"
DEFAULT_RPC_WS_URL = "wss://rpc.fullnode.kuru.io/"
DEFAULT_KURU_WS_URL = "wss://ws.kuru.io/"
DEFAULT_KURU_API_URL = "https://api.kuru.io/"

# Contract addresses (Monad mainnet)
DEFAULT_MM_ENTRYPOINT_ADDRESS = "0xA9d8269ad1Bd6e2a02BD8996a338Dc5C16aef440"
DEFAULT_MARGIN_CONTRACT_ADDRESS = "0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5"
DEFAULT_ORDERBOOK_IMPLEMENTATION = "0xea2Cc8769Fb04Ff1893Ed11cf517b7F040C823CD"
DEFAULT_MARGIN_ACCOUNT_IMPLEMENTATION = "0x57cF97FE1FAC7D78B07e7e0761410cb2e91F0ca7"

# WebSocket price precision (universal across all Kuru markets)
WEBSOCKET_PRICE_PRECISION = 10**18

# Known market configs: trading_pair -> market details
# Format: "BASE-QUOTE" -> {market_address, base_token, quote_token, decimals, precisions}
KNOWN_MARKETS = {
    "MON-USDC": {
        "market_address": "0x065C9d28E428A0db40191a54d33d5b7c71a9C394",
        "base_token": "0x0000000000000000000000000000000000000000",
        "quote_token": "0x754704Bc059F8C67012fEd69BC8A327a5aafb603",
        "market_symbol": "MON-USDC",
        "base_token_decimals": 18,
        "quote_token_decimals": 6,
        "price_precision": 100000000,
        "size_precision": 10000000000,
        "base_symbol": "MON",
        "quote_symbol": "USDC",
    },
}


class KuruOrderStatus(str, Enum):
    """Maps Kuru OrderStatus values to string keys for lookup."""
    CREATED = "created"
    SENT = "sent"
    PLACED = "placed"
    PARTIALLY_FILLED = "partially_filled"
    FULLY_FILLED = "fully_filled"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

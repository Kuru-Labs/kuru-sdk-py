from kuru_sdk_py.config_defaults import (
    DEFAULT_RPC_URL,
    DEFAULT_RPC_WS_URL,
    DEFAULT_KURU_WS_URL,
    DEFAULT_KURU_API_URL,
)

EXCHANGE_NAME = "kuru"
DEFAULT_DOMAIN = "kuru"

CLIENT_ORDER_ID_PREFIX = "kuru"
MAX_ORDER_ID_LEN = 64

# No traditional REST API rate limits for on-chain DEX
RATE_LIMITS = []

# Pre-configured known markets (avoids on-chain fetch for common pairs)
KNOWN_MARKETS = {
    # MON-USDC on Monad mainnet
    "0x065C9d28E428A0db40191a54d33d5b7c71a9C394": {
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
    },
}

# Default maker/taker fee estimates (in basis points)
DEFAULT_MAKER_FEE_BPS = 0
DEFAULT_TAKER_FEE_BPS = 10

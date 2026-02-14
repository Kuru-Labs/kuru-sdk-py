import time
from typing import Optional

from pydantic import BaseModel, SecretStr, Field

from src.configs import MarketConfig, ConfigManager

from hummingbot_connector.kuru import kuru_constants as CONSTANTS


class KuruConfigMap(BaseModel):
    """Configuration for the Kuru connector (Pydantic model for Hummingbot UI)."""

    kuru_private_key: SecretStr = Field(
        ...,
        description="Wallet private key (with 0x prefix)",
    )
    kuru_market_address: str = Field(
        ...,
        description="On-chain market contract address",
    )
    kuru_rpc_url: Optional[str] = Field(
        default=None,
        description="HTTP RPC endpoint (defaults to Monad mainnet)",
    )
    kuru_rpc_ws_url: Optional[str] = Field(
        default=None,
        description="WebSocket RPC endpoint",
    )
    kuru_ws_url: Optional[str] = Field(
        default=None,
        description="Kuru orderbook WebSocket URL",
    )
    kuru_api_url: Optional[str] = Field(
        default=None,
        description="Kuru REST API URL",
    )


def get_market_config(market_address: str, rpc_url: Optional[str] = None) -> MarketConfig:
    """
    Get MarketConfig for a market address.

    Checks KNOWN_MARKETS first for cached config, falls back to
    fetching from chain for unknown addresses.

    Args:
        market_address: On-chain market contract address
        rpc_url: Optional RPC URL override for chain fetching

    Returns:
        MarketConfig instance
    """
    normalized = market_address.strip()

    # Check known markets (case-insensitive)
    for addr, config_dict in CONSTANTS.KNOWN_MARKETS.items():
        if addr.lower() == normalized.lower():
            return MarketConfig(**config_dict)

    # Unknown market - fetch from chain
    return ConfigManager.load_market_config(
        market_address=normalized,
        fetch_from_chain=True,
        rpc_url=rpc_url or CONSTANTS.DEFAULT_RPC_URL,
    )


def trading_pair_from_market_config(market_config: MarketConfig) -> str:
    """Derive Hummingbot-style trading pair (e.g. 'MON-USDC') from MarketConfig."""
    return market_config.market_symbol


def get_current_server_time() -> float:
    """Return current time. DEX has no server time drift concern."""
    return time.time()

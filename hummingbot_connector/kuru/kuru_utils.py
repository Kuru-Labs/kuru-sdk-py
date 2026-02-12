"""Configuration map and utility helpers for Kuru DEX connector."""

from typing import Optional

from pydantic import Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap, ClientFieldData

from hummingbot_connector.kuru.kuru_constants import KNOWN_MARKETS


class KuruConfigMap(BaseConnectorConfigMap):
    """Hummingbot config map for the Kuru DEX connector."""

    connector: str = Field(default="kuru", const=True, client_data=None)

    kuru_private_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your private key for Kuru DEX",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        ),
    )

    kuru_rpc_url: Optional[str] = Field(
        default=None,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter Monad RPC URL (leave blank for default)",
            is_connect_key=False,
            prompt_on_new=False,
        ),
    )

    kuru_api_url: Optional[str] = Field(
        default=None,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter Kuru API URL (leave blank for default)",
            is_connect_key=False,
            prompt_on_new=False,
        ),
    )

    class Config:
        title = "kuru"


KEYS = KuruConfigMap.construct()


def trading_pair_to_market_address(trading_pair: str) -> Optional[str]:
    """Convert a Hummingbot trading pair (e.g. 'MON-USDC') to a Kuru market address."""
    market_info = KNOWN_MARKETS.get(trading_pair)
    if market_info:
        return market_info["market_address"]
    return None


def trading_pair_to_market_info(trading_pair: str) -> Optional[dict]:
    """Return full market info dict for a trading pair, or None if unknown."""
    return KNOWN_MARKETS.get(trading_pair)


def split_trading_pair(trading_pair: str) -> tuple[str, str]:
    """Split 'BASE-QUOTE' into (base, quote)."""
    parts = trading_pair.split("-")
    return parts[0], parts[1]

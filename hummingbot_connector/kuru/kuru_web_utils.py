"""REST helpers for Kuru DEX connector."""

import aiohttp
from typing import Any, Optional

from hummingbot_connector.kuru.kuru_constants import DEFAULT_KURU_API_URL


async def get_active_orders(
    user_address: str,
    market_address: str,
    api_url: str = DEFAULT_KURU_API_URL,
) -> list[dict[str, Any]]:
    """Fetch active orders for a user on a specific market via the Kuru REST API."""
    url = f"{api_url.rstrip('/')}/api/v1/orders/active"
    params = {
        "userAddress": user_address,
        "marketAddress": market_address,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("orders", [])


async def get_market_info(
    market_address: str,
    api_url: str = DEFAULT_KURU_API_URL,
) -> Optional[dict[str, Any]]:
    """Fetch market metadata (trading rules, precisions) via the Kuru REST API."""
    url = f"{api_url.rstrip('/')}/api/v1/markets/{market_address}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.json()

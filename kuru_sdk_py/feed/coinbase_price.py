from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import aiohttp
from loguru import logger

from kuru_sdk_py.utils.decimal_utils import to_decimal


@dataclass
class CoinbasePriceResult:
    price: Decimal
    base: str
    quote: str


class CoinbasePriceFeed:
    BASE_URL = "https://api.coinbase.com/v2/prices"

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_price(self, base: str, quote: str) -> CoinbasePriceResult:
        url = f"{self.BASE_URL}/{base}-{quote}/spot"
        logger.debug(f"Fetching Coinbase price: {url}")

        session = await self._get_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ValueError(
                    f"Coinbase API returned HTTP {resp.status} for {base}-{quote}: {text}"
                )
            data = await resp.json()

        try:
            amount_str = data["data"]["amount"]
        except (KeyError, TypeError) as e:
            raise ValueError(
                f"Unexpected Coinbase response structure for {base}-{quote}: {data}"
            ) from e

        price = to_decimal(amount_str)
        logger.debug(f"Coinbase {base}-{quote} price: {price}")
        return CoinbasePriceResult(price=price, base=base, quote=quote)

    async def close(self):
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

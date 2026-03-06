from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import aiohttp
from loguru import logger

from kuru_sdk_py.utils.decimal_utils import to_decimal


@dataclass
class BinancePriceResult:
    price: Decimal
    symbol: str


class BinancePriceFeed:
    BASE_URL = "https://api.binance.com/api/v3/ticker/price"

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_price(self, symbol: str) -> BinancePriceResult:
        url = self.BASE_URL
        params = {"symbol": symbol}
        logger.debug(f"Fetching Binance price for symbol: {symbol}")

        session = await self._get_session()
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ValueError(
                    f"Binance API returned HTTP {resp.status} for {symbol}: {text}"
                )
            data = await resp.json()

        try:
            price_str = data["price"]
        except (KeyError, TypeError) as e:
            raise ValueError(
                f"Unexpected Binance response structure for {symbol}: {data}"
            ) from e

        price = to_decimal(price_str)
        logger.debug(f"Binance {symbol} price: {price}")
        return BinancePriceResult(price=price, symbol=symbol)

    async def close(self):
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

"""Tests for BinancePriceFeed."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from kuru_sdk_py.feed.binance_price import BinancePriceFeed, BinancePriceResult


def make_mock_response(status: int, json_data=None, text_data: str = ""):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=text_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def make_mock_session(response):
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


class TestBinancePriceFeed:
    @pytest.mark.asyncio
    async def test_get_price_btcusdt(self):
        """Successful response returns BinancePriceResult with correct values."""
        mock_resp = make_mock_response(
            200, json_data={"symbol": "BTCUSDT", "price": "65432.10000000"}
        )
        mock_session = make_mock_session(mock_resp)

        feed = BinancePriceFeed(session=mock_session)
        result = await feed.get_price("BTCUSDT")

        assert isinstance(result, BinancePriceResult)
        assert isinstance(result.price, Decimal)
        assert result.price == Decimal("65432.10000000")
        assert result.symbol == "BTCUSDT"

        mock_session.get.assert_called_once_with(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
        )

    @pytest.mark.asyncio
    async def test_get_price_raises_on_http_error(self):
        """Non-2xx HTTP response raises ValueError."""
        mock_resp = make_mock_response(400, text_data="Bad symbol")
        mock_session = make_mock_session(mock_resp)

        feed = BinancePriceFeed(session=mock_session)
        with pytest.raises(ValueError, match="HTTP 400"):
            await feed.get_price("INVALID")

    @pytest.mark.asyncio
    async def test_get_price_raises_on_malformed_response(self):
        """Missing price key raises ValueError."""
        mock_resp = make_mock_response(200, json_data={"symbol": "BTCUSDT"})
        mock_session = make_mock_session(mock_resp)

        feed = BinancePriceFeed(session=mock_session)
        with pytest.raises(ValueError, match="Unexpected Binance response structure"):
            await feed.get_price("BTCUSDT")

    @pytest.mark.asyncio
    async def test_get_price_raises_on_null_response(self):
        """None/unexpected JSON raises ValueError."""
        mock_resp = make_mock_response(200, json_data=None)
        mock_session = make_mock_session(mock_resp)

        feed = BinancePriceFeed(session=mock_session)
        with pytest.raises(ValueError, match="Unexpected Binance response structure"):
            await feed.get_price("BTCUSDT")

    @pytest.mark.asyncio
    async def test_context_manager_closes_session(self):
        """__aexit__ closes the session when the feed owns it."""
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()

        feed = BinancePriceFeed()
        feed._session = mock_session
        # _owns_session is True because no session was passed to __init__

        async with feed:
            pass

        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_does_not_close_external_session(self):
        """__aexit__ does not close a session it does not own."""
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()

        feed = BinancePriceFeed(session=mock_session)
        # _owns_session is False because a session was passed in

        async with feed:
            pass

        mock_session.close.assert_not_called()

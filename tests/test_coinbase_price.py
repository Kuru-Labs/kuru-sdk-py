"""Tests for CoinbasePriceFeed."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kuru_sdk_py.feed.coinbase_price import CoinbasePriceFeed, CoinbasePriceResult


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


class TestCoinbasePriceFeed:
    @pytest.mark.asyncio
    async def test_get_price_btc_usd(self):
        """Successful response returns CoinbasePriceResult with correct values."""
        mock_resp = make_mock_response(
            200, json_data={"data": {"base": "BTC", "currency": "USD", "amount": "65432.10"}}
        )
        mock_session = make_mock_session(mock_resp)

        feed = CoinbasePriceFeed(session=mock_session)
        result = await feed.get_price("BTC", "USD")

        assert isinstance(result, CoinbasePriceResult)
        assert isinstance(result.price, Decimal)
        assert result.price == Decimal("65432.10")
        assert result.base == "BTC"
        assert result.quote == "USD"

        mock_session.get.assert_called_once_with(
            "https://api.coinbase.com/v2/prices/BTC-USD/spot"
        )

    @pytest.mark.asyncio
    async def test_get_price_raises_on_http_error(self):
        """Non-2xx HTTP response raises ValueError."""
        mock_resp = make_mock_response(400, text_data="Bad Request")
        mock_session = make_mock_session(mock_resp)

        feed = CoinbasePriceFeed(session=mock_session)
        with pytest.raises(ValueError, match="HTTP 400"):
            await feed.get_price("BTC", "USD")

    @pytest.mark.asyncio
    async def test_get_price_raises_on_malformed_response(self):
        """Missing data.amount key raises ValueError."""
        mock_resp = make_mock_response(200, json_data={"data": {}})
        mock_session = make_mock_session(mock_resp)

        feed = CoinbasePriceFeed(session=mock_session)
        with pytest.raises(ValueError, match="Unexpected Coinbase response structure"):
            await feed.get_price("BTC", "USD")

    @pytest.mark.asyncio
    async def test_get_price_raises_on_missing_data_key(self):
        """Missing top-level data key raises ValueError."""
        mock_resp = make_mock_response(200, json_data={"error": "not found"})
        mock_session = make_mock_session(mock_resp)

        feed = CoinbasePriceFeed(session=mock_session)
        with pytest.raises(ValueError, match="Unexpected Coinbase response structure"):
            await feed.get_price("ETH", "USD")

    @pytest.mark.asyncio
    async def test_context_manager_closes_session(self):
        """__aexit__ closes the session when the feed owns it."""
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()

        # Feed owns the session (created internally)
        feed = CoinbasePriceFeed()
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

        feed = CoinbasePriceFeed(session=mock_session)
        # _owns_session is False because a session was passed in

        async with feed:
            pass

        mock_session.close.assert_not_called()

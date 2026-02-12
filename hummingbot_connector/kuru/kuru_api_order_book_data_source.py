"""Orderbook data source for Kuru DEX.

Connects to Kuru's WebSocket feed and emits OrderBookMessage snapshots
for Hummingbot's orderbook tracker.
"""

import asyncio
import logging
import time
from decimal import Decimal
from typing import Optional

from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource

from src.feed.orderbook_ws import (
    KuruFrontendOrderbookClient,
    FrontendOrderbookUpdate,
    WEBSOCKET_PRICE_PRECISION,
)
from hummingbot_connector.kuru.kuru_constants import DEFAULT_KURU_WS_URL

logger = logging.getLogger(__name__)


class KuruAPIOrderBookDataSource(OrderBookTrackerDataSource):
    """Feeds Hummingbot's orderbook tracker with Kuru WebSocket snapshots."""

    def __init__(
        self,
        trading_pair: str,
        market_address: str,
        size_precision: int,
        connector: ExchangePyBase,
        ws_url: str = DEFAULT_KURU_WS_URL,
    ):
        super().__init__(trading_pair)
        self._trading_pair = trading_pair
        self._market_address = market_address
        self._size_precision = size_precision
        self._connector = connector
        self._ws_url = ws_url
        self._ob_client: Optional[KuruFrontendOrderbookClient] = None
        self._update_queue: asyncio.Queue[FrontendOrderbookUpdate] = asyncio.Queue()

    async def listen_for_subscriptions(self):
        """Connect to Kuru WS and pump updates into the internal queue.

        The KuruFrontendOrderbookClient handles reconnection internally.
        """
        while True:
            try:
                self._ob_client = KuruFrontendOrderbookClient(
                    ws_url=self._ws_url,
                    market_address=self._market_address,
                    update_queue=self._update_queue,
                )
                await self._ob_client.connect()
                await self._ob_client.subscribe()
                # Block until the connection drops (client will push to queue)
                while self._ob_client.is_connected():
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    f"Error in Kuru orderbook WS for {self._trading_pair}. Reconnecting..."
                )
                await asyncio.sleep(5.0)
            finally:
                if self._ob_client is not None:
                    try:
                        await self._ob_client.close()
                    except Exception:
                        pass

    async def listen_for_order_book_diffs(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """Not used -- Kuru sends full snapshots, not diffs."""
        # Keep alive so the tracker doesn't exit
        await asyncio.sleep(float("inf"))

    async def listen_for_order_book_snapshots(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """Convert Kuru FrontendOrderbookUpdate into Hummingbot OrderBookMessage snapshots."""
        while True:
            try:
                update: FrontendOrderbookUpdate = await self._update_queue.get()
                snapshot_msg = self._convert_to_snapshot(update)
                if snapshot_msg is not None:
                    output.put_nowait(snapshot_msg)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error processing Kuru orderbook snapshot")
                await asyncio.sleep(1.0)

    def _convert_to_snapshot(self, update: FrontendOrderbookUpdate) -> Optional[OrderBookMessage]:
        """Convert a Kuru FrontendOrderbookUpdate to an OrderBookMessage snapshot."""
        bids = update.b
        asks = update.a
        if bids is None and asks is None:
            return None

        timestamp = time.time()

        bid_entries = []
        if bids:
            for raw_price, raw_size in bids:
                price = Decimal(str(raw_price)) / Decimal(str(WEBSOCKET_PRICE_PRECISION))
                size = Decimal(str(raw_size)) / Decimal(str(self._size_precision))
                bid_entries.append([float(price), float(size)])

        ask_entries = []
        if asks:
            for raw_price, raw_size in asks:
                price = Decimal(str(raw_price)) / Decimal(str(WEBSOCKET_PRICE_PRECISION))
                size = Decimal(str(raw_size)) / Decimal(str(self._size_precision))
                ask_entries.append([float(price), float(size)])

        content = {
            "trading_pair": self._trading_pair,
            "update_id": int(timestamp * 1e3),
            "bids": bid_entries,
            "asks": ask_entries,
        }

        return OrderBookMessage(
            message_type=OrderBookMessageType.SNAPSHOT,
            content=content,
            timestamp=timestamp,
        )

    async def get_new_order_book(self, trading_pair: str):
        """Return a fresh OrderBook populated from the latest snapshot."""
        from hummingbot_connector.kuru.kuru_order_book import KuruOrderBook
        return KuruOrderBook()

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        """Return the latest snapshot message (used for initial load)."""
        update = await self._update_queue.get()
        msg = self._convert_to_snapshot(update)
        if msg is None:
            return OrderBookMessage(
                message_type=OrderBookMessageType.SNAPSHOT,
                content={
                    "trading_pair": trading_pair,
                    "update_id": int(time.time() * 1e3),
                    "bids": [],
                    "asks": [],
                },
                timestamp=time.time(),
            )
        return msg

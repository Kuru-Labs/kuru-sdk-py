"""User stream data source for Kuru DEX.

The Kuru SDK handles order event processing internally:
  blockchain -> RpcWebsocket -> OrdersManager -> callback -> KuruExchange

This data source is thin -- the real event processing happens in
KuruExchange._on_kuru_order_update(). This class exists to satisfy
Hummingbot's UserStreamTracker interface.
"""

import asyncio
import logging

from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource

logger = logging.getLogger(__name__)


class KuruAPIUserStreamDataSource(UserStreamTrackerDataSource):
    """Minimal user stream data source for Kuru DEX.

    Order events are delivered via KuruClient's order callback mechanism
    directly to KuruExchange, so this data source doesn't need to manage
    its own WebSocket connection.
    """

    def __init__(self):
        super().__init__()
        self._last_recv_time: float = 0.0

    @property
    def last_recv_time(self) -> float:
        return self._last_recv_time

    async def listen_for_user_stream(self, output: asyncio.Queue):
        """Keep alive -- events arrive via SDK callback, not through this queue.

        Balance updates can be polled periodically if needed.
        """
        while True:
            try:
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                raise

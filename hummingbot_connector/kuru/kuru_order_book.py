"""Kuru DEX OrderBook subclass for Hummingbot."""

from hummingbot.core.data_type.order_book import OrderBook


class KuruOrderBook(OrderBook):
    """Thin OrderBook subclass for Kuru DEX.

    Kuru sends full orderbook snapshots via WebSocket,
    so no special diff-merging logic is needed here.
    """
    pass

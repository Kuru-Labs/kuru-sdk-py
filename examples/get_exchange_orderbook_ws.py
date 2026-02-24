"""
Example: Exchange WebSocket Orderbook Streaming

This example demonstrates how to connect to the exchange WebSocket feed
and stream real-time orderbook updates in Binance-compatible format.

The exchange WebSocket provides:
- Binary WebSocket messages (JSON serialized to bytes)
- Binance-style depth updates
- Pre-normalized Decimal prices and sizes
- Optional Monad-enhanced updates with blockchain state

IMPORTANT - Delta updates vs. full snapshots:
    Unlike the frontend orderbook WebSocket (KuruFrontendOrderbookClient), which sends
    a full L2 snapshot on connect and may re-send the full book on each update,
    the exchange WebSocket sends INCREMENTAL DELTA UPDATES only. Each DepthUpdate's
    `b` and `a` fields contain only the price levels that changed since the last update.

    A size of 0.0 means that price level was removed from the book.

    To maintain a full local orderbook you must:
        1. Seed it from a REST snapshot (or the first message if the server provides one).
        2. Apply each delta: update the size for a price level, or delete it if size is 0.0.

    Example local orderbook maintenance:

        orderbook = {"bids": {}, "asks": {}}

        while True:
            update = await update_queue.get()

            # Apply bid deltas (prices and sizes are pre-normalized floats)
            for price, size in update.b:
                if size == 0.0:
                    orderbook["bids"].pop(price, None)  # Level removed
                else:
                    orderbook["bids"][price] = size     # Level added or updated

            # Apply ask deltas
            for price, size in update.a:
                if size == 0.0:
                    orderbook["asks"].pop(price, None)  # Level removed
                else:
                    orderbook["asks"][price] = size     # Level added or updated

            best_bid = max(orderbook["bids"]) if orderbook["bids"] else None
            best_ask = min(orderbook["asks"]) if orderbook["asks"] else None

Usage:
    PYTHONPATH=. uv run python examples/get_exchange_orderbook_ws.py

Note: This example is read-only and does not require a wallet or private key.
"""

import asyncio

from kuru_sdk_py.configs import ConfigManager
from kuru_sdk_py.feed.exchange_ws import (
    DepthUpdate,
    ExchangeWebsocketClient,
    MonadDepthUpdate,
)

import dotenv
import os

dotenv.load_dotenv()


async def main():
    """Stream orderbook updates from the exchange WebSocket."""
    # Load connection configuration
    connection_config = ConfigManager.load_connection_config()

    # Load market configuration (uses MARKET_ADDRESS env var)
    market_config = ConfigManager.load_market_config(
        market_address=os.getenv("MARKET_ADDRESS"),
        fetch_from_chain=True,
    )

    # Create queue for orderbook updates
    update_queue = asyncio.Queue()

    # Error callback (optional)
    def on_error(error: Exception):
        print(f"Error: {error}")

    # Create client
    client = ExchangeWebsocketClient(
        ws_url=connection_config.exchange_ws_url,
        market_config=market_config,
        update_queue=update_queue,
        on_error=on_error,
    )

    print(f"Connecting to exchange WebSocket for market {market_config.market_address}")
    print(f"Exchange URL: {connection_config.exchange_ws_url}")
    print(f"Waiting for orderbook updates...\n")

    # Use context manager for automatic connection/cleanup
    async with client:
        print(f"Connected to exchange WebSocket")
        print(f"Streaming depth updates...\n")

        update_count = 0

        # Stream updates
        while True:
            update = await update_queue.get()
            update_count += 1

            # Detect update type
            if isinstance(update, MonadDepthUpdate):
                print(f"\nMonad Depth Update #{update_count}")
                print(f"   State: {update.state}")
                print(f"   Block: {update.blockNumber}")
                print(f"   Block ID: {update.blockId[:10]}...")
            else:
                print(f"\nDepth Update #{update_count}")

            # Print event info
            print(f"   Event time: {update.E} ms")
            print(f"   Update IDs: {update.U} - {update.u}")

            # Print orderbook if available (prices and sizes are pre-normalized Decimals)
            if update.b and update.a:
                asks = sorted(
                    update.a,
                    key=lambda x: x[0],
                    reverse=True,  # descending: highest ask first
                )
                bids = sorted(
                    update.b,
                    key=lambda x: x[0],
                )  # ascending: lowest bid first

                best_ask_price, _ = asks[-1]  # lowest ask
                best_bid_price, _ = bids[-1]  # highest bid
                spread = best_ask_price - best_bid_price
                spread_bps = (spread / best_bid_price) * 10000

                col_width = 52
                print(f"\n   {'PRICE':>16}  {'SIZE':<16}")
                print(f"   {'-' * col_width}")

                # Asks: descending (highest first)
                for p, s in asks[:5]:
                    print(f"   \033[31m{p:>16.8f}  {s:<16.4f}\033[0m")

                # Spread
                print(f"   {'-' * col_width}")
                print(f"   Spread: {spread:.8f} ({spread_bps:.2f} bps)")
                print(f"   {'-' * col_width}")

                # Bids: ascending (lowest first)
                for p, s in bids[:5]:
                    print(f"   \033[32m{p:>16.8f}  {s:<16.4f}\033[0m")

            print("\n" + "=" * 80)

            # Optional: stop after N updates (remove in production)
            # if update_count >= 10:
            #     print("\nReceived 10 updates, stopping...")
            #     break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nDisconnecting from exchange WebSocket...")
    except Exception as e:
        print(f"\nFatal error: {e}")
        raise

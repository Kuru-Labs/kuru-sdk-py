"""
Example: Exchange WebSocket Orderbook Streaming

This example demonstrates how to connect to the exchange WebSocket feed
and stream real-time orderbook updates in Binance-compatible format.

The exchange WebSocket provides:
- Binary WebSocket messages (JSON serialized to bytes)
- Binance-style depth updates
- U256 string representations for prices/sizes (scaled to 10^18)
- Optional Monad-enhanced updates with blockchain state

Usage:
    PYTHONPATH=. uv run python examples/get_exchange_orderbook_ws.py

Note: This example is read-only and does not require a wallet or private key.
"""

import asyncio

from src.configs import ConfigManager
from src.feed.exchange_ws import (
    DepthUpdate,
    ExchangeWebsocketClient,
    MonadDepthUpdate,
)


async def main():
    """Stream orderbook updates from the exchange WebSocket."""
    # Load connection configuration
    connection_config = ConfigManager.load_connection_config()

    # Market address (MON-USDC)
    market_address = "mon_usdc"

    # Create queue for orderbook updates
    update_queue = asyncio.Queue()

    # Error callback (optional)
    def on_error(error: Exception):
        print(f"❌ Error: {error}")

    # Create client
    client = ExchangeWebsocketClient(
        ws_url=connection_config.exchange_ws_url,
        market_address=market_address,
        update_queue=update_queue,
        on_error=on_error,
    )

    print(f"🔗 Connecting to exchange WebSocket for market {market_address}")
    print(f"📡 Exchange URL: {connection_config.exchange_ws_url}")
    print(f"⏳ Waiting for orderbook updates...\n")

    # Use context manager for automatic connection/cleanup
    async with client:
        print(f"✅ Connected to exchange WebSocket")
        print(f"📊 Streaming depth updates...\n")

        update_count = 0

        # Stream updates
        while True:
            update = await update_queue.get()
            update_count += 1

            # Detect update type
            if isinstance(update, MonadDepthUpdate):
                print(f"\n🔷 Monad Depth Update #{update_count}")
                print(f"   State: {update.state}")
                print(f"   Block: {update.blockNumber}")
                print(f"   Block ID: {update.blockId[:10]}...")
            else:
                print(f"\n📦 Depth Update #{update_count}")

            # Print event info
            print(f"   Event time: {update.E} ms")
            print(f"   Update IDs: {update.U} - {update.u}")

            # Print best bid/ask if available
            if update.b and update.a:
                # Convert U256 strings to floats
                best_bid_price = ExchangeWebsocketClient.format_price(update.b[0][0])
                best_bid_size = ExchangeWebsocketClient.format_size(update.b[0][1])

                best_ask_price = ExchangeWebsocketClient.format_price(update.a[0][0])
                best_ask_size = ExchangeWebsocketClient.format_size(update.a[0][1])

                spread = best_ask_price - best_bid_price
                spread_bps = (spread / best_bid_price) * 10000

                print(f"\n   📈 Best Bid: {best_bid_price:.8f} (size: {best_bid_size:.4f})")
                print(f"   📉 Best Ask: {best_ask_price:.8f} (size: {best_ask_size:.4f})")
                print(f"   💹 Spread: {spread:.8f} ({spread_bps:.2f} bps)")

                # Print depth levels
                print(f"\n   📊 Bids ({len(update.b)} levels):")
                for i, (price, size) in enumerate(update.b[:5]):  # Top 5
                    p = ExchangeWebsocketClient.format_price(price)
                    s = ExchangeWebsocketClient.format_size(size)
                    print(f"      {i+1}. {p:.8f} @ {s:.4f}")

                print(f"\n   📊 Asks ({len(update.a)} levels):")
                for i, (price, size) in enumerate(update.a[:5]):  # Top 5
                    p = ExchangeWebsocketClient.format_price(price)
                    s = ExchangeWebsocketClient.format_size(size)
                    print(f"      {i+1}. {p:.8f} @ {s:.4f}")

            print("\n" + "=" * 80)

            # Optional: stop after N updates (remove in production)
            # if update_count >= 10:
            #     print("\n✅ Received 10 updates, stopping...")
            #     break


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Disconnecting from exchange WebSocket...")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        raise

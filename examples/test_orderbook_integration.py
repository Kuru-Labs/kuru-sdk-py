"""
Test script for orderbook WebSocket integration.

This script demonstrates how to use the orderbook streaming feature
in KuruClient by subscribing to real-time orderbook updates.
"""
import sys
from pathlib import Path
import asyncio
import signal
import os
from loguru import logger
from dotenv import load_dotenv

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.client import KuruClient
from src.feed.orderbook_ws import FrontendOrderbookUpdate, KuruFrontendOrderbookClient
from src.configs import ConfigManager, MARKETS

load_dotenv()


async def orderbook_handler(update: FrontendOrderbookUpdate):
    """
    Handle incoming orderbook updates.

    Args:
        update: FrontendOrderbookUpdate containing orderbook data
    """
    # Log best bid/ask if available
    if update.b and update.a:
        best_bid = KuruFrontendOrderbookClient.format_websocket_price(update.b[0][0])
        best_ask = KuruFrontendOrderbookClient.format_websocket_price(update.a[0][0])
        spread = best_ask - best_bid
        logger.info(f"Best bid: {best_bid}, Best ask: {best_ask}, Spread: {spread}")

    # Log events if present
    if update.events:
        for event in update.events:
            event_type = event.e
            timestamp = event.ts
            logger.info(f"Event: {event_type} at timestamp {timestamp}")

            # Log additional details for trades
            if event_type == "Trade" and event.p and event.s:
                price = KuruFrontendOrderbookClient.format_websocket_price(event.p)
                logger.info(f"  Trade price: {price}")


async def main():
    """Main test function."""
    logger.info("Starting orderbook integration test...")

    # Load configurations
    market_config = MARKETS["MON-USDC"]
    connection_config = ConfigManager.load_connection_config()
    wallet_config = ConfigManager.load_wallet_config(
        private_key=os.getenv("PRIVATE_KEY"),
    )

    # Create client
    client = await KuruClient.create(
        market_config=market_config,
        connection_config=connection_config,
        wallet_config=wallet_config,
    )

    # Set orderbook callback before starting
    client.set_orderbook_callback(orderbook_handler)

    # Start client
    logger.info("Starting KuruClient...")
    await client.start()

    # Subscribe to orderbook
    logger.info("Subscribing to orderbook...")
    await client.subscribe_to_orderbook()

    # Wait for updates (run for 30 seconds)
    logger.info("Listening for orderbook updates for 30 seconds...")
    await asyncio.sleep(30)

    # Stop gracefully
    logger.info("Stopping client...")
    await client.stop()

    logger.success("Test completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())

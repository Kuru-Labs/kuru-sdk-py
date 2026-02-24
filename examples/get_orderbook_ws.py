"""
Example: WebSocket Orderbook Subscription

This example demonstrates how to:
1. Connect to Kuru's WebSocket feed for real-time orderbook updates
2. Subscribe to a specific market
3. Process and display orderbook updates
4. Handle graceful shutdown

Prerequisites:
- None! This is a read-only example that doesn't require authentication or deposits.
- Internet connection to wss://ws.kuru.io/

Configuration:
- Set MARKET_ADDRESS environment variable to override the default market
- Default market: 0x122C0D8683Cab344163fB73E28E741754257e3Fa (MON-USDC)

Usage:
    uv run python examples/get_orderbook_ws.py

    # Or with custom market:
    MARKET_ADDRESS=0x... uv run python examples/get_orderbook_ws.py
"""

import sys
from pathlib import Path
from loguru import logger
import asyncio
from decimal import Decimal
import os
from typing import List, Tuple, Optional
from datetime import datetime

# Load environment variables from .env file
import dotenv
dotenv.load_dotenv()

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))

# SDK imports
from kuru_sdk_py.feed.orderbook_ws import KuruFrontendOrderbookClient, FrontendOrderbookUpdate, FrontendEvent
from kuru_sdk_py.configs import market_config_from_market_address, MarketConfig


# === HELPER FUNCTIONS ===


def format_event(event: FrontendEvent, market_config: MarketConfig) -> str:
    """
    Format a frontend event for display.

    Args:
        event: FrontendEvent to format
        market_config: MarketConfig for context

    Returns:
        Formatted event string
    """
    event_type = event.e

    # Format based on event type
    if event_type == "Trade":
        price_str = f"{event.p:.8f}" if event.p is not None else "N/A"
        size_str = f"{event.s:.8f}" if event.s is not None else "N/A"
        side = "Buy" if event.ib else "Sell"
        taker = event.t[:8] + "..." if event.t else "N/A"
        maker = event.m[:8] + "..." if event.m else "N/A"
        return f"  [Trade] {price_str} @ {size_str} | {side} | Taker: {taker} | Maker: {maker}"

    elif event_type == "OrderCreated":
        price_str = f"{event.p:.8f}" if event.p is not None else "N/A"
        size_str = f"{event.s:.8f}" if event.s is not None else "N/A"
        side = "Buy" if event.ib else "Sell"
        return f"  [OrderCreated] {price_str} @ {size_str} | {side}"

    elif event_type == "OrderCanceled":
        price_str = f"{event.p:.8f}" if event.p is not None else "N/A"
        size_str = f"{event.s:.8f}" if event.s is not None else "N/A"
        side = "Buy" if event.ib else "Sell"
        return f"  [OrderCanceled] {price_str} @ {size_str} | {side}"

    else:
        # Generic event formatting
        return f"  [{event_type}] Timestamp: {event.ts}"


def calculate_spread(
    bids: Optional[List[Tuple[Decimal, Decimal]]],
    asks: Optional[List[Tuple[Decimal, Decimal]]]
) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """
    Calculate orderbook spread.

    Args:
        bids: List of (price, size) tuples
        asks: List of (price, size) tuples

    Returns:
        Tuple of (spread_amount, spread_percentage) or (None, None) if unable to calculate
    """
    if not bids or not asks or len(bids) == 0 or len(asks) == 0:
        return None, None

    best_bid = bids[0][0]
    best_ask = asks[0][0]

    # Calculate spread
    spread = best_ask - best_bid

    # Calculate percentage spread
    if best_bid > 0:
        spread_percentage = (spread / best_bid) * 100
    else:
        spread_percentage = 0.0

    return spread, spread_percentage


def print_orderbook_update(update: FrontendOrderbookUpdate, market_config: MarketConfig) -> None:
    """
    Print a formatted orderbook update.

    This function displays:
    - Market information
    - Events (if any)
    - Top 5 asks
    - Top 5 bids
    - Spread calculation

    Args:
        update: FrontendOrderbookUpdate from WebSocket
        market_config: MarketConfig for formatting
    """
    try:
        # Print header
        print("\n" + "=" * 50)
        print(f"ORDERBOOK UPDATE - {datetime.now().strftime('%H:%M:%S')}")
        print("=" * 50)
        print(f"Market: {market_config.market_symbol} ({market_config.market_address[:10]}...)")

        # Print events if any
        if update.events and len(update.events) > 0:
            print(f"\nEvents ({len(update.events)}):")
            for event in update.events:
                print(format_event(event, market_config))

        # Print asks (top 5)
        if update.a and len(update.a) > 0:
            print("\nASKS (Selling) - Top 5:")
            print(f"  {'Price':<15} | {'Size':<15}")
            print(f"  {'-' * 15}-+-{'-' * 15}")
            for price, size in update.a[:5]:
                print(f"  {price:<15.8f} | {size:<15.8f}")
        else:
            print("\nASKS (Selling): No asks")

        # Print bids (top 5)
        if update.b and len(update.b) > 0:
            print("\nBIDS (Buying) - Top 5:")
            print(f"  {'Price':<15} | {'Size':<15}")
            print(f"  {'-' * 15}-+-{'-' * 15}")
            for price, size in update.b[:5]:
                print(f"  {price:<15.8f} | {size:<15.8f}")
        else:
            print("\nBIDS (Buying): No bids")

        # Calculate and print spread
        spread, spread_pct = calculate_spread(update.b, update.a)
        if spread is not None:
            print(f"\nSpread: {spread:.8f} ({spread_pct:.4f}%)")
        else:
            print("\nSpread: N/A (incomplete orderbook)")

        print("=" * 50)

    except Exception as e:
        logger.error(f"Error formatting orderbook update: {e}")


# === MAIN FUNCTION ===


async def main():
    """
    Main function to run the WebSocket orderbook example.

    This function:
    1. Loads configuration from environment
    2. Creates WebSocket client
    3. Subscribes to market orderbook
    4. Processes and displays updates in real-time
    """
    # Get market address from environment or use default
    market_address = os.getenv("MARKET_ADDRESS", "0x065C9d28E428A0db40191a54d33d5b7c71a9C394")

    logger.info(f"Loading market configuration for {market_address}")

    # Fetch market configuration
    try:
        market_config = market_config_from_market_address(
            market_address=market_address,
            rpc_url="https://rpc.monad.xyz/",
        )
        logger.info(f"Market config loaded: {market_config.market_symbol}")
        logger.info(f"  Base: {market_config.base_symbol} (decimals: {market_config.base_token_decimals})")
        logger.info(f"  Quote: {market_config.quote_symbol} (decimals: {market_config.quote_token_decimals})")
        logger.info(f"  Price precision: {market_config.price_precision}")
        logger.info(f"  Size precision: {market_config.size_precision}")
    except Exception as e:
        logger.error(f"Failed to load market configuration: {e}")
        raise

    # Create queue for orderbook updates
    # The WebSocket client will push updates to this queue
    update_queue = asyncio.Queue()

    # Error callback for WebSocket errors
    def on_error(error: Exception) -> None:
        logger.error(f"WebSocket error: {error}")

    logger.info("\n" + "=" * 80)
    logger.info("Starting WebSocket Orderbook Client")
    logger.info("=" * 80)

    # Initialize and connect WebSocket client using async context manager
    # This automatically connects on enter and closes on exit
    async with KuruFrontendOrderbookClient(
        ws_url="wss://ws.kuru.io/",
        market_address=market_address,
        update_queue=update_queue,
        size_precision=market_config.size_precision,
        on_error=on_error,
        max_reconnect_attempts=5,
        reconnect_delay=1.0,
    ) as client:
        logger.success(f"Connected to WebSocket!")
        logger.success(f"Subscribed to market: {market_config.market_symbol}")
        logger.info("Waiting for orderbook updates... (Press Ctrl+C to exit)")
        logger.info("=" * 80 + "\n")

        try:
            update_count = 0
            # Infinite loop to process updates
            while True:
                # Block until an update arrives on the queue
                # The WebSocket client runs in the background and pushes updates here
                update = await update_queue.get()

                update_count += 1
                logger.info(f"Received update #{update_count} with {len(update.events)} events")

                # Format and print the update
                print_orderbook_update(update, market_config)

        except KeyboardInterrupt:
            # User pressed Ctrl+C - graceful shutdown
            logger.info("\n\nShutting down...")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            raise

    # Context manager automatically closes the WebSocket connection here
    logger.success("WebSocket connection closed. Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())

"""
Example: Place Many Orders with 1-Second Spacing

This example demonstrates:
- Using ConfigManager to load configuration from environment variables
- Depositing base and quote assets to the margin account
- Placing multiple rounds of buy and sell orders through KuruClient
- 1-second spacing between order batches
- Cancelling orders from previous rounds
- Simple callback that prints processed orders from the queue

Required environment variables:
- PRIVATE_KEY: Your wallet private key
- MARKET_ADDRESS: The market contract address (default: 0x6eB96A614E49b0dAc69F48E799C5C825AF9B33fA)

Optional environment variables:
- RPC_URL: Custom RPC endpoint (default: https://rpc.fullnode.kuru.io/)
- RPC_WS_URL: Custom WebSocket RPC endpoint (default: wss://rpc.fullnode.kuru.io/)
- BASE_DEPOSIT_AMOUNT: Base tokens to deposit (default: 100)
- QUOTE_DEPOSIT_AMOUNT: Quote tokens to deposit (default: 1000)
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

from kuru_sdk_py.client import KuruClient
from kuru_sdk_py.manager.order import Order, OrderType, OrderSide
from kuru_sdk_py.configs import ConfigManager


# Simple callback that prints processed orders
async def print_order_callback(order: Order):
    """Simple callback that prints received orders from the queue."""
    logger.success(f"Received order from queue: {order}")

async def main():
    """Main function that places many orders with 1-second spacing."""

    # Load environment variables
    load_dotenv()

    # Initialize configs using ConfigManager
    logger.info("Loading configuration...")

    # Wallet config (loads PRIVATE_KEY from .env)
    wallet_config = ConfigManager.load_wallet_config()
    logger.info(f"Wallet: {wallet_config.user_address}")

    # Connection config (loads RPC URLs from .env or uses defaults)
    connection_config = ConfigManager.load_connection_config()
    logger.info(f"RPC: {connection_config.rpc_url}")

    # Market config (fetches from blockchain)
    market_config = ConfigManager.load_market_config(
        market_address=os.getenv("MARKET_ADDRESS", "0x6eB96A614E49b0dAc69F48E799C5C825AF9B33fA"),
        fetch_from_chain=True,  # Automatically fetch token info, decimals, precision
    )
    logger.info(f"Market: {market_config.market_symbol}")

    # Create client with configs
    logger.info("Creating KuruClient...")
    client = await KuruClient.create(
        market_config=market_config,
        connection_config=connection_config,
        wallet_config=wallet_config,
    )

    # Set callback to print orders
    client.set_order_callback(print_order_callback)

    # Setup signal handler for graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_shutdown():
        """Handle SIGINT (Ctrl+C) gracefully."""
        logger.warning("🛑 Received interrupt signal, shutting down gracefully...")
        shutdown_event.set()

    # Register signal handler (asyncio-safe)
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, handle_shutdown)

    try:
        # Start client
        logger.info("Starting client...")
        await client.start()

        # Deposit base and quote assets to margin account
        base_deposit_amount = float(os.getenv("BASE_DEPOSIT_AMOUNT", "100"))  # Default: 100 base tokens
        quote_deposit_amount = float(os.getenv("QUOTE_DEPOSIT_AMOUNT", "1000"))  # Default: 1000 quote tokens

        logger.info(f"Depositing {base_deposit_amount} base tokens to margin account...")
        base_tx = await client.user.deposit_base(base_deposit_amount, auto_approve=True)
        logger.success(f"Base deposit confirmed (TX: {base_tx})")

        logger.info(f"Depositing {quote_deposit_amount} quote tokens to margin account...")
        quote_tx = await client.user.deposit_quote(quote_deposit_amount, auto_approve=True)
        logger.success(f"Quote deposit confirmed (TX: {quote_tx})")

        # Check margin balances
        margin_base, margin_quote = await client.user.get_margin_balances()
        logger.info(f"Margin balances after deposit - Base: {margin_base} wei, Quote: {margin_quote} wei")

        # Cancel all active orders before placing new ones
        logger.info("Cancelling all active orders for the market...")
        # await client.cancel_all_active_orders_for_market()
        logger.success("All active orders cancelled")

        # Place multiple rounds of orders with 1-second spacing
        num_rounds = 10  # Number of order batches to place
        base_price = 2  # Starting price

        logger.info(f"Placing {num_rounds} rounds of orders with 1-second spacing...")

        for i in range(num_rounds):
            # Check for shutdown signal
            if shutdown_event.is_set():
                logger.info("Shutdown requested, stopping order placement...")
                break

            # Create buy and sell orders for this round
            orders = [
                # Buy orders
                Order(
                    cloid=f"buy-{i}-1",
                    order_type=OrderType.LIMIT,
                    side=OrderSide.BUY,
                    price=base_price + i,
                    size=11,
                    post_only=True
                ),
                Order(
                    cloid=f"buy-{i}-2",
                    order_type=OrderType.LIMIT,
                    side=OrderSide.BUY,
                    price=base_price + i - 1,
                    size=12,
                    post_only=True
                ),
                # Sell orders
                Order(
                    cloid=f"sell-{i}-1",
                    order_type=OrderType.LIMIT,
                    side=OrderSide.SELL,
                    price=base_price + 20 + i,
                    size=13,
                    post_only=True
                ),
                Order(
                    cloid=f"sell-{i}-2",
                    order_type=OrderType.LIMIT,
                    side=OrderSide.SELL,
                    price=base_price + 21 + i,
                    size=14,
                    post_only=True
                ),
            ]

            # On third iteration (i=2), cancel orders from first iteration (i=0)
            if i == 4:
                cancel_orders = [
                    Order(cloid="buy-0-1", order_type=OrderType.CANCEL),
                    Order(cloid="buy-0-2", order_type=OrderType.CANCEL),
                    Order(cloid="sell-0-1", order_type=OrderType.CANCEL),
                    Order(cloid="sell-0-2", order_type=OrderType.CANCEL),
                    Order(cloid="buy-1-1", order_type=OrderType.CANCEL),
                    Order(cloid="buy-1-2", order_type=OrderType.CANCEL),
                    Order(cloid="sell-1-1", order_type=OrderType.CANCEL),
                    Order(cloid="sell-1-2", order_type=OrderType.CANCEL),
                    Order(cloid="buy-2-1", order_type=OrderType.CANCEL),
                    Order(cloid="buy-2-2", order_type=OrderType.CANCEL),
                ]
                orders.extend(cancel_orders)
                logger.info("Adding 4 cancel orders for iteration 0")

            try:
                # Place orders
                txhash = await client.place_orders(orders, post_only=True)
                order_count = len(orders)
                logger.info(f"Round {i+1}/{num_rounds}: Placed {order_count} orders (TX: {txhash})")

                # Only sleep after successful placement
                if i < num_rounds - 1:
                    # Use wait with timeout to allow immediate shutdown during sleep
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                        # If shutdown_event was set, break the loop
                        logger.info("Shutdown requested during sleep, stopping...")
                        break
                    except asyncio.TimeoutError:
                        # Normal case - timeout means no shutdown, continue
                        pass

            except ValueError as e:
                # Validation error - invalid order parameters
                logger.error(f"Round {i+1}/{num_rounds}: Invalid order data - {e}")
                logger.warning("Skipping this round and continuing...")
                continue

            except Exception as e:
                # Transaction failure - gas estimation, revert, network error, etc.
                logger.error(f"Round {i+1}/{num_rounds}: Transaction failed - {e}")
                logger.warning("Skipping this round and continuing...")
                continue

        logger.success(f"Finished attempting all {num_rounds} rounds of orders!")

        # Keep running to receive order updates
        logger.info("Listening for order updates... Press Ctrl+C to stop")
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=60.0)
            logger.info("Shutdown requested")
        except asyncio.TimeoutError:
            logger.info("60 seconds elapsed, shutting down normally")

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Gracefully stop
        await client.stop()
        logger.success("Client stopped")


if __name__ == "__main__":
    asyncio.run(main())

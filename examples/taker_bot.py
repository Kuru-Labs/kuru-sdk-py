"""
Example: Taker Bot - Market Order Testing

This example demonstrates:
- Using a separate taker account with TAKER_PRIVATE_KEY
- Depositing funds to margin account
- Placing market buy and sell orders continuously
- Testing against limit orders from place_many_orders.py
- Receiving and logging trade events
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
from kuru_sdk_py.manager.order import Order
from kuru_sdk_py.configs import initialize_kuru_mm_config, market_config_from_market_address


# Simple callback that prints trade events
async def trade_event_callback(order: Order):
    """Callback to log trade events from the queue."""
    logger.success(f"Trade event received: {order}")


async def main():
    """Main function that places market buy and sell orders continuously."""

    # Load environment variables
    load_dotenv()

    # Validate TAKER_PRIVATE_KEY is set
    taker_private_key = os.getenv("TAKER_PRIVATE_KEY")
    if not taker_private_key:
        logger.error("TAKER_PRIVATE_KEY environment variable is required")
        logger.info("Please set TAKER_PRIVATE_KEY in your .env file or environment")
        return

    # Initialize configs with taker private key
    kuru_config = initialize_kuru_mm_config(
        private_key=taker_private_key,
        rpc_url=os.getenv("RPC_URL", "https://rpc.monad.xyz/"),
        rpc_ws_url=os.getenv("RPC_WS_URL", "wss://rpc.monad.xyz/")
    )

    market_config = market_config_from_market_address(
        market_address=os.getenv("MARKET_ADDRESS", "0x6eB96A614E49b0dAc69F48E799C5C825AF9B33fA"),
        rpc_url=os.getenv("RPC_URL", "https://rpc.monad.xyz/")
    )

    # Get configuration from environment
    taker_base_deposit = float(os.getenv("TAKER_BASE_DEPOSIT", "50"))
    taker_quote_deposit = float(os.getenv("TAKER_QUOTE_DEPOSIT", "500"))
    trade_interval = float(os.getenv("TRADE_INTERVAL", "5"))
    market_buy_quote_amount = float(os.getenv("MARKET_BUY_QUOTE_AMOUNT", "10"))
    market_sell_size = float(os.getenv("MARKET_SELL_SIZE", "5"))

    # Create client
    logger.info("Creating KuruClient for taker bot...")
    client = await KuruClient.create(market_config, kuru_config)

    # Set callback to log trade events
    client.set_order_callback(trade_event_callback)

    # Setup shutdown event
    shutdown_event = asyncio.Event()

    try:
        # Start client
        logger.info("Starting taker client...")
        await client.start()

        # Setup signal handler AFTER client.start() to override client's handlers
        loop = asyncio.get_event_loop()

        def signal_handler():
            """Handle SIGINT (Ctrl+C) gracefully."""
            logger.warning("Received interrupt signal, shutting down gracefully...")
            shutdown_event.set()

        # Register signal handlers for SIGINT and SIGTERM
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)

        # Deposit base and quote assets to margin account
        logger.info(f"Depositing {taker_base_deposit} base tokens to margin account...")
        base_tx = await client.user.deposit_base(taker_base_deposit, auto_approve=True)
        logger.success(f"Base deposit confirmed (TX: {base_tx})")

        logger.info(f"Depositing {taker_quote_deposit} quote tokens to margin account...")
        quote_tx = await client.user.deposit_quote(taker_quote_deposit, auto_approve=True)
        logger.success(f"Quote deposit confirmed (TX: {quote_tx})")

        # Check margin balances
        margin_base, margin_quote = await client.user.get_margin_balances()
        logger.info(f"Margin balances after deposit - Base: {margin_base} wei, Quote: {margin_quote} wei")

        # Trading loop - alternate between market buy and market sell
        logger.info("Starting trading loop... Press Ctrl+C to stop")
        logger.info(f"Trade interval: {trade_interval} seconds")
        logger.info(f"Market buy quote amount: {market_buy_quote_amount}")
        logger.info(f"Market sell size: {market_sell_size}")

        iteration = 0
        while True:
            # Check for shutdown signal
            if shutdown_event.is_set():
                logger.info("Shutdown requested, stopping trading loop...")
                break

            iteration += 1
            logger.info(f"\n--- Trading Iteration {iteration} ---")

            # Place market buy order
            try:
                # Calculate minimum amount out with 5% slippage tolerance
                # Assuming base price, we can set a conservative min_amount_out
                # For simplicity, using 0 (no slippage protection) or a small fixed value
                min_base_amount = 0  # Can be adjusted based on expected price

                logger.info(
                    f"Placing market BUY: quote_amount={market_buy_quote_amount}, "
                    f"min_amount_out={min_base_amount}"
                )

                buy_txhash = await client.place_market_buy(
                    quote_amount=market_buy_quote_amount,
                    min_amount_out=min_base_amount,
                    is_margin=True,
                    is_fill_or_kill=False
                )
                logger.success(f"Market buy order placed (TX: {buy_txhash})")

            except Exception as e:
                logger.error(f"Market buy failed: {e}")
                logger.warning("Continuing to next order...")

            # Wait before next trade
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=trade_interval)
                # If shutdown_event was set, break the loop
                logger.info("Shutdown requested during sleep, stopping...")
                break
            except asyncio.TimeoutError:
                # Normal case - timeout means no shutdown, continue
                pass

            # Check for shutdown signal again
            if shutdown_event.is_set():
                logger.info("Shutdown requested, stopping trading loop...")
                break

            # Place market sell order
            try:
                # Calculate minimum amount out with 5% slippage tolerance
                min_quote_amount = 0  # Can be adjusted based on expected price

                logger.info(
                    f"Placing market SELL: size={market_sell_size}, "
                    f"min_amount_out={min_quote_amount}"
                )

                sell_txhash = await client.place_market_sell(
                    size=market_sell_size,
                    min_amount_out=min_quote_amount,
                    is_margin=True,
                    is_fill_or_kill=False
                )
                logger.success(f"Market sell order placed (TX: {sell_txhash})")

            except Exception as e:
                logger.error(f"Market sell failed: {e}")
                logger.warning("Continuing to next iteration...")

            # Wait before next iteration
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=trade_interval)
                # If shutdown_event was set, break the loop
                logger.info("Shutdown requested during sleep, stopping...")
                break
            except asyncio.TimeoutError:
                # Normal case - timeout means no shutdown, continue
                pass

        logger.info("Trading loop ended")

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        # Gracefully stop
        logger.info("Stopping taker client...")
        await client.stop()
        logger.success("Taker client stopped")


if __name__ == "__main__":
    asyncio.run(main())

"""
Simple Market Making Bot

This example demonstrates a basic market making strategy that:
- Deposits base and quote assets to the margin account
- Fetches MON price from Coinbase (MON-USD as proxy for MON-USDC)
- Maintains bid/ask spreads around the mid price
- Automatically cancels and replaces orders every 6 seconds
- Tracks confirmed orders via callbacks
- Handles graceful shutdown with order cancellation

Market: MON-USDC (0x065C9d28E428A0db40191a54d33d5b7c71a9C394)
Price Source: Coinbase MON-USD spot price
"""

import sys
from pathlib import Path
import asyncio
import signal
import os
import time
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv
import aiohttp

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))

from kuru_sdk_py.client import KuruClient
from kuru_sdk_py.manager.order import Order, OrderType, OrderSide, OrderStatus
from kuru_sdk_py.configs import ConfigManager

# ============================================================================
# Configuration
# ============================================================================

COINBASE_API_URL = "https://api.coinbase.com/v2/prices/MON-USD/spot"
BPS_INCREMENT = 0.1  # 10 basis points per level
UPDATE_INTERVAL = 6.0  # seconds
ORDER_SIZE = 200.0  # MON per order (~$2 per order at $0.02)


# ============================================================================
# Helper Functions
# ============================================================================

async def get_mon_price() -> float:
    """Fetch current MON price from Coinbase API."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(COINBASE_API_URL) as response:
                data = await response.json()
                return float(data["data"]["amount"])
    except Exception as e:
        logger.error(f"Error fetching MON price from Coinbase: {e}")
        raise


# ============================================================================
# Main Function
# ============================================================================

async def main():
    """Main function that runs the market making strategy."""

    # Load environment variables from .env file
    load_dotenv()

    # Track active orders (updated by callback)
    active_cloids = set()

    # ========================================================================
    # Initialize Configs using ConfigManager
    # ========================================================================
    # The ConfigManager automatically loads from environment variables (.env file)
    # and uses sensible defaults. You can override any value explicitly.

    # Wallet config (loads PRIVATE_KEY from .env)
    wallet_config = ConfigManager.load_wallet_config()
    logger.info(f"Wallet: {wallet_config.user_address}")

    # Connection config (loads RPC URLs from .env or uses defaults)
    connection_config = ConfigManager.load_connection_config()
    logger.info(f"RPC: {connection_config.rpc_url}")

    # Market config (fetches from blockchain)
    market_config = ConfigManager.load_market_config(
        market_address=os.getenv("MARKET_ADDRESS", "0x065c9d28e428a0db40191a54d33d5b7c71a9c394"),
        fetch_from_chain=True,  # Fetch token info, decimals, precision from chain
    )
    logger.info(f"Market: {market_config.market_symbol}")

    # Optional: Customize behavior configs (uncomment to override defaults)
    # transaction_config = ConfigManager.load_transaction_config(
    #     timeout=180,  # 3 minutes for slower networks
    #     poll_latency=1.0,  # 1 second RPC sync delay
    # )
    # order_execution_config = ConfigManager.load_order_execution_config(
    #     post_only=True,  # Only maker orders (default)
    #     auto_approve=True,  # Auto approve tokens (default)
    # )

    # Create client with configs
    # Behavioral configs (transaction, websocket, order_execution) use defaults if not provided
    logger.info("Creating KuruClient...")
    client = await KuruClient.create(
        market_config=market_config,
        connection_config=connection_config,
        wallet_config=wallet_config,
        # Optional configs use defaults:
        # transaction_config=transaction_config,
        # websocket_config=websocket_config,
        # order_execution_config=order_execution_config,
    )

    # Order callback to track confirmed orders
    async def order_callback(order: Order):
        """Callback to track order state changes."""
        if order.status == OrderStatus.ORDER_CREATED:
            active_cloids.add(order.cloid)
            logger.debug(f"✓ Order {order.cloid} confirmed (ID: {order.kuru_order_id})")

        elif order.status == OrderStatus.ORDER_CANCELLED:
            active_cloids.discard(order.cloid)
            logger.debug(f"✗ Order {order.cloid} cancelled")

        elif order.status == OrderStatus.ORDER_FULLY_FILLED:
            active_cloids.discard(order.cloid)
            logger.success(
                f"✓ Order {order.cloid} filled! "
                f"Side: {order.side.value if order.side else 'N/A'}, "
                f"Price: {order.price}, Size: {order.size}"
            )

        elif order.status == OrderStatus.ORDER_PARTIALLY_FILLED:
            logger.info(f"⚡ Order {order.cloid} partially filled")

    client.set_order_callback(order_callback)

    # Setup signal handler for graceful shutdown
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_shutdown():
        """Handle SIGINT (Ctrl+C) gracefully."""
        logger.warning("\n🛑 Shutdown signal received...")
        shutdown_event.set()

    # Use asyncio's signal handler for proper async support
    loop.add_signal_handler(signal.SIGINT, handle_shutdown)

    try:
        # Start client
        logger.info("Starting client...")
        await client.start()
        logger.success(f"Connected to market: {market_config.market_symbol}")

        # Check margin balances first
        base_deposit_amount = float(os.getenv("BASE_DEPOSIT_AMOUNT", "600"))
        quote_deposit_amount = float(os.getenv("QUOTE_DEPOSIT_AMOUNT", "15"))

        margin_base_wei, margin_quote_wei = await client.user.get_margin_balances()
        margin_base = float(margin_base_wei) / (10 ** market_config.base_token_decimals)
        margin_quote = float(margin_quote_wei) / (10 ** market_config.quote_token_decimals)

        logger.info(f"\n💰 Current Margin Balances:")
        logger.info(f"   Base: {margin_base:.4f} {market_config.base_symbol}")
        logger.info(f"   Quote: {margin_quote:.2f} {market_config.quote_symbol}\n")

        # Deposit base if below threshold (half of deposit amount)
        if base_deposit_amount > 0 and margin_base < (base_deposit_amount / 2):
            logger.info(f"Base balance below threshold ({base_deposit_amount / 2:.4f}). Depositing {base_deposit_amount} {market_config.base_symbol}...")
            base_tx = await client.user.deposit_base(base_deposit_amount, auto_approve=True)
            logger.success(f"Base deposit confirmed (TX: {base_tx})")
            # Update balance after deposit
            margin_base_wei, _ = await client.user.get_margin_balances()
            margin_base = float(margin_base_wei) / (10 ** market_config.base_token_decimals)
        elif base_deposit_amount > 0:
            logger.info(f"Base balance sufficient. Skipping deposit.")

        # Deposit quote if below threshold (half of deposit amount)
        if quote_deposit_amount > 0 and margin_quote < (quote_deposit_amount / 2):
            logger.info(f"Quote balance below threshold ({quote_deposit_amount / 2:.2f}). Depositing {quote_deposit_amount} {market_config.quote_symbol}...")
            quote_tx = await client.user.deposit_quote(quote_deposit_amount, auto_approve=True)
            logger.success(f"Quote deposit confirmed (TX: {quote_tx})")
            # Update balance after deposit
            _, margin_quote_wei = await client.user.get_margin_balances()
            margin_quote = float(margin_quote_wei) / (10 ** market_config.quote_token_decimals)
        elif quote_deposit_amount > 0:
            logger.info(f"Quote balance sufficient. Skipping deposit.")

        logger.info(f"\n💰 Final Margin Balances:")
        logger.info(f"   Base: {margin_base:.4f} {market_config.base_symbol}")
        logger.info(f"   Quote: {margin_quote:.2f} {market_config.quote_symbol}\n")

        if margin_base == 0 or margin_quote == 0:
            logger.warning("⚠️  Low or zero balance detected!")
            logger.warning("   Set BASE_DEPOSIT_AMOUNT and QUOTE_DEPOSIT_AMOUNT env vars to auto-deposit")

        # Market making loop
        logger.info("🚀 Starting market making loop...\n")
        loop_count = 0

        while not shutdown_event.is_set():
            try:
                loop_count += 1

                # Fetch MON price from Coinbase
                mon_price = await get_mon_price()

                # Get current balances
                margin_base_wei, margin_quote_wei = await client.user.get_margin_balances()
                margin_base = float(margin_base_wei) / (10 ** market_config.base_token_decimals)
                margin_quote = float(margin_quote_wei) / (10 ** market_config.quote_token_decimals)

                # Calculate price precision (for rounding)
                price_decimals = len(str(market_config.price_precision)) - 1

                # Build orders list
                orders = []

                # Cancel all active orders
                for cloid in list(active_cloids):
                    orders.append(Order(cloid=cloid, order_type=OrderType.CANCEL))

                # Generate unique timestamp for this batch
                timestamp = int(time.time() * 1000)

                # Create new orders
                sell_orders = []
                buy_orders = []

                # 2 sell orders above mid price
                for i in range(2):
                    price = mon_price * (1 + BPS_INCREMENT * (i + 1))
                    final_price = round(price, price_decimals)

                    cloid = f"sell-{timestamp}-{i}"
                    orders.append(Order(
                        cloid=cloid,
                        order_type=OrderType.LIMIT,
                        side=OrderSide.SELL,
                        price=final_price,
                        size=ORDER_SIZE,
                        post_only=False
                    ))
                    sell_orders.append((final_price, ORDER_SIZE))

                # 2 buy orders below mid price
                for i in range(2):
                    price = mon_price * (1 - BPS_INCREMENT * (i + 1))
                    final_price = round(price, price_decimals)

                    cloid = f"buy-{timestamp}-{i}"
                    orders.append(Order(
                        cloid=cloid,
                        order_type=OrderType.LIMIT,
                        side=OrderSide.BUY,
                        price=final_price,
                        size=ORDER_SIZE,
                        post_only=False
                    ))
                    buy_orders.append((final_price, ORDER_SIZE))

                # Place orders
                num_cancels = len(active_cloids)
                num_new = len(orders) - num_cancels

                txhash = await client.place_orders(orders, post_only=False)

                # Log update
                logger.info("=" * 70)
                logger.info(f"Market Making Update #{loop_count} - {datetime.now().strftime('%H:%M:%S')}")
                logger.info("=" * 70)
                logger.info(f"Transaction Hash: {txhash}")
                logger.info(f"MON Price: ${mon_price:.4f}")
                logger.info(f"Base Balance: {margin_base:.4f} {market_config.base_symbol}")
                logger.info(f"Quote Balance: ${margin_quote:.2f}")
                logger.info(f"Orders Cancelled: {num_cancels}")
                logger.info(f"Orders Created: {num_new}")

                logger.info("\nSells:")
                for i, (price, size) in enumerate(sell_orders, 1):
                    logger.info(f"  {i}. {size} {market_config.base_symbol} @ ${price:.2f}")

                logger.info("\nBuys:")
                for i, (price, size) in enumerate(buy_orders, 1):
                    logger.info(f"  {i}. {size} {market_config.base_symbol} @ ${price:.2f}")

                logger.info("=" * 70 + "\n")

                # Wait for next update or shutdown
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=UPDATE_INTERVAL)
                    # Shutdown requested
                    break
                except asyncio.TimeoutError:
                    # Normal case - continue
                    pass

            except Exception as e:
                logger.error(f"Error in market making loop: {e}")
                # Wait before retrying
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=UPDATE_INTERVAL)
                    break
                except asyncio.TimeoutError:
                    pass

        # Shutdown: Cancel all active orders
        logger.info(f"\n🛑 Cancelling {len(active_cloids)} active orders...")
        if active_cloids:
            cancel_orders = [Order(cloid=c, order_type=OrderType.CANCEL) for c in active_cloids]
            try:
                cancel_tx = await client.place_orders(cancel_orders)
                logger.success(f"✓ Orders cancelled (TX: {cancel_tx})")
            except Exception as e:
                logger.error(f"Error cancelling orders: {e}")

    except KeyboardInterrupt:
        logger.warning("\n🛑 Keyboard interrupt received...")
    finally:
        # Remove signal handler
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except Exception:
            pass  # Signal handler may already be removed

        # Gracefully stop
        await client.stop()
        logger.success("✓ Client stopped")


if __name__ == "__main__":
    asyncio.run(main())

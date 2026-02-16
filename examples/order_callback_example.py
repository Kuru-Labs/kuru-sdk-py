"""
Example demonstrating the order callback feature in KuruClient.

This example shows how to:
1. Set up a callback to automatically process order updates
2. React to different order states (placed, filled, cancelled)
3. Use the callback for real-time order monitoring
"""

import asyncio
import os
from loguru import logger
from kuru_sdk_py.client import KuruClient
from kuru_sdk_py.manager.order import Order, OrderStatus, OrderType, OrderSide
from kuru_sdk_py.configs import initialize_kuru_mm_config, market_config_from_market_address


# Define your order callback
async def handle_order_update(order: Order):
    """
    Callback function that gets invoked for every order update.

    This is called automatically when orders are:
    - Placed on the orderbook
    - Partially filled
    - Fully filled
    - Cancelled
    """
    logger.info(f"📦 Order Update Received")
    logger.info(f"   CLOID: {order.cloid}")
    logger.info(f"   Status: {order.status.value}")
    logger.info(f"   Type: {order.order_type.value}")

    if order.kuru_order_id:
        logger.info(f"   Kuru Order ID: {order.kuru_order_id}")

    if order.txhash:
        logger.info(f"   TX Hash: {order.txhash}")

    # React to different order states
    if order.status == OrderStatus.ORDER_PLACED:
        logger.success(f"✅ Order {order.cloid} successfully placed on orderbook!")

    elif order.status == OrderStatus.ORDER_PARTIALLY_FILLED:
        logger.info(f"⚡ Order {order.cloid} partially filled")
        # You could implement re-balancing logic here

    elif order.status == OrderStatus.ORDER_FULLY_FILLED:
        logger.success(f"✅ Order {order.cloid} fully filled!")
        # You could trigger new order placement here

    elif order.status == OrderStatus.ORDER_CANCELLED:
        logger.warning(f"❌ Order {order.cloid} cancelled")


async def main():
    """Main example demonstrating order callback usage."""

    # Initialize configurations
    logger.info("Initializing configurations...")

    kuru_config = initialize_kuru_mm_config(
        private_key=os.getenv("PRIVATE_KEY"),
        rpc_url=os.getenv("RPC_URL", "https://rpc.fullnode.kuru.io/"),
        rpc_ws_url=os.getenv("RPC_WS_URL", "wss://rpc.fullnode.kuru.io/")
    )

    market_config = market_config_from_market_address(
        market_address=os.getenv("MARKET_ADDRESS", "0x065C9d28E428A0db40191a54d33d5b7c71a9C394"),
        rpc_url=os.getenv("RPC_URL", "https://rpc.fullnode.kuru.io/")
    )

    # Create client
    logger.info("Creating KuruClient...")
    client = await KuruClient.create(market_config, kuru_config)

    # Set the order callback BEFORE starting the client
    # This ensures the consumer task starts when client.start() is called
    logger.info("Setting order callback...")
    client.set_order_callback(handle_order_update)

    try:
        # Start the client (this will automatically start the order consumer)
        logger.info("Starting KuruClient...")
        await client.start()
        logger.success("Client started! Order consumer is now running in the background.")

        # Example: Place some orders
        # When these orders are processed, your callback will be invoked automatically
        logger.info("\nPlacing example orders...")

        orders = [
            Order(
                cloid="buy-order-1",
                order_type=OrderType.LIMIT,
                side=OrderSide.BUY,
                price=100,
                size=10,
                post_only=True
            ),
            Order(
                cloid="sell-order-1",
                order_type=OrderType.LIMIT,
                side=OrderSide.SELL,
                price=110,
                size=10,
                post_only=True
            ),
        ]

        # Place orders (callback will be triggered as events come in)
        txhash = await client.place_orders(orders)
        logger.success(f"Orders placed! TX: {txhash}")

        # Keep the client running to receive order updates
        logger.info("\nListening for order updates... (Press Ctrl+C to stop)")
        logger.info("Your callback will be invoked automatically as orders are processed.")

        # Advanced: You can also access the queue directly if needed
        logger.info(f"\nQueue size: {client.orders_manager.processed_orders_queue.qsize()}")

        # Run for some time (or indefinitely)
        await asyncio.sleep(60)

    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        # Gracefully stop the client
        # This will:
        # 1. Cancel the order consumer task
        # 2. Process any remaining orders in the queue
        # 3. Disconnect the websocket
        await client.stop()
        logger.success("Client stopped gracefully")


async def example_dynamic_callback():
    """
    Example showing dynamic callback changes.

    You can change or disable the callback at runtime.
    """
    client = await KuruClient.create(market_config, kuru_config)

    # Start with one callback
    async def callback_phase1(order: Order):
        logger.info(f"Phase 1: Processing {order.cloid}")

    client.set_order_callback(callback_phase1)
    await client.start()

    # ... place some orders ...

    # Change to a different callback
    async def callback_phase2(order: Order):
        logger.info(f"Phase 2: Processing {order.cloid}")

    client.set_order_callback(callback_phase2)

    # ... place more orders ...

    # Disable callback (orders still go to queue for direct access)
    client.set_order_callback(None)

    # Now you can access the queue directly
    while not client.orders_manager.processed_orders_queue.empty():
        order = await client.orders_manager.processed_orders_queue.get()
        logger.info(f"Manual processing: {order.cloid}")

    await client.stop()


async def example_error_handling():
    """
    Example showing robust error handling in callbacks.

    If your callback raises an exception, it will be logged
    but processing continues for subsequent orders.
    """
    async def potentially_failing_callback(order: Order):
        try:
            # Your processing logic that might fail
            if order.status == OrderStatus.ORDER_FILLED:
                # Simulate database write that might fail
                # await save_to_database(order)
                pass
        except Exception as e:
            logger.error(f"Failed to process order {order.cloid}: {e}")
            # Maybe add to a retry queue
            # await retry_queue.put(order)

    client = await KuruClient.create(market_config, kuru_config)
    client.set_order_callback(potentially_failing_callback)
    await client.start()

    # Even if the callback fails for one order,
    # subsequent orders will still be processed

    await asyncio.sleep(30)
    await client.stop()


if __name__ == "__main__":
    # Run the main example
    asyncio.run(main())

    # Or run the dynamic callback example
    # asyncio.run(example_dynamic_callback())

    # Or run the error handling example
    # asyncio.run(example_error_handling())

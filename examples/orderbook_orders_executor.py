"""
Example demonstrating OrdersExecutor for placing batch orders on the order book.

IMPORTANT PREREQUISITES:
1. EIP-7702 Authorization: You must authorize the MM Entrypoint contract before running
   this example. Run: uv run python examples/eip7701_auth.py
   The authorization persists until explicitly revoked, so you only need to run it once.

2. Margin Deposits: This example automatically deposits margin collateral before placing orders.
   The MM Entrypoint contract requires sufficient margin balance to place orders.

   Without margin deposits, the transaction will succeed (no revert) but orders will be
   silently skipped and won't appear on the orderbook. This is because the contract performs
   a balance check and skips orders with insufficient collateral without emitting OrderCreated events.

3. Precision Conversion: Order prices and sizes are now specified as float values (e.g., 0.1, 0.001).
   The OrdersExecutor automatically converts these to integers using the market's price_precision and
   size_precision before sending to the contract.
"""
import sys
from pathlib import Path
from loguru import logger
import asyncio

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))

from kuru_sdk_py.executor.orders_executor import OrdersExecutor
from kuru_sdk_py.user.user import User
from kuru_sdk_py.configs import KuruMMConfig
from kuru_sdk_py.configs import MarketConfig, market_config_from_market_address, initialize_kuru_mm_config
import dotenv
import os
from kuru_sdk_py.manager.order import Order, OrderSide, OrderType
from kuru_sdk_py.utils import string_to_bytes32


dotenv.load_dotenv()

config = initialize_kuru_mm_config(
    private_key=os.getenv("PRIVATE_KEY"),
    rpc_url="https://rpc.monad.xyz/",
)

market_config = market_config_from_market_address(
    market_address="0x6eB96A614E49b0dAc69F48E799C5C825AF9B33fA",
    rpc_url="https://rpc.monad.xyz/",
)

logger.info(f"Market config: {market_config}")


orders = [
    Order(
        cloid="buy-1",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        price=1,
        size=11,
        post_only=True,
    ),
    Order(
        cloid="sell-1",
        order_type=OrderType.LIMIT,
        side=OrderSide.SELL,
        price=2,
        size=11,
        post_only=True,
    ),
]


async def main():
    """Main function to create and use the OrdersExecutor instance."""
    
    # Create OrdersExecutor instance
    executor = await OrdersExecutor.create(
        mm_entrypoint_address=market_config.mm_entrypoint_address,
        config=config,
        order_book_address=market_config.market_address,
        market_config=market_config,
    )
    
    logger.info("OrdersExecutor instance created successfully!")
    logger.info(f"MM Entrypoint: {executor.contract_address}")
    logger.info(f"Order Book: {executor.order_book_address}")
    logger.info(f"User Address: {executor.user_address}")

    # Create User instance for margin management
    user = User(
        user_address=config.user_address,
        market_config=market_config,
        rpc_url=config.rpc_url,
        private_key=config.private_key,
    )

    # Check initial margin balances
    logger.info("\n" + "=" * 80)
    logger.info("Checking Margin Balances")
    logger.info("=" * 80)
    base_balance = await user.get_margin_base_balance()
    quote_balance = await user.get_margin_quote_balance()
    logger.info(f"Initial base margin balance: {base_balance} wei")
    logger.info(f"Initial quote margin balance: {quote_balance} wei")

    # Deposit margin collateral (required for orders to be placed)
    logger.info("\n--- Depositing Margin Collateral ---")
    logger.info("Orders require margin deposits to be placed on the orderbook.")
    logger.info("Without sufficient margin, the transaction will succeed but orders will be silently skipped.")

    # Deposit sufficient collateral for test orders
    await user.deposit_base(10.0, auto_approve=True)
    await user.deposit_quote(10.0, auto_approve=True)

    logger.success("Margin deposits completed!")

    # Check updated margin balances
    base_balance = await user.get_margin_base_balance()
    quote_balance = await user.get_margin_quote_balance()
    logger.info(f"Updated base margin balance: {base_balance} wei")
    logger.info(f"Updated quote margin balance: {quote_balance} wei")
    logger.info("=" * 80 + "\n")

    # split the orders list into buy orders, sell orders and cancel orders
    buy_orders = [order for order in orders if order.side == OrderSide.BUY]
    sell_orders = [order for order in orders if order.side == OrderSide.SELL]
    cancel_orders = [order for order in orders if order.order_type == OrderType.CANCEL]

    # sort the buy orders by price in descending order
    buy_orders.sort(key=lambda x: x.price, reverse=True)
    # sort the sell orders by price in ascending order
    sell_orders.sort(key=lambda x: x.price)
    
    # Convert string cloids to bytes32 format
    buy_cloids = [string_to_bytes32(order.cloid) for order in buy_orders]
    buy_prices = [order.price for order in buy_orders]
    buy_sizes = [order.size for order in buy_orders]

    sell_cloids = [string_to_bytes32(order.cloid) for order in sell_orders]
    sell_prices = [order.price for order in sell_orders]
    sell_sizes = [order.size for order in sell_orders]

    cancel_cloids = [string_to_bytes32(order.cloid) for order in cancel_orders]

    kuru_order_ids_to_cancel = []
    # for cancel_order in cancel_orders:
    #     kuru_order_id = orders_manager.get_kuru_order_id(cancel_order.cloid)
    #     if kuru_order_id is not None:
    #         kuru_order_ids_to_cancel.append(kuru_order_id)
    #     else:
    #         logger.error(f"Kuru order ID not found for cancel order {cancel_order.cloid}")
    #         continue

    txhash = await executor.place_order(buy_cloids, sell_cloids, cancel_cloids, buy_prices, buy_sizes, sell_prices, sell_sizes, kuru_order_ids_to_cancel, post_only=False)



if __name__ == "__main__":
    asyncio.run(main())

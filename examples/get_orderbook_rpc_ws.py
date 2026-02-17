"""
Example: connect to the RPC websocket and log Kuru topics.

Notes:
- RpcWebsocket filters orderbook events by user_address (owner/maker/taker).
- Set USER_ADDRESS or PRIVATE_KEY in your environment to see your events.
"""
import asyncio
import os
import sys
from pathlib import Path

import dotenv
from loguru import logger
from web3 import Account

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))

from kuru_sdk_py.configs import KuruTopicsSignature, MON_USDC_MARKET
from kuru_sdk_py.feed.rpc_ws import RpcWebsocket
from kuru_sdk_py.manager.events import (
    OrderCreatedEvent,
    OrdersCanceledEvent,
    TradeEvent,
    BatchUpdateMMEvent,
)


dotenv.load_dotenv()


class EventLogger:
    async def on_order_created(self, event: OrderCreatedEvent) -> None:
        logger.info(f"OrderCreated: {event}")

    async def on_orders_cancelled(self, event: OrdersCanceledEvent) -> None:
        logger.info(f"OrdersCanceled: {event}")

    async def on_trade(self, event: TradeEvent) -> None:
        logger.info(f"Trade: {event}")

    async def on_batch_update_mm(self, event: BatchUpdateMMEvent) -> None:
        logger.info(f"BatchUpdateMM: {event}")


def resolve_user_address() -> str:
    user_address = os.getenv("USER_ADDRESS")
    if user_address:
        return user_address

    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        raise ValueError("Set USER_ADDRESS or PRIVATE_KEY in your environment.")

    return Account.from_key(private_key).address


async def main() -> None:
    rpc_ws_url = os.getenv("RPC_WS_URL", "wss://rpc.monad.xyz/")
    user_address = resolve_user_address()

    logger.info(f"Connecting to RPC WS: {rpc_ws_url}")
    logger.info(f"Market: {MON_USDC_MARKET.market_symbol}")
    logger.info(f"Orderbook: {MON_USDC_MARKET.market_address}")
    logger.info(f"MM Entrypoint: {MON_USDC_MARKET.mm_entrypoint_address}")
    logger.info(f"User address (filter): {user_address}")

    orders_manager = EventLogger()
    rpc_ws = RpcWebsocket(
        rpc_url=rpc_ws_url,
        orderbook_address=MON_USDC_MARKET.market_address,
        mm_entrypoint_address=MON_USDC_MARKET.mm_entrypoint_address,
        user_address=user_address,
        orders_manager=orders_manager,
    )

    await rpc_ws.connect()
    await rpc_ws.create_log_subscription(KuruTopicsSignature)
    logger.info("Subscribed to Kuru topics. Waiting for events...")

    try:
        await rpc_ws.process_subscription_logs()
    finally:
        await rpc_ws.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")

import sys
from pathlib import Path
from loguru import logger
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))

from kuru_sdk_py.user.user import User
from kuru_sdk_py.configs import initialize_kuru_mm_config, market_config_from_market_address


async def main():
    """
    Example demonstrating EIP-7702 authorization revocation.

    This removes the delegation from your EOA to the MM Entrypoint contract,
    returning your account to normal EOA behavior.
    """

    # Setup configuration
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        raise ValueError(
            "PRIVATE_KEY environment variable not set. "
            "Please set it with: export PRIVATE_KEY=your_private_key"
        )

    config = initialize_kuru_mm_config(
        private_key=private_key,
        rpc_url="https://rpc.monad.xyz/",
    )

    market_config = market_config_from_market_address(
        market_address="0x6eB96A614E49b0dAc69F48E799C5C825AF9B33fA",
        rpc_url="https://rpc.monad.xyz/",
    )

    logger.info(f"Market config: {market_config}")

    # Create User instance
    user = User(
        user_address=config.user_address,
        market_config=market_config,
        rpc_url=config.rpc_url,
        private_key=config.private_key,
    )

    logger.info("\n" + "=" * 80)
    logger.info("EIP-7702 Revocation Example")
    logger.info("=" * 80)

    try:
        logger.info("\n--- Revoking Authorization ---")
        logger.info(f"Revoking MM Entrypoint: {user.mm_entrypoint_address}")
        logger.info(f"For user address: {user.user_address}")

        revoke_tx_hash = await user.eip_7702_revoke()
        logger.info(f"Revocation transaction sent: {revoke_tx_hash}")
        logger.success("EIP-7702 authorization revoked!")
        logger.info("Your EOA is back to normal state")

        logger.info("\n" + "=" * 80)
        logger.success("Revocation completed successfully!")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"Error during EIP-7702 revocation: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())

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
from kuru_sdk_py.configs import KuruMMConfig
from kuru_sdk_py.configs import MarketConfig, market_config_from_market_address, initialize_kuru_mm_config


async def main():
    """
    Example script demonstrating User class deposit and withdraw operations
    for both base and quote tokens.
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

    # Create User instance with market config
    user = User(
        user_address=config.user_address,
        market_config=market_config,
        rpc_url=config.rpc_url,
        private_key=config.private_key,
    )

    logger.info("\n" + "=" * 80)
    logger.info("Starting Deposit and Withdraw Tests")
    logger.info("=" * 80)

    try:
        # Define test amounts in human-readable format
        # The User class will automatically convert these to the correct wei values
        # based on the token decimals from market_config
        base_deposit_amount = 10 # 0.1 base tokens
        quote_deposit_amount = 10  # 0.1 quote tokens

        # Test 1: Check initial balances
        logger.info("\n--- Test 1: Check Initial Balances ---")
        base_balance = await user.get_base_balance()
        quote_balance = await user.get_quote_balance()
        logger.info(f"Base token balance: {base_balance} wei")
        logger.info(f"Quote token balance: {quote_balance} wei")

        # Test 2: Check initial allowances
        logger.info("\n--- Test 2: Check Initial Allowances ---")
        base_allowance = await user.get_base_allowance()
        quote_allowance = await user.get_quote_allowance()
        logger.info(f"Base token allowance: {base_allowance} wei")
        logger.info(f"Quote token allowance: {quote_allowance} wei")

        # Test 3: Deposit base tokens
        logger.info("\n--- Test 3: Deposit Base Tokens ---")
        logger.info(f"Depositing {base_deposit_amount} base tokens...")
        base_deposit_tx = await user.deposit_base(base_deposit_amount, auto_approve=True)
        logger.info(f"Base deposit transaction hash: {base_deposit_tx}")
        logger.success("Base token deposit successful!")

        # Test 4: Deposit quote tokens
        logger.info("\n--- Test 4: Deposit Quote Tokens ---")
        logger.info(f"Depositing {quote_deposit_amount} quote tokens...")
        quote_deposit_tx = await user.deposit_quote(quote_deposit_amount, auto_approve=True)
        logger.info(f"Quote deposit transaction hash: {quote_deposit_tx}")
        logger.success("Quote token deposit successful!")

        # Test 5: Withdraw base tokens
        # logger.info("\n--- Test 5: Withdraw Base Tokens ---")
        # base_withdraw_amount = base_deposit_amount / 2  # Withdraw half of deposited amount
        # logger.info(f"Withdrawing {base_withdraw_amount} base tokens...")
        # base_withdraw_tx = await user.withdraw_base(base_withdraw_amount)
        # logger.info(f"Base withdraw transaction hash: {base_withdraw_tx}")
        # logger.success("Base token withdrawal successful!")

        # # Test 6: Withdraw quote tokens
        # logger.info("\n--- Test 6: Withdraw Quote Tokens ---")
        # quote_withdraw_amount = quote_deposit_amount / 2  # Withdraw half of deposited amount
        # logger.info(f"Withdrawing {quote_withdraw_amount} quote tokens...")
        # quote_withdraw_tx = await user.withdraw_quote(quote_withdraw_amount)
        # logger.info(f"Quote withdraw transaction hash: {quote_withdraw_tx}")
        # logger.success("Quote token withdrawal successful!")

        # Test 7: Check final balances
        logger.info("\n--- Test 7: Check Final Balances ---")
        final_base_balance = await user.get_base_balance()
        final_quote_balance = await user.get_quote_balance()
        logger.info(f"Final base token balance: {final_base_balance} wei")
        logger.info(f"Final quote token balance: {final_quote_balance} wei")

        logger.info("\n" + "=" * 80)
        logger.success("All tests completed successfully!")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"Error during deposit/withdraw tests: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())

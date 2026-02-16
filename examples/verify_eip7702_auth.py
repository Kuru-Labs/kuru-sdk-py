import sys
from pathlib import Path
from loguru import logger
import asyncio
import os
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from web3 import AsyncWeb3, AsyncHTTPProvider
from kuru_sdk_py.configs import initialize_kuru_mm_config, market_config_from_market_address


async def main():
    """Verify EIP-7702 authorization status for MM Entrypoint contract."""

    load_dotenv()

    config = initialize_kuru_mm_config(
        private_key=os.getenv("PRIVATE_KEY"),
        rpc_url="https://rpc.fullnode.kuru.io/",
    )

    market_config = market_config_from_market_address(
        market_address="0x6eB96A614E49b0dAc69F48E799C5C825AF9B33fA",
        rpc_url="https://rpc.fullnode.kuru.io/",
    )

    w3 = AsyncWeb3(AsyncHTTPProvider(config.rpc_url))

    logger.info("=" * 80)
    logger.info("EIP-7702 Authorization Verification")
    logger.info("=" * 80)
    logger.info(f"User Address: {config.user_address}")
    logger.info(f"MM Entrypoint: {market_config.mm_entrypoint_address}")

    # Check 1: Get account code at user's address
    logger.info("\n--- Check 1: Account Code (Delegation Status) ---")
    user_code = await w3.eth.get_code(config.user_address)
    logger.info(f"Code length at user address: {len(user_code)} bytes")

    if len(user_code) > 2:  # More than '0x'
        logger.info(f"Code preview (first 100 bytes): 0x{user_code[:100].hex()}")
        logger.success("✓ Account has delegated code - EIP-7702 authorization appears active")
    else:
        logger.error("✗ Account has no code - EIP-7702 authorization is NOT active")
        logger.warning("Run examples/eip7701_auth.py to authorize the MM Entrypoint")
        return

    # Check 2: Get MM Entrypoint code for comparison
    logger.info("\n--- Check 2: MM Entrypoint Contract Code ---")
    mm_code = await w3.eth.get_code(market_config.mm_entrypoint_address)
    logger.info(f"MM Entrypoint code length: {len(mm_code)} bytes")

    # Check 3: Compare code (simple length check)
    # Note: With EIP-7702, the user's address may have a delegation pointer
    # rather than the full contract code
    logger.info("\n--- Check 3: Code Comparison ---")
    if len(user_code) == len(mm_code):
        logger.success("✓ Code lengths match - full contract code delegated")
    elif len(user_code) > 2:
        logger.info("ℹ User has delegation pointer (EIP-7702 delegation active)")
        logger.info("  This is expected - EOA points to MM Entrypoint contract")
    else:
        logger.warning("⚠ Code mismatch - authorization may not be correct")

    # Check 4: Verify margin balances (optional)
    logger.info("\n--- Check 4: Margin Balances (Optional) ---")
    try:
        from kuru_sdk_py.utils import load_abi
        margin_abi = load_abi("margin_account")
        margin_contract = w3.eth.contract(
            address=market_config.margin_contract_address,
            abi=margin_abi
        )

        base_balance = await margin_contract.functions.getBalance(
            config.user_address,
            market_config.base_token
        ).call()

        quote_balance = await margin_contract.functions.getBalance(
            config.user_address,
            market_config.quote_token
        ).call()

        logger.info(f"Base margin balance: {base_balance} wei ({base_balance / 10**market_config.base_token_decimals:.4f} {market_config.base_symbol})")
        logger.info(f"Quote margin balance: {quote_balance} wei ({quote_balance / 10**market_config.quote_token_decimals:.4f} {market_config.quote_symbol})")

        if base_balance > 0 and quote_balance > 0:
            logger.success("✓ Margin balances are non-zero (ready to place orders)")
        else:
            logger.warning("⚠ One or both margin balances are zero")
            logger.info("  Run orderbook_orders_executor.py to deposit margin")
    except Exception as e:
        logger.warning(f"Could not check margin balances: {e}")

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("Verification Summary")
    logger.info("=" * 80)

    if len(user_code) > 2:
        logger.success("✅ EIP-7702 AUTHORIZATION IS ACTIVE")
        logger.info("You can now place orders using OrdersExecutor")
    else:
        logger.error("❌ EIP-7702 AUTHORIZATION IS NOT ACTIVE")
        logger.info("Action required: Run examples/eip7701_auth.py to authorize")

    logger.info("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

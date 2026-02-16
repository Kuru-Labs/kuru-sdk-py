"""
Configuration Examples for Kuru MM SDK

This file demonstrates all the different ways to configure the Kuru MM SDK
using the new ConfigManager system.

Run this file to see example output:
    python examples/config_examples.py
"""

import sys
from pathlib import Path
import os
from dotenv import load_dotenv

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))

from kuru_sdk_py.configs import (
    ConfigManager,
    ConfigPresets,
    TransactionConfig,
    WebSocketConfig,
    OrderExecutionConfig,
)
from loguru import logger


# ============================================================================
# Pattern 1: Simple - Use All Defaults (Most Common)
# ============================================================================

def example_1_simple_defaults():
    """
    Simplest approach - load everything from environment variables.

    Required .env variables:
    - PRIVATE_KEY
    - MARKET_ADDRESS (or pass directly)

    All other values use sensible defaults.
    """
    logger.info("=" * 80)
    logger.info("PATTERN 1: Simple Defaults")
    logger.info("=" * 80)

    load_dotenv()

    # These three lines are all you need!
    wallet_config = ConfigManager.load_wallet_config()
    connection_config = ConfigManager.load_connection_config()
    market_config = ConfigManager.load_market_config(
        market_address=os.getenv("MARKET_ADDRESS"),
        fetch_from_chain=True
    )

    logger.info(f"✓ Wallet: {wallet_config.user_address}")
    logger.info(f"✓ RPC: {connection_config.rpc_url}")
    logger.info(f"✓ Market: {market_config.market_address}")
    logger.info("✓ Transaction config: Using defaults (timeout=120s, poll_latency=0.4s)")
    logger.info("✓ WebSocket config: Using defaults (max_reconnect=5, heartbeat=30s)")
    logger.info("✓ Order execution: Using defaults (post_only=True, auto_approve=True)")
    logger.info("")


# ============================================================================
# Pattern 2: Custom Timeouts for Slow Network
# ============================================================================

def example_2_custom_timeouts():
    """
    Override transaction timeouts for slower networks.

    Use case: Your network is slow and transactions take longer to confirm.
    """
    logger.info("=" * 80)
    logger.info("PATTERN 2: Custom Timeouts for Slow Network")
    logger.info("=" * 80)

    load_dotenv()

    wallet_config = ConfigManager.load_wallet_config()
    connection_config = ConfigManager.load_connection_config()
    market_config = ConfigManager.load_market_config(
        market_address=os.getenv("MARKET_ADDRESS"),
        fetch_from_chain=True
    )

    # Override transaction config for slower network
    transaction_config = ConfigManager.load_transaction_config(
        timeout=300,  # 5 minutes instead of 2
        poll_latency=1.0,  # 1 second instead of 0.4s
    )

    logger.info(f"✓ Transaction timeout: {transaction_config.timeout}s (default: 120s)")
    logger.info(f"✓ Poll latency: {transaction_config.poll_latency}s (default: 0.4s)")
    logger.info("✓ Other configs: Using defaults")
    logger.info("")


# ============================================================================
# Pattern 3: Power User - Full Customization
# ============================================================================

def example_3_power_user():
    """
    Customize every aspect of configuration.

    Use case: You're a power user who wants fine-grained control over:
    - Custom RPC endpoints
    - Gas optimization parameters
    - Reconnection behavior
    - Order execution defaults
    """
    logger.info("=" * 80)
    logger.info("PATTERN 3: Power User - Full Customization")
    logger.info("=" * 80)

    load_dotenv()

    # Wallet config
    wallet_config = ConfigManager.load_wallet_config()

    # Custom premium RPC endpoints
    connection_config = ConfigManager.load_connection_config(
        rpc_url="https://premium-rpc.example.com",
        rpc_ws_url="wss://premium-ws.example.com",
        auto_env=False,  # Don't load from .env
    )

    # Market config
    market_config = ConfigManager.load_market_config(
        market_address=os.getenv("MARKET_ADDRESS"),
        fetch_from_chain=True,
        rpc_url=connection_config.rpc_url,  # Use custom RPC
    )

    # Aggressive transaction settings (fail fast)
    transaction_config = ConfigManager.load_transaction_config(
        timeout=60,  # 1 minute
        poll_latency=0.1,  # 100ms
        gas_adjustment_per_slot=7000,  # Custom for your RPC provider
        gas_buffer_multiplier=1.05,  # 5% buffer instead of 10%
    )

    # Aggressive reconnection (give up faster)
    websocket_config = ConfigManager.load_websocket_config(
        max_reconnect_attempts=3,
        reconnect_delay=0.5,
        heartbeat_interval=15.0,  # More frequent heartbeats
    )

    # Custom order execution (allow taker orders)
    order_execution_config = ConfigManager.load_order_execution_config(
        post_only=False,  # Allow taker orders
        auto_approve=False,  # Manual approval control
        use_access_list=True,  # Keep gas optimization
    )

    logger.info(f"✓ Custom RPC: {connection_config.rpc_url}")
    logger.info(f"✓ Transaction timeout: {transaction_config.timeout}s")
    logger.info(f"✓ Gas buffer: {transaction_config.gas_buffer_multiplier}x")
    logger.info(f"✓ Max reconnects: {websocket_config.max_reconnect_attempts}")
    logger.info(f"✓ Post-only: {order_execution_config.post_only}")
    logger.info("")


# ============================================================================
# Pattern 4: Using Presets
# ============================================================================

def example_4_presets():
    """
    Use pre-configured presets for common scenarios.

    Available presets:
    - conservative(): Longer timeouts, more retries (production)
    - aggressive(): Shorter timeouts, fewer retries (HFT)
    - testnet(): Optimized for slower testnets
    """
    logger.info("=" * 80)
    logger.info("PATTERN 4: Using Presets")
    logger.info("=" * 80)

    load_dotenv()

    wallet_config = ConfigManager.load_wallet_config()
    connection_config = ConfigManager.load_connection_config()
    market_config = ConfigManager.load_market_config(
        market_address=os.getenv("MARKET_ADDRESS"),
        fetch_from_chain=True
    )

    # Get conservative preset for production
    preset = ConfigPresets.conservative()

    logger.info("✓ Using CONSERVATIVE preset:")
    logger.info(f"  - Transaction timeout: {preset['transaction_config'].timeout}s")
    logger.info(f"  - Poll latency: {preset['transaction_config'].poll_latency}s")
    logger.info(f"  - Max reconnects: {preset['websocket_config'].max_reconnect_attempts}")
    logger.info(f"  - Reconnect delay: {preset['websocket_config'].reconnect_delay}s")
    logger.info("")

    # You would use it like:
    # client = await KuruClient.create(
    #     market_config=market_config,
    #     connection_config=connection_config,
    #     wallet_config=wallet_config,
    #     **preset  # Applies preset configs
    # )


# ============================================================================
# Pattern 5: One-Liner Convenience
# ============================================================================

def example_5_one_liner():
    """
    Load all configs at once with minimal code.

    Use case: You just want it to work with minimal configuration.
    """
    logger.info("=" * 80)
    logger.info("PATTERN 5: One-Liner Convenience")
    logger.info("=" * 80)

    load_dotenv()

    # Load ALL configs at once
    configs = ConfigManager.load_all_configs(
        market_address=os.getenv("MARKET_ADDRESS"),
        fetch_from_chain=True,
    )

    logger.info("✓ Loaded all configs in one call:")
    logger.info(f"  - wallet_config: {configs['wallet_config'].user_address}")
    logger.info(f"  - connection_config: {configs['connection_config'].rpc_url}")
    logger.info(f"  - market_config: {configs['market_config'].market_address}")
    logger.info(f"  - transaction_config: timeout={configs['transaction_config'].timeout}s")
    logger.info(f"  - websocket_config: max_reconnect={configs['websocket_config'].max_reconnect_attempts}")
    logger.info(f"  - order_execution_config: post_only={configs['order_execution_config'].post_only}")
    logger.info(f"  - cache_config: pending_tx_ttl={configs['cache_config'].pending_tx_ttl}s")
    logger.info("")

    # You would use it like:
    # client = await KuruClient.create(**configs)


# ============================================================================
# Pattern 6: Environment Variable Overrides
# ============================================================================

def example_6_env_overrides():
    """
    Set configuration via environment variables.

    Use case: Different configs for dev/staging/production without code changes.

    Supported environment variables:
    - PRIVATE_KEY (required)
    - RPC_URL, RPC_WS_URL, KURU_WS_URL, KURU_API_URL
    - MARKET_ADDRESS
    - KURU_TRANSACTION_TIMEOUT
    - KURU_POLL_LATENCY
    - KURU_MAX_RECONNECT_ATTEMPTS
    - KURU_RECONNECT_DELAY
    - KURU_POST_ONLY (true/false)
    - KURU_AUTO_APPROVE (true/false)
    - KURU_USE_ACCESS_LIST (true/false)
    """
    logger.info("=" * 80)
    logger.info("PATTERN 6: Environment Variable Overrides")
    logger.info("=" * 80)

    # Set some env vars (in real usage, these would be in .env file)
    os.environ["KURU_TRANSACTION_TIMEOUT"] = "180"
    os.environ["KURU_MAX_RECONNECT_ATTEMPTS"] = "10"
    os.environ["KURU_POST_ONLY"] = "false"

    load_dotenv()

    # Load configs - they'll automatically pick up the env vars
    wallet_config = ConfigManager.load_wallet_config()
    connection_config = ConfigManager.load_connection_config()
    transaction_config = ConfigManager.load_transaction_config()  # Will use KURU_TRANSACTION_TIMEOUT
    websocket_config = ConfigManager.load_websocket_config()  # Will use KURU_MAX_RECONNECT_ATTEMPTS
    order_execution_config = ConfigManager.load_order_execution_config()  # Will use KURU_POST_ONLY

    logger.info("✓ Loaded from environment variables:")
    logger.info(f"  - Transaction timeout: {transaction_config.timeout}s (from KURU_TRANSACTION_TIMEOUT)")
    logger.info(f"  - Max reconnects: {websocket_config.max_reconnect_attempts} (from KURU_MAX_RECONNECT_ATTEMPTS)")
    logger.info(f"  - Post-only: {order_execution_config.post_only} (from KURU_POST_ONLY)")
    logger.info("")

    # Clean up
    del os.environ["KURU_TRANSACTION_TIMEOUT"]
    del os.environ["KURU_MAX_RECONNECT_ATTEMPTS"]
    del os.environ["KURU_POST_ONLY"]


# ============================================================================
# Pattern 7: Per-Method Overrides
# ============================================================================

def example_7_per_method_overrides():
    """
    Set default behavior in config, but override per method call.

    Use case: You want conservative defaults but need to be aggressive
    for specific urgent orders.
    """
    logger.info("=" * 80)
    logger.info("PATTERN 7: Per-Method Overrides")
    logger.info("=" * 80)

    load_dotenv()

    # Set conservative defaults
    order_execution_config = ConfigManager.load_order_execution_config(
        post_only=True,  # Default: maker-only
    )

    logger.info("✓ Config defaults:")
    logger.info(f"  - post_only: {order_execution_config.post_only}")
    logger.info("")
    logger.info("✓ Method call examples:")
    logger.info("  - await client.place_orders(regular_orders)")
    logger.info("    → Uses config default (post_only=True)")
    logger.info("")
    logger.info("  - await client.place_orders(urgent_orders, post_only=False)")
    logger.info("    → Overrides to allow taker orders for this specific call")
    logger.info("")
    logger.info("Config provides sensible defaults, but you can override anytime!")
    logger.info("")


# ============================================================================
# Pattern 8: Testnet Configuration
# ============================================================================

def example_8_testnet():
    """
    Optimized configuration for testnet deployment.

    Use case: Testing on a testnet with slower block times.
    """
    logger.info("=" * 80)
    logger.info("PATTERN 8: Testnet Configuration")
    logger.info("=" * 80)

    load_dotenv()

    wallet_config = ConfigManager.load_wallet_config()

    # Testnet RPC endpoints
    connection_config = ConfigManager.load_connection_config(
        rpc_url="https://testnet-rpc.example.com",
        rpc_ws_url="wss://testnet-ws.example.com",
    )

    market_config = ConfigManager.load_market_config(
        market_address=os.getenv("TESTNET_MARKET_ADDRESS", "0x..."),
        fetch_from_chain=True,
        rpc_url=connection_config.rpc_url,
    )

    # Use testnet preset
    preset = ConfigPresets.testnet()

    logger.info("✓ Testnet configuration:")
    logger.info(f"  - RPC: {connection_config.rpc_url}")
    logger.info(f"  - Transaction timeout: {preset['transaction_config'].timeout}s (longer for testnet)")
    logger.info(f"  - Poll latency: {preset['transaction_config'].poll_latency}s (slower sync)")
    logger.info("")


# ============================================================================
# Main - Run All Examples
# ============================================================================

def main():
    """Run all configuration examples."""
    logger.info("")
    logger.info("╔" + "═" * 78 + "╗")
    logger.info("║" + " " * 20 + "KURU MM SDK - Configuration Examples" + " " * 21 + "║")
    logger.info("╚" + "═" * 78 + "╝")
    logger.info("")

    # Note: These examples use environment variables
    # Create a .env file with at minimum:
    # PRIVATE_KEY=0x...
    # MARKET_ADDRESS=0x...

    try:
        example_1_simple_defaults()
        example_2_custom_timeouts()
        example_3_power_user()
        example_4_presets()
        example_5_one_liner()
        example_6_env_overrides()
        example_7_per_method_overrides()
        example_8_testnet()

        logger.info("=" * 80)
        logger.info("✓ All examples completed successfully!")
        logger.info("=" * 80)
        logger.info("")
        logger.info("Next steps:")
        logger.info("1. Copy the pattern that fits your use case")
        logger.info("2. Set up your .env file with required variables")
        logger.info("3. Use the configs with: await KuruClient.create(...)")
        logger.info("")

    except Exception as e:
        logger.error(f"Error running examples: {e}")
        logger.info("")
        logger.info("Note: Some examples require environment variables to be set.")
        logger.info("Create a .env file with:")
        logger.info("  PRIVATE_KEY=0x...")
        logger.info("  MARKET_ADDRESS=0x...")
        logger.info("")


if __name__ == "__main__":
    main()

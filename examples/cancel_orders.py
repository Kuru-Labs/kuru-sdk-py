"""
Example: Cancel Orders with Access List Comparison

This example demonstrates:
- Fetching active orders from the Kuru API
- Building manual access lists using build_access_list_for_cancel_only()
- Fetching RPC-generated access lists using eth_createAccessList
- Comparing gas estimates for 3 scenarios: manual AL, RPC AL, no AL
- Executing transaction with chosen access list mode
- Saving comparison data to JSON for analysis

Usage:
    python examples/cancel_orders.py --mode manual   # Use manual access list (default)
    python examples/cancel_orders.py --mode rpc      # Use RPC access list
    python examples/cancel_orders.py --mode none     # Use no access list
"""

import sys
from pathlib import Path
import asyncio
import signal
import os
import json
import argparse
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.client import KuruClient
from src.manager.order import Order, OrderType, OrderSide
from src.configs import initialize_kuru_mm_config, market_config_from_market_address
from src.transaction.access_list import build_access_list_for_cancel_only


# Simple callback that prints processed orders
async def print_order_callback(order: Order):
    """Simple callback that prints received orders from the queue."""
    logger.success(f"Received order from queue: {order}")

async def main():
    """Main function that tests different access list scenarios."""

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Cancel orders with different access list scenarios')
    parser.add_argument(
        '--mode',
        choices=['manual', 'rpc', 'none'],
        default='manual',
        help='Access list mode: manual (default), rpc, or none'
    )
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    # Initialize configs
    kuru_config = initialize_kuru_mm_config(
        private_key=os.getenv("PRIVATE_KEY"),
        rpc_url=os.getenv("RPC_URL", "https://rpc.fullnode.kuru.io/"),
        rpc_ws_url=os.getenv("RPC_WS_URL", "wss://rpc.fullnode.kuru.io/")
    )

    market_config = market_config_from_market_address(
        market_address=os.getenv("MARKET_ADDRESS", "0x6eB96A614E49b0dAc69F48E799C5C825AF9B33fA"),
        rpc_url=os.getenv("RPC_URL", "https://rpc.fullnode.kuru.io/")
    )

    # Create client
    logger.info("Creating KuruClient...")
    client = await KuruClient.create(market_config, kuru_config)

    # Set callback to print orders
    client.set_order_callback(print_order_callback)

    # Setup signal handler for graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig, frame):
        """Handle SIGINT (Ctrl+C) gracefully."""
        logger.warning("Received interrupt signal, shutting down gracefully...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)

    try:
        # Start client
        logger.info("Starting client...")
        await client.start()

        # === STEP 1: Fetch active orders ===
        logger.info("Fetching active orders from API...")
        orders = client.user.get_active_orders()

        if not orders:
            logger.warning("No active orders to cancel")
            return

        logger.info(f"Found {len(orders)} active orders to cancel")

        # Extract order metadata for access list building
        orders_to_cancel = []
        order_ids = []
        for order in orders:
            order_id = order["orderid"]
            price = int(order["price"])  # Price in precision units
            is_buy = order["isbuy"]
            orders_to_cancel.append((order_id, price, is_buy))
            order_ids.append(order_id)

        logger.info(f"Order IDs to cancel: {order_ids}")

        # === STEP 2: Build contract function call ===
        function_call = client.executor.orderbook_contract.functions.batchCancelOrders(order_ids)

        # === STEP 3: Build manual access list ===
        logger.info("Building manual access list using build_access_list_for_cancel_only()...")
        manual_access_list = build_access_list_for_cancel_only(
            user_address=client.executor.user_address,
            orderbook_address=client.executor.order_book_address,
            margin_account_address=client.executor.market_config.margin_contract_address,
            base_token_address=client.executor.market_config.base_token,
            quote_token_address=client.executor.market_config.quote_token,
            orderbook_implementation=client.executor.market_config.orderbook_implementation,
            margin_account_implementation=client.executor.market_config.margin_account_implementation,
            orders_to_cancel=orders_to_cancel,
        )
        logger.success(f"Manual access list built with {len(manual_access_list)} addresses")

        # === STEP 4: Fetch RPC-generated access list ===
        logger.info("Fetching RPC-generated access list using eth_createAccessList...")
        try:
            # Get current nonce and gas price
            nonce = await client.executor.w3.eth.get_transaction_count(client.executor.user_address, 'latest')
            gas_price = await client.executor.w3.eth.gas_price

            # Build transaction parameters for RPC call (need to build the tx to get the data)
            tx_params_for_build = {
                "from": client.executor.user_address,
                "nonce": nonce,
                "gasPrice": gas_price,
                "value": 0,
            }

            # Build the transaction to get the encoded data
            built_tx = await function_call.build_transaction(tx_params_for_build)

            # Create simplified params for eth_createAccessList
            tx_params = {
                "from": client.executor.user_address,
                "to": client.executor.order_book_address,
                "data": built_tx["data"],
                "gasPrice": gas_price,
            }

            # Call eth_createAccessList
            rpc_response = await client.executor.w3.eth.create_access_list(tx_params)

            # Convert AttributeDict to regular dict for JSON serialization
            rpc_access_list_raw = rpc_response['accessList']
            rpc_access_list = [
                {
                    'address': entry['address'],
                    'storageKeys': list(entry['storageKeys'])
                }
                for entry in rpc_access_list_raw
            ]
            logger.success(f"RPC access list fetched with {len(rpc_access_list)} addresses")
        except Exception as e:
            logger.error(f"Failed to fetch RPC access list: {e}")
            rpc_access_list = None

        # === STEP 5: Print both access lists ===
        logger.info("\n" + "="*80)
        logger.info("MANUAL ACCESS LIST (from build_access_list_for_cancel_only):")
        logger.info("="*80)
        print(json.dumps(manual_access_list, indent=2))

        if rpc_access_list:
            logger.info("\n" + "="*80)
            logger.info("RPC ACCESS LIST (from eth_createAccessList):")
            logger.info("="*80)
            print(json.dumps(rpc_access_list, indent=2))

        # === STEP 6: Estimate gas for all 3 scenarios ===
        logger.info("\n" + "="*80)
        logger.info("GAS ESTIMATION FOR ALL SCENARIOS:")
        logger.info("="*80)

        gas_estimates = {}

        # Get nonce and gas price for building transactions
        nonce = await client.executor.w3.eth.get_transaction_count(client.executor.user_address, 'latest')
        gas_price = await client.executor.w3.eth.gas_price

        # Scenario 1: Manual access list
        try:
            tx_manual = await function_call.build_transaction({
                "from": client.executor.user_address,
                "nonce": nonce,
                "gasPrice": gas_price,
                "value": 0,
                "accessList": manual_access_list,
            })
            gas_manual = await client.executor.w3.eth.estimate_gas(tx_manual)
            gas_estimates['manual'] = gas_manual
            logger.info(f"✅ Manual access list:  {gas_manual:,} gas")
        except Exception as e:
            logger.error(f"❌ Manual access list estimation failed: {e}")
            gas_estimates['manual'] = None

        # Scenario 2: RPC access list
        if rpc_access_list:
            try:
                tx_rpc = await function_call.build_transaction({
                    "from": client.executor.user_address,
                    "nonce": nonce,
                    "gasPrice": gas_price,
                    "value": 0,
                    "accessList": rpc_access_list,
                })
                gas_rpc = await client.executor.w3.eth.estimate_gas(tx_rpc)
                gas_estimates['rpc'] = gas_rpc
                logger.info(f"✅ RPC access list:      {gas_rpc:,} gas")
            except Exception as e:
                logger.error(f"❌ RPC access list estimation failed: {e}")
                gas_estimates['rpc'] = None

        # Scenario 3: No access list
        try:
            tx_none = await function_call.build_transaction({
                "from": client.executor.user_address,
                "nonce": nonce,
                "gasPrice": gas_price,
                "value": 0,
            })
            gas_none = await client.executor.w3.eth.estimate_gas(tx_none)
            gas_estimates['none'] = gas_none
            logger.info(f"✅ No access list:       {gas_none:,} gas")
        except Exception as e:
            logger.error(f"❌ No access list estimation failed: {e}")
            gas_estimates['none'] = None

        # Calculate savings
        logger.info("\n" + "="*80)
        logger.info("GAS SAVINGS ANALYSIS:")
        logger.info("="*80)
        if gas_estimates['none'] and gas_estimates['manual']:
            savings_manual = gas_estimates['none'] - gas_estimates['manual']
            percent_manual = (savings_manual / gas_estimates['none']) * 100
            logger.info(f"Manual vs No AL:  {savings_manual:,} gas saved ({percent_manual:.2f}%)")
        if gas_estimates['none'] and gas_estimates['rpc']:
            savings_rpc = gas_estimates['none'] - gas_estimates['rpc']
            percent_rpc = (savings_rpc / gas_estimates['none']) * 100
            logger.info(f"RPC vs No AL:     {savings_rpc:,} gas saved ({percent_rpc:.2f}%)")
        if gas_estimates['manual'] and gas_estimates['rpc']:
            diff = gas_estimates['manual'] - gas_estimates['rpc']
            if diff > 0:
                logger.info(f"Manual vs RPC:    {diff:,} gas MORE (RPC is more efficient)")
            elif diff < 0:
                logger.info(f"Manual vs RPC:    {abs(diff):,} gas LESS (Manual is more efficient)")
            else:
                logger.info(f"Manual vs RPC:    IDENTICAL")

        # === STEP 7: Calculate statistics ===
        manual_storage_keys = sum(len(entry.get('storageKeys', [])) for entry in manual_access_list)
        rpc_storage_keys = sum(len(entry.get('storageKeys', [])) for entry in rpc_access_list) if rpc_access_list else 0

        statistics = {
            "manual_addresses": len(manual_access_list),
            "manual_storage_keys": manual_storage_keys,
            "rpc_addresses": len(rpc_access_list) if rpc_access_list else 0,
            "rpc_storage_keys": rpc_storage_keys,
            "gas_estimates": {k: int(v) if v else None for k, v in gas_estimates.items()},
        }

        logger.info("\n" + "="*80)
        logger.info("STATISTICS:")
        logger.info("="*80)
        logger.info(f"Manual: {statistics['manual_addresses']} addresses, {statistics['manual_storage_keys']} storage keys")
        if rpc_access_list:
            logger.info(f"RPC: {statistics['rpc_addresses']} addresses, {statistics['rpc_storage_keys']} storage keys")

        # === STEP 8: Save to JSON file ===
        comparison_data = {
            "timestamp": datetime.now().isoformat(),
            "execution_mode": args.mode,
            "orders_cancelled": order_ids,
            "manually_built_access_list": manual_access_list,
            "rpc_generated_access_list": rpc_access_list,
            "statistics": statistics,
        }

        json_file_path = "access_lists_comparison.json"
        with open(json_file_path, 'w') as f:
            json.dump(comparison_data, f, indent=2)
        logger.success(f"Access lists comparison saved to {json_file_path}")

        # === STEP 9: Execute transaction with chosen mode ===
        logger.info("\n" + "="*80)
        logger.info(f"EXECUTING TRANSACTION WITH MODE: {args.mode.upper()}")
        logger.info("="*80)

        # Select access list based on mode
        if args.mode == 'manual':
            chosen_access_list = manual_access_list
            logger.info("Using MANUAL access list")
        elif args.mode == 'rpc':
            chosen_access_list = rpc_access_list
            logger.info("Using RPC access list")
        else:  # none
            chosen_access_list = None
            logger.info("Using NO access list")

        # Execute transaction
        txhash = await client.executor._send_transaction(function_call, access_list=chosen_access_list)
        logger.success(f"Transaction sent: {txhash}")

        # Wait for confirmation
        logger.info("Waiting for transaction confirmation...")
        receipt = await client.executor._wait_for_transaction_receipt(txhash)
        actual_gas_used = receipt['gasUsed']
        logger.success(f"Transaction confirmed: {txhash}")
        logger.success(f"Actual gas used: {actual_gas_used:,}")

        # Compare estimated vs actual
        estimated_gas = gas_estimates.get(args.mode)
        if estimated_gas:
            gas_diff = actual_gas_used - estimated_gas
            logger.info(f"Estimated: {estimated_gas:,}, Actual: {actual_gas_used:,}, Diff: {gas_diff:,}")

        # === STEP 10: Update JSON with transaction data ===
        comparison_data["transaction_hash"] = txhash
        comparison_data["actual_gas_used"] = int(actual_gas_used)
        comparison_data["estimated_gas"] = int(estimated_gas) if estimated_gas else None
        with open(json_file_path, 'w') as f:
            json.dump(comparison_data, f, indent=2)
        logger.success(f"Updated {json_file_path} with transaction data")

        logger.success("All active orders cancelled successfully!")

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Gracefully stop
        await client.stop()
        logger.success("Client stopped")


if __name__ == "__main__":
    asyncio.run(main())

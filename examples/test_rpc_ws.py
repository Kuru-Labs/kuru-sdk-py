"""
Simple script to connect to a WebSocket RPC and subscribe to contract events.
"""

import asyncio
import json
from web3 import AsyncWeb3
from web3.providers import WebSocketProvider


async def subscribe_to_events(
    rpc_url: str,
    contract_address: str,
    topic: str,
):
    """
    Subscribe to events from a contract using WebSocket.

    Args:
        rpc_url: WebSocket RPC URL (e.g., wss://mainnet.infura.io/ws/v3/YOUR_KEY)
        contract_address: The contract address to monitor
        topic: The event topic (keccak256 hash of the event signature)
    """
    async with AsyncWeb3(WebSocketProvider(rpc_url)) as w3:
        # Verify connection
        chain_id = await w3.eth.chain_id
        print(f"Connected to chain ID: {chain_id}")

        # Create subscription filter
        subscription = await w3.eth.subscribe(
            "logs",
            {
                "address": contract_address,
                "topics": [topic],
            },
        )
        print(f"Subscribed with ID: {subscription}")
        print(f"Listening for events from {contract_address}...")
        print(f"Topic: {topic}")
        print("-" * 60)

        # Listen for events
        async for response in w3.socket.process_subscriptions():
            result = response.get("result")
            if result:
                print(f"\nNew Event Received:")
                print(f"  Block: {result.get('blockNumber')}")
                print(f"  Tx Hash: {result.get('transactionHash')}")
                print(f"  Log Index: {result.get('logIndex')}")
                print(f"  Data: {result.get('data')}")
                print(f"  Topics: {result.get('topics')}")
                print("-" * 60)


async def main():
    # Configuration - Replace with your values
    RPC_URL = (
        "wss://rpc.monad.xyz/"  # e.g., wss://mainnet.infura.io/ws/v3/YOUR_KEY
    )
    CONTRACT_ADDRESS = "0x0AfE9FE5FB3357d5EaDe01936E8106cC00354c61"
    EVENT_TOPIC = "0xbb5727df79c4917b83df1b9d289621e496b81d656364d1887ec7ece6e9f45f90"  # keccak256 hash of event signature

    try:
        await subscribe_to_events(RPC_URL, CONTRACT_ADDRESS, EVENT_TOPIC)
    except KeyboardInterrupt:
        print("\nStopping subscription...")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())

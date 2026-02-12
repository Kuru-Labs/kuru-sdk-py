"""Example usage of AsyncMemCache."""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path to import src module
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.async_mem_cache import AsyncMemCache


async def handle_expiry(key: str, value: dict):
    """Custom callback function to handle expired keys."""
    print(f"⏰ Key '{key}' expired with value: {value}")
    # You can add custom cleanup logic here
    # For example: save to database, log to file, trigger alerts, etc.


async def main():
    """Demonstrate AsyncMemCache usage."""

    print("=== AsyncMemCache Usage Example ===\n")

    # Create a cache with 5 second TTL
    cache = AsyncMemCache(
        ttl=5.0,  # Items expire after 5 seconds of no access
        on_expire=handle_expiry,  # Callback when items expire
        check_interval=1.0,  # Check for expired items every 1 second
    )

    # Use context manager for automatic start/stop
    async with cache:
        print("1. Setting some cache entries...")
        await cache.set("order_123", {"symbol": "BTC/USD", "price": 50000, "size": 0.5})
        await cache.set("order_456", {"symbol": "ETH/USD", "price": 3000, "size": 2.0})
        await cache.set("order_789", {"symbol": "SOL/USD", "price": 100, "size": 10.0})
        print("   ✓ Three orders cached\n")

        # Retrieve cached values
        print("2. Retrieving cached values...")
        order = await cache.get("order_123")
        print(f"   Order 123: {order}\n")

        # Check if key exists
        print("3. Checking if keys exist...")
        exists = await cache.has("order_456")
        print(f"   Order 456 exists: {exists}")
        exists = await cache.has("order_999")
        print(f"   Order 999 exists: {exists}\n")

        # Delete a key manually
        print("4. Manually deleting order_789...")
        deleted = await cache.delete("order_789")
        print(f"   Deleted: {deleted}")
        print("   (No expiry callback triggered for manual deletes)\n")

        # Demonstrate TTL extension on access
        print("5. Demonstrating TTL extension...")
        print("   Accessing order_123 every 3 seconds to keep it alive...")
        for i in range(3):
            await asyncio.sleep(3)
            order = await cache.get("order_123")
            print(f"   Access {i+1}: Still cached - {order}")
        print("   (TTL was extended on each access)\n")

        # Let order_456 expire naturally
        print("6. Letting order_456 expire naturally...")
        print("   Not accessing it for 6 seconds...")
        await asyncio.sleep(6)
        print("   (Expiry callback should have been triggered)\n")

        # Verify it's gone
        order = await cache.get("order_456")
        print(f"   Order 456 after expiry: {order}\n")

        # Clear all remaining entries
        print("7. Clearing all remaining entries...")
        await cache.clear()
        print("   ✓ Cache cleared (no callbacks triggered)\n")

        # Add new entries to demonstrate context manager cleanup
        print("8. Adding entries before context exit...")
        await cache.set("temp_order", {"symbol": "DOGE/USD", "price": 0.1})
        print("   ✓ Temporary order added\n")

    print("=== Context exited, cache stopped ===")
    print("\nExample completed!")


if __name__ == "__main__":
    asyncio.run(main())

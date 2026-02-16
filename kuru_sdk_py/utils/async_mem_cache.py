"""Async in-memory cache with TTL and custom expiry callbacks."""

import asyncio
import time
from typing import Any, Callable, Dict, Optional


class AsyncMemCache:
    """
    An async in-memory cache with global TTL that extends expiration on access
    and triggers custom async callbacks when keys expire.

    Features:
    - Global TTL for all cached items
    - Lazy expiration: TTL extends on each access
    - Custom async callbacks on natural expiration
    - Thread-safe operations with asyncio locks
    - Context manager support for lifecycle management

    Example:
        async def handle_expiry(key: str, value: dict):
            print(f"Key {key} expired with value {value}")

        cache = AsyncMemCache(ttl=60.0, on_expire=handle_expiry)
        async with cache:
            await cache.set("order_123", {"price": 100, "size": 10})
            data = await cache.get("order_123")  # Extends TTL
            # After 60s of no access, handle_expiry is called
    """

    def __init__(
        self,
        ttl: float,
        on_expire: Optional[Callable[[str, dict], Any]] = None,
        check_interval: float = 1.0,
    ):
        """
        Initialize the async memory cache.

        Args:
            ttl: Time-to-live in seconds for cached items (global for all keys)
            on_expire: Optional async callback function to invoke when a key expires naturally.
                      Receives (key: str, value: dict) as arguments.
                      If None, expired items are silently removed.
            check_interval: How often to check for expired keys in seconds (default: 1.0)
        """
        self._ttl = ttl
        self._on_expire = on_expire
        self._check_interval = check_interval

        # Storage for cached data
        self._cache: Dict[str, dict] = {}

        # Storage for expiration timestamps
        self._expiry_times: Dict[str, float] = {}

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        # Background monitor task
        self._monitor_task: Optional[asyncio.Task] = None

        # Flag to control monitor loop
        self._running = False

    async def set(self, key: str, value: dict) -> None:
        """
        Store a dictionary in the cache with the global TTL.

        If the key already exists, it will be overwritten and the TTL will be reset.

        Args:
            key: The cache key
            value: The dictionary value to store
        """
        async with self._lock:
            self._cache[key] = value
            self._expiry_times[key] = time.time() + self._ttl

    async def get(self, key: str) -> Optional[dict]:
        """
        Retrieve a dictionary from the cache.

        If the key exists, its TTL will be extended (lazy expiration).

        Args:
            key: The cache key to retrieve

        Returns:
            The cached dictionary if found, None otherwise
        """
        async with self._lock:
            if key in self._cache:
                # Extend TTL on access
                self._expiry_times[key] = time.time() + self._ttl
                return self._cache[key]
            return None

    async def delete(self, key: str) -> bool:
        """
        Manually remove a dictionary from the cache.

        Args:
            key: The cache key to delete

        Returns:
            True if the key was deleted, False if it didn't exist
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                del self._expiry_times[key]
                return True
            return False

    async def has(self, key: str) -> bool:
        """
        Check if a key exists in the cache.

        If the key exists, its TTL will be extended (lazy expiration).

        Args:
            key: The cache key to check

        Returns:
            True if the key exists, False otherwise
        """
        async with self._lock:
            if key in self._cache:
                # Extend TTL on access
                self._expiry_times[key] = time.time() + self._ttl
                return True
            return False

    async def clear(self) -> None:
        """
        Clear all cached items.

        This removes all keys and their expiration times from the cache.
        No expiry callbacks will be triggered.
        """
        async with self._lock:
            self._cache.clear()
            self._expiry_times.clear()

    async def _monitor_expiry(self) -> None:
        """
        Background task that monitors for expired keys and triggers callbacks.

        This runs continuously while the cache is active, checking for expired
        keys at regular intervals defined by check_interval.

        When a key expires naturally (without being accessed), the on_expire
        callback is invoked with the key and its value.
        """
        while self._running:
            try:
                current_time = time.time()
                expired_items = []

                # Find expired keys
                async with self._lock:
                    for key, expiry_time in list(self._expiry_times.items()):
                        if current_time >= expiry_time:
                            # Key has expired
                            if key in self._cache:
                                expired_items.append((key, self._cache[key]))
                                del self._cache[key]
                                del self._expiry_times[key]

                # Invoke callbacks outside the lock to avoid blocking
                if self._on_expire is not None:
                    for key, value in expired_items:
                        try:
                            await self._on_expire(key, value)
                        except Exception as e:
                            # Log error but continue processing other expired keys
                            print(f"Error in expiry callback for key {key}: {e}")

                # Wait before next check
                await asyncio.sleep(self._check_interval)

            except asyncio.CancelledError:
                # Task was cancelled, exit gracefully
                break
            except Exception as e:
                # Unexpected error, log and continue
                print(f"Error in expiry monitor: {e}")
                await asyncio.sleep(self._check_interval)

    async def start(self) -> None:
        """
        Start the background expiration monitor.

        This starts the background task that monitors for expired keys.
        Should be called before using the cache.

        Raises:
            RuntimeError: If the cache is already running
        """
        if self._running:
            raise RuntimeError("Cache is already running")

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_expiry())

    async def stop(self) -> None:
        """
        Stop the background expiration monitor and cleanup.

        This stops the background task and waits for it to complete.
        Should be called when done using the cache.
        """
        if not self._running:
            return

        self._running = False

        if self._monitor_task:
            # Cancel the monitor task
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    async def __aenter__(self):
        """
        Async context manager entry.

        Automatically starts the cache when entering the context.

        Returns:
            Self for use in the context
        """
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Async context manager exit.

        Automatically stops the cache when exiting the context.

        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred

        Returns:
            False to propagate any exceptions
        """
        await self.stop()
        return False

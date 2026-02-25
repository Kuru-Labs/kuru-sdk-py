"""Tests for AsyncMemCache functionality."""

import asyncio
import pytest
from kuru_sdk_py.utils.async_mem_cache import AsyncMemCache


class TestAsyncMemCache:
    """Test suite for AsyncMemCache."""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test cache initialization."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=1.0, on_expire=on_expire)

        assert cache._ttl == 1.0
        assert cache._on_expire == on_expire
        assert cache._running is False
        assert len(cache._cache) == 0
        assert len(cache._expiry_times) == 0

    @pytest.mark.asyncio
    async def test_basic_set_and_get(self):
        """Test basic set and get operations."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=10.0, on_expire=on_expire)
        async with cache:
            # Set a value
            await cache.set("key1", {"data": "value1"})

            # Get the value
            result = await cache.get("key1")
            assert result == {"data": "value1"}

            # Get non-existent key
            result = await cache.get("key_not_exists")
            assert result is None

    @pytest.mark.asyncio
    async def test_set_overwrites_existing_key(self):
        """Test that set overwrites existing keys."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=10.0, on_expire=on_expire)
        async with cache:
            await cache.set("key1", {"data": "value1"})
            await cache.set("key1", {"data": "value2"})

            result = await cache.get("key1")
            assert result == {"data": "value2"}

    @pytest.mark.asyncio
    async def test_delete(self):
        """Test delete operation."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=10.0, on_expire=on_expire)
        async with cache:
            # Set and delete
            await cache.set("key1", {"data": "value1"})
            result = await cache.delete("key1")
            assert result is True

            # Key should not exist
            result = await cache.get("key1")
            assert result is None

            # Delete non-existent key
            result = await cache.delete("key_not_exists")
            assert result is False

    @pytest.mark.asyncio
    async def test_has(self):
        """Test has operation."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=10.0, on_expire=on_expire)
        async with cache:
            # Check non-existent key
            result = await cache.has("key1")
            assert result is False

            # Set and check
            await cache.set("key1", {"data": "value1"})
            result = await cache.has("key1")
            assert result is True

    @pytest.mark.asyncio
    async def test_clear(self):
        """Test clear operation."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=10.0, on_expire=on_expire)
        async with cache:
            # Set multiple keys
            await cache.set("key1", {"data": "value1"})
            await cache.set("key2", {"data": "value2"})
            await cache.set("key3", {"data": "value3"})

            # Clear all
            await cache.clear()

            # All keys should be gone
            assert await cache.has("key1") is False
            assert await cache.has("key2") is False
            assert await cache.has("key3") is False

            # No callbacks should have been triggered
            assert len(callback_called) == 0

    @pytest.mark.asyncio
    async def test_ttl_extension_on_get(self):
        """Test that get extends TTL."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        # Short TTL and fast check interval for testing
        cache = AsyncMemCache(ttl=0.5, on_expire=on_expire, check_interval=0.1)
        async with cache:
            await cache.set("key1", {"data": "value1"})

            # Access the key multiple times to extend TTL
            for _ in range(5):
                await asyncio.sleep(0.3)  # Sleep less than TTL
                result = await cache.get("key1")
                assert result == {"data": "value1"}

            # Total time elapsed: ~1.5 seconds
            # Without TTL extension, key would have expired
            # With TTL extension, key should still exist
            assert await cache.has("key1") is True
            assert len(callback_called) == 0

    @pytest.mark.asyncio
    async def test_ttl_extension_on_has(self):
        """Test that has extends TTL."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        # Short TTL and fast check interval for testing
        cache = AsyncMemCache(ttl=0.5, on_expire=on_expire, check_interval=0.1)
        async with cache:
            await cache.set("key1", {"data": "value1"})

            # Check the key multiple times to extend TTL
            for _ in range(5):
                await asyncio.sleep(0.3)  # Sleep less than TTL
                exists = await cache.has("key1")
                assert exists is True

            # Key should still exist due to TTL extension
            assert len(callback_called) == 0

    @pytest.mark.asyncio
    async def test_expiry_callback_triggered(self):
        """Test that expiry callback is triggered when key expires."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        # Short TTL for testing
        cache = AsyncMemCache(ttl=0.3, on_expire=on_expire, check_interval=0.1)
        async with cache:
            await cache.set("key1", {"data": "value1"})

            # Wait for expiry
            await asyncio.sleep(0.6)

            # Callback should have been called
            assert len(callback_called) == 1
            assert callback_called[0] == ("key1", {"data": "value1"})

            # Key should be gone
            assert await cache.has("key1") is False

    @pytest.mark.asyncio
    async def test_multiple_keys_expiry(self):
        """Test expiry of multiple keys."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        # Short TTL for testing
        cache = AsyncMemCache(ttl=0.3, on_expire=on_expire, check_interval=0.1)
        async with cache:
            await cache.set("key1", {"data": "value1"})
            await cache.set("key2", {"data": "value2"})
            await cache.set("key3", {"data": "value3"})

            # Wait for expiry
            await asyncio.sleep(0.6)

            # All callbacks should have been called
            assert len(callback_called) == 3

            # Check all keys expired
            keys_expired = {item[0] for item in callback_called}
            assert keys_expired == {"key1", "key2", "key3"}

    @pytest.mark.asyncio
    async def test_expiry_callback_error_handling(self):
        """Test that errors in callback don't crash the monitor."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))
            if key == "key1":
                raise ValueError("Test error")

        # Short TTL for testing
        cache = AsyncMemCache(ttl=0.3, on_expire=on_expire, check_interval=0.1)
        async with cache:
            await cache.set("key1", {"data": "value1"})
            await cache.set("key2", {"data": "value2"})

            # Wait for expiry
            await asyncio.sleep(0.6)

            # Both callbacks should have been attempted
            assert len(callback_called) == 2

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        """Test manual start/stop lifecycle."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=1.0, on_expire=on_expire)

        # Initially not running
        assert cache._running is False

        # Start the cache
        await cache.start()
        assert cache._running is True
        assert cache._monitor_task is not None

        # Stop the cache
        await cache.stop()
        assert cache._running is False
        assert cache._monitor_task is None

    @pytest.mark.asyncio
    async def test_start_when_already_running_raises_error(self):
        """Test that starting an already running cache raises an error."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=1.0, on_expire=on_expire)

        await cache.start()

        with pytest.raises(RuntimeError, match="Cache is already running"):
            await cache.start()

        await cache.stop()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=1.0, on_expire=on_expire)

        assert cache._running is False

        async with cache:
            assert cache._running is True
            await cache.set("key1", {"data": "value1"})

        # Cache should be stopped after exiting context
        assert cache._running is False

    @pytest.mark.asyncio
    async def test_concurrent_access(self):
        """Test concurrent access to the cache."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=10.0, on_expire=on_expire)
        async with cache:
            # Create multiple concurrent tasks
            async def set_get_task(key_num: int):
                key = f"key{key_num}"
                await cache.set(key, {"num": key_num})
                result = await cache.get(key)
                assert result == {"num": key_num}

            # Run 10 concurrent tasks
            tasks = [set_get_task(i) for i in range(10)]
            await asyncio.gather(*tasks)

            # All keys should exist
            for i in range(10):
                assert await cache.has(f"key{i}") is True

    @pytest.mark.asyncio
    async def test_deleted_key_no_callback(self):
        """Test that manually deleted keys don't trigger callbacks."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=0.3, on_expire=on_expire, check_interval=0.1)
        async with cache:
            await cache.set("key1", {"data": "value1"})

            # Delete before expiry
            await cache.delete("key1")

            # Wait past expiry time
            await asyncio.sleep(0.6)

            # Callback should not have been called
            assert len(callback_called) == 0

    @pytest.mark.asyncio
    async def test_peek_returns_value_without_extending_ttl(self):
        """peek() should not extend TTL."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=0.3, on_expire=on_expire, check_interval=0.05)
        async with cache:
            await cache.set("key1", {"data": "value1"})

            # Repeated peeks should not refresh expiration.
            for _ in range(3):
                await asyncio.sleep(0.08)
                assert await cache.peek("key1") == {"data": "value1"}

            # Wait a bit past original TTL and verify natural expiry happened.
            await asyncio.sleep(0.2)
            assert await cache.peek("key1") is None
            assert len(callback_called) == 1
            assert callback_called[0] == ("key1", {"data": "value1"})

    @pytest.mark.asyncio
    async def test_get_still_extends_ttl_after_peek_added(self):
        """get() should keep its existing TTL-extension semantics."""
        callback_called = []

        async def on_expire(key: str, value: dict):
            callback_called.append((key, value))

        cache = AsyncMemCache(ttl=0.3, on_expire=on_expire, check_interval=0.05)
        async with cache:
            await cache.set("key1", {"data": "value1"})

            # Access by get() before TTL each time to extend expiration.
            for _ in range(4):
                await asyncio.sleep(0.15)
                assert await cache.get("key1") == {"data": "value1"}

            # Key should still be present due to TTL refresh via get().
            assert await cache.peek("key1") == {"data": "value1"}
            assert len(callback_called) == 0

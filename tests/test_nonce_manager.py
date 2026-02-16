"""Tests for NonceManager functionality."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from kuru_sdk_py.transaction.nonce_manager import NonceManager, NonceState


class TestNonceManager:
    """Test suite for NonceManager."""

    def setup_method(self):
        """Reset NonceManager state before each test."""
        # Clear class-level state to ensure test isolation
        NonceManager._nonce_states = {}

    @pytest.mark.asyncio
    async def test_first_nonce_fetches_from_rpc(self):
        """Test that first nonce call fetches from RPC."""
        # Create mock Web3 instance
        mock_w3 = MagicMock()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=10)

        address = "0x1234567890123456789012345678901234567890"

        # First call should fetch from RPC
        nonce = await NonceManager.get_and_increment_nonce(mock_w3, address)

        # Assert nonce is correct
        assert nonce == 10

        # Assert RPC was called once with correct parameters
        mock_w3.eth.get_transaction_count.assert_called_once_with(address, 'latest')

        # Assert internal state is incremented
        nonce_state = NonceManager._nonce_states[address]
        assert nonce_state.current_nonce == 11
        assert nonce_state.initialized is True

    @pytest.mark.asyncio
    async def test_sequential_increments_without_rpc(self):
        """Test that sequential calls increment without RPC after first call."""
        # Create mock Web3 instance
        mock_w3 = MagicMock()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=10)

        address = "0x1234567890123456789012345678901234567890"

        # Call get_and_increment_nonce 3 times
        nonce1 = await NonceManager.get_and_increment_nonce(mock_w3, address)
        nonce2 = await NonceManager.get_and_increment_nonce(mock_w3, address)
        nonce3 = await NonceManager.get_and_increment_nonce(mock_w3, address)

        # Assert sequential nonces
        assert nonce1 == 10
        assert nonce2 == 11
        assert nonce3 == 12

        # Assert RPC called only once (initialization)
        assert mock_w3.eth.get_transaction_count.call_count == 1

        # Assert internal state
        nonce_state = NonceManager._nonce_states[address]
        assert nonce_state.current_nonce == 13

    @pytest.mark.asyncio
    async def test_concurrent_nonce_allocation(self):
        """Test that concurrent calls get sequential nonces without duplicates."""
        # Create mock Web3 instance
        mock_w3 = MagicMock()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=10)

        address = "0x1234567890123456789012345678901234567890"

        # Spawn 5 concurrent get_and_increment_nonce calls
        tasks = [
            NonceManager.get_and_increment_nonce(mock_w3, address)
            for _ in range(5)
        ]
        nonces = await asyncio.gather(*tasks)

        # Assert we got 5 nonces
        assert len(nonces) == 5

        # Assert all nonces are sequential and unique
        sorted_nonces = sorted(nonces)
        assert sorted_nonces == [10, 11, 12, 13, 14]

        # Assert no duplicates
        assert len(set(nonces)) == 5

        # Assert RPC called only once
        assert mock_w3.eth.get_transaction_count.call_count == 1

        # Assert final internal state
        nonce_state = NonceManager._nonce_states[address]
        assert nonce_state.current_nonce == 15

    @pytest.mark.asyncio
    async def test_failure_resets_nonce(self):
        """Test that mark_transaction_failed resets nonce state."""
        # Create mock Web3 instance
        mock_w3 = MagicMock()
        # First call returns 10, second call (after reset) returns 15
        mock_w3.eth.get_transaction_count = AsyncMock(side_effect=[10, 15])

        address = "0x1234567890123456789012345678901234567890"

        # Get first nonce
        nonce1 = await NonceManager.get_and_increment_nonce(mock_w3, address)
        assert nonce1 == 10

        # Verify state is initialized
        nonce_state = NonceManager._nonce_states[address]
        assert nonce_state.initialized is True
        assert nonce_state.current_nonce == 11

        # Mark transaction as failed
        await NonceManager.mark_transaction_failed(address)

        # Verify state is reset
        assert nonce_state.initialized is False
        assert nonce_state.current_nonce is None

        # Get nonce again - should fetch from RPC again
        nonce2 = await NonceManager.get_and_increment_nonce(mock_w3, address)
        assert nonce2 == 15

        # Assert RPC called twice (once initially, once after reset)
        assert mock_w3.eth.get_transaction_count.call_count == 2

        # Verify state is reinitialized
        assert nonce_state.initialized is True
        assert nonce_state.current_nonce == 16

    @pytest.mark.asyncio
    async def test_multiple_addresses_independent(self):
        """Test that different addresses maintain separate nonce state."""
        # Create mock Web3 instance
        mock_w3 = MagicMock()
        # Return different nonces for different addresses
        mock_w3.eth.get_transaction_count = AsyncMock(side_effect=[10, 5])

        address_a = "0x1234567890123456789012345678901234567890"
        address_b = "0xABCDEF1234567890123456789012345678901234"

        # Get nonce for address A
        nonce_a1 = await NonceManager.get_and_increment_nonce(mock_w3, address_a)
        assert nonce_a1 == 10

        # Get nonce for address B
        nonce_b1 = await NonceManager.get_and_increment_nonce(mock_w3, address_b)
        assert nonce_b1 == 5

        # Get nonce for address A again
        nonce_a2 = await NonceManager.get_and_increment_nonce(mock_w3, address_a)
        assert nonce_a2 == 11

        # Get nonce for address B again
        nonce_b2 = await NonceManager.get_and_increment_nonce(mock_w3, address_b)
        assert nonce_b2 == 6

        # Assert RPC called twice (once per address)
        assert mock_w3.eth.get_transaction_count.call_count == 2

        # Assert separate state maintained
        state_a = NonceManager._nonce_states[address_a]
        state_b = NonceManager._nonce_states[address_b]

        assert state_a.current_nonce == 12
        assert state_b.current_nonce == 7
        assert state_a is not state_b

    @pytest.mark.asyncio
    async def test_mark_failed_on_nonexistent_address(self):
        """Test that marking failure on non-existent address doesn't crash."""
        address = "0x1234567890123456789012345678901234567890"

        # Should not raise exception
        await NonceManager.mark_transaction_failed(address)

        # Address should not be in state (no state created)
        assert address not in NonceManager._nonce_states

    @pytest.mark.asyncio
    async def test_nonce_state_dataclass(self):
        """Test NonceState dataclass initialization."""
        state = NonceState()

        assert state.current_nonce is None
        assert state.initialized is False
        assert isinstance(state.lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_high_concurrency(self):
        """Test high concurrency with 50 simultaneous nonce requests."""
        # Create mock Web3 instance
        mock_w3 = MagicMock()
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=100)

        address = "0x1234567890123456789012345678901234567890"

        # Spawn 50 concurrent requests
        num_requests = 50
        tasks = [
            NonceManager.get_and_increment_nonce(mock_w3, address)
            for _ in range(num_requests)
        ]
        nonces = await asyncio.gather(*tasks)

        # Assert we got all nonces
        assert len(nonces) == num_requests

        # Assert all nonces are sequential and unique
        sorted_nonces = sorted(nonces)
        expected_nonces = list(range(100, 100 + num_requests))
        assert sorted_nonces == expected_nonces

        # Assert no duplicates
        assert len(set(nonces)) == num_requests

        # Assert RPC called only once
        assert mock_w3.eth.get_transaction_count.call_count == 1

        # Assert final internal state
        nonce_state = NonceManager._nonce_states[address]
        assert nonce_state.current_nonce == 100 + num_requests

    @pytest.mark.asyncio
    async def test_reset_during_concurrent_operations(self):
        """Test that reset during concurrent operations maintains consistency."""
        # Create mock Web3 instance
        mock_w3 = MagicMock()
        mock_w3.eth.get_transaction_count = AsyncMock(side_effect=[10, 20])

        address = "0x1234567890123456789012345678901234567890"

        # Get initial nonce
        nonce1 = await NonceManager.get_and_increment_nonce(mock_w3, address)
        assert nonce1 == 10

        # Spawn concurrent operations
        async def get_nonce_with_delay():
            await asyncio.sleep(0.01)  # Small delay
            return await NonceManager.get_and_increment_nonce(mock_w3, address)

        # Start concurrent requests
        task1 = asyncio.create_task(get_nonce_with_delay())
        task2 = asyncio.create_task(get_nonce_with_delay())

        # Mark as failed in the middle
        await NonceManager.mark_transaction_failed(address)

        # Wait for tasks to complete
        results = await asyncio.gather(task1, task2)

        # Both should get fresh nonces from RPC (20, 21)
        sorted_results = sorted(results)
        assert sorted_results == [20, 21]

        # Assert RPC called twice (initial + after reset)
        assert mock_w3.eth.get_transaction_count.call_count == 2

"""Global nonce manager for local nonce tracking and automatic increment."""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional
from loguru import logger
from web3 import AsyncWeb3


@dataclass
class NonceState:
    """Per-address nonce state with async thread-safety.

    Attributes:
        current_nonce: The next nonce to use for transactions. None if not initialized.
        initialized: Whether nonce has been fetched from RPC at least once.
        lock: Async lock for thread-safe operations on this address's state.
    """

    current_nonce: Optional[int] = None
    initialized: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class NonceManager:
    """Global nonce manager for all addresses.

    Provides local nonce tracking with automatic increment to reduce RPC calls.
    Thread-safe for concurrent transaction sending from multiple components.

    Usage:
        # Get nonce and increment for next transaction
        nonce = await NonceManager.get_and_increment_nonce(w3, address)

        # Mark transaction as failed to force resync
        await NonceManager.mark_transaction_failed(address)

    Features:
    - Fetches from RPC only on first use per address
    - Automatically increments nonce after each allocation
    - Auto-resets on transaction failures to prevent desynchronization
    - Thread-safe with asyncio locks
    - Global state shared across all transaction senders
    """

    # Class-level storage (singleton pattern)
    _nonce_states: Dict[str, NonceState] = {}
    _states_lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def get_and_increment_nonce(cls, w3: AsyncWeb3, address: str) -> int:
        """Get current nonce and atomically increment for next transaction.

        On first call for an address: fetches from RPC using 'latest' block.
        On subsequent calls: returns and increments local nonce.

        Thread-safe for concurrent calls. Multiple concurrent calls will receive
        sequential nonces (N, N+1, N+2, ...) and can send transactions in parallel.

        Args:
            w3: AsyncWeb3 instance for RPC calls
            address: Checksummed Ethereum address

        Returns:
            Nonce to use for the transaction

        Example:
            nonce = await NonceManager.get_and_increment_nonce(w3, "0x123...")
            # First call: fetches from RPC, returns 10, increments internal to 11
            # Second call: returns 11, increments internal to 12
            # etc.
        """
        # Get or create NonceState for this address
        async with cls._states_lock:
            if address not in cls._nonce_states:
                cls._nonce_states[address] = NonceState()
            nonce_state = cls._nonce_states[address]

        # Acquire per-address lock for thread-safe nonce allocation
        async with nonce_state.lock:
            # Initialize from RPC if needed
            if not nonce_state.initialized:
                logger.debug(f"Fetching initial nonce from RPC for {address}")
                nonce_from_rpc = await w3.eth.get_transaction_count(address, "latest")
                nonce_state.current_nonce = nonce_from_rpc
                nonce_state.initialized = True
                logger.info(f"Initialized nonce for {address}: {nonce_from_rpc}")

            # Get current nonce to return
            nonce_to_return = nonce_state.current_nonce

            # Increment for next transaction
            nonce_state.current_nonce += 1

            return nonce_to_return

    @classmethod
    async def mark_transaction_failed(cls, address: str) -> None:
        """Mark transaction as failed and reset nonce state.

        Forces next transaction to fetch fresh nonce from RPC. This prevents
        nonce desynchronization when transactions fail before being sent.

        Called automatically by AsyncTransactionSenderMixin when:
        - Gas estimation fails
        - Transaction validation fails
        - Network errors occur

        Args:
            address: Checksummed Ethereum address to reset

        Example:
            try:
                # ... send transaction ...
            except Exception:
                await NonceManager.mark_transaction_failed(address)
                raise
        """
        # Get NonceState for this address
        async with cls._states_lock:
            if address not in cls._nonce_states:
                # No state exists, nothing to reset
                return
            nonce_state = cls._nonce_states[address]

        # Reset nonce state
        async with nonce_state.lock:
            nonce_state.initialized = False
            nonce_state.current_nonce = None
            logger.warning(
                f"Nonce reset for {address} due to transaction failure, will fetch from RPC on next transaction"
            )

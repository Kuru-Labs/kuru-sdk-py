"""Mixin classes for transaction sending functionality."""

from loguru import logger
from typing import Protocol, runtime_checkable, Optional
from web3 import AsyncWeb3
from eth_account.signers.local import LocalAccount
import asyncio
import time

from .nonce_manager import NonceManager
from kuru_sdk_py.exceptions import (
    KuruContractError,
    KuruInsufficientFundsError,
    KuruTransactionError,
)
from kuru_sdk_py.utils.errors import decode_contract_error, extract_error_selector
from kuru_sdk_py.configs import TransactionConfig
from kuru_sdk_py.config_defaults import (
    DEFAULT_CHAIN_ID,
    DEFAULT_GAS_PRICE_REFRESH_INTERVAL,
)


@runtime_checkable
class AsyncTransactionSenderProtocol(Protocol):
    """Protocol defining required attributes for AsyncTransactionSenderMixin."""

    w3: AsyncWeb3
    account: LocalAccount
    user_address: str
    transaction_config: TransactionConfig

    async def _get_effective_gas_price(
        self,
        override_gas_price: Optional[int] = None,
    ) -> int: ...


class AsyncTransactionSenderMixin:
    """Mixin providing async transaction sending capability.

    Classes using this mixin must have the following attributes:
    - self.w3: AsyncWeb3 instance
    - self.account: Account from private key (LocalAccount)
    - self.user_address: Checksummed user address (str)

    Example:
        class MyContract(AsyncTransactionSenderMixin):
            def __init__(self, rpc_url: str, private_key: str):
                self.w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
                self.account = self.w3.eth.account.from_key(private_key)
                self.user_address = self.account.address

            async def do_something(self):
                tx_hash = await self._send_transaction(some_function_call)
    """

    @staticmethod
    def _is_nonce_too_low_error(error: Exception) -> bool:
        """Return True when exception indicates nonce-too-low failure."""
        error_str = str(error).lower()
        if "nonce too low" in error_str:
            return True
        if "already known" in error_str:
            return True
        if (
            hasattr(error, "args")
            and error.args
            and isinstance(error.args[0], dict)
            and error.args[0].get("code") in (-32000, -32010)
            and "nonce" in str(error.args[0]).lower()
        ):
            return True
        return False

    def _ensure_gas_price_state(self) -> None:
        """Initialize gas price cache state lazily for mixin users."""
        if hasattr(self, "_gas_price_lock"):
            return

        self._gas_price_lock = asyncio.Lock()
        self._cached_gas_price: Optional[int] = None
        self._cached_gas_price_updated_at: Optional[float] = None
        self._gas_price_worker_task: Optional[asyncio.Task] = None
        self._gas_price_worker_running = False

    def _get_chain_id(self) -> int:
        """Read configured chain ID, falling back for lightweight test doubles."""
        tx_config = getattr(self, "transaction_config", None)
        return int(getattr(tx_config, "chain_id", DEFAULT_CHAIN_ID))

    def _get_gas_price_refresh_interval(self) -> float:
        """Read configured refresh interval, falling back for lightweight test doubles."""
        tx_config = getattr(self, "transaction_config", None)
        return float(
            getattr(
                tx_config,
                "gas_price_refresh_interval",
                DEFAULT_GAS_PRICE_REFRESH_INTERVAL,
            )
        )

    async def _refresh_cached_gas_price(self) -> int:
        """Fetch current gas price from RPC and store it in cache."""
        self._ensure_gas_price_state()

        latest_gas_price = await self.w3.eth.gas_price
        async with self._gas_price_lock:
            self._cached_gas_price = int(latest_gas_price)
            self._cached_gas_price_updated_at = time.monotonic()
            return self._cached_gas_price

    async def _gas_price_worker_loop(self) -> None:
        """Background loop that refreshes gas price at fixed interval."""
        interval = max(0.1, self._get_gas_price_refresh_interval())
        while self._gas_price_worker_running:
            try:
                await self._refresh_cached_gas_price()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Gas price refresh failed: {e}")

            await asyncio.sleep(interval)

    async def start_gas_price_worker(self) -> None:
        """Start background gas price polling worker."""
        self._ensure_gas_price_state()

        if self._gas_price_worker_running:
            return

        self._gas_price_worker_running = True
        self._gas_price_worker_task = asyncio.create_task(self._gas_price_worker_loop())

        # Prime cache immediately so first tx avoids a cold gas price fetch.
        try:
            await self._refresh_cached_gas_price()
        except Exception as e:
            logger.debug(f"Initial gas price fetch failed, worker will retry: {e}")

    async def stop_gas_price_worker(self) -> None:
        """Stop background gas price polling worker."""
        self._ensure_gas_price_state()

        self._gas_price_worker_running = False
        if self._gas_price_worker_task is not None:
            self._gas_price_worker_task.cancel()
            try:
                await self._gas_price_worker_task
            except asyncio.CancelledError:
                pass
            self._gas_price_worker_task = None

    async def _get_effective_gas_price(
        self,
        override_gas_price: Optional[int] = None,
    ) -> int:
        """
        Return gas price for transaction submission.

        Priority:
        1) Explicit per-call override
        2) Cached gas price from background worker
        3) One-off fetch + cache fill
        """
        if override_gas_price is not None:
            return int(override_gas_price)

        self._ensure_gas_price_state()
        await self.start_gas_price_worker()

        async with self._gas_price_lock:
            if self._cached_gas_price is not None:
                return self._cached_gas_price

        return await self._refresh_cached_gas_price()

    async def _send_transaction(
        self: AsyncTransactionSenderProtocol,
        function_call,
        value: int = 0,
        access_list: Optional[list[dict]] = None,
        gas_price: Optional[int] = None,
    ) -> str:
        """Build, sign, and send a transaction to the blockchain.

        Args:
            function_call: The contract function call to execute
            value: ETH value to send with transaction (in wei)
            access_list: Optional EIP-2930 access list to reduce gas costs
                Format: [{'address': '0x...', 'storageKeys': ['0x...', ...]}, ...]

        Returns:
            Transaction hash as hex string

        Raises:
            ValueError: If transaction parameters are invalid
            Exception: If transaction fails
        """
        max_nonce_retries = 5
        attempt = 0
        tx = {}

        try:
            t_fn_start = time.perf_counter()
            while True:
                attempt += 1
                try:
                    t0 = time.perf_counter()
                    nonce = await NonceManager.get_and_increment_nonce(
                        self.w3, self.user_address
                    )
                    dt_nonce = time.perf_counter() - t0

                    t0 = time.perf_counter()
                    effective_gas_price = await self._get_effective_gas_price(gas_price)
                    dt_gas_price = time.perf_counter() - t0

                    # Build transaction parameters
                    tx_params = {
                        "from": self.user_address,
                        "nonce": nonce,
                        "value": value,
                        "gasPrice": effective_gas_price,
                        "chainId": self._get_chain_id(),
                    }

                    # Add access list if provided
                    if access_list:
                        tx_params["accessList"] = access_list

                    # Build transaction via public web3 API while preventing its internal
                    # default gas estimation by supplying a temporary gas value.
                    # We remove this placeholder before our single explicit estimate call.
                    t0 = time.perf_counter()
                    tx = await function_call.build_transaction(
                        {
                            **tx_params,
                            # Placeholder to skip build_transaction default gas estimation.
                            "gas": 21_000,
                        }
                    )
                    tx.pop("gas", None)
                    dt_build_tx = time.perf_counter() - t0

                    # Estimate gas
                    try:
                        t0 = time.perf_counter()
                        estimated_gas = await self.w3.eth.estimate_gas(tx)
                        dt_estimate_gas = time.perf_counter() - t0

                        # Manually adjust gas when access list is provided
                        # RPC may overestimate gas per storage slot
                        if access_list:
                            total_storage_slots = sum(
                                len(entry.get("storageKeys", [])) for entry in access_list
                            )
                            # Use config for gas adjustment
                            adjusted_gas = estimated_gas - (
                                total_storage_slots
                                * self.transaction_config.gas_adjustment_per_slot
                            ) + self.transaction_config.gas_buffer
                            candidate_gas = int(
                                adjusted_gas * self.transaction_config.gas_buffer_multiplier
                            )
                            if candidate_gas < 21_000:
                                logger.warning(
                                    "Access-list gas adjustment produced non-viable gas "
                                    f"(estimated={estimated_gas}, adjusted={candidate_gas}, "
                                    f"slots={total_storage_slots}); falling back to estimated gas"
                                )
                                tx["gas"] = int(estimated_gas)
                            else:
                                tx["gas"] = candidate_gas
                        else:
                            tx["gas"] = int(estimated_gas)
                    except Exception as e:
                        # Try to decode contract error for better error message
                        decoded_error = decode_contract_error(e)
                        selector = extract_error_selector(e)

                        if decoded_error:
                            error_msg = f"Transaction would revert: {decoded_error}"
                            logger.error(
                                f"Gas estimation failed with contract error: {decoded_error}"
                            )
                            logger.debug(f"Original exception: {e}")
                        else:
                            error_msg = f"Transaction would fail: {e}"
                            logger.error(f"Gas estimation failed: {e}")

                        raise KuruContractError(
                            error_msg,
                            revert_reason=decoded_error,
                            selector=selector,
                        ) from e

                    t0 = time.perf_counter()
                    signed_tx = self.account.sign_transaction(tx)
                    dt_sign = time.perf_counter() - t0

                    t0 = time.perf_counter()
                    tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
                    dt_send_raw = time.perf_counter() - t0
                    tx_hash_hex = tx_hash.hex()

                    logger.info(
                        f"tx_timings | "
                        f"nonce={dt_nonce*1000:.1f}ms "
                        f"gas_price={dt_gas_price*1000:.1f}ms "
                        f"build_tx={dt_build_tx*1000:.1f}ms "
                        f"estimate_gas={dt_estimate_gas*1000:.1f}ms "
                        f"sign={dt_sign*1000:.1f}ms "
                        f"send_raw={dt_send_raw*1000:.1f}ms "
                        f"total={(time.perf_counter()-t_fn_start)*1000:.1f}ms"
                    )
                    return tx_hash_hex
                except Exception as e:
                    if self._is_nonce_too_low_error(e) and attempt < max_nonce_retries:
                        logger.warning(
                            f"Nonce send failed ({e}); retrying with a new nonce "
                            f"(attempt {attempt + 1}/{max_nonce_retries})"
                        )
                        await NonceManager.mark_transaction_failed(self.user_address)
                        continue
                    raise
        except KuruTransactionError as e:
            # Mark nonce as failed to force resync on next transaction
            await NonceManager.mark_transaction_failed(self.user_address)
            logger.error(f"Transaction validation failed: {e}")
            raise
        except Exception as e:
            # Mark nonce as failed to force resync on next transaction
            await NonceManager.mark_transaction_failed(self.user_address)

            # Check for insufficient funds error
            error_str = str(e)
            tx_data = locals().get("tx", {})
            if "Insufficient funds" in error_str or (
                hasattr(e, "args")
                and isinstance(e.args[0], dict)
                and e.args[0].get("code") == -32003
            ):
                # Get current balance for helpful error message
                try:
                    current_balance = await self.w3.eth.get_balance(self.user_address)
                    estimated_gas_cost = tx_data.get("gas", 0) * tx_data.get(
                        "gasPrice", 0
                    )
                    total_required = value + estimated_gas_cost

                    logger.error(
                        f"Insufficient funds for transaction:\n"
                        f"  Current balance: {current_balance} wei ({current_balance / 1e18:.6f} native tokens)\n"
                        f"  Required: {total_required} wei ({total_required / 1e18:.6f} native tokens)\n"
                        f"    - Transaction value: {value} wei\n"
                        f"    - Estimated gas cost: {estimated_gas_cost} wei\n"
                        f"  Shortfall: {total_required - current_balance} wei ({(total_required - current_balance) / 1e18:.6f} native tokens)"
                    )
                    raise KuruInsufficientFundsError(
                        f"Insufficient funds: wallet has {current_balance / 1e18:.6f} native tokens but needs "
                        f"{total_required / 1e18:.6f} native tokens ({value / 1e18:.6f} for transfer + "
                        f"{estimated_gas_cost / 1e18:.6f} for gas). Please add more native tokens to your wallet.",
                    )
                except KuruInsufficientFundsError:
                    raise
                except Exception:
                    # Fallback if balance check fails
                    raise KuruInsufficientFundsError(
                        f"Insufficient funds for transaction. Please ensure your wallet has enough native tokens "
                        f"to cover both the transaction value ({value / 1e18:.6f} tokens) and gas costs.",
                    )

            # Try to decode contract error for better error message
            decoded_error = decode_contract_error(e)
            selector = extract_error_selector(e)

            if decoded_error:
                error_msg = f"Transaction failed with contract error: {decoded_error}"
                logger.error(error_msg)
                logger.debug(f"Original exception: {e}")
                raise KuruContractError(
                    error_msg,
                    revert_reason=decoded_error,
                    selector=selector,
                ) from e
            else:
                logger.error(f"Failed to send transaction: {e}")
                raise KuruTransactionError(f"Transaction failed: {e}") from e

    async def _wait_for_transaction_receipt(
        self: AsyncTransactionSenderProtocol,
        tx_hash: str,
        timeout: Optional[int] = None,
        poll_latency: Optional[float] = None,
    ):
        """Wait for transaction to be mined and return receipt.

        Args:
            tx_hash: Transaction hash to wait for
            timeout: Maximum time to wait in seconds.
                    If None, uses transaction_config.timeout
            poll_latency: Time to wait after confirmation for RPC sync.
                         If None, uses transaction_config.poll_latency

        Returns:
            Transaction receipt

        Raises:
            TimeoutError: If transaction not confirmed within timeout
        """
        # Use config defaults if not specified
        if timeout is None:
            timeout = self.transaction_config.timeout
        if poll_latency is None:
            poll_latency = self.transaction_config.poll_latency

        receipt = await self.w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=timeout
        )

        # Brief delay to allow RPC node to update nonce state
        if poll_latency > 0:
            await asyncio.sleep(poll_latency)

        return receipt

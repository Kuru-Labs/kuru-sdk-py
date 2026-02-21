"""Mixin classes for transaction sending functionality."""

from loguru import logger
from typing import Protocol, runtime_checkable, Optional
from web3 import AsyncWeb3
from eth_account.signers.local import LocalAccount
import asyncio

from .nonce_manager import NonceManager
from kuru_sdk_py.utils.errors import decode_contract_error
from kuru_sdk_py.configs import TransactionConfig


@runtime_checkable
class AsyncTransactionSenderProtocol(Protocol):
    """Protocol defining required attributes for AsyncTransactionSenderMixin."""

    w3: AsyncWeb3
    account: LocalAccount
    user_address: str
    transaction_config: TransactionConfig


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

    async def _send_transaction(
        self: AsyncTransactionSenderProtocol,
        function_call,
        value: int = 0,
        access_list: Optional[list[dict]] = None,
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
        try:
            # Get nonce from local manager (fetches from RPC only if not initialized)
            nonce = await NonceManager.get_and_increment_nonce(
                self.w3, self.user_address
            )

            # Get gas price
            gas_price = await self.w3.eth.gas_price

            # Build transaction parameters
            tx_params = {
                "from": self.user_address,
                "nonce": nonce,
                "value": value,
                "gasPrice": gas_price,
            }

            # Add access list if provided
            if access_list:
                tx_params["accessList"] = access_list

            # Build transaction
            tx = await function_call.build_transaction(tx_params)

            # Estimate gas
            try:
                estimated_gas = await self.w3.eth.estimate_gas(tx)

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
                    final_gas = int(
                        adjusted_gas * self.transaction_config.gas_buffer_multiplier
                    )
                    tx["gas"] = final_gas
                else:
                    tx["gas"] = int(estimated_gas)
            except Exception as e:
                # Try to decode contract error for better error message
                decoded_error = decode_contract_error(e)

                if decoded_error:
                    error_msg = f"Transaction would revert: {decoded_error}"
                    logger.error(
                        f"Gas estimation failed with contract error: {decoded_error}"
                    )
                    logger.debug(f"Original exception: {e}")
                else:
                    error_msg = f"Transaction would fail: {e}"
                    logger.error(f"Gas estimation failed: {e}")

                raise ValueError(error_msg)

            # Sign transaction
            signed_tx = self.account.sign_transaction(tx)

            # Send transaction
            tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()

            logger.info(f"Transaction sent: {tx_hash_hex}")
            return tx_hash_hex

        except ValueError as e:
            # Mark nonce as failed to force resync on next transaction
            await NonceManager.mark_transaction_failed(self.user_address)
            logger.error(f"Transaction validation failed: {e}")
            raise
        except Exception as e:
            # Mark nonce as failed to force resync on next transaction
            await NonceManager.mark_transaction_failed(self.user_address)

            # Check for insufficient funds error
            error_str = str(e)
            if "Insufficient funds" in error_str or (
                hasattr(e, "args")
                and isinstance(e.args[0], dict)
                and e.args[0].get("code") == -32003
            ):
                # Get current balance for helpful error message
                try:
                    current_balance = await self.w3.eth.get_balance(self.user_address)
                    estimated_gas_cost = tx.get("gas", 0) * tx.get("gasPrice", 0)
                    total_required = value + estimated_gas_cost

                    logger.error(
                        f"Insufficient funds for transaction:\n"
                        f"  Current balance: {current_balance} wei ({current_balance / 1e18:.6f} native tokens)\n"
                        f"  Required: {total_required} wei ({total_required / 1e18:.6f} native tokens)\n"
                        f"    - Transaction value: {value} wei\n"
                        f"    - Estimated gas cost: {estimated_gas_cost} wei\n"
                        f"  Shortfall: {total_required - current_balance} wei ({(total_required - current_balance) / 1e18:.6f} native tokens)"
                    )
                    raise Exception(
                        f"Insufficient funds: wallet has {current_balance / 1e18:.6f} native tokens but needs "
                        f"{total_required / 1e18:.6f} native tokens ({value / 1e18:.6f} for transfer + "
                        f"{estimated_gas_cost / 1e18:.6f} for gas). Please add more native tokens to your wallet."
                    )
                except:
                    # Fallback if balance check fails
                    raise Exception(
                        f"Insufficient funds for transaction. Please ensure your wallet has enough native tokens "
                        f"to cover both the transaction value ({value / 1e18:.6f} tokens) and gas costs."
                    )

            # Try to decode contract error for better error message
            decoded_error = decode_contract_error(e)

            if decoded_error:
                error_msg = f"Transaction failed with contract error: {decoded_error}"
                logger.error(error_msg)
                logger.debug(f"Original exception: {e}")
                raise Exception(error_msg)
            else:
                logger.error(f"Failed to send transaction: {e}")
                raise Exception(f"Transaction failed: {e}")

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

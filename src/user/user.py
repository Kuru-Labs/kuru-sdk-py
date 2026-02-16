from loguru import logger
from web3 import Web3, AsyncWeb3, AsyncHTTPProvider
from web3.contract import Contract, AsyncContract
from typing import Optional
import requests

from src.configs import (
    MarketConfig,
    ConnectionConfig,
    WalletConfig,
    TransactionConfig,
    OrderExecutionConfig,
)
from src.utils import (
    load_abi,
    ZERO_ADDRESS,
    is_native_token,
)
from src.transaction.transaction import AsyncTransactionSenderMixin


class User(AsyncTransactionSenderMixin):
    """
    User class for managing margin account operations, token balances, and approvals.

    Handles:
    - Balance queries for base and quote tokens (native and ERC20)
    - Allowance checks for ERC20 tokens
    - Automatic approval management
    - Deposits to margin account (native and ERC20)
    - Withdrawals from margin account
    - EIP-7702 authorization and revocation for MM Entrypoint contract
    """

    def __init__(
        self,
        market_config: MarketConfig,
        connection_config: ConnectionConfig,
        wallet_config: WalletConfig,
        transaction_config: TransactionConfig,
        order_execution_config: OrderExecutionConfig,
    ):
        """
        Initialize the User with margin contract, token contracts, and MM entrypoint.

        Args:
            market_config: Market configuration (token addresses, decimals, contract addresses)
            connection_config: Connection configuration (RPC URLs, API URLs)
            wallet_config: Wallet configuration (private key, user address)
            transaction_config: Transaction behavior configuration
            order_execution_config: Order execution defaults
        """
        # Store all configs
        self.market_config = market_config
        self.connection_config = connection_config
        self.wallet_config = wallet_config
        self.transaction_config = transaction_config
        self.order_execution_config = order_execution_config

        # Extract commonly used values from configs
        # From MarketConfig
        margin_contract_address = market_config.margin_contract_address
        base_token_address = market_config.base_token
        quote_token_address = market_config.quote_token
        mm_entrypoint_address = market_config.mm_entrypoint_address
        self.base_token_decimals = market_config.base_token_decimals
        self.quote_token_decimals = market_config.quote_token_decimals
        self.market_address = Web3.to_checksum_address(market_config.market_address)

        # From ConnectionConfig
        self.rpc_url = connection_config.rpc_url
        self.kuru_api_url = connection_config.kuru_api_url

        # From WalletConfig
        self.user_address = Web3.to_checksum_address(wallet_config.user_address)
        private_key = wallet_config.private_key

        # Normalize all addresses
        self.margin_contract_address = Web3.to_checksum_address(margin_contract_address)
        self.base_token_address = Web3.to_checksum_address(base_token_address)
        self.quote_token_address = Web3.to_checksum_address(quote_token_address)
        self.mm_entrypoint_address = Web3.to_checksum_address(mm_entrypoint_address)

        # Initialize AsyncWeb3
        self.w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))

        # Create account from private key for signing transactions
        self.account = self.w3.eth.account.from_key(private_key)

        # Load ABIs
        margin_account_abi = load_abi("margin_account")
        erc20_abi = load_abi("erc20")
        mm_entrypoint_abi = load_abi("mm_entrypoint")

        # Initialize margin contract
        self.margin_contract: AsyncContract = self.w3.eth.contract(
            address=self.margin_contract_address, abi=margin_account_abi
        )

        # Initialize MM entrypoint contract
        self.mm_entrypoint_contract: AsyncContract = self.w3.eth.contract(
            address=self.mm_entrypoint_address, abi=mm_entrypoint_abi
        )

        # Initialize token contracts (only if not native token)
        self.base_token_contract: Optional[AsyncContract] = None
        if not is_native_token(self.base_token_address):
            self.base_token_contract = self.w3.eth.contract(
                address=self.base_token_address, abi=erc20_abi
            )

        self.quote_token_contract: Optional[AsyncContract] = None
        if not is_native_token(self.quote_token_address):
            self.quote_token_contract = self.w3.eth.contract(
                address=self.quote_token_address, abi=erc20_abi
            )

        logger.info(
            f"User initialized for {self.user_address} | "
            f"Margin: {self.margin_contract_address} | "
            f"MM Entrypoint: {self.mm_entrypoint_address}"
        )

    # Conversion helper methods

    def _convert_base_amount(self, amount: float) -> int:
        """
        Convert float amount to base token integer value based on decimals.

        Args:
            amount: Human-readable amount as float (e.g., 0.1, 1.0)

        Returns:
            Integer amount in token's smallest unit (wei equivalent)

        Example:
            For base token with 18 decimals:
            0.1 -> 100000000000000000 (10^17)
            1.0 -> 1000000000000000000 (10^18)
        """
        return int(amount * (10**self.base_token_decimals))

    def _convert_quote_amount(self, amount: float) -> int:
        """
        Convert float amount to quote token integer value based on decimals.

        Args:
            amount: Human-readable amount as float (e.g., 0.1, 1.0)

        Returns:
            Integer amount in token's smallest unit (wei equivalent)

        Example:
            For quote token with 6 decimals:
            0.1 -> 100000 (10^5)
            1.0 -> 1000000 (10^6)
        """
        return int(amount * (10**self.quote_token_decimals))

    # Balance query methods

    async def get_base_balance(self) -> int:
        """
        Get the user's base token balance.

        Returns:
            Base token balance in wei

        Raises:
            Exception: If balance query fails
        """
        try:
            if is_native_token(self.base_token_address):
                # Native token - query chain balance
                balance = await self.w3.eth.get_balance(self.user_address)
            else:
                # ERC20 token - query contract
                balance = await self.base_token_contract.functions.balanceOf(
                    self.user_address
                ).call()
            return balance
        except Exception as e:
            logger.error(f"Failed to get base token balance: {e}")
            raise

    async def get_quote_balance(self) -> int:
        """
        Get the user's quote token balance.

        Returns:
            Quote token balance in wei

        Raises:
            Exception: If balance query fails
        """
        try:
            if is_native_token(self.quote_token_address):
                # Native token - query chain balance
                balance = await self.w3.eth.get_balance(self.user_address)
            else:
                # ERC20 token - query contract
                balance = await self.quote_token_contract.functions.balanceOf(
                    self.user_address
                ).call()
            return balance
        except Exception as e:
            logger.error(f"Failed to get quote token balance: {e}")
            raise

    async def get_balances(self) -> tuple[int, int]:
        """
        Get both base and quote token balances.

        Returns:
            Tuple of (base_balance, quote_balance) in wei
        """
        base_balance = await self.get_base_balance()
        quote_balance = await self.get_quote_balance()
        logger.info(f"Balances - Base: {base_balance} wei, Quote: {quote_balance} wei")
        return (base_balance, quote_balance)

    # Margin balance query methods

    async def get_margin_base_balance(self) -> int:
        """
        Get the user's base token balance in the MARGIN CONTRACT.

        This is different from get_base_balance() which queries wallet balance.
        Orders require margin contract balance to be placed.

        Returns:
            Base token balance in margin contract (wei)

        Raises:
            Exception: If balance query fails
        """
        try:
            balance = await self.margin_contract.functions.getBalance(
                self.user_address, self.base_token_address
            ).call()
            return balance
        except Exception as e:
            logger.error(f"Failed to get margin base balance: {e}")
            raise

    async def get_margin_quote_balance(self) -> int:
        """
        Get the user's quote token balance in the MARGIN CONTRACT.

        This is different from get_quote_balance() which queries wallet balance.
        Orders require margin contract balance to be placed.

        Returns:
            Quote token balance in margin contract (wei)

        Raises:
            Exception: If balance query fails
        """
        try:
            balance = await self.margin_contract.functions.getBalance(
                self.user_address, self.quote_token_address
            ).call()
            return balance
        except Exception as e:
            logger.error(f"Failed to get margin quote balance: {e}")
            raise

    async def get_margin_balances(self) -> tuple[int, int]:
        """
        Get both base and quote token balances in the MARGIN CONTRACT.

        This is different from get_balances() which queries wallet balances.
        Use this to check if you have sufficient margin to place orders.

        Returns:
            Tuple of (base_balance, quote_balance) in wei
        """
        base_balance = await self.get_margin_base_balance()
        quote_balance = await self.get_margin_quote_balance()
        logger.info(
            f"Margin balances - Base: {base_balance} wei, Quote: {quote_balance} wei"
        )
        return (base_balance, quote_balance)

    # Active orders query method

    def get_active_orders(self) -> list[dict]:
        """
        Get active orders for the user from the Kuru API.

        Returns:
            List of active order dictionaries, or empty list if none found

        Raises:
            Exception: If API request fails
        """
        url = f"{self.kuru_api_url}/api/v2/{self.user_address}/user/orders/active/{self.market_address}?limit=100"

        try:
            response = requests.get(url)

            if response.status_code != 200:
                logger.error(
                    f"Failed to fetch active orders: {response.status_code} {response.text}"
                )
                raise Exception(
                    f"Failed to fetch active orders: HTTP {response.status_code}"
                )

            data = response.json()

            # Extract orders from nested data structure
            orders = data.get("data", {}).get("data", [])
            logger.debug(f"Found {len(orders)} active orders")

            return orders

        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise Exception(f"Failed to fetch active orders: {e}")

    # Allowance query methods

    async def get_base_allowance(self) -> int:
        """
        Get the margin contract's allowance for base token.

        Returns:
            Current allowance in wei. Returns 2**256 - 1 for native tokens
            (since they don't require approval).

        Raises:
            Exception: If allowance query fails
        """
        try:
            if is_native_token(self.base_token_address):
                # Native token doesn't require approval
                allowance = 2**256 - 1
                logger.debug("Base token is native - no approval needed")
            else:
                # ERC20 token - query allowance
                allowance = await self.base_token_contract.functions.allowance(
                    self.user_address, self.margin_contract_address
                ).call()
                logger.debug(
                    f"Base token allowance for margin contract: {allowance} wei"
                )
            return allowance
        except Exception as e:
            logger.error(f"Failed to get base token allowance: {e}")
            raise

    async def get_quote_allowance(self) -> int:
        """
        Get the margin contract's allowance for quote token.

        Returns:
            Current allowance in wei. Returns 2**256 - 1 for native tokens
            (since they don't require approval).

        Raises:
            Exception: If allowance query fails
        """
        try:
            if is_native_token(self.quote_token_address):
                # Native token doesn't require approval
                allowance = 2**256 - 1
                logger.debug("Quote token is native - no approval needed")
            else:
                # ERC20 token - query allowance
                allowance = await self.quote_token_contract.functions.allowance(
                    self.user_address, self.margin_contract_address
                ).call()
                logger.debug(
                    f"Quote token allowance for margin contract: {allowance} wei"
                )
            return allowance
        except Exception as e:
            logger.error(f"Failed to get quote token allowance: {e}")
            raise

    # _send_transaction is inherited from AsyncTransactionSenderMixin

    # Approval methods

    async def approve_base(self, amount: int) -> str:
        """
        Approve margin contract to spend base tokens.

        This method waits for the approval transaction to be confirmed on-chain
        before returning.

        Args:
            amount: Amount to approve in wei

        Returns:
            Transaction hash as hex string

        Raises:
            ValueError: If base token is native (doesn't need approval)
            Exception: If transaction fails
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        if is_native_token(self.base_token_address):
            logger.info("Base token is native - no approval needed")
            return None

        logger.info(
            f"Approving {amount} wei of base token for margin contract {self.margin_contract_address}"
        )

        function_call = self.base_token_contract.functions.approve(
            self.margin_contract_address, amount
        )
        tx_hash = await self._send_transaction(function_call, value=0)

        await self._wait_for_transaction_receipt(tx_hash)

        return tx_hash

    async def approve_quote(self, amount: int) -> str:
        """
        Approve margin contract to spend quote tokens.

        This method waits for the approval transaction to be confirmed on-chain
        before returning.

        Args:
            amount: Amount to approve in wei

        Returns:
            Transaction hash as hex string

        Raises:
            ValueError: If quote token is native (doesn't need approval)
            Exception: If transaction fails
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        if is_native_token(self.quote_token_address):
            logger.info("Quote token is native - no approval needed")
            return None

        logger.info(
            f"Approving {amount} wei of quote token for margin contract {self.margin_contract_address}"
        )

        function_call = self.quote_token_contract.functions.approve(
            self.margin_contract_address, amount
        )
        tx_hash = await self._send_transaction(function_call, value=0)

        await self._wait_for_transaction_receipt(tx_hash)

        return tx_hash

    async def approve_max_base(self) -> str:
        """
        Approve maximum uint256 amount of base tokens for margin contract.

        This method waits for the approval transaction to be confirmed on-chain
        before returning.

        Returns:
            Transaction hash as hex string

        Raises:
            ValueError: If base token is native
            Exception: If transaction fails
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        max_uint256 = 2**256 - 1
        logger.info(
            f"Approving max amount ({max_uint256}) of base token for margin contract"
        )
        return await self.approve_base(max_uint256)

    async def approve_max_quote(self) -> str:
        """
        Approve maximum uint256 amount of quote tokens for margin contract.

        This method waits for the approval transaction to be confirmed on-chain
        before returning.

        Returns:
            Transaction hash as hex string

        Raises:
            ValueError: If quote token is native
            Exception: If transaction fails
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        max_uint256 = 2**256 - 1
        logger.info(
            f"Approving max amount ({max_uint256}) of quote token for margin contract"
        )
        return await self.approve_quote(max_uint256)

    # Deposit methods

    async def deposit_base(self, amount: float, auto_approve: bool = True) -> str:
        """
        Deposit base tokens to margin account.

        For ERC20 tokens, automatically approves if needed when auto_approve=True.
        For native tokens, sends the amount as transaction value.

        This method waits for the deposit transaction (and approval if needed)
        to be confirmed on-chain before returning.

        Args:
            amount: Amount to deposit in human-readable units (e.g., 0.1, 1.0)
            auto_approve: If True, automatically approve if allowance insufficient

        Returns:
            Transaction hash as hex string

        Raises:
            ValueError: If allowance insufficient and auto_approve=False
            Exception: If transaction fails
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        # Convert float amount to integer wei value
        amount_wei = self._convert_base_amount(amount)

        is_native = is_native_token(self.base_token_address)

        # Handle approval for ERC20 tokens
        if not is_native:
            current_allowance = await self.get_base_allowance()
            if current_allowance < amount_wei:
                if auto_approve:
                    logger.info(
                        f"Auto-approving base token allowance: {amount_wei} wei (had {current_allowance} wei)"
                    )
                    await self.approve_base(amount_wei)
                else:
                    raise ValueError(
                        f"Insufficient allowance for base token. Need {amount_wei} wei, have {current_allowance} wei. "
                        f"Call approve_base() or set auto_approve=True"
                    )

        # Determine parameters for deposit call
        token_param = ZERO_ADDRESS if is_native else self.base_token_address
        value_param = amount_wei if is_native else 0

        # Pre-flight balance check for native token deposits
        if is_native and value_param > 0:
            current_balance = await self.w3.eth.get_balance(self.user_address)
            # Rough gas estimate (actual will be calculated by _send_transaction)
            estimated_gas = 100_000
            gas_price = await self.w3.eth.gas_price
            estimated_gas_cost = estimated_gas * gas_price
            total_required = value_param + estimated_gas_cost

            if current_balance < total_required:
                raise ValueError(
                    f"Insufficient native token balance for deposit:\n"
                    f"  Current balance: {current_balance} wei ({current_balance / 1e18:.6f} tokens)\n"
                    f"  Required: ~{total_required} wei (~{total_required / 1e18:.6f} tokens)\n"
                    f"    - Deposit amount: {value_param} wei ({amount} tokens)\n"
                    f"    - Estimated gas: ~{estimated_gas_cost} wei (~{estimated_gas_cost / 1e18:.6f} tokens)\n"
                    f"  Please add more native tokens to your wallet."
                )
            elif current_balance < total_required * 1.1:  # Less than 10% buffer
                logger.warning(
                    f"Low native token balance: {current_balance / 1e18:.6f} tokens. "
                    f"Required: ~{total_required / 1e18:.6f} tokens (including gas). "
                    f"Transaction may fail if gas costs are higher than estimated."
                )

        logger.info(
            f"Depositing {amount} base tokens ({amount_wei} wei) "
            f"({'native' if is_native else 'ERC20'}) to margin account {self.margin_contract_address}"
        )

        # Build and send deposit transaction
        function_call = self.margin_contract.functions.deposit(
            self.user_address, token_param, amount_wei
        )
        tx_hash = await self._send_transaction(function_call, value=value_param)

        await self._wait_for_transaction_receipt(tx_hash)

        return tx_hash

    async def deposit_quote(self, amount: float, auto_approve: bool = True) -> str:
        """
        Deposit quote tokens to margin account.

        For ERC20 tokens, automatically approves if needed when auto_approve=True.
        For native tokens, sends the amount as transaction value.

        This method waits for the deposit transaction (and approval if needed)
        to be confirmed on-chain before returning.

        Args:
            amount: Amount to deposit in human-readable units (e.g., 0.1, 1.0)
            auto_approve: If True, automatically approve if allowance insufficient

        Returns:
            Transaction hash as hex string

        Raises:
            ValueError: If allowance insufficient and auto_approve=False
            Exception: If transaction fails
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        # Convert float amount to integer wei value
        amount_wei = self._convert_quote_amount(amount)

        is_native = is_native_token(self.quote_token_address)

        # Handle approval for ERC20 tokens
        if not is_native:
            current_allowance = await self.get_quote_allowance()
            if current_allowance < amount_wei:
                if auto_approve:
                    logger.info(
                        f"Auto-approving quote token allowance: {amount_wei} wei (had {current_allowance} wei)"
                    )
                    await self.approve_quote(amount_wei)
                else:
                    raise ValueError(
                        f"Insufficient allowance for quote token. Need {amount_wei} wei, have {current_allowance} wei. "
                        f"Call approve_quote() or set auto_approve=True"
                    )

        # Determine parameters for deposit call
        token_param = ZERO_ADDRESS if is_native else self.quote_token_address
        value_param = amount_wei if is_native else 0

        # Pre-flight balance check for native token deposits
        if is_native and value_param > 0:
            current_balance = await self.w3.eth.get_balance(self.user_address)
            # Rough gas estimate (actual will be calculated by _send_transaction)
            estimated_gas = 100_000
            gas_price = await self.w3.eth.gas_price
            estimated_gas_cost = estimated_gas * gas_price
            total_required = value_param + estimated_gas_cost

            if current_balance < total_required:
                raise ValueError(
                    f"Insufficient native token balance for deposit:\n"
                    f"  Current balance: {current_balance} wei ({current_balance / 1e18:.6f} tokens)\n"
                    f"  Required: ~{total_required} wei (~{total_required / 1e18:.6f} tokens)\n"
                    f"    - Deposit amount: {value_param} wei ({amount} tokens)\n"
                    f"    - Estimated gas: ~{estimated_gas_cost} wei (~{estimated_gas_cost / 1e18:.6f} tokens)\n"
                    f"  Please add more native tokens to your wallet."
                )
            elif current_balance < total_required * 1.1:  # Less than 10% buffer
                logger.warning(
                    f"Low native token balance: {current_balance / 1e18:.6f} tokens. "
                    f"Required: ~{total_required / 1e18:.6f} tokens (including gas). "
                    f"Transaction may fail if gas costs are higher than estimated."
                )

        logger.info(
            f"Depositing {amount} quote tokens ({amount_wei} wei) "
            f"({'native' if is_native else 'ERC20'}) to margin account {self.margin_contract_address}"
        )

        # Build and send deposit transaction
        function_call = self.margin_contract.functions.deposit(
            self.user_address, token_param, amount_wei
        )
        tx_hash = await self._send_transaction(function_call, value=value_param)

        await self._wait_for_transaction_receipt(tx_hash)

        return tx_hash

    # Withdraw methods

    async def withdraw_base(self, amount: float) -> str:
        """
        Withdraw base tokens from margin account.

        This method waits for the withdrawal transaction to be confirmed
        on-chain before returning.

        Args:
            amount: Amount to withdraw in human-readable units (e.g., 0.1, 1.0)

        Returns:
            Transaction hash as hex string

        Raises:
            Exception: If transaction fails (e.g., insufficient margin balance)
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        # Convert float amount to integer wei value
        amount_wei = self._convert_base_amount(amount)

        is_native = is_native_token(self.base_token_address)
        token_param = ZERO_ADDRESS if is_native else self.base_token_address

        logger.info(
            f"Withdrawing {amount} base tokens ({amount_wei} wei) "
            f"({'native' if is_native else 'ERC20'}) from margin account {self.margin_contract_address}"
        )

        # Build and send withdraw transaction
        function_call = self.margin_contract.functions.withdraw(amount_wei, token_param)
        tx_hash = await self._send_transaction(function_call, value=0)

        await self._wait_for_transaction_receipt(tx_hash)

        return tx_hash

    async def withdraw_quote(self, amount: float) -> str:
        """
        Withdraw quote tokens from margin account.

        This method waits for the withdrawal transaction to be confirmed
        on-chain before returning.

        Args:
            amount: Amount to withdraw in human-readable units (e.g., 0.1, 1.0)

        Returns:
            Transaction hash as hex string

        Raises:
            Exception: If transaction fails (e.g., insufficient margin balance)
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        # Convert float amount to integer wei value
        amount_wei = self._convert_quote_amount(amount)

        is_native = is_native_token(self.quote_token_address)
        token_param = ZERO_ADDRESS if is_native else self.quote_token_address

        logger.info(
            f"Withdrawing {amount} quote tokens ({amount_wei} wei) "
            f"({'native' if is_native else 'ERC20'}) from margin account {self.margin_contract_address}"
        )

        # Build and send withdraw transaction
        function_call = self.margin_contract.functions.withdraw(amount_wei, token_param)
        tx_hash = await self._send_transaction(function_call, value=0)

        await self._wait_for_transaction_receipt(tx_hash)

        return tx_hash

    # EIP-7702 Authorization

    async def eip_7702_auth(self, nonce: Optional[int] = None) -> str:
        """
        Create and send an EIP-7702 authorization transaction.

        This authorizes the MM Entrypoint contract to execute code on behalf of the user's EOA.
        The authorization is persistent and remains active until explicitly revoked using
        eip_7702_revoke().

        This method waits for the authorization transaction to be confirmed on-chain
        before returning.

        Args:
            nonce: Optional nonce to use for authorization. If None, uses current nonce.
                   For persistent authorization, use current nonce (default).

        Returns:
            Transaction hash as hex string

        Raises:
            Exception: If authorization or transaction fails
            TimeoutError: If transaction not confirmed within timeout (120s)
        """

        logger.success(f"Using account: {self.account.address}")

        # Fetch and log native balance before authorization
        native_balance = await self.w3.eth.get_balance(self.user_address)
        native_balance_readable = native_balance / 1e18
        logger.info(
            f"Native balance: {native_balance} wei ({native_balance_readable:.6f} tokens)"
        )

        try:
            # Get chain ID and nonce
            chain_id = await self.w3.eth.chain_id

            if nonce is None:
                nonce = await self.w3.eth.get_transaction_count(self.user_address)
                logger.info(f"Using current nonce for authorization: {nonce}")
            else:
                logger.info(f"Using provided nonce for authorization: {nonce}")

            logger.info(
                f"Creating EIP-7702 authorization for MM Entrypoint {self.mm_entrypoint_address} "
                f"on chain {chain_id}"
            )

            # Build authorization
            authorization = {
                "chainId": chain_id,
                "address": self.mm_entrypoint_address,
                "nonce": nonce + 1,
            }

            # Sign authorization with private key
            signed_auth = self.account.sign_authorization(authorization)
            logger.debug(f"Signed authorization created: {type(signed_auth).__name__}")

            # Get gas price parameters
            max_priority_fee = await self.w3.eth.max_priority_fee
            latest_block = await self.w3.eth.get_block("latest")
            base_fee = latest_block["baseFeePerGas"]
            max_fee = base_fee * 2 + max_priority_fee  # 2x base fee + priority

            logger.info(f"Gas price parameters:")
            logger.info(f"  Base fee: {base_fee} wei ({base_fee / 1e9:.2f} gwei)")
            logger.info(
                f"  Max priority fee: {max_priority_fee} wei ({max_priority_fee / 1e9:.2f} gwei)"
            )
            logger.info(f"  Max fee per gas: {max_fee} wei ({max_fee / 1e9:.2f} gwei)")

            # Build type 4 transaction
            tx = {
                "chainId": chain_id,
                "nonce": nonce,
                "gas": 100_000,  # Reasonable default for simple authorization
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority_fee,
                "to": self.user_address,  # Send to MM Entrypoint contract
                "value": 0,
                "authorizationList": [signed_auth],
                "data": b"",
            }

            # Estimate gas for type 4 transaction
            try:
                estimated_gas = await self.w3.eth.estimate_gas(tx)
                tx["gas"] = int(estimated_gas)
                logger.debug(f"Estimated gas: {estimated_gas}, using: {tx['gas']}")
            except Exception as e:
                logger.warning(f"Gas estimation failed, using default: {e}")
                # Keep default gas limit

            # Pre-flight balance check
            estimated_gas_cost = tx["gas"] * tx["maxFeePerGas"]
            total_required = tx["value"] + estimated_gas_cost

            logger.info(f"Transaction cost estimate:")
            logger.info(f"  Gas: {tx['gas']}")
            logger.info(
                f"  Gas cost: {estimated_gas_cost} wei ({estimated_gas_cost / 1e18:.6f} tokens)"
            )
            logger.info(
                f"  Total required: {total_required} wei ({total_required / 1e18:.6f} tokens)"
            )

            if native_balance < total_required:
                raise ValueError(
                    f"Insufficient native token balance for EIP-7702 authorization:\n"
                    f"  Current balance: {native_balance} wei ({native_balance / 1e18:.6f} tokens)\n"
                    f"  Required: {total_required} wei ({total_required / 1e18:.6f} tokens)\n"
                    f"    - Transaction value: {tx['value']} wei\n"
                    f"    - Estimated gas cost: {estimated_gas_cost} wei ({estimated_gas_cost / 1e18:.6f} tokens)\n"
                    f"  Shortfall: {total_required - native_balance} wei ({(total_required - native_balance) / 1e18:.6f} tokens)\n"
                    f"  Please add more native tokens to your wallet."
                )

            # Sign transaction
            signed_tx = self.account.sign_transaction(tx)

            # Send transaction
            tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()

            logger.info(f"EIP-7702 authorization transaction sent: {tx_hash_hex}")

            # Wait for transaction confirmation
            tx_receipt = await self._wait_for_transaction_receipt(tx_hash_hex)
            logger.info(
                f"EIP-7702 authorization confirmed in block {tx_receipt['blockNumber']}"
            )

            return tx_receipt

        except Exception as e:
            logger.error(f"Failed to create EIP-7702 authorization: {e}")
            raise Exception(f"EIP-7702 authorization failed: {e}")

    async def eip_7702_revoke(self, nonce: Optional[int] = None) -> str:
        """
        Revoke EIP-7702 authorization by removing delegation to MM Entrypoint.

        This sends a revocation authorization using the zero address as the delegation target.
        According to EIP-7702 specification, setting the authorization address to
        0x0000000000000000000000000000000000000000 clears the account's code and resets
        the code hash to empty, removing the smart contract delegation.

        This restores the EOA to its original state without any delegation active.

        This method waits for the revocation transaction to be confirmed on-chain
        before returning.

        Args:
            nonce: Optional nonce to use for revocation. If None, uses current nonce.
                   For immediate revocation, use current nonce (default).

        Returns:
            Transaction hash as hex string

        Raises:
            Exception: If revocation or transaction fails
            TimeoutError: If transaction not confirmed within timeout (120s)
        """
        try:
            # Get chain ID and nonce
            chain_id = await self.w3.eth.chain_id

            if nonce is None:
                nonce = await self.w3.eth.get_transaction_count(self.user_address)
                logger.info(f"Using current nonce for revocation: {nonce}")
            else:
                logger.info(f"Using provided nonce for revocation: {nonce}")

            logger.info(
                f"Revoking EIP-7702 authorization (clearing delegation) "
                f"on chain {chain_id}"
            )

            # Build revocation authorization with zero address
            revocation_auth = {
                "chainId": chain_id,
                "address": ZERO_ADDRESS,  # Zero address triggers revocation
                "nonce": nonce + 1,
            }

            # Sign revocation authorization with private key
            signed_auth = self.account.sign_authorization(revocation_auth)
            logger.debug(
                f"Signed revocation authorization created: {type(signed_auth).__name__}"
            )

            # Get gas price parameters
            max_priority_fee = await self.w3.eth.max_priority_fee
            latest_block = await self.w3.eth.get_block("latest")
            base_fee = latest_block["baseFeePerGas"]
            max_fee = base_fee * 2 + max_priority_fee  # 2x base fee + priority

            # Build type 4 transaction
            tx = {
                "chainId": chain_id,
                "nonce": nonce,
                "gas": 100_000,  # Reasonable default for revocation
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority_fee,
                "to": self.user_address,  # Send to MM Entrypoint contract
                "value": 0,
                "authorizationList": [signed_auth],
                "data": b"",
            }

            # Estimate gas for type 4 transaction
            try:
                estimated_gas = await self.w3.eth.estimate_gas(tx)
                tx["gas"] = int(estimated_gas)
                logger.debug(f"Estimated gas: {estimated_gas}, using: {tx['gas']}")
            except Exception as e:
                logger.warning(f"Gas estimation failed, using default: {e}")
                # Keep default gas limit

            # Sign transaction
            signed_tx = self.account.sign_transaction(tx)

            # Send transaction
            tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = tx_hash.hex()

            logger.info(f"EIP-7702 revocation transaction sent: {tx_hash_hex}")

            # Wait for transaction confirmation
            await self._wait_for_transaction_receipt(tx_hash_hex)
            logger.info(f"EIP-7702 revocation confirmed")

            return tx_hash_hex

        except Exception as e:
            logger.error(f"Failed to revoke EIP-7702 authorization: {e}")
            raise Exception(f"EIP-7702 revocation failed: {e}")

    async def close(self) -> None:
        """Close the HTTP provider session."""
        try:
            if hasattr(self.w3.provider, "_session") and self.w3.provider._session:
                await self.w3.provider._session.close()
        except Exception:
            pass

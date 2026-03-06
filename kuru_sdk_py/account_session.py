from loguru import logger
from web3 import AsyncWeb3, AsyncHTTPProvider, Web3

from kuru_sdk_py.configs import ConnectionConfig, WalletConfig, TransactionConfig
from kuru_sdk_py.transaction.transaction import AsyncTransactionSenderMixin


class AccountSession(AsyncTransactionSenderMixin):
    """Shared account-scoped transport and signing state."""

    def __init__(
        self,
        connection_config: ConnectionConfig,
        wallet_config: WalletConfig,
        transaction_config: TransactionConfig,
    ) -> None:
        self.connection_config = connection_config
        self.wallet_config = wallet_config
        self.transaction_config = transaction_config

        self.user_address = Web3.to_checksum_address(wallet_config.user_address)
        self.w3 = AsyncWeb3(AsyncHTTPProvider(connection_config.rpc_url))
        self.account = self.w3.eth.account.from_key(wallet_config.private_key)

    async def close(self) -> None:
        """Close shared workers and HTTP session."""
        try:
            await self.stop_gas_price_worker()
        except Exception as e:
            logger.debug(f"Error stopping shared gas worker: {e}")

        try:
            if hasattr(self.w3.provider, "_session") and self.w3.provider._session:
                await self.w3.provider._session.close()
        except Exception as e:
            logger.debug(f"Error closing shared HTTP provider session: {e}")

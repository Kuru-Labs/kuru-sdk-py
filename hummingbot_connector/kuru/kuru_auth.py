"""Authentication wrapper for Kuru DEX connector.

Holds the private key and derives the wallet address.
Blockchain authentication (EIP-7702) is handled by the Kuru SDK itself.
"""

from eth_account import Account

from src.configs import WalletConfig


class KuruAuth:
    """Simple credential holder for Kuru DEX blockchain auth."""

    def __init__(self, private_key: str):
        self._private_key = private_key
        account = Account.from_key(private_key)
        self._wallet_address: str = account.address

    @property
    def wallet_address(self) -> str:
        return self._wallet_address

    @property
    def private_key(self) -> str:
        return self._private_key

    def get_wallet_config(self) -> WalletConfig:
        """Return a Kuru SDK WalletConfig for client initialization."""
        return WalletConfig(
            private_key=self._private_key,
            user_address=self._wallet_address,
        )

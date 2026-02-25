"""Custom exception hierarchy for the Kuru SDK."""

from __future__ import annotations

from typing import Optional


class KuruError(Exception):
    """Base exception for all SDK-specific errors."""


class KuruConfigError(KuruError):
    """Raised for invalid or missing SDK configuration."""


class KuruConnectionError(KuruError):
    """Raised for network or connectivity failures."""


class KuruWebSocketError(KuruConnectionError):
    """Raised for websocket-specific connection failures."""


class KuruTransactionError(KuruError):
    """Raised for transaction submission/confirmation failures."""

    def __init__(
        self,
        message: str,
        tx_hash: Optional[str] = None,
        revert_reason: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.tx_hash = tx_hash
        self.revert_reason = revert_reason


class KuruContractError(KuruTransactionError):
    """Raised when a contract call/transaction reverts with contract error data."""

    def __init__(
        self,
        message: str,
        tx_hash: Optional[str] = None,
        revert_reason: Optional[str] = None,
        selector: Optional[str] = None,
    ) -> None:
        super().__init__(message, tx_hash=tx_hash, revert_reason=revert_reason)
        self.selector = selector


class KuruInsufficientFundsError(KuruTransactionError):
    """Raised when wallet balance is insufficient for value + gas."""


class KuruAuthorizationError(KuruError):
    """Raised when authorization/revocation flows fail."""


class KuruOrderError(KuruError):
    """Raised for order placement, cancellation, or validation failures."""


class KuruTimeoutError(KuruError):
    """Raised when an operation exceeds configured timeout."""

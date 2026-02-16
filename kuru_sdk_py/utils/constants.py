"""Constants and utility functions for common values used across the codebase."""

# Zero address constant - used to represent native token (e.g., MON/ETH)
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def is_native_token(address: str) -> bool:
    """Check if the given address represents a native token (zero address).

    Args:
        address: The token address to check

    Returns:
        True if the address is the zero address (native token), False otherwise
    """
    return address.lower() == ZERO_ADDRESS.lower()

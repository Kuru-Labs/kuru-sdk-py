"""
Validation utilities for configuration values

This module provides reusable validation functions for config values,
ensuring consistency across all config classes and providing clear
error messages when validation fails.
"""

from typing import Any, Optional
from web3 import Web3


def validate_ethereum_address(address: str, field_name: str = "address") -> str:
    """
    Validate and normalize an Ethereum address to checksum format.

    Args:
        address: The Ethereum address to validate (with or without 0x prefix)
        field_name: Name of the field being validated (for error messages)

    Returns:
        Checksummed Ethereum address

    Raises:
        ValueError: If address format is invalid

    Examples:
        >>> validate_ethereum_address("0xA9d8269ad1Bd6e2a02BD8996a338Dc5C16aef440")
        '0xA9d8269ad1Bd6e2a02BD8996a338Dc5C16aef440'

        >>> validate_ethereum_address("0xinvalid")
        ValueError: Invalid Ethereum address for 'address': ...
    """
    if not address:
        raise ValueError(f"{field_name} cannot be empty")

    try:
        # Add 0x prefix if missing
        if not address.startswith("0x"):
            address = f"0x{address}"

        # Validate and checksum
        checksummed = Web3.to_checksum_address(address)
        return checksummed
    except Exception as e:
        raise ValueError(
            f"Invalid Ethereum address for '{field_name}': {address}. "
            f"Expected 40 hex characters (with optional 0x prefix). Error: {e}"
        )


def validate_private_key(private_key: str, field_name: str = "private_key") -> str:
    """
    Validate private key format and length.

    Args:
        private_key: The private key to validate (with or without 0x prefix)
        field_name: Name of the field being validated (for error messages)

    Returns:
        Private key with 0x prefix

    Raises:
        ValueError: If private key format is invalid

    Examples:
        >>> validate_private_key("0x" + "a" * 64)
        '0x...'

        >>> validate_private_key("invalid")
        ValueError: Invalid private key format
    """
    if not private_key:
        raise ValueError(f"{field_name} cannot be empty")

    # Add 0x prefix if missing
    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    # Validate length (0x + 64 hex characters = 66 total)
    if len(private_key) != 66:
        raise ValueError(
            f"Invalid private key format for '{field_name}'. "
            f"Expected 32 bytes (64 hex characters), got {len(private_key) - 2} characters. "
            f"Private key should be 64 hex characters (with optional 0x prefix)."
        )

    # Validate hex format
    try:
        int(private_key, 16)
    except ValueError:
        raise ValueError(
            f"Invalid private key format for '{field_name}'. "
            f"Private key must contain only hexadecimal characters (0-9, a-f)."
        )

    return private_key


def validate_required_field(value: Any, field_name: str) -> None:
    """
    Ensure a required field is not None or empty.

    Args:
        value: The value to check
        field_name: Name of the field being validated (for error messages)

    Raises:
        ValueError: If value is None or empty

    Examples:
        >>> validate_required_field("value", "market_address")
        # No error

        >>> validate_required_field(None, "market_address")
        ValueError: market_address is required

        >>> validate_required_field("", "market_address")
        ValueError: market_address cannot be empty
    """
    if value is None:
        raise ValueError(
            f"{field_name} is required. "
            f"Please provide it as an argument or set the {field_name.upper()} environment variable."
        )

    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field_name} cannot be empty")


def validate_positive_number(
    value: float,
    field_name: str,
    allow_zero: bool = False,
    value_type: str = "number"
) -> None:
    """
    Validate that a number is positive (and optionally non-zero).

    Args:
        value: The number to validate
        field_name: Name of the field being validated (for error messages)
        allow_zero: Whether zero is acceptable (default: False)
        value_type: Type description for error message (e.g., "timeout", "delay")

    Raises:
        ValueError: If value is negative or zero (when not allowed)

    Examples:
        >>> validate_positive_number(120, "timeout", value_type="seconds")
        # No error

        >>> validate_positive_number(0, "timeout")
        ValueError: timeout must be positive

        >>> validate_positive_number(0, "timeout", allow_zero=True)
        # No error

        >>> validate_positive_number(-1, "timeout")
        ValueError: timeout must be positive
    """
    if allow_zero:
        if value < 0:
            raise ValueError(
                f"{field_name} must be non-negative, got {value}. "
                f"Expected a {value_type} >= 0."
            )
    else:
        if value <= 0:
            raise ValueError(
                f"{field_name} must be positive, got {value}. "
                f"Expected a {value_type} > 0."
            )


def validate_url_format(url: str, field_name: str = "url") -> None:
    """
    Validate URL format (basic check for http/https/ws/wss scheme).

    Args:
        url: The URL to validate
        field_name: Name of the field being validated (for error messages)

    Raises:
        ValueError: If URL format is invalid

    Examples:
        >>> validate_url_format("https://rpc.kuru.io")
        # No error

        >>> validate_url_format("wss://ws.kuru.io")
        # No error

        >>> validate_url_format("invalid-url")
        ValueError: Invalid URL format
    """
    if not url:
        raise ValueError(f"{field_name} cannot be empty")

    valid_schemes = ("http://", "https://", "ws://", "wss://")
    if not url.startswith(valid_schemes):
        raise ValueError(
            f"Invalid URL format for '{field_name}': {url}. "
            f"URL must start with one of: {', '.join(valid_schemes)}"
        )


def validate_percentage(value: float, field_name: str) -> None:
    """
    Validate that a value is a valid percentage (0.0 to 100.0).

    Args:
        value: The percentage value to validate
        field_name: Name of the field being validated (for error messages)

    Raises:
        ValueError: If value is not between 0 and 100

    Examples:
        >>> validate_percentage(10.5, "slippage")
        # No error

        >>> validate_percentage(150, "slippage")
        ValueError: slippage must be between 0 and 100
    """
    if not (0.0 <= value <= 100.0):
        raise ValueError(
            f"{field_name} must be between 0 and 100 (inclusive), got {value}"
        )


def validate_multiplier(value: float, field_name: str, min_value: float = 1.0) -> None:
    """
    Validate that a multiplier is >= min_value (typically 1.0).

    Args:
        value: The multiplier to validate
        field_name: Name of the field being validated (for error messages)
        min_value: Minimum acceptable value (default: 1.0)

    Raises:
        ValueError: If value is less than min_value

    Examples:
        >>> validate_multiplier(1.1, "gas_buffer_multiplier")
        # No error

        >>> validate_multiplier(0.9, "gas_buffer_multiplier")
        ValueError: gas_buffer_multiplier must be >= 1.0
    """
    if value < min_value:
        raise ValueError(
            f"{field_name} must be >= {min_value}, got {value}. "
            f"A multiplier less than {min_value} would reduce the value instead of increasing it."
        )


def validate_boolean_env(value: str, field_name: str) -> bool:
    """
    Parse and validate a boolean value from environment variable.

    Accepts: "true", "1", "yes", "on" (case-insensitive) as True
             "false", "0", "no", "off" (case-insensitive) as False

    Args:
        value: The string value to parse
        field_name: Name of the field being validated (for error messages)

    Returns:
        Parsed boolean value

    Raises:
        ValueError: If value cannot be parsed as boolean

    Examples:
        >>> validate_boolean_env("true", "post_only")
        True

        >>> validate_boolean_env("FALSE", "post_only")
        False

        >>> validate_boolean_env("1", "post_only")
        True

        >>> validate_boolean_env("invalid", "post_only")
        ValueError: Invalid boolean value
    """
    value_lower = value.strip().lower()

    if value_lower in ("true", "1", "yes", "on"):
        return True
    elif value_lower in ("false", "0", "no", "off"):
        return False
    else:
        raise ValueError(
            f"Invalid boolean value for '{field_name}': {value}. "
            f"Expected one of: true, false, 1, 0, yes, no, on, off (case-insensitive)"
        )

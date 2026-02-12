from loguru import logger
import json
from pathlib import Path


def load_abi(abi_name: str) -> list:
    abi_path = Path(__file__).parent.parent / "abis" / f"{abi_name}.json"
    with open(abi_path) as f:
        abi_data = json.load(f)
        return abi_data["abi"] if "abi" in abi_data else abi_data


def string_to_bytes32(text: str) -> bytes:
    """
    Convert a string to bytes32 format for Solidity contracts.

    Args:
        text: String to convert (max 32 bytes when encoded)

    Returns:
        32-byte padded bytes value

    Raises:
        ValueError: If text is longer than 32 bytes when encoded
    """
    # Encode string to bytes
    encoded = text.encode('utf-8')

    # Check length
    if len(encoded) > 32:
        raise ValueError(f"String '{text}' is too long for bytes32 (max 32 bytes, got {len(encoded)})")

    # Right-pad with zeros to 32 bytes
    return encoded.ljust(32, b'\x00')


def bytes32_to_string(data: bytes) -> str:
    """
    Convert bytes32 format from Solidity contracts back to a string.

    Args:
        data: bytes32 value (32 bytes, possibly right-padded with zeros)

    Returns:
        Decoded UTF-8 string with trailing null bytes removed

    Raises:
        ValueError: If data is not 32 bytes
    """
    if len(data) != 32:
        raise ValueError(f"Expected 32 bytes for bytes32, got {len(data)}")

    # Remove trailing null bytes
    trimmed = data.rstrip(b'\x00')

    # Decode to UTF-8 string
    return trimmed.decode('utf-8')


def normalize_hex(value) -> str | None:
    """
    Normalize hex values to lowercase strings.

    Args:
        value: The value to normalize (str, bytes, or object with hex() method)

    Returns:
        Lowercase hex string or None
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.lower()
    if hasattr(value, "hex"):
        try:
            hx = value.hex()
            if isinstance(hx, str):
                return hx.lower()
        except Exception:
            pass
    if isinstance(value, (bytes, bytearray)):
        return ("0x" + bytes(value).hex()).lower()
    return str(value).lower()


def parse_web3_subscription_message(data: dict) -> tuple[str | None, dict | None]:
    """
    Parse subscription message from various web3 formats.

    Supports multiple JSON-RPC message formats:
    - Raw: {"method":"eth_subscription","params":{"subscription":..., "result":...}}
    - Direct: {"subscription":..., "result":...}
    - Nested: {"params":{"subscription":..., "result":...}}

    Args:
        data: The parsed JSON-RPC message

    Returns:
        Tuple of (subscription_id, log) or (None, None)
    """
    if not isinstance(data, dict):
        return None, None

    # Raw JSON-RPC format
    if data.get("method") == "eth_subscription":
        params = data.get("params", {})
        if isinstance(params, dict):
            return params.get("subscription"), params.get("result")

    # Direct format
    if "subscription" in data and "result" in data:
        return data.get("subscription"), data.get("result")

    # Nested params format
    params = data.get("params")
    if isinstance(params, dict) and "subscription" in params:
        return params.get("subscription"), params.get("result")

    return None, None


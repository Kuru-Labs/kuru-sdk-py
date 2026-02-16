contract_errors = {
  "0xbb55fd27": "Insufficient Liquidity",
  "0x3cd146b1": "Invalid Spread",
  "0xff633a38": "Length Mismatch",
  "0xa9269545": "Market Fee Error",
  "0x004b65ba": "Market State Error",
  "0xfd993161": "NativeAssetInsufficient",
  "0xead59376": "Native Asset Not Required",
  "0x70d7ec56": "Native Asset Transfer Failed",
  "0xa0cdd781": "Only Owner Allowed",
  "0xb1460438": "Only Vault Allowed",
  "0x829f7240": "Order Already Filled Or Cancelled",
  "0x06e6da4d": "Post Only Error",
  "0x91f53656": "Price Error",
  "0x0a5c4f1f": "Size Error",
  "0x8199f5f3": "Slippage Exceeded",
  "0x272d3bf7": "Tick Size Error",
  "0x0b252431": "Too Much Size Filled",
  "0x7939f424": "Transfer From Failed",
  "0xf4d678b8": "Insufficient Balance",
  "0xcd41a9e3": "NativeAssetMismatch",
  "0xe84c4d58": "Only Router Allowed",
  "0xe8430787": "Only Verified Markets Allowed",
  "0x8579befe": "Zero Address Not Allowed",
  "0x130e7978": "Base And Quote Asset Same",
  "0x9db8d5b1": "Invalid Market",
  "0xd09b273e": "No Markets Passed",
  "0xd226f9d4": "Insufficient Liquidity Minted",
  "0xb9873846": "Insufficient Quote Token",
  "0x40accb6f": "Uint32Overflow",
  "0x05d13eef": "Vault Deposit Price Crosses OrderBook",
  "0xd8415400": "Vault Liquidity Insufficient",
  "0x98de0cd0": "Vault Deposit Uses Invalid Price",
  "0xbb2b4138": "Incorrect Order Type Passed",
  "0x6a2628d9": "New Size Exceeds Partially Filled Size",
  "0xf8aa715b": "KuruFlowEntrypoint_BuyAndSellTokensAreSame",
  "0x5264a63f": "KuruFlowEntrypoint_InsufficientAmountAfterFees",
  "0x266ae8e1": "KuruFlowEntrypoint_InsufficientNativeValue",
  "0x0bc52ea8": "KuruFlowEntrypoint_InvalidFeeCollector",
  "0x0040cf18": "KuruFlowEntrypoint_InvalidFeeStructure",
  "0x4d1e7c56": "KuruFlowEntrypoint_InvalidReferrer",
  "0xfa1b73c8": "KuruFlowEntrypoint_InvalidRouter",
  "0x1e4fbdf7": "OwnableInvalidOwner",
  "0x118cdaa7": "OwnableUnauthorizedAccount",
  "0x3ee5aeb5": "ReentrancyGuardReentrantCall",
  "0x3e3f8f73": "ApproveFailed",
  "0x25f0fa4c": "ApproveResetFailed",
  "0xdbc3a71f": "InsufficientNativeBalance",
  "0xbf337638": "InvalidOpCode",
  "0x2946cbf1": "InvalidPoolType",
  "0x2366c6ba": "InvalidTokenForUnwrap",
  "0x2b2def77": "InvalidTokenForWrap",
  "0xa0c968e7": "NativeSendFailed",
  "0x5274afe7": "SafeERC20FailedOperation",
  "0x936bb5ad": "TokenMismatch",
  "0xe233e012": "Uint96Overflow",
  "0xf0cbbb4b": "UniV3CallbackInvalidSource",
  "0x6d09f943": "UniV3CallbackMissed",
  "0x24292634": "UniV3CallbackNegativeAmount",
  "0x1e2ce7e0": "ZeroRouterBalance",
  "0x947d5a84": "InvalidLength",
  "0xb4fa3fb3": "InvalidInput",
  "0x81ceff30": "SwapFailed",
  "0x52465b1c": "SharesMintedZero",
  "0x31c14458": "SharesBurnedZero",
  "0xf4b3b1bc": "NativeTransferFailed",
  "0xa075c656": "WithdrawalExceedsRestingBalance",
  "0xbc6ff8a8": "WithdrawSwapMinOutNotMet",
  "0x9766eeb0": "GasCrankCooldown",
  "0x2765c58a": "GasCrankWithdrawExceedsMax",
  "0x0f2e5b6c": "DepositCooldownActive"
}


import re
from typing import Optional


def extract_error_selector(error_data) -> Optional[str]:
    """
    Extract 4-byte error selector from various error data formats.

    Handles:
    - Hex strings (0xbb55fd27...)
    - Exception args containing hex data
    - Dictionary with 'data' key
    - String representations of exceptions

    Args:
        error_data: Raw error data from exception or transaction

    Returns:
        Error selector as "0x" prefixed hex string (10 chars) or None if not found
    """
    try:
        # Handle None or empty input
        if not error_data:
            return None

        # If it's an exception, try to get data from args
        if hasattr(error_data, 'args') and error_data.args:
            error_data = error_data.args[0]

        # Handle dictionary format (common in web3.py errors)
        if isinstance(error_data, dict):
            if 'data' in error_data:
                hex_data = error_data['data']
                if isinstance(hex_data, str) and hex_data.startswith('0x') and len(hex_data) >= 10:
                    return hex_data[:10].lower()
            # Some RPC providers put error in 'message' field
            if 'message' in error_data:
                error_data = error_data['message']

        # Convert to string for pattern matching
        error_str = str(error_data)

        # Look for hex pattern in string (e.g., "execution reverted: 0xbb55fd27")
        match = re.search(r'0x[0-9a-fA-F]{8,}', error_str)
        if match:
            # Return first 10 characters (0x + 8 hex chars = error selector)
            return match.group(0)[:10].lower()

        # If the string itself is a valid hex selector
        if isinstance(error_data, str) and error_data.startswith('0x') and len(error_data) >= 10:
            return error_data[:10].lower()

    except Exception:
        # Defensive: never crash, just return None
        pass

    return None


def decode_contract_error(error_data) -> Optional[str]:
    """
    Decode contract error to human-readable message.

    Args:
        error_data: Raw error data from exception or transaction

    Returns:
        Human-readable error message or None if selector not found
    """
    selector = extract_error_selector(error_data)

    if not selector:
        return None

    # Look up in known errors dictionary
    if selector in contract_errors:
        return f"{contract_errors[selector]} (error: {selector})"
    else:
        return f"Unknown contract error (error: {selector})"


def format_error_message(base_message: str, error_data, include_selector: bool = True) -> str:
    """
    Format complete error message with decoded contract error.

    Args:
        base_message: Base error description
        error_data: Raw error data to decode
        include_selector: Whether to include hex selector (default: True)

    Returns:
        Formatted error message combining base + decoded error
    """
    decoded = decode_contract_error(error_data)

    if decoded:
        return f"{base_message}: {decoded}"
    else:
        return base_message
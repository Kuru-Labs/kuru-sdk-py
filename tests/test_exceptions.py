from kuru_sdk_py.exceptions import (
    KuruAuthorizationError,
    KuruConfigError,
    KuruConnectionError,
    KuruContractError,
    KuruError,
    KuruInsufficientFundsError,
    KuruOrderError,
    KuruTimeoutError,
    KuruTransactionError,
    KuruWebSocketError,
)
from kuru_sdk_py.utils.errors import decode_contract_error, extract_error_selector


def test_exception_hierarchy():
    assert issubclass(KuruConfigError, KuruError)
    assert issubclass(KuruConnectionError, KuruError)
    assert issubclass(KuruWebSocketError, KuruConnectionError)
    assert issubclass(KuruTransactionError, KuruError)
    assert issubclass(KuruContractError, KuruTransactionError)
    assert issubclass(KuruInsufficientFundsError, KuruTransactionError)
    assert issubclass(KuruAuthorizationError, KuruError)
    assert issubclass(KuruOrderError, KuruError)
    assert issubclass(KuruTimeoutError, KuruError)


def test_kuru_contract_error_carries_selector_and_reason():
    selector = "0xbb55fd27"
    error = KuruContractError(
        "Contract reverted",
        tx_hash="0x123",
        revert_reason="Insufficient Liquidity",
        selector=selector,
    )
    assert error.tx_hash == "0x123"
    assert error.revert_reason == "Insufficient Liquidity"
    assert error.selector == selector


def test_contract_error_utils_integrate_with_kuru_contract_error():
    raw_error = "execution reverted: 0xbb55fd27"
    selector = extract_error_selector(raw_error)
    decoded = decode_contract_error(raw_error)

    error = KuruContractError(
        "Transaction would revert",
        revert_reason=decoded,
        selector=selector,
    )

    assert error.selector == "0xbb55fd27"
    assert error.revert_reason is not None
    assert "Insufficient Liquidity" in error.revert_reason

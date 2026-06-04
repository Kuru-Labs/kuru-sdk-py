from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kuru_sdk_py.configs import TransactionConfig
from kuru_sdk_py.transaction.transaction import (
    AsyncTransactionSenderMixin,
    LocalGasCounts,
)


class _TxHash:
    def __init__(self, value: str):
        self._value = value

    def hex(self) -> str:
        return self._value


def _make_sender(transaction_config: TransactionConfig):
    sender = SimpleNamespace()
    sender.user_address = "0x0000000000000000000000000000000000000001"
    sender.transaction_config = transaction_config
    sender.account = MagicMock()
    sender.account.sign_transaction.return_value = SimpleNamespace(
        raw_transaction=b"signed",
        hash=_TxHash("0xabc"),
    )
    sender.w3 = SimpleNamespace(
        eth=SimpleNamespace(
            estimate_gas=AsyncMock(return_value=210_000),
            send_raw_transaction=AsyncMock(return_value=_TxHash("0xabc")),
            get_balance=AsyncMock(return_value=10**18),
        )
    )
    return sender


@pytest.mark.asyncio
async def test_send_transaction_uses_local_formula_without_rpc_estimate(monkeypatch):
    sender = _make_sender(
        TransactionConfig(
            local_gas_estimation=True,
            gas_adjustment_per_slot=999_999,
            gas_buffer_multiplier=9.0,
            gas_buffer=888_888,
        )
    )
    function_call = SimpleNamespace(
        build_transaction=AsyncMock(
            return_value={"from": sender.user_address, "gasPrice": 7, "value": 0}
        )
    )

    nonce_mock = AsyncMock(return_value=3)
    fail_mock = AsyncMock()
    monkeypatch.setattr(
        "kuru_sdk_py.transaction.transaction.NonceManager.get_and_increment_nonce",
        nonce_mock,
    )
    monkeypatch.setattr(
        "kuru_sdk_py.transaction.transaction.NonceManager.mark_transaction_failed",
        fail_mock,
    )

    tx_hash = await AsyncTransactionSenderMixin._send_transaction(
        sender,
        function_call,
        access_list=[{"address": sender.user_address, "storageKeys": ["0x1", "0x2"]}],
        gas_price=7,
        local_gas_counts=LocalGasCounts(n_buy=1, n_sell=2, n_cancel=3),
    )

    assert tx_hash == "0xabc"
    function_call.build_transaction.assert_awaited_once_with(
        {
            "from": sender.user_address,
            "nonce": 3,
            "value": 0,
            "gasPrice": 7,
            "accessList": [
                {"address": sender.user_address, "storageKeys": ["0x1", "0x2"]}
            ],
        }
    )
    sender.w3.eth.estimate_gas.assert_not_awaited()
    signed_tx = sender.account.sign_transaction.call_args.args[0]
    assert signed_tx["gas"] == 804_000 + 888_888
    fail_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_transaction_keeps_current_rpc_estimation_when_flag_is_false(
    monkeypatch,
):
    sender = _make_sender(
        TransactionConfig(
            local_gas_estimation=False,
            gas_adjustment_per_slot=100,
            gas_buffer_multiplier=1.5,
            gas_buffer=50,
        )
    )
    sender.w3.eth.estimate_gas = AsyncMock(return_value=1_000_000)
    function_call = SimpleNamespace(
        build_transaction=AsyncMock(
            return_value={"from": sender.user_address, "gasPrice": 9, "value": 0}
        )
    )

    monkeypatch.setattr(
        "kuru_sdk_py.transaction.transaction.NonceManager.get_and_increment_nonce",
        AsyncMock(return_value=4),
    )
    monkeypatch.setattr(
        "kuru_sdk_py.transaction.transaction.NonceManager.mark_transaction_failed",
        AsyncMock(),
    )

    await AsyncTransactionSenderMixin._send_transaction(
        sender,
        function_call,
        access_list=[{"address": sender.user_address, "storageKeys": ["0x1", "0x2"]}],
        gas_price=9,
        local_gas_counts=LocalGasCounts(n_buy=5, n_sell=5, n_cancel=5),
    )

    sender.w3.eth.estimate_gas.assert_awaited_once()
    signed_tx = sender.account.sign_transaction.call_args.args[0]
    assert signed_tx["gas"] == int((1_000_000 - 200 + 50) * 1.5)


@pytest.mark.asyncio
async def test_send_transaction_uses_rpc_estimation_when_counts_are_missing(monkeypatch):
    sender = _make_sender(TransactionConfig(local_gas_estimation=True))
    sender.w3.eth.estimate_gas = AsyncMock(return_value=123_456)
    function_call = SimpleNamespace(
        build_transaction=AsyncMock(
            return_value={"from": sender.user_address, "gasPrice": 11, "value": 0}
        )
    )

    monkeypatch.setattr(
        "kuru_sdk_py.transaction.transaction.NonceManager.get_and_increment_nonce",
        AsyncMock(return_value=5),
    )
    monkeypatch.setattr(
        "kuru_sdk_py.transaction.transaction.NonceManager.mark_transaction_failed",
        AsyncMock(),
    )

    await AsyncTransactionSenderMixin._send_transaction(
        sender,
        function_call,
        gas_price=11,
    )

    sender.w3.eth.estimate_gas.assert_awaited_once()
    signed_tx = sender.account.sign_transaction.call_args.args[0]
    assert signed_tx["gas"] == 123_456


@pytest.mark.asyncio
async def test_send_transaction_calls_before_send_before_broadcast(monkeypatch):
    sender = _make_sender(TransactionConfig(local_gas_estimation=True))
    function_call = SimpleNamespace(
        build_transaction=AsyncMock(
            return_value={"from": sender.user_address, "gasPrice": 11, "value": 0}
        )
    )
    events = []

    async def before_send(txhash):
        events.append(("before_send", txhash))

    async def send_raw_transaction(raw_transaction):
        events.append(("send_raw_transaction", raw_transaction))
        return _TxHash("0xabc")

    sender.w3.eth.send_raw_transaction = AsyncMock(side_effect=send_raw_transaction)

    monkeypatch.setattr(
        "kuru_sdk_py.transaction.transaction.NonceManager.get_and_increment_nonce",
        AsyncMock(return_value=5),
    )
    monkeypatch.setattr(
        "kuru_sdk_py.transaction.transaction.NonceManager.mark_transaction_failed",
        AsyncMock(),
    )

    await AsyncTransactionSenderMixin._send_transaction(
        sender,
        function_call,
        gas_price=11,
        before_send=before_send,
    )

    assert events == [
        ("before_send", "0xabc"),
        ("send_raw_transaction", b"signed"),
    ]

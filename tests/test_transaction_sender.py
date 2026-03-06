from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kuru_sdk_py.configs import TransactionConfig
from kuru_sdk_py.transaction.nonce_manager import NonceManager
from kuru_sdk_py.transaction.transaction import AsyncTransactionSenderMixin


class _DummyFunctionCall:
    async def build_transaction(self, tx_params):
        return {
            **tx_params,
            "to": "0x0000000000000000000000000000000000000001",
            "data": "0x",
        }


class _DummySender(AsyncTransactionSenderMixin):
    def __init__(self):
        self.user_address = "0x0000000000000000000000000000000000000002"
        self.transaction_config = TransactionConfig(
            timeout=120,
            poll_latency=0.4,
            chain_id=143,
            gas_price_refresh_interval=1.0,
            gas_adjustment_per_slot=6500,
            gas_buffer_multiplier=1.35,
            gas_buffer=40_000,
        )
        self.account = SimpleNamespace(
            sign_transaction=Mock(
                return_value=SimpleNamespace(raw_transaction=b"\x01\x02")
            )
        )
        self.w3 = SimpleNamespace(
            eth=SimpleNamespace(
                estimate_gas=AsyncMock(return_value=100_000),
                send_raw_transaction=AsyncMock(
                    return_value=SimpleNamespace(hex=lambda: "0xtxhash")
                ),
                gas_price=AsyncMock(return_value=1),
                get_balance=AsyncMock(return_value=10**20),
            )
        )


@pytest.mark.asyncio
async def test_send_transaction_falls_back_to_estimated_gas_on_access_list_underflow(
    monkeypatch,
):
    sender = _DummySender()

    monkeypatch.setattr(
        NonceManager,
        "get_and_increment_nonce",
        AsyncMock(return_value=7),
    )
    monkeypatch.setattr(
        NonceManager,
        "mark_transaction_failed",
        AsyncMock(return_value=None),
    )

    access_list = [{"address": "0x0000000000000000000000000000000000000003", "storageKeys": ["0x00"] * 40}]

    tx_hash = await sender._send_transaction(
        _DummyFunctionCall(),
        access_list=access_list,
        gas_price=2,
    )

    assert tx_hash == "0xtxhash"
    signed_tx = sender.account.sign_transaction.call_args[0][0]
    assert signed_tx["gas"] == 100_000
    assert signed_tx["chainId"] == 143
    sender.w3.eth.estimate_gas.assert_awaited_once()
    sender.w3.eth.send_raw_transaction.assert_awaited_once()

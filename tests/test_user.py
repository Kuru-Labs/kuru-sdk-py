from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kuru_sdk_py.exceptions import KuruInsufficientFundsError
from kuru_sdk_py.user.user import User
from kuru_sdk_py.utils import ZERO_ADDRESS


class _AwaitableValue:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def _inner():
            return self.value

        return _inner().__await__()


def _make_user(base_native: bool = False) -> User:
    user = User.__new__(User)
    user.user_address = "0x0000000000000000000000000000000000000001"
    user.margin_contract_address = "0x0000000000000000000000000000000000000002"
    user.base_token_address = (
        ZERO_ADDRESS if base_native else "0x0000000000000000000000000000000000000003"
    )
    user.quote_token_address = "0x0000000000000000000000000000000000000004"
    user.base_token_decimals = 18
    user.quote_token_decimals = 6

    user.w3 = SimpleNamespace(
        eth=SimpleNamespace(
            get_balance=AsyncMock(return_value=10**20),
            gas_price=_AwaitableValue(10**9),
        )
    )
    user.margin_contract = MagicMock()
    user.margin_contract.functions.deposit.return_value = "deposit_call"

    user.base_token_contract = MagicMock()
    user.base_token_contract.functions.allowance.return_value.call = AsyncMock(
        return_value=0
    )
    user.base_token_contract.functions.approve.return_value = "approve_call"

    user.quote_token_contract = MagicMock()
    user.quote_token_contract.functions.allowance.return_value.call = AsyncMock(
        return_value=0
    )
    user.quote_token_contract.functions.approve.return_value = "quote_approve_call"

    user._send_transaction = AsyncMock(return_value="0xtx")
    user._wait_for_transaction_receipt = AsyncMock(return_value={})
    return user


def test_convert_amount_precision():
    user = User.__new__(User)
    user.base_token_decimals = 18
    user.quote_token_decimals = 6

    assert user._convert_base_amount(Decimal("0.123")) == 123000000000000000
    assert user._convert_quote_amount(Decimal("0.123456")) == 123456


@pytest.mark.asyncio
async def test_deposit_base_erc20_requires_allowance_when_auto_approve_false():
    user = _make_user(base_native=False)
    user.base_token_contract.functions.allowance.return_value.call = AsyncMock(
        return_value=10
    )

    with pytest.raises(KuruInsufficientFundsError, match="Insufficient allowance"):
        await user.deposit_base(Decimal("1"), auto_approve=False)


@pytest.mark.asyncio
async def test_deposit_base_erc20_auto_approves_and_uses_zero_value():
    user = _make_user(base_native=False)
    user.base_token_contract.functions.allowance.return_value.call = AsyncMock(
        return_value=0
    )
    user._send_transaction = AsyncMock(side_effect=["0xapprove", "0xdeposit"])

    txhash = await user.deposit_base(Decimal("1"), auto_approve=True)

    assert txhash == "0xdeposit"
    assert user._send_transaction.await_args_list[0].kwargs["value"] == 0
    assert user._send_transaction.await_args_list[1].kwargs["value"] == 0
    user.margin_contract.functions.deposit.assert_called_once_with(
        user.user_address, user.base_token_address, 10**18
    )


@pytest.mark.asyncio
async def test_deposit_base_native_uses_value_transfer():
    user = _make_user(base_native=True)
    user._send_transaction = AsyncMock(return_value="0xnative")

    txhash = await user.deposit_base(Decimal("1"), auto_approve=True)

    assert txhash == "0xnative"
    user.margin_contract.functions.deposit.assert_called_once_with(
        user.user_address, ZERO_ADDRESS, 10**18
    )
    assert user._send_transaction.await_args.kwargs["value"] == 10**18


@pytest.mark.asyncio
async def test_deposit_base_native_fails_preflight_on_low_balance():
    user = _make_user(base_native=True)
    user.w3.eth.get_balance = AsyncMock(return_value=1)

    with pytest.raises(KuruInsufficientFundsError, match="Insufficient native token"):
        await user.deposit_base(Decimal("1"), auto_approve=True)

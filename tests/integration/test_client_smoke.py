import os

import pytest

from kuru_sdk_py.client import KuruClient
from kuru_sdk_py.configs import ConfigManager


@pytest.mark.integration
@pytest.mark.asyncio
async def test_kuru_client_lifecycle_smoke(ensure_rpc_available, integration_rpc_url):
    market_address = os.getenv("MARKET_ADDRESS")
    private_key = os.getenv("PRIVATE_KEY")
    if not market_address or not private_key:
        pytest.skip("MARKET_ADDRESS and PRIVATE_KEY are required for integration smoke")

    market_config = ConfigManager.load_market_config(
        market_address=market_address,
        fetch_from_chain=True,
        rpc_url=integration_rpc_url,
        auto_env=False,
    )
    connection_config = ConfigManager.load_connection_config(
        rpc_url=integration_rpc_url,
        auto_env=True,
    )
    wallet_config = ConfigManager.load_wallet_config(
        private_key=private_key,
        auto_env=False,
    )

    client = await KuruClient.create(
        market_config=market_config,
        connection_config=connection_config,
        wallet_config=wallet_config,
    )
    await client.user.get_margin_balances()
    await client.stop()

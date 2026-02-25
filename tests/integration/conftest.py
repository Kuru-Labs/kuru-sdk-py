import os

import pytest
from dotenv import load_dotenv
from web3 import Web3


load_dotenv(".env.test")


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_INTEGRATION") == "1":
        return

    skip_marker = pytest.mark.skip(
        reason="Integration tests are disabled by default. Set RUN_INTEGRATION=1."
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture
def integration_rpc_url() -> str:
    return os.getenv("RPC_URL", "https://rpc.monad.xyz")


@pytest.fixture
def ensure_rpc_available(integration_rpc_url: str) -> None:
    w3 = Web3(Web3.HTTPProvider(integration_rpc_url))
    if not w3.is_connected():
        pytest.skip(f"RPC unavailable at {integration_rpc_url}")

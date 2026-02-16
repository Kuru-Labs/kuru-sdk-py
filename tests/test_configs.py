import os
from unittest.mock import MagicMock, patch
from web3 import Web3
import pytest
from kuru_sdk_py.configs import (
    MarketConfig,
    KuruMMConfig,
    initialize_kuru_mm_config,
    market_config_from_market_address,
)


class TestInitializeKuruMMConfig:
    def test_initialize_with_valid_private_key(self, caplog):
        """Test initialization with valid private key"""
        config = initialize_kuru_mm_config(
            private_key="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            rpc_url="https://custom-rpc.example.com",
            rpc_ws_url="ws://custom-rpc.example.com",
            kuru_ws_url="wss://custom-ws.example.com",
            kuru_api_url="https://custom-api.example.com",
        )

        assert isinstance(config, KuruMMConfig)
        assert config.rpc_url == "https://custom-rpc.example.com"
        assert config.rpc_ws_url == "ws://custom-rpc.example.com"
        assert config.kuru_ws_url == "wss://custom-ws.example.com"
        assert config.kuru_api_url == "https://custom-api.example.com"
        assert (
            config.private_key
            == "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        )


class TestMarketConfigFromMarketAddress:
    def test_market_config_fetch_success(self):
        """Test successful market config fetch from blockchain with real addresses"""
        market_address = "0x065C9d28E428A0db40191a54d33d5b7c71a9C394"
        mm_entrypoint_address = "0x0B4D25ce6e9ad4C88157C2721E5DafA22934E1C8"
        margin_contract_address = "0x2A68ba1833cDf93fa9Da1EEbd7F46242aD8E90c5"
        rpc_url = os.getenv("KURU_RPC_URL", "https://rpc.fullnode.kuru.io/")

        config = market_config_from_market_address(
            market_address=market_address,
            mm_entrypoint_address=mm_entrypoint_address,
            margin_contract_address=margin_contract_address,
            rpc_url=rpc_url,
        )

        print(f"\n=== Test Market Config ===")
        print(f"Market Address: {config.market_address}")
        print(f"MM Entrypoint Address: {config.mm_entrypoint_address}")
        print(f"Margin Contract Address: {config.margin_contract_address}")
        print(f"Market Symbol: {config.market_symbol}")
        print(f"Base Token: {config.base_token}")
        print(f"Base Symbol: {config.base_symbol}")
        print(f"Base Decimals: {config.base_token_decimals}")
        print(f"Quote Token: {config.quote_token}")
        print(f"Quote Symbol: {config.quote_symbol}")
        print(f"Quote Decimals: {config.quote_token_decimals}")
        print(f"Price Precision: {config.price_precision}")
        print(f"Size Precision: {config.size_precision}")
        print(f"Orderbook Implementation: {config.orderbook_implementation}")
        print(f"Margin Account Implementation: {config.margin_account_implementation}")
        print(f"Tick Size: {config.tick_size}")
        print(f"========================\n")

        assert isinstance(config, MarketConfig)
        assert config.market_address == market_address
        assert config.mm_entrypoint_address == mm_entrypoint_address
        assert config.margin_contract_address == margin_contract_address

        assert config.base_token.startswith("0x")
        assert config.quote_token.startswith("0x")
        assert len(config.base_token) == 42
        assert len(config.quote_token) == 42

        assert isinstance(config.base_token_decimals, int)
        assert isinstance(config.quote_token_decimals, int)
        assert config.quote_token_decimals > 0

        assert isinstance(config.price_precision, int)
        assert isinstance(config.size_precision, int)
        assert config.price_precision > 0
        assert config.size_precision > 0

        assert isinstance(config.base_symbol, str)
        assert isinstance(config.quote_symbol, str)
        assert len(config.base_symbol) > 0
        assert len(config.quote_symbol) > 0

        assert config.market_symbol == f"{config.base_symbol}-{config.quote_symbol}"

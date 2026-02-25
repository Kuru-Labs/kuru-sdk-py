from kuru_sdk_py.configs import (
    ClientConfig,
    ConnectionConfig,
    MarketConfig,
    OrderExecutionConfig,
    TransactionConfig,
    WalletConfig,
    WebSocketConfig,
)


def _mock_market_config() -> MarketConfig:
    return MarketConfig(
        market_address="0x0000000000000000000000000000000000000001",
        base_token="0x0000000000000000000000000000000000000002",
        quote_token="0x0000000000000000000000000000000000000003",
        market_symbol="TEST",
        mm_entrypoint_address="0x0000000000000000000000000000000000000004",
        margin_contract_address="0x0000000000000000000000000000000000000005",
        base_token_decimals=18,
        quote_token_decimals=6,
        price_precision=100_000_000,
        size_precision=10_000_000_000,
        base_symbol="BASE",
        quote_symbol="QUOTE",
        orderbook_implementation="0x0000000000000000000000000000000000000006",
        margin_account_implementation="0x0000000000000000000000000000000000000007",
        tick_size=1,
    )


def test_client_config_to_configs(monkeypatch):
    monkeypatch.setattr(
        "kuru_sdk_py.configs.ConfigManager.load_market_config",
        lambda **kwargs: _mock_market_config(),
    )

    config = ClientConfig(
        market_address="0x0000000000000000000000000000000000000001",
        private_key="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    )
    (
        market_config,
        connection_config,
        wallet_config,
        transaction_config,
        websocket_config,
        order_execution_config,
    ) = config.to_configs()

    assert isinstance(market_config, MarketConfig)
    assert isinstance(connection_config, ConnectionConfig)
    assert isinstance(wallet_config, WalletConfig)
    assert isinstance(transaction_config, TransactionConfig)
    assert isinstance(websocket_config, WebSocketConfig)
    assert isinstance(order_execution_config, OrderExecutionConfig)

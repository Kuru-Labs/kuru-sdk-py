# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python SDK (`kuru-mm-py`) for building market maker bots on the Kuru orderbook protocol. The SDK handles batch order placement/cancellation, order lifecycle tracking, real-time orderbook feeds, and margin account integration via EIP-7702 authorization.

## Development Commands

### Running Tests
```bash
uv run pytest tests/ -v
```

### Running Examples
Examples must be run with `PYTHONPATH=.` from the project root:
```bash
# Market making bot (requires PRIVATE_KEY and MARKET_ADDRESS in .env)
PYTHONPATH=. uv run python examples/simple_market_making_bot.py

# Orderbook WebSocket streaming (read-only, no wallet needed)
PYTHONPATH=. uv run python examples/get_orderbook_ws.py

# Batch order placement
PYTHONPATH=. uv run python examples/place_many_orders.py
```

### Package Management
This project uses `uv` (not pip) for dependency management:
```bash
uv sync          # Install dependencies
uv add <package> # Add a dependency
```

Use uv to run python scripts to ensure the virtual environment is activated and dependencies are available.

## Architecture

### Core Components

The SDK is organized into these key modules:

1. **`KuruClient`** (`kuru_sdk_py/client.py`)
   Main facade that orchestrates all operations. Manages lifecycle of all components and provides high-level API for order placement, cancellation, and event subscriptions.

2. **`OrdersManager`** (`kuru_sdk_py/manager/orders_manager.py`)
   Tracks order lifecycle and state. Maps client order IDs (cloids) to on-chain order IDs and processes events from the blockchain.

3. **`OrdersExecutor`** (`kuru_sdk_py/executor/orders_executor.py`)
   Executes orders on-chain via the MM Entrypoint contract. Handles batch cancel/replace operations, price/size conversion, tick rounding, and access list optimization.

4. **`User`** (`kuru_sdk_py/user/user.py`)
   Manages user operations: margin deposits/withdrawals, token approvals, EIP-7702 authorization, and balance queries.

5. **`RpcWebsocket`** (`kuru_sdk_py/feed/rpc_ws.py`)
   Listens to blockchain events via WebSocket RPC. Processes `OrderCreated`, `OrdersCanceled`, `Trade`, and `BatchUpdateMM` events for order lifecycle tracking.

6. **`KuruFrontendOrderbookClient`** (`kuru_sdk_py/feed/orderbook_ws.py`)
   Streams real-time orderbook updates via Kuru's WebSocket API. Handles reconnection, subscription management, and data formatting.

### Configuration System

The config system (`kuru_sdk_py/configs.py`) uses specialized dataclasses:

- **`MarketConfig`**: Market parameters (addresses, tick size, precision, token info)
- **`ConnectionConfig`**: RPC/API endpoints
- **`WalletConfig`**: Private key and derived address (sensitive)
- **`TransactionConfig`**: Transaction timeouts, gas adjustments
- **`WebSocketConfig`**: Reconnection behavior and heartbeat settings
- **`OrderExecutionConfig`**: Order defaults (post_only, auto_approve, access lists)
- **`CacheConfig`**: TTL for transaction and event caches

Use `ConfigManager` to load configs from environment variables with defaults.

### Order Flow

1. User creates `Order` objects with cloids (client order IDs)
2. `OrdersExecutor` converts float prices/sizes to integers using precision settings
3. Executor applies tick rounding based on `price_rounding` parameter
4. Batch transaction sent to MM Entrypoint contract via EIP-7702 authorization
5. `RpcWebsocket` receives on-chain events
6. `OrdersManager` maps events to orders by txhash and cloid
7. Order status updates delivered via callback or queue

### Key Patterns

- **Async factory pattern**: Components use `create()` class methods instead of `__init__` for async initialization
- **Global nonce manager**: `NonceManager` (`kuru_sdk_py/transaction/nonce_manager.py`) provides thread-safe nonce tracking to reduce RPC calls
- **Event caching**: Recent transactions and trades are cached to handle duplicate events
- **WebSocket resilience**: Auto-reconnection with exponential backoff for both RPC and orderbook feeds

## Important Concepts

### EIP-7702 Authorization
Orders must be placed through the MM Entrypoint contract, which requires one-time EIP-7702 authorization. `client.start()` handles this automatically. Authorization persists on-chain until explicitly revoked.

### Margin Accounts
Orders consume margin balances (via the margin account contract), NOT wallet balances. Users must deposit base/quote tokens before trading. Margin balances can be queried via `client.user.get_margin_balances()`.

### Price/Size Precision
- `price_precision` and `size_precision` are multipliers (e.g., 10^18) that convert floats to on-chain integers
- `tick_size` is the minimum price increment in integer units
- The SDK handles conversion automatically, but price rounding behavior can be configured

### WebSocket Price Formatting
Both WebSocket clients (`KuruFrontendOrderbookClient` and `ExchangeWebsocketClient`) pre-normalize prices and sizes to human-readable floats before placing data on the queue. No manual conversion is needed when consuming updates from the queue.

### Access List Optimization
Enable `KURU_USE_ACCESS_LIST=true` to use EIP-2930 access lists, which can reduce gas costs by pre-declaring storage access patterns.

## Testing Approach

When writing tests:
- Use `pytest` with async support (`pytest-asyncio`)
- Mock Web3 RPC calls where possible to avoid network dependencies
- Test individual components (nonce manager, cache, config validation) in isolation
- See `tests/` for examples of unit tests

## Common Pitfalls

1. **Not calling `client.start()`**: Authorization and event listening won't work without it
2. **Forgetting `PYTHONPATH=.`**: Examples won't find the `kuru_sdk_py` module without it
3. **Using wallet balances instead of margin**: Orders require margin deposits first
4. **Not handling WebSocket disconnects**: Always set reconnection handlers or use SDK's built-in resilience
5. **Ignoring tick size**: Prices that don't align to tick size will cause transaction reverts
6. **Mixing up price formats**: WebSocket prices are always 10^18, API/on-chain prices use market precision
7. **Using `pip` instead of `uv`**: This project requires `uv` for dependency management

## File Organization

```
kuru_sdk_py/
├── client.py              # Main KuruClient facade
├── configs.py             # Configuration system with dataclasses
├── config_defaults.py     # Default values for all configs
├── manager/               # Order lifecycle and event management
│   ├── orders_manager.py  # Order tracking and state
│   ├── order.py           # Order dataclass and enums
│   └── events.py          # Event dataclasses
├── executor/              # Order execution
│   └── orders_executor.py # Batch cancel/place logic
├── feed/                  # WebSocket feeds
│   ├── rpc_ws.py          # RPC WebSocket for events
│   ├── orderbook_ws.py    # Orderbook WebSocket client
│   └── orderbook.py       # Orderbook REST API client
├── user/                  # User operations
│   └── user.py            # Deposits, withdrawals, approvals
├── transaction/           # Transaction utilities
│   ├── transaction.py     # Transaction building and sending
│   ├── nonce_manager.py   # Global nonce tracking
│   └── access_list.py     # EIP-2930 access list generation
├── utils/                 # Shared utilities
│   ├── async_mem_cache.py # In-memory cache with TTL
│   ├── validation.py      # Input validation
│   └── errors.py          # Error handling
└── abis/                  # Contract ABIs (JSON files)
```

## Environment Variables

Required:
- `PRIVATE_KEY`: Wallet private key (with 0x prefix)
- `MARKET_ADDRESS`: Orderbook contract address

Optional (with defaults):
- `RPC_URL`: HTTP RPC endpoint
- `RPC_WS_URL`: WebSocket RPC endpoint
- `KURU_WS_URL`: Kuru orderbook WebSocket URL
- `KURU_API_URL`: Kuru REST API URL
- `KURU_RPC_LOGS_SUBSCRIPTION`: `logs` or `monadLogs` (for proposed-state streaming)
- `KURU_POST_ONLY`: Default post-only behavior
- `KURU_AUTO_APPROVE`: Auto-approve token transfers
- `KURU_USE_ACCESS_LIST`: Enable EIP-2930 access lists

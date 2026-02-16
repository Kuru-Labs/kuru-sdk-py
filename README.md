# Kuru Market Maker SDK (Python)

A Python SDK for building market maker bots on the [Kuru](https://kuru.io) orderbook protocol.

## Features

- **Batch Cancel/Replace** - Atomically cancel and place orders in a single transaction via the MM Entrypoint
- **Order Lifecycle Tracking** - Track orders from creation through fills and cancellations via on-chain events
- **Real-time Orderbook Feed** - WebSocket client for live best bid/ask with auto-reconnection
- **Margin Account Integration** - Deposit, withdraw, and query margin balances
- **EIP-7702 Authorization** - Automatic MM Entrypoint authorization for your EOA
- **Gas Optimization** - EIP-2930 access list support to reduce gas costs

## Installation

```bash
pip install kuru-mm-py
# or
uv add kuru-mm-py
```

Create a `.env` file:

```bash
# Required
PRIVATE_KEY=0x...
MARKET_ADDRESS=0x...

# Optional endpoints (defaults exist)
RPC_URL=https://rpc.fullnode.kuru.io/
RPC_WS_URL=wss://rpc.fullnode.kuru.io/
KURU_WS_URL=wss://ws.kuru.io/
KURU_API_URL=https://api.kuru.io/

# Optional WebSocket behavior
KURU_RPC_LOGS_SUBSCRIPTION=monadLogs  # Set to logs for commited-state streaming

# Optional trading defaults
KURU_POST_ONLY=true
KURU_AUTO_APPROVE=true
KURU_USE_ACCESS_LIST=true
```

## Core Concepts

Kuru market making interacts with three on-chain components:

- **Orderbook contract** (`MARKET_ADDRESS`): holds the limit orderbook and emits `OrderCreated`, `OrdersCanceled`, and `Trade` events.
- **Margin account contract**: holds your trading balances. Orders consume margin balances, not wallet balances.
- **MM Entrypoint (EIP-7702 delegation)**: your EOA is authorized to delegate to the MM Entrypoint contract, enabling batch cancel and place operations in a single transaction.

The SDK provides:

- `KuruClient`: the main facade for execution, event listeners, and orderbook streaming.
- `User`: deposits/withdrawals, approvals, and EIP-7702 authorization.
- `Order`: your local representation of a limit order or cancel.

## Market Making Guide

A typical market making bot has four concurrent loops:

1. **Market data ingestion** - subscribe to the orderbook WebSocket for best bid/ask.
2. **Quote generation** - decide where to quote (spreads, levels, sizes) based on your model + inventory.
3. **Execution** - atomically cancel + place quotes using `client.place_orders()`.
4. **Order lifecycle & fills** - listen to on-chain events to track placements, fills, and cancellations.

The SDK handles (1), (3), and (4). You supply (2).

### Step 1 - Create a client

```python
import os
from dotenv import load_dotenv

from src.client import KuruClient
from src.configs import ConfigManager

load_dotenv()

configs = ConfigManager.load_all_configs(
    market_address=os.environ["MARKET_ADDRESS"],
    fetch_from_chain=True,
)

client = await KuruClient.create(**configs)
```

`fetch_from_chain=True` reads the market's on-chain params and sets `price_precision`, `size_precision`, `tick_size`, and token addresses/decimals/symbols automatically.

### Step 2 - Fund margin

Orders are backed by margin balances. Deposit base and/or quote before quoting:

```python
await client.user.deposit_base(10.0, auto_approve=True)
await client.user.deposit_quote(500.0, auto_approve=True)

margin_base_wei, margin_quote_wei = await client.user.get_margin_balances()
```

If you quote both sides, keep both base and quote margin funded; otherwise your orders will revert.

### Step 3 - Start the client

`client.start()` performs EIP-7702 authorization and connects to the RPC WebSocket for on-chain event tracking.

Set an order callback to track your order lifecycle:

```python
from src.manager.order import Order, OrderStatus

active_cloids: set[str] = set()

async def on_order(order: Order) -> None:
    if order.status in (OrderStatus.ORDER_PLACED, OrderStatus.ORDER_PARTIALLY_FILLED):
        active_cloids.add(order.cloid)
    if order.status in (OrderStatus.ORDER_CANCELLED, OrderStatus.ORDER_FULLY_FILLED):
        active_cloids.discard(order.cloid)

client.set_order_callback(on_order)
await client.start()
```

Alternatively, read from `client.orders_manager.processed_orders_queue` instead of using callbacks.

### Step 4 - Subscribe to real-time orderbook data

```python
from src.feed.orderbook_ws import KuruFrontendOrderbookClient, FrontendOrderbookUpdate

best_bid: float | None = None
best_ask: float | None = None

async def on_orderbook(update: FrontendOrderbookUpdate) -> None:
    global best_bid, best_ask
    if update.b:
        best_bid = update.b[0][0]
    if update.a:
        best_ask = update.a[0][0]

client.set_orderbook_callback(on_orderbook)
await client.subscribe_to_orderbook()
```

**WebSocket price/size units:** Prices and sizes in `FrontendOrderbookUpdate` are pre-normalized to human-readable floats. No manual conversion is needed.

### Step 5 - Cancel and place quotes

Build a grid of orders, cancel stale ones, and send them in a single batch transaction:

```python
import time
from src.manager.order import Order, OrderType, OrderSide

def build_grid(mid: float) -> list[Order]:
    ts = int(time.time() * 1000)
    spread_bps = 10  # 0.10%
    size = 5.0

    bid = mid * (1 - spread_bps / 10_000)
    ask = mid * (1 + spread_bps / 10_000)

    return [
        Order(cloid=f"bid-{ts}", order_type=OrderType.LIMIT, side=OrderSide.BUY, price=bid, size=size),
        Order(cloid=f"ask-{ts}", order_type=OrderType.LIMIT, side=OrderSide.SELL, price=ask, size=size),
    ]

orders = []
orders += [Order(cloid=c, order_type=OrderType.CANCEL) for c in active_cloids]
orders += build_grid(mid=100.0)

txhash = await client.place_orders(
    orders,
    post_only=True,           # maker-only (recommended)
    price_rounding="default",  # buy rounds down, sell rounds up
)
```

#### Cancel all orders

To cancel all active orders for the market (useful for circuit breakers or graceful shutdown):

```python
await client.cancel_all_active_orders_for_market()
```

This cancels every order you have on the book for this market in a single transaction, regardless of whether you're tracking them locally.

#### Tick size and rounding

On-chain prices must align to `market_config.tick_size`. When you pass float prices to `place_orders()`, the SDK converts them to integers using `price_precision` and then rounds to tick size.

`price_rounding` options:

- `"default"` - round **down** for buys and **up** for sells (recommended)
- `"down"` - round down for both sides
- `"up"` - round up for both sides
- `"none"` - no tick rounding (only if you already quantize yourself)

### Step 6 - React to fills and inventory

The SDK delivers order status updates via the callback or queue:

- `ORDER_PLACED` - confirmed on book
- `ORDER_PARTIALLY_FILLED` - size reduced
- `ORDER_FULLY_FILLED` - size goes to 0
- `ORDER_CANCELLED` - removed from book

Typical market maker reactions:

- Record fills and PnL
- Adjust inventory targets
- Skew spreads based on inventory (long base -> tighten asks, widen bids)
- Refresh quotes more aggressively after fills

Compute inventory from margin balances:

```python
margin_base_wei, margin_quote_wei = await client.user.get_margin_balances()
base = margin_base_wei / (10 ** market_config.base_token_decimals)
quote = margin_quote_wei / (10 ** market_config.quote_token_decimals)
```

## Order Types

### Limit Orders

```python
from src.manager.order import Order, OrderType, OrderSide

Order(
    cloid="my-bid-1",
    order_type=OrderType.LIMIT,
    side=OrderSide.BUY,
    price=100.0,
    size=1.0,
)
```

### Market Orders

```python
await client.place_market_buy(quote_amount=100.0, min_amount_out=0.9)
await client.place_market_sell(size=1.0, min_amount_out=90.0)
```

### Cancel Orders

```python
Order(cloid="my-bid-1", order_type=OrderType.CANCEL)
```

### Batch Updates

```python
orders = [
    Order(cloid="bid-1", order_type=OrderType.LIMIT, side=OrderSide.BUY, price=99.0, size=1.0),
    Order(cloid="ask-1", order_type=OrderType.LIMIT, side=OrderSide.SELL, price=101.0, size=1.0),
    Order(cloid="old-bid", order_type=OrderType.CANCEL),
]
txhash = await client.place_orders(orders, post_only=True)
```

## WebSocket Feeds

The SDK provides two standalone WebSocket clients for real-time orderbook data. Both can be used independently of `KuruClient` — no wallet or private key required.

### `KuruFrontendOrderbookClient` (orderbook_ws)

Connects to Kuru's frontend orderbook WebSocket. Delivers a **full L2 snapshot** on connect, then incremental updates with events (trades, order placements, cancellations).

- Prices and sizes are pre-normalized to human-readable floats.
- Each update may contain `b` (bids) and `a` (asks) with all current levels at that price, plus an `events` list describing what changed.

```python
import asyncio
from src.configs import ConfigManager
from src.feed.orderbook_ws import KuruFrontendOrderbookClient, FrontendOrderbookUpdate

market_config = ConfigManager.load_market_config(market_address="0x...", fetch_from_chain=True)
connection_config = ConfigManager.load_connection_config()

update_queue: asyncio.Queue[FrontendOrderbookUpdate] = asyncio.Queue()

client = KuruFrontendOrderbookClient(
    ws_url=connection_config.kuru_ws_url,
    market_address=market_config.market_address,
    update_queue=update_queue,
    size_precision=market_config.size_precision,
)

async with client:
    while True:
        update = await update_queue.get()

        if update.b:
            best_bid = update.b[0][0]  # Already a float
        if update.a:
            best_ask = update.a[0][0]  # Already a float
```

You can also subscribe via `KuruClient` instead of using the client directly:

```python
client.set_orderbook_callback(on_orderbook)
await client.subscribe_to_orderbook()
```

---

### `ExchangeWebsocketClient` (exchange_ws)

Connects to the exchange WebSocket in **Binance-compatible format**. Delivers incremental depth updates (`depthUpdate`) and optionally Monad-enhanced updates with blockchain state (`monadDepthUpdate`).

- Messages are binary (JSON serialized to bytes).
- Prices and sizes are pre-normalized to human-readable floats.
- `MonadDepthUpdate` includes `blockNumber`, `blockId`, and `state` (`"proposed"` | `"committed"` | `"finalized"`).

**Key difference from `orderbook_ws`:** The exchange WebSocket sends **incremental delta updates only** — `b` and `a` contain only the price levels that changed, not the full book. A size of `0.0` means that level was removed. You must maintain a local orderbook and apply each delta.

```python
import asyncio
from src.configs import ConfigManager
from src.feed.exchange_ws import ExchangeWebsocketClient, DepthUpdate, MonadDepthUpdate

market_config = ConfigManager.load_market_config(market_address="0x...", fetch_from_chain=True)
connection_config = ConfigManager.load_connection_config()

update_queue = asyncio.Queue()

client = ExchangeWebsocketClient(
    ws_url=connection_config.exchange_ws_url,
    market_config=market_config,
    update_queue=update_queue,
)

# Local orderbook — must be seeded and maintained manually
orderbook = {"bids": {}, "asks": {}}

async with client:
    while True:
        update = await update_queue.get()

        # Apply bid deltas (prices and sizes are pre-normalized floats)
        for price, size in update.b:
            if size == 0.0:
                orderbook["bids"].pop(price, None)  # Level removed
            else:
                orderbook["bids"][price] = size     # Level added or updated

        # Apply ask deltas
        for price, size in update.a:
            if size == 0.0:
                orderbook["asks"].pop(price, None)  # Level removed
            else:
                orderbook["asks"][price] = size     # Level added or updated

        # For MonadDepthUpdate, blockchain state is also available:
        if isinstance(update, MonadDepthUpdate):
            print(f"Block {update.blockNumber} ({update.state})")
```

---

### Choosing between the two

| | `KuruFrontendOrderbookClient` | `ExchangeWebsocketClient` |
|---|---|---|
| Initial snapshot | Full L2 book on connect | None (delta-only) |
| Update style | Full levels at changed prices + events | Incremental deltas |
| Local book required | No | Yes |
| Event detail (trades, orders) | Yes (`events` field) | No |
| Blockchain state | No | Yes (monad variant) |
| Message format | Text JSON | Binary JSON |

---

## Configuration

The SDK uses `ConfigManager` to load configuration from environment variables with sensible defaults. See the [Environment Variables](#installation) section above for the full list.

For advanced configuration (custom timeouts, reconnection behavior, gas settings, presets), see `examples/config_examples.py`.

```python
from src.configs import ConfigManager, ConfigPresets

# One-liner: load everything from env vars
configs = ConfigManager.load_all_configs(
    market_address=os.environ["MARKET_ADDRESS"],
    fetch_from_chain=True,
)
client = await KuruClient.create(**configs)

# Or use presets for common scenarios
preset = ConfigPresets.conservative()  # Longer timeouts, more retries (production)
preset = ConfigPresets.aggressive()    # Shorter timeouts, fewer retries (HFT)
preset = ConfigPresets.testnet()       # Optimized for slower testnets
```

## Production Guidance

### Use a dedicated RPC

The default public endpoints can be rate-limited. For production, use a dedicated RPC provider via `RPC_URL` and `RPC_WS_URL`.

### Quote cadence and gas

Batch cancel/replace every second is expensive on-chain. Common approaches:

- Update only when mid price moves beyond a threshold
- Update at a slower cadence (e.g., 5-15s) unless volatility spikes
- Use fewer levels or smaller grids
- Enable EIP-2930 access list optimization (`KURU_USE_ACCESS_LIST=true`)

### Safety checks

- **Stale data guard** - don't quote if your market data feed is older than N milliseconds
- **Min/max size** - ensure order sizes meet market constraints (or the tx will revert)
- **Balance guard** - ensure margin balances can support your outstanding orders
- **Circuit breakers** - stop quoting on repeated failures, disconnects, or extreme spreads. Use `await client.cancel_all_active_orders_for_market()` to cancel all outstanding orders when the circuit breaker triggers

## Examples

```bash
# End-to-end market making bot (requires PRIVATE_KEY + MARKET_ADDRESS)
PYTHONPATH=. uv run python examples/simple_market_making_bot.py

# Read-only frontend orderbook stream (full snapshots, no wallet required)
PYTHONPATH=. uv run python examples/get_orderbook_ws.py

# Read-only exchange orderbook stream (Binance-compatible delta updates, no wallet required)
PYTHONPATH=. uv run python examples/get_exchange_orderbook_ws.py

# Repeated batch placement + cancels
PYTHONPATH=. uv run python examples/place_many_orders.py
```

## Testing

```bash
uv run pytest tests/ -v
```

## Requirements

- Python >= 3.14
- Dependencies managed via uv (see `pyproject.toml`)

# Market Making on Kuru (Python SDK Guide)

This guide explains how professional market makers can use `kuru-mm-py` to quote Kuru **orderbook contracts** (limit orders via the MM Entrypoint) with real-time market data, order lifecycle tracking, and safe cancel/replace loops.

## What you’re building

A typical market making bot on Kuru has four loops running concurrently:

1. **Market data ingestion**: subscribe to the Kuru frontend orderbook WebSocket and maintain an in-memory view of best bid/ask (or the full book).
2. **Quote generation**: decide where to quote (spreads, levels, sizes) based on your model + inventory.
3. **Execution**: atomically **cancel + place** quotes using the MM Entrypoint `batchUpdateMM` (wrapped by `KuruClient.place_orders()`).
4. **Order lifecycle & fills**: listen to on-chain events via the RPC WebSocket to learn when orders are placed, partially filled, fully filled, or cancelled (delivered through the SDK callback/queue).

The SDK wires (1), (3), and (4) for you. You supply (2).

---

## Core concepts (Kuru contracts)

Kuru market making via this SDK interacts with three on-chain components:

- **Orderbook contract** (the “market”, e.g. `MARKET_ADDRESS`): holds the limit orderbook and emits `OrderCreated`, `OrdersCanceled`, and `Trade` events.
- **Margin account contract**: holds your trading balances. Your orders consume **margin balances**, not your wallet balances.
- **MM Entrypoint contract**: exposes a batch method to place/cancel many orders efficiently.

The SDK uses these building blocks:

- `KuruClient`: the main façade you use in your bot (execution + event listeners + optional orderbook stream).
- `User`: deposits/withdrawals, approvals, and EIP-7702 authorization.
- `Order`: your local representation of a limit order or cancel.

---

## Installation and environment

This repo uses `uv` for dependency management.

```bash
uv sync
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

# Optional WebSocket behavior / RPC event stream
KURU_RPC_LOGS_SUBSCRIPTION=logs  # Set to monadLogs for proposed-state streaming (if supported by your RPC)

# Optional trading defaults
KURU_POST_ONLY=true
KURU_AUTO_APPROVE=true
KURU_USE_ACCESS_LIST=true
```

Most examples in this repo run from the project root with:

```bash
PYTHONPATH=. uv run python examples/simple_market_making_bot.py
```

---

## Step 1 — Load configs and create a client

Use `ConfigManager` so your bot can be configured via environment variables in production.

```python
import os
from dotenv import load_dotenv

from src.client import KuruClient
from src.configs import ConfigManager

load_dotenv()

wallet_config = ConfigManager.load_wallet_config()
connection_config = ConfigManager.load_connection_config()
market_config = ConfigManager.load_market_config(
    market_address=os.environ["MARKET_ADDRESS"],
    fetch_from_chain=True,
)

client = await KuruClient.create(
    market_config=market_config,
    connection_config=connection_config,
    wallet_config=wallet_config,
)
```

### What `fetch_from_chain=True` gives you

The SDK reads the market’s on-chain params and sets:

- `market_config.price_precision`: multiplier used to convert your float price into an on-chain integer.
- `market_config.size_precision`: multiplier used to convert your float size into an on-chain integer.
- `market_config.tick_size`: minimum price increment **in integer price units**.
- token addresses/decimals/symbols (base and quote).

---

## Step 2 — Fund margin and approvals

Orders are backed by **margin balances**. You typically:

1) deposit base and/or quote into the margin account, and
2) keep some native token in the wallet for gas.

```python
# Deposit 10 base and 500 quote into margin.
# For ERC-20 tokens, approvals are handled automatically when auto_approve=True.
await client.user.deposit_base(10.0, auto_approve=True)
await client.user.deposit_quote(500.0, auto_approve=True)

margin_base_wei, margin_quote_wei = await client.user.get_margin_balances()
```

Practical production notes:

- If you quote both sides, keep both base and quote margin funded; otherwise your orders will revert or be rejected.
- If your base token is native (e.g., MON), depositing base also requires wallet native balance for the deposit value + gas.

---

## Step 3 — Start the client (on-chain listeners + authorization)

`await client.start()` does two critical things:

1) **EIP-7702 authorization** of the MM Entrypoint for your EOA (required by Kuru’s MM flow), and
2) connects to the **RPC WebSocket** and starts listening for on-chain events to track your order lifecycle.

```python
await client.start()
```

If you want the SDK to deliver order lifecycle updates to you automatically, set an order callback **before** `start()`:

```python
from src.manager.order import Order, OrderStatus

active_cloids: set[str] = set()

async def on_order(order: Order) -> None:
    # Most market makers treat these as “live on book”
    if order.status in (OrderStatus.ORDER_PLACED, OrderStatus.ORDER_PARTIALLY_FILLED):
        active_cloids.add(order.cloid)

    # Terminal states: no longer live
    if order.status in (OrderStatus.ORDER_CANCELLED, OrderStatus.ORDER_FULLY_FILLED):
        active_cloids.discard(order.cloid)

client.set_order_callback(on_order)
await client.start()
```

If you prefer not to use callbacks, you can also read from:

- `client.orders_manager.processed_orders_queue`

---

## Step 4 — Subscribe to real-time orderbook data (optional but recommended)

For quoting, you usually want best bid/ask with low latency. The SDK can subscribe to Kuru’s frontend orderbook WebSocket:

```python
from src.feed.orderbook_ws import KuruFrontendOrderbookClient, FrontendOrderbookUpdate

best_bid: float | None = None
best_ask: float | None = None

async def on_orderbook(update: FrontendOrderbookUpdate) -> None:
    global best_bid, best_ask

    if update.b:
        best_bid = KuruFrontendOrderbookClient.format_websocket_price(update.b[0][0])
    if update.a:
        best_ask = KuruFrontendOrderbookClient.format_websocket_price(update.a[0][0])

client.set_orderbook_callback(on_orderbook)
await client.subscribe_to_orderbook()
```

### Important: WebSocket price/size units

The **frontend orderbook WebSocket** uses fixed units:

- Prices are always sent in `10^18` format (regardless of market precision)
- Sizes are sent in the market’s `size_precision` format

Use:

- `KuruFrontendOrderbookClient.format_websocket_price(raw_price_1e18) -> float`
- `KuruFrontendOrderbookClient.format_websocket_size(raw_size, market_config.size_precision) -> float`

---

## Step 5 — Create and send quotes (cancel + place)

### Order objects

This SDK places/cancels via a unified `Order` dataclass.

```python
from src.manager.order import Order, OrderType, OrderSide

# Limit buy
Order(
    cloid="bid-1",
    order_type=OrderType.LIMIT,
    side=OrderSide.BUY,
    price=0.95,   # float, will be converted using price_precision
    size=10.0,    # float in base units, will be converted using size_precision
)

# Cancel (by cloid)
Order(cloid="bid-1", order_type=OrderType.CANCEL)
```

### Batch updates are the default

`KuruClient.place_orders()` sends a single transaction that can include:

- multiple buy limits,
- multiple sell limits, and
- cancels (by cloid, mapped to on-chain order IDs once known).

That is the standard “market maker replace loop”.

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
    post_only=True,          # maker-only (recommended for market making)
    price_rounding="default" # tick-size rounding (buy down, sell up)
)
```

### Tick size and rounding

Prices on-chain must align to `market_config.tick_size` (in integer price units).

When you pass float prices to `place_orders()`, the SDK:

1) converts floats to integers using `price_precision`, then
2) optionally rounds to tick size.

Use `price_rounding`:

- `"default"`: round **down** for buys and **up** for sells (recommended)
- `"down"`: round down for both sides
- `"up"`: round up for both sides
- `"none"`: no tick rounding (not recommended unless you already quantize)

---

## Step 6 — React to fills and inventory (typical MM logic)

The SDK will update your `Order` objects when it sees on-chain events:

- `ORDER_PLACED`: confirmed on book
- `ORDER_PARTIALLY_FILLED`: size reduced
- `ORDER_FULLY_FILLED`: size goes to 0
- `ORDER_CANCELLED`: removed from book

Market makers typically:

- record fills and PnL,
- adjust inventory targets,
- skew spreads and/or sizes based on inventory,
- refresh quotes more aggressively after fills.

Example inventory skew (simple):

- If you are long base, tighten asks and widen bids.
- If you are short base, tighten bids and widen asks.

You can compute inventory using margin balances:

```python
margin_base_wei, margin_quote_wei = await client.user.get_margin_balances()
base = margin_base_wei / (10 ** market_config.base_token_decimals)
quote = margin_quote_wei / (10 ** market_config.quote_token_decimals)
```

---

## Operational guidance (production)

### Use your own RPC

The default public RPC/WebSocket endpoints can be rate-limited. For production market making, use a dedicated RPC provider and pass it via:

- `RPC_URL`, `RPC_WS_URL`

### Quote cadence and gas

Batch cancel/replace every second is expensive on-chain. Common approaches:

- update only when mid price moves beyond a threshold,
- update at a slower cadence (e.g., 5–15s) unless volatility spikes,
- use fewer levels or smaller grids,
- rely on the SDK’s EIP-2930 access list optimization (`KURU_USE_ACCESS_LIST=true`) to reduce gas.

### Safety checks to implement

- **Stale data guard**: don’t quote if your market data feed is older than N milliseconds.
- **Min/max size**: ensure order sizes meet market constraints (or the tx will revert).
- **Balance guard**: ensure margin balances can support your outstanding orders.
- **Circuit breakers**: stop quoting on repeated failures, disconnects, or extreme spreads.

---

## Related examples in this repo

- `examples/simple_market_making_bot.py`: end-to-end market making loop (cancel/replace).
- `examples/get_orderbook_ws.py`: subscribe to orderbook WebSocket and print top-of-book.
- `examples/place_many_orders.py`: illustrates repeated batch placement + cancels.

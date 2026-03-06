from __future__ import annotations

import asyncio
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from kuru_sdk_py.client import KuruClient
from kuru_sdk_py.configs import (
    CacheConfig,
    ConfigManager,
    ConnectionConfig,
    MarketConfig,
    OrderExecutionConfig,
    TransactionConfig,
    WalletConfig,
    WebSocketConfig,
)
from kuru_sdk_py.manager.order import Order, OrderStatus, OrderType


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def _parse_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _safe_percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None

    values = sorted(values)
    if len(values) == 1:
        return values[0]

    rank = (len(values) - 1) * (percentile / 100.0)
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    weight = rank - low

    return values[low] * (1.0 - weight) + values[high] * weight


@dataclass
class BenchmarkSettings:
    warmup: int
    iterations: int
    timeout_sec: float
    batch_sizes: tuple[int, ...] = (1, 2, 5, 10)
    market_quote_amount: Decimal = Decimal("0.01")


@dataclass
class CallbackEvent:
    cloid: str
    status: OrderStatus
    callback_time: float
    txhash: Optional[str]
    kuru_order_id: Optional[int]


class CallbackTracker:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[CallbackEvent]] = defaultdict(asyncio.Queue)

    async def callback(self, order: Order) -> None:
        event = CallbackEvent(
            cloid=order.cloid,
            status=order.status,
            callback_time=perf_counter(),
            txhash=order.txhash,
            kuru_order_id=order.kuru_order_id,
        )
        await self._queues[order.cloid].put(event)

    async def wait_for_next_event(
        self,
        cloid: str,
        timeout_sec: float,
    ) -> Optional[CallbackEvent]:
        queue = self._queues[cloid]
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            return None


@dataclass
class SeriesStats:
    samples: list[float] = field(default_factory=list)
    success_count: int = 0
    timeout_count: int = 0
    mismatch_count: int = 0
    error_count: int = 0
    mismatch_statuses: Counter[str] = field(default_factory=Counter)
    error_types: Counter[str] = field(default_factory=Counter)

    def record_sample(self, value: float) -> None:
        self.samples.append(value)
        self.success_count += 1

    def record_timeout(self) -> None:
        self.timeout_count += 1

    def record_mismatch(self, status: OrderStatus) -> None:
        self.mismatch_count += 1
        self.mismatch_statuses[status.value] += 1

    def record_error(self, exc: Exception) -> None:
        self.error_count += 1
        self.error_types[type(exc).__name__] += 1

    def to_summary(self) -> dict[str, Any]:
        count = len(self.samples)
        values = sorted(self.samples)

        return {
            "count": count,
            "success_count": self.success_count,
            "timeout_count": self.timeout_count,
            "mismatch_count": self.mismatch_count,
            "error_count": self.error_count,
            "min": values[0] if values else None,
            "max": values[-1] if values else None,
            "mean": (sum(values) / count) if values else None,
            "p50": _safe_percentile(values, 50),
            "p95": _safe_percentile(values, 95),
            "p99": _safe_percentile(values, 99),
            "mismatch_statuses": dict(self.mismatch_statuses),
            "error_types": dict(self.error_types),
        }


class BenchmarkReporter:
    def __init__(self, settings: BenchmarkSettings, root: Path) -> None:
        self.settings = settings
        self.root = root
        self.started_at = datetime.now(timezone.utc)
        self.run_id = self.started_at.strftime("%Y%m%dT%H%M%SZ")
        self._series: dict[str, SeriesStats] = {}
        self._notes: dict[str, Any] = {}
        self.output_path: Optional[Path] = None

    def series(self, name: str) -> SeriesStats:
        if name not in self._series:
            self._series[name] = SeriesStats()
        return self._series[name]

    def add_note(self, name: str, value: Any) -> None:
        self._notes[name] = value

    def print_table(self) -> None:
        col_series = 42
        col_n = 5
        col_time = 9

        header = (
            f"{'Series':<{col_series}} {'N':>{col_n}} "
            f"{'min(s)':>{col_time}} {'mean(s)':>{col_time}} "
            f"{'p50(s)':>{col_time}} {'p95(s)':>{col_time}} "
            f"{'p99(s)':>{col_time}} {'max(s)':>{col_time}} "
            f"{'TO':>{col_n}} {'ERR':>{col_n}}"
        )
        separator = "-" * len(header)

        print(f"\n{'=' * len(header)}")
        print("BENCHMARK RESULTS")
        print(separator)
        print(header)
        print(separator)

        def _fmt(v: Optional[float]) -> str:
            return f"{v:.4f}" if v is not None else "  n/a  "

        for name, stats in sorted(self._series.items()):
            s = stats.to_summary()
            print(
                f"{name:<{col_series}} {s['count']:>{col_n}} "
                f"{_fmt(s['min']):>{col_time}} {_fmt(s['mean']):>{col_time}} "
                f"{_fmt(s['p50']):>{col_time}} {_fmt(s['p95']):>{col_time}} "
                f"{_fmt(s['p99']):>{col_time}} {_fmt(s['max']):>{col_time}} "
                f"{s['timeout_count']:>{col_n}} {s['error_count']:>{col_n}}"
            )

        print(f"{'=' * len(header)}\n")

    def write(self) -> Path:
        out_dir = self.root / "benchmark_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{self.run_id}_live.json"

        payload = {
            "run_id": self.run_id,
            "started_at_utc": self.started_at.isoformat(),
            "mode": "live_chain_only",
            "policy": "trend_only",
            "settings": {
                "warmup": self.settings.warmup,
                "iterations": self.settings.iterations,
                "timeout_sec": self.settings.timeout_sec,
                "batch_sizes": list(self.settings.batch_sizes),
                "market_quote_amount": str(self.settings.market_quote_amount),
            },
            "notes": self._notes,
            "metrics": {
                name: stats.to_summary() for name, stats in sorted(self._series.items())
            },
        }

        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.output_path = out_path
        return out_path


@pytest.fixture(scope="session")
def benchmark_settings() -> BenchmarkSettings:
    load_dotenv()
    load_dotenv(".env.test")

    if os.getenv("RUN_BENCHMARKS") != "1":
        pytest.skip("Benchmarks are disabled. Set RUN_BENCHMARKS=1 to run live benchmarks.")

    if not os.getenv("PRIVATE_KEY"):
        pytest.skip("PRIVATE_KEY is required for live benchmarks.")

    if not os.getenv("MARKET_ADDRESS"):
        pytest.skip("MARKET_ADDRESS is required for live benchmarks.")

    settings = BenchmarkSettings(
        warmup=_parse_int_env("BENCH_WARMUP", 3),
        iterations=_parse_int_env("BENCH_ITERATIONS", 30),
        timeout_sec=_parse_float_env("BENCH_TIMEOUT_SEC", 45.0),
        market_quote_amount=Decimal(os.getenv("BENCH_MARKET_QUOTE", "0.01")),
    )

    if settings.warmup < 0 or settings.iterations <= 0 or settings.timeout_sec <= 0:
        raise ValueError("Invalid benchmark settings: warmup>=0, iterations>0, timeout>0 required")

    return settings


@pytest.fixture(scope="session")
def benchmark_configs(benchmark_settings: BenchmarkSettings) -> dict[str, Any]:
    del benchmark_settings  # settings fixture ensures env gating before config load

    private_key = os.getenv("PRIVATE_KEY")
    market_address = os.getenv("MARKET_ADDRESS")

    connection_config: ConnectionConfig = ConfigManager.load_connection_config(auto_env=True)
    wallet_config: WalletConfig = ConfigManager.load_wallet_config(
        private_key=private_key,
        auto_env=False,
    )
    market_config: MarketConfig = ConfigManager.load_market_config(
        market_address=market_address,
        fetch_from_chain=True,
        rpc_url=connection_config.rpc_url,
        auto_env=False,
    )
    transaction_config: TransactionConfig = ConfigManager.load_transaction_config(auto_env=True)
    websocket_config: WebSocketConfig = ConfigManager.load_websocket_config(auto_env=True)
    order_execution_config: OrderExecutionConfig = ConfigManager.load_order_execution_config(auto_env=True)
    cache_config: CacheConfig = ConfigManager.load_cache_config(auto_env=False)

    return {
        "market_config": market_config,
        "connection_config": connection_config,
        "wallet_config": wallet_config,
        "transaction_config": transaction_config,
        "websocket_config": websocket_config,
        "order_execution_config": order_execution_config,
        "cache_config": cache_config,
    }


@pytest.fixture(scope="session")
def benchmark_reporter(benchmark_settings: BenchmarkSettings) -> BenchmarkReporter:
    root = Path(__file__).resolve().parents[2]
    reporter = BenchmarkReporter(settings=benchmark_settings, root=root)
    yield reporter
    reporter.print_table()
    output = reporter.write()
    print(f"[benchmark] Wrote benchmark results to: {output}")


@pytest.fixture
def cloid_factory() -> Any:
    counter = 0

    def _make(prefix: str) -> str:
        nonlocal counter
        counter += 1
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return f"b-{prefix[:6]}-{ts_ms:x}-{counter:03d}"

    return _make


@pytest.fixture
def progress_printer() -> Any:
    def _print(message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[benchmark {ts} UTC] {message}", flush=True)

    return _print


@pytest_asyncio.fixture
async def benchmark_client(benchmark_configs: dict[str, Any]) -> Any:
    client = await KuruClient.create(**benchmark_configs)
    tracker = CallbackTracker()
    client.set_order_callback(tracker.callback)
    await client.start()

    context = {
        "client": client,
        "tracker": tracker,
        "active_cloids": set(),
    }

    try:
        yield context
    finally:
        active_cloids = list(context["active_cloids"])
        if active_cloids:
            cancel_orders = [Order(cloid=cloid, order_type=OrderType.CANCEL) for cloid in active_cloids]
            try:
                await client.place_orders(cancel_orders)
            except Exception:
                # Best-effort cleanup only.
                pass

        await client.stop()

from __future__ import annotations

from decimal import Decimal
from time import perf_counter
from typing import Any, Callable, Optional

import pytest

from kuru_sdk_py.client import KuruClient
from kuru_sdk_py.manager.order import Order, OrderSide, OrderStatus, OrderType


pytestmark = [
    pytest.mark.benchmark_live,
    pytest.mark.integration,
    pytest.mark.asyncio,
]


def _to_human(wei_amount: int, decimals: int) -> Decimal:
    return Decimal(wei_amount) / (Decimal(10) ** Decimal(decimals))


def _phase_start(progress: Callable[[str], None], name: str, detail: str = "") -> None:
    suffix = f" | {detail}" if detail else ""
    progress(f"START {name}{suffix}")


def _phase_end(progress: Callable[[str], None], name: str, summary: dict[str, Any]) -> None:
    progress(
        f"END {name} | success={summary['success_count']} timeout={summary['timeout_count']} "
        f"mismatch={summary['mismatch_count']} error={summary['error_count']} p50={summary['p50']} p95={summary['p95']}"
    )


async def _choose_limit_order(client: KuruClient, cloid: str) -> Order:
    base_wei, quote_wei = await client.user.get_margin_balances()
    base_human = _to_human(base_wei, client.market_config.base_token_decimals)
    quote_human = _to_human(quote_wei, client.market_config.quote_token_decimals)

    buy_price = Decimal("0.01")
    sell_price = Decimal("0.03")
    min_size = Decimal("210")

    if quote_human > Decimal("0.001"):
        side = OrderSide.BUY
        affordable = quote_human / buy_price if buy_price > 0 else Decimal("0")
        size = min(Decimal("220"), affordable * Decimal("0.1"))
        size = max(size, min_size)
        price = buy_price
    elif base_human > Decimal("0"):
        side = OrderSide.SELL
        size = min(Decimal("220"), base_human * Decimal("0.1"))
        size = max(size, min_size)
        price = sell_price
    else:
        # Deterministic fallback; failures are recorded in benchmark stats.
        side = OrderSide.BUY
        price = buy_price
        size = min_size

    return Order(
        cloid=cloid,
        order_type=OrderType.LIMIT,
        side=side,
        price=price,
        size=size,
        post_only=True,
    )


async def _best_effort_cancel_cloids(client: KuruClient, cloids: list[str]) -> bool:
    if not cloids:
        return True

    try:
        cancel_orders = [Order(cloid=cloid, order_type=OrderType.CANCEL) for cloid in cloids]
        await client.place_orders(cancel_orders)
        return True
    except Exception:
        return False


async def _place_single_limit_and_measure(
    *,
    client: KuruClient,
    tracker: Any,
    cloid_factory: Any,
    timeout_sec: float,
    active_cloids: set[str],
    reporter: Any,
    prefix: str,
) -> Optional[str]:
    cloid = cloid_factory(prefix)
    order = await _choose_limit_order(client, cloid)

    t0 = perf_counter()
    await client.place_orders([order], post_only=True)
    t_submit = perf_counter()

    event = await tracker.wait_for_next_event(cloid, timeout_sec)

    submit_series = reporter.series(f"{prefix}.t_submit")
    place_series = reporter.series(f"{prefix}.t_place_callback")
    event_series = reporter.series(f"{prefix}.t_event_after_submit")

    submit_series.record_sample(t_submit - t0)

    if event is None:
        place_series.record_timeout()
        event_series.record_timeout()
        return None

    if event.status != OrderStatus.ORDER_PLACED:
        place_series.record_mismatch(event.status)
        event_series.record_mismatch(event.status)
        return None

    place_series.record_sample(event.callback_time - t0)
    event_series.record_sample(event.callback_time - t_submit)
    active_cloids.add(cloid)

    return cloid


async def _cancel_single_order_and_measure(
    *,
    client: KuruClient,
    tracker: Any,
    cloid: str,
    timeout_sec: float,
    reporter: Any,
) -> bool:
    series = reporter.series("cancel.t_cancel_callback")

    t0 = perf_counter()
    await client.place_orders([Order(cloid=cloid, order_type=OrderType.CANCEL)])

    event = await tracker.wait_for_next_event(cloid, timeout_sec)

    if event is None:
        series.record_timeout()
        return False

    if event.status != OrderStatus.ORDER_CANCELLED:
        series.record_mismatch(event.status)
        return False

    series.record_sample(event.callback_time - t0)
    return True


async def _warmup_limit_placements(
    *,
    client: KuruClient,
    tracker: Any,
    cloid_factory: Any,
    active_cloids: set[str],
    timeout_sec: float,
    warmup: int,
    progress: Callable[[str], None],
) -> None:
    if warmup <= 0:
        return

    for i in range(warmup):
        progress(f"warmup limit placement {i + 1}/{warmup}")
        try:
            cloid = cloid_factory("warmup-place")
            order = await _choose_limit_order(client, cloid)
            await client.place_orders([order], post_only=True)
            event = await tracker.wait_for_next_event(cloid, timeout_sec)
            if event and event.status == OrderStatus.ORDER_PLACED:
                active_cloids.add(cloid)
                cancelled = await _best_effort_cancel_cloids(client, [cloid])
                if cancelled:
                    active_cloids.discard(cloid)
        except Exception:
            continue


async def test_benchmark_limit_primary_and_splits(
    benchmark_settings: Any,
    benchmark_reporter: Any,
    benchmark_client: dict[str, Any],
    cloid_factory: Any,
    progress_printer: Callable[[str], None],
) -> None:
    client: KuruClient = benchmark_client["client"]
    tracker = benchmark_client["tracker"]
    active_cloids: set[str] = benchmark_client["active_cloids"]

    _phase_start(
        progress_printer,
        "limit_primary",
        f"warmup={benchmark_settings.warmup} iterations={benchmark_settings.iterations} timeout={benchmark_settings.timeout_sec}s",
    )

    await _warmup_limit_placements(
        client=client,
        tracker=tracker,
        cloid_factory=cloid_factory,
        active_cloids=active_cloids,
        timeout_sec=benchmark_settings.timeout_sec,
        warmup=benchmark_settings.warmup,
        progress=progress_printer,
    )

    for i in range(benchmark_settings.iterations):
        progress_printer(f"limit_primary iteration {i + 1}/{benchmark_settings.iterations}")
        try:
            placed_cloid = await _place_single_limit_and_measure(
                client=client,
                tracker=tracker,
                cloid_factory=cloid_factory,
                timeout_sec=benchmark_settings.timeout_sec,
                active_cloids=active_cloids,
                reporter=benchmark_reporter,
                prefix="limit_primary",
            )
            if placed_cloid is not None:
                cancelled = await _best_effort_cancel_cloids(client, [placed_cloid])
                if cancelled:
                    active_cloids.discard(placed_cloid)
        except Exception as exc:
            benchmark_reporter.series("limit_primary.t_submit").record_error(exc)
            benchmark_reporter.series("limit_primary.t_place_callback").record_error(exc)
            benchmark_reporter.series("limit_primary.t_event_after_submit").record_error(exc)

    benchmark_reporter.add_note("primary_metric", "limit_primary.t_place_callback")
    benchmark_reporter.add_note("event_point", "callback_invocation")

    _phase_end(
        progress_printer,
        "limit_primary",
        benchmark_reporter.series("limit_primary.t_place_callback").to_summary(),
    )


async def test_benchmark_cancel_latency(
    benchmark_settings: Any,
    benchmark_reporter: Any,
    benchmark_client: dict[str, Any],
    cloid_factory: Any,
    progress_printer: Callable[[str], None],
) -> None:
    client: KuruClient = benchmark_client["client"]
    tracker = benchmark_client["tracker"]
    active_cloids: set[str] = benchmark_client["active_cloids"]

    _phase_start(progress_printer, "cancel_latency", f"iterations={benchmark_settings.iterations}")

    for i in range(benchmark_settings.iterations):
        progress_printer(f"cancel_latency iteration {i + 1}/{benchmark_settings.iterations}")
        cloid: Optional[str] = None
        try:
            cloid = cloid_factory("cancel-base")
            order = await _choose_limit_order(client, cloid)
            await client.place_orders([order], post_only=True)
            event = await tracker.wait_for_next_event(cloid, benchmark_settings.timeout_sec)

            if event is None:
                benchmark_reporter.series("cancel.t_cancel_callback").record_timeout()
                continue

            if event.status != OrderStatus.ORDER_PLACED:
                benchmark_reporter.series("cancel.t_cancel_callback").record_mismatch(event.status)
                continue

            active_cloids.add(cloid)
            cancelled = await _cancel_single_order_and_measure(
                client=client,
                tracker=tracker,
                cloid=cloid,
                timeout_sec=benchmark_settings.timeout_sec,
                reporter=benchmark_reporter,
            )
            if cancelled:
                active_cloids.discard(cloid)

        except Exception as exc:
            benchmark_reporter.series("cancel.t_cancel_callback").record_error(exc)
            if cloid is not None:
                active_cloids.add(cloid)

    _phase_end(
        progress_printer,
        "cancel_latency",
        benchmark_reporter.series("cancel.t_cancel_callback").to_summary(),
    )


async def test_benchmark_market_execution_latency(
    benchmark_settings: Any,
    benchmark_reporter: Any,
    benchmark_client: dict[str, Any],
    progress_printer: Callable[[str], None],
) -> None:
    client: KuruClient = benchmark_client["client"]
    series = benchmark_reporter.series("market_execution.t_send_to_receipt")

    base_wei, quote_wei = await client.user.get_margin_balances()
    base_human = _to_human(base_wei, client.market_config.base_token_decimals)
    quote_human = _to_human(quote_wei, client.market_config.quote_token_decimals)

    _phase_start(
        progress_printer,
        "market_execution",
        f"iterations={benchmark_settings.iterations} quote_balance={quote_human} base_balance={base_human}",
    )

    for i in range(benchmark_settings.iterations):
        progress_printer(f"market_execution iteration {i + 1}/{benchmark_settings.iterations}")
        try:
            t0 = perf_counter()
            if quote_human >= benchmark_settings.market_quote_amount:
                txhash = await client.place_market_buy(
                    quote_amount=benchmark_settings.market_quote_amount,
                    min_amount_out=Decimal("0"),
                    is_margin=True,
                    is_fill_or_kill=False,
                )
            elif base_human >= Decimal("0.000001"):
                sell_size = min(Decimal("0.0001"), base_human * Decimal("0.1"))
                txhash = await client.place_market_sell(
                    size=sell_size,
                    min_amount_out=Decimal("0"),
                    is_margin=True,
                    is_fill_or_kill=False,
                )
            else:
                series.record_mismatch(OrderStatus.ORDER_FAILED)
                progress_printer("market_execution skipped: insufficient margin balance")
                break

            await client.executor._wait_for_transaction_receipt(txhash)
            series.record_sample(perf_counter() - t0)

        except Exception as exc:
            series.record_error(exc)

    _phase_end(progress_printer, "market_execution", series.to_summary())


async def test_benchmark_cold_path_latency(
    benchmark_settings: Any,
    benchmark_reporter: Any,
    benchmark_configs: dict[str, Any],
    cloid_factory: Any,
    progress_printer: Callable[[str], None],
) -> None:
    series = benchmark_reporter.series("cold_path.t_start_plus_first_place_callback")

    _phase_start(progress_printer, "cold_path", "start client + first place callback")

    try:
        cold_client = await KuruClient.create(**benchmark_configs)
        tracker = _AdHocTracker()
        cold_client.set_order_callback(tracker.callback)

        t0 = perf_counter()
        await cold_client.start()

        cold_cloid = cloid_factory("cold-path")
        cold_order = await _choose_limit_order(cold_client, cold_cloid)
        await cold_client.place_orders([cold_order], post_only=True)
        cold_event = await tracker.wait_for_next_event(cold_cloid, benchmark_settings.timeout_sec)

        if cold_event is None:
            series.record_timeout()
        elif cold_event.status != OrderStatus.ORDER_PLACED:
            series.record_mismatch(cold_event.status)
        else:
            series.record_sample(cold_event.callback_time - t0)

        await _best_effort_cancel_cloids(cold_client, [cold_cloid])
        await cold_client.stop()
    except Exception as exc:
        series.record_error(exc)

    _phase_end(progress_printer, "cold_path", series.to_summary())


async def test_benchmark_batch_size_scaling(
    benchmark_settings: Any,
    benchmark_reporter: Any,
    benchmark_client: dict[str, Any],
    cloid_factory: Any,
    progress_printer: Callable[[str], None],
) -> None:
    client: KuruClient = benchmark_client["client"]
    tracker = benchmark_client["tracker"]
    active_cloids: set[str] = benchmark_client["active_cloids"]

    _phase_start(
        progress_printer,
        "batch_scaling",
        f"batch_sizes={list(benchmark_settings.batch_sizes)} iterations={benchmark_settings.iterations}",
    )

    for batch_size in benchmark_settings.batch_sizes:
        progress_printer(f"batch_scaling start batch_size={batch_size}")
        submit_series = benchmark_reporter.series(f"batch_size_{batch_size}.t_submit")
        place_series = benchmark_reporter.series(f"batch_size_{batch_size}.t_place_callback")
        event_series = benchmark_reporter.series(f"batch_size_{batch_size}.t_event_after_submit")

        for i in range(benchmark_settings.iterations):
            progress_printer(
                f"batch_scaling size={batch_size} iteration {i + 1}/{benchmark_settings.iterations}"
            )
            cloids: list[str] = []
            orders: list[Order] = []
            placed_cloids: list[str] = []

            try:
                for _ in range(batch_size):
                    cloid = cloid_factory(f"batch-{batch_size}")
                    cloids.append(cloid)
                    orders.append(await _choose_limit_order(client, cloid))

                t0 = perf_counter()
                await client.place_orders(orders, post_only=True)
                t_submit = perf_counter()
                submit_series.record_sample(t_submit - t0)

                for cloid in cloids:
                    event = await tracker.wait_for_next_event(cloid, benchmark_settings.timeout_sec)
                    if event is None:
                        place_series.record_timeout()
                        event_series.record_timeout()
                        continue

                    if event.status != OrderStatus.ORDER_PLACED:
                        place_series.record_mismatch(event.status)
                        event_series.record_mismatch(event.status)
                        continue

                    place_series.record_sample(event.callback_time - t0)
                    event_series.record_sample(event.callback_time - t_submit)
                    active_cloids.add(cloid)
                    placed_cloids.append(cloid)

            except Exception as exc:
                submit_series.record_error(exc)
                place_series.record_error(exc)
                event_series.record_error(exc)
            finally:
                cancelled = await _best_effort_cancel_cloids(client, placed_cloids)
                if cancelled:
                    for cloid in placed_cloids:
                        active_cloids.discard(cloid)

        _phase_end(progress_printer, f"batch_scaling(size={batch_size})", place_series.to_summary())


async def test_benchmark_gas_estimation(
    benchmark_settings: Any,
    benchmark_reporter: Any,
    benchmark_client: dict[str, Any],
    progress_printer: Callable[[str], None],
) -> None:
    client: KuruClient = benchmark_client["client"]
    executor = client.executor
    gas_price_series = benchmark_reporter.series("gas_estimation.t_eth_gas_price")
    estimate_gas_series = benchmark_reporter.series("gas_estimation.t_estimate_gas")

    _phase_start(progress_printer, "gas_estimation", f"iterations={benchmark_settings.iterations}")

    # Build a minimal single-buy batchUpdateMM transaction once to reuse across iterations.
    price_precision = executor.market_config.price_precision
    size_precision = executor.market_config.size_precision
    tick_size = executor.market_config.tick_size
    raw_price = int(Decimal("0.01") * Decimal(price_precision))
    if tick_size > 1:
        raw_price = (raw_price // tick_size) * tick_size
    raw_size = int(Decimal("210") * Decimal(size_precision))
    dummy_cloid = b"\x00" * 32

    function_call = executor.contract.functions.batchUpdateMM(
        {
            "orderBook": executor.order_book_address,
            "buyCloids": [dummy_cloid],
            "sellCloids": [],
            "cancelCloids": [],
            "buyPrices": [raw_price],
            "buySizes": [raw_size],
            "sellPrices": [],
            "sellSizes": [],
            "orderIdsToCancel": [],
            "postOnly": True,
        }
    )

    try:
        gas_price = await executor.w3.eth.gas_price
        nonce = await executor.w3.eth.get_transaction_count(executor.user_address, "pending")
        base_tx = await function_call.build_transaction(
            {
                "from": executor.user_address,
                "nonce": nonce,
                "gasPrice": int(gas_price),
                "chainId": executor._get_chain_id(),
                "gas": 21_000,
            }
        )
        base_tx.pop("gas", None)
    except Exception as exc:
        progress_printer(f"gas_estimation setup failed: {exc}")
        gas_price_series.record_error(exc)
        estimate_gas_series.record_error(exc)
        _phase_end(progress_printer, "gas_estimation", estimate_gas_series.to_summary())
        return

    for i in range(benchmark_settings.iterations):
        progress_printer(f"gas_estimation iteration {i + 1}/{benchmark_settings.iterations}")

        try:
            t0 = perf_counter()
            await executor.w3.eth.gas_price
            gas_price_series.record_sample(perf_counter() - t0)
        except Exception as exc:
            gas_price_series.record_error(exc)

        try:
            t0 = perf_counter()
            await executor.w3.eth.estimate_gas(base_tx)
            estimate_gas_series.record_sample(perf_counter() - t0)
        except Exception as exc:
            estimate_gas_series.record_error(exc)

    _phase_end(progress_printer, "gas_estimation", estimate_gas_series.to_summary())


async def test_benchmark_rpc_baseline(
    benchmark_settings: Any,
    benchmark_reporter: Any,
    benchmark_client: dict[str, Any],
    progress_printer: Callable[[str], None],
) -> None:
    client: KuruClient = benchmark_client["client"]
    series = benchmark_reporter.series("rpc_baseline.t_get_block_number")

    _phase_start(progress_printer, "rpc_baseline", f"iterations={benchmark_settings.iterations}")

    for i in range(benchmark_settings.iterations):
        progress_printer(f"rpc_baseline iteration {i + 1}/{benchmark_settings.iterations}")
        try:
            t0 = perf_counter()
            await client.executor.w3.eth.get_block_number()
            series.record_sample(perf_counter() - t0)
        except Exception as exc:
            series.record_error(exc)

    _phase_end(progress_printer, "rpc_baseline", series.to_summary())


class _AdHocTracker:
    def __init__(self) -> None:
        import asyncio

        self._queues: dict[str, asyncio.Queue[Any]] = {}

    async def callback(self, order: Order) -> None:
        import asyncio

        queue = self._queues.setdefault(order.cloid, asyncio.Queue())
        await queue.put(
            _AdHocEvent(
                status=order.status,
                callback_time=perf_counter(),
            )
        )

    async def wait_for_next_event(self, cloid: str, timeout_sec: float) -> Optional["_AdHocEvent"]:
        import asyncio

        queue = self._queues.setdefault(cloid, asyncio.Queue())
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            return None


class _AdHocEvent:
    def __init__(self, status: OrderStatus, callback_time: float) -> None:
        self.status = status
        self.callback_time = callback_time

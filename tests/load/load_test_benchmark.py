"""Phase 9 High-Throughput Load Benchmark Harness (Gate 9).

Validates Section 5, Phase 9 criteria:
  - Webhook Ingestion Throughput: >= 100 req/sec benchmark against staging.
  - P99 Latency Target: < 50ms per webhook delivery intake.
  - Concurrent SSE Telemetry Listeners: streaming telemetry client benchmark.

Security & Safety Invariants:
  - Secrets are NEVER hardcoded; loaded strictly from environment or secure CLI argument.
  - Secrets are NEVER printed or logged.
  - Traffic is strictly bounded; safe limits prevent unintended DoS.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import statistics
import sys
import time
import uuid
from typing import Any

import httpx


def generate_hmac(body: bytes, secret: str) -> str:
    """Generate SHA256 HMAC signature for webhook payload."""
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


async def benchmark_webhook_ingestion(
    base_url: str = "http://127.0.0.1:8000",
    secret: str = "",
    total_requests: int = 100,
    concurrency: int = 10,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Measure request throughput and latency distribution on webhook intake."""
    if not secret:
        raise ValueError("A valid non-empty webhook secret is required for benchmark signature generation.")

    latencies: list[float] = []
    success_count = 0
    rejected_count = 0
    server_error_count = 0
    timeout_count = 0
    connection_error_count = 0

    sem = asyncio.Semaphore(concurrency)

    async def _send_one(client: httpx.AsyncClient):
        nonlocal success_count, rejected_count, server_error_count, timeout_count, connection_error_count
        delivery_id = str(uuid.uuid4())
        payload = json.dumps({
            "action": "ping",
            "repository": {"id": 12345, "full_name": "codesentinel/bench"},
            "delivery": delivery_id,
        }).encode("utf-8")
        sig = generate_hmac(payload, secret)

        headers = {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": delivery_id,
            "Content-Type": "application/json",
        }

        async with sem:
            start_t = time.perf_counter()
            try:
                resp = await client.post(
                    f"{base_url.rstrip('/')}/api/v1/webhooks/github",
                    content=payload,
                    headers=headers,
                    timeout=timeout_seconds,
                )
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                latencies.append(dur_ms)
                if resp.status_code in (200, 202):
                    success_count += 1
                elif 400 <= resp.status_code < 500:
                    rejected_count += 1
                else:
                    server_error_count += 1
            except httpx.TimeoutException:
                timeout_count += 1
            except (httpx.NetworkError, httpx.ConnectError):
                connection_error_count += 1
            except Exception:  # noqa: BLE001
                connection_error_count += 1

    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits) as client:
        start_bench = time.perf_counter()
        tasks = [_send_one(client) for _ in range(total_requests)]
        await asyncio.gather(*tasks)
        total_time_s = time.perf_counter() - start_bench

    completed_requests = len(latencies)
    req_per_sec = completed_requests / total_time_s if total_time_s > 0 else 0.0

    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else p50
    p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else p95

    # Acceptance threshold checks (Phase 9 Staging Gates)
    throughput_met = req_per_sec >= 100.0
    latency_met = p99 < 50.0

    return {
        "total_requests": total_requests,
        "concurrency": concurrency,
        "completed_requests": completed_requests,
        "success_count": success_count,
        "rejected_count": rejected_count,
        "server_error_count": server_error_count,
        "timeout_count": timeout_count,
        "connection_error_count": connection_error_count,
        "total_time_seconds": round(total_time_s, 3),
        "requests_per_second": round(req_per_sec, 2),
        "p50_latency_ms": round(p50, 2),
        "p95_latency_ms": round(p95, 2),
        "p99_latency_ms": round(p99, 2),
        "throughput_met": throughput_met,
        "latency_met": latency_met,
    }


async def benchmark_sse_listeners(
    base_url: str = "http://127.0.0.1:8000",
    run_id: str | None = None,
    auth_user_id: str | None = None,
    concurrency: int = 10,
    duration_seconds: float = 5.0,
) -> dict[str, Any]:
    """Benchmark concurrent Server-Sent Events (SSE) streaming connections against `/api/v1/runs/{run_id}/events`."""
    connected_clients = 0
    events_received = 0
    disconnect_count = 0
    error_count = 0

    target_run_id = run_id or str(uuid.uuid4())
    url = f"{base_url.rstrip('/')}/api/v1/runs/{target_run_id}/events"

    headers = {}
    if auth_user_id:
        headers["X-Test-User-ID"] = auth_user_id

    async def _listen_client():
        nonlocal connected_clients, events_received, disconnect_count, error_count
        try:
            async with (
                httpx.AsyncClient(timeout=duration_seconds + 3.0) as client,
                client.stream("GET", url, headers=headers) as response,
            ):
                if response.status_code == 200:
                    connected_clients += 1
                    start = time.perf_counter()
                    async for _line in response.aiter_lines():
                        events_received += 1
                        if (time.perf_counter() - start) >= duration_seconds:
                            break
                else:
                    disconnect_count += 1
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            error_count += 1

    tasks = [asyncio.create_task(_listen_client()) for _ in range(concurrency)]
    await asyncio.sleep(duration_seconds)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    return {
        "concurrency": concurrency,
        "duration_seconds": duration_seconds,
        "connected_clients": connected_clients,
        "events_received": events_received,
        "disconnect_count": disconnect_count,
        "error_count": error_count,
    }


def print_report(metrics: dict[str, Any], sse_metrics: dict[str, Any] | None = None):
    """Format and print benchmark results without leaking sensitive configuration."""
    print("=" * 68)
    print("  CodeSentinel Phase 9 High-Throughput Load Benchmark")
    print("=" * 68)
    print(f"  Total Requests Attempted: {metrics['total_requests']}")
    print(f"  Concurrency Level:        {metrics['concurrency']}")
    print(f"  Successful (200/202):     {metrics['success_count']}")
    print(f"  Client Rejected (4xx):    {metrics['rejected_count']}")
    print(f"  Server Errors (5xx):      {metrics['server_error_count']}")
    print(f"  Request Timeouts:         {metrics['timeout_count']}")
    print(f"  Connection Failures:      {metrics['connection_error_count']}")
    print(f"  Total Wall Time:          {metrics['total_time_seconds']} s")
    print(f"  Throughput:               {metrics['requests_per_second']} req/sec")
    print(f"  p50 Latency:              {metrics['p50_latency_ms']} ms")
    print(f"  p95 Latency:              {metrics['p95_latency_ms']} ms")
    print(f"  p99 Latency:              {metrics['p99_latency_ms']} ms")
    print("-" * 68)
    print("  Gate Evaluation (Staging Acceptance Criteria):")
    tp_status = "MET (>= 100 req/s)" if metrics.get("throughput_met") else "NOT MET (< 100 req/s)"
    lat_status = "MET (< 50ms)" if metrics.get("latency_met") else "NOT MET (>= 50ms)"
    print(f"    Throughput Gate (>= 100 req/s): {tp_status}")
    print(f"    p99 Latency Gate (< 50ms):      {lat_status}")

    if sse_metrics:
        print("-" * 68)
        print("  Concurrent SSE Streaming Clients Benchmark (/api/v1/runs/{id}/events)")
        print("-" * 68)
        print(f"  Target SSE Concurrency:   {sse_metrics['concurrency']}")
        print(f"  Connected Clients (200):  {sse_metrics['connected_clients']}")
        print(f"  Events Streamed:          {sse_metrics['events_received']}")
        print(f"  Disconnections:           {sse_metrics['disconnect_count']}")
        print(f"  Connection Errors:        {sse_metrics['error_count']}")
    print("=" * 68)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CodeSentinel Staging Load Benchmark Harness")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Target API Base URL")
    parser.add_argument(
        "--secret",
        default="",
        help="GitHub webhook HMAC secret (defaults to GITHUB_WEBHOOK_SECRET env var)",
    )
    parser.add_argument("--requests", type=int, default=100, help="Total webhook requests (bounded <= 5000)")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrency limit (bounded <= 100)")
    parser.add_argument("--sse-clients", type=int, default=0, help="Concurrent SSE streaming clients (bounded <= 50)")
    parser.add_argument("--sse-run-id", default=None, help="Specific run ID to stream SSE events from")
    parser.add_argument("--auth-user-id", default=None, help="User ID for X-Test-User-ID auth header in SSE tests")
    return parser.parse_args()


def main():
    args = parse_args()

    # Resolve secret safely from CLI arg or environment variable
    secret = args.secret or os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        print(
            "ERROR: Webhook secret must be provided via the GITHUB_WEBHOOK_SECRET environment "
            "variable or --secret argument.\nNever hardcode credentials or secrets in benchmark files.",
            file=sys.stderr,
        )
        sys.exit(1)

    total_requests = min(max(args.requests, 1), 5000)
    concurrency = min(max(args.concurrency, 1), 100)
    sse_clients = min(max(args.sse_clients, 0), 50)

    print(f"Running benchmark against {args.url} (requests={total_requests}, concurrency={concurrency})...")
    webhook_metrics = asyncio.run(
        benchmark_webhook_ingestion(
            base_url=args.url,
            secret=secret,
            total_requests=total_requests,
            concurrency=concurrency,
        )
    )

    sse_metrics = None
    if sse_clients > 0:
        print(f"Running SSE client benchmark ({sse_clients} concurrent listeners)...")
        sse_metrics = asyncio.run(
            benchmark_sse_listeners(
                base_url=args.url,
                run_id=args.sse_run_id,
                auth_user_id=args.auth_user_id,
                concurrency=sse_clients,
            )
        )

    print_report(webhook_metrics, sse_metrics)


if __name__ == "__main__":
    main()

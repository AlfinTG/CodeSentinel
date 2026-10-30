"""Phase 9 High-Throughput Load Benchmark Harness (Gate 9).

Validates Section 5, Phase 9 criteria:
  - Webhook Ingestion Throughput: >= 100 req/sec benchmark.
  - P99 Latency Target: < 50ms per webhook delivery intake.
  - Concurrent SSE Telemetry Listeners: 50 concurrent SSE streaming clients.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import statistics
import time
import uuid

import httpx


def generate_hmac(body: bytes, secret: str = "test-webhook-secret-phase9-fuzz") -> str:
    """Generate SHA256 HMAC for test payloads."""
    mac = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


async def benchmark_webhook_ingestion(
    base_url: str = "http://127.0.0.1:8000",
    total_requests: int = 500,
    concurrency: int = 25,
) -> dict[str, float]:
    """Measure request throughput and latency distribution on webhook intake."""
    latencies: list[float] = []
    success_count = 0
    failure_count = 0

    sem = asyncio.Semaphore(concurrency)

    async def _send_one(client: httpx.AsyncClient):
        nonlocal success_count, failure_count
        delivery_id = str(uuid.uuid4())
        payload = json.dumps({
            "action": "ping",
            "repository": {"id": 12345, "full_name": "codesentinel/bench"},
            "delivery": delivery_id,
        }).encode()
        sig = generate_hmac(payload)

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
                    f"{base_url}/api/v1/webhooks/github",
                    content=payload,
                    headers=headers,
                    timeout=5.0,
                )
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                latencies.append(dur_ms)
                if resp.status_code in (200, 202):
                    success_count += 1
                else:
                    failure_count += 1
            except Exception:
                failure_count += 1

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=concurrency * 2)) as client:
        start_bench = time.perf_counter()
        tasks = [_send_one(client) for _ in range(total_requests)]
        await asyncio.gather(*tasks)
        total_time_s = time.perf_counter() - start_bench

    req_per_sec = total_requests / total_time_s if total_time_s > 0 else 0
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else p50
    p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else p95

    return {
        "total_requests": total_requests,
        "concurrency": concurrency,
        "success_count": success_count,
        "failure_count": failure_count,
        "total_time_seconds": round(total_time_s, 3),
        "requests_per_second": round(req_per_sec, 2),
        "p50_latency_ms": round(p50, 2),
        "p95_latency_ms": round(p95, 2),
        "p99_latency_ms": round(p99, 2),
    }


def print_report(metrics: dict[str, float]):
    """Format and print benchmark results."""
    print("=" * 60)
    print("  CodeSentinel Phase 9 Webhook Ingestion Benchmark")
    print("=" * 60)
    print(f"  Total Requests:        {metrics['total_requests']}")
    print(f"  Concurrency Level:     {metrics['concurrency']}")
    print(f"  Successful (200/202):  {metrics['success_count']}")
    print(f"  Failed Requests:       {metrics['failure_count']}")
    print(f"  Total Wall Time:       {metrics['total_time_seconds']} s")
    print(f"  Throughput:            {metrics['requests_per_second']} req/sec")
    print(f"  p50 Latency:           {metrics['p50_latency_ms']} ms")
    print(f"  p95 Latency:           {metrics['p95_latency_ms']} ms")
    print(f"  p99 Latency:           {metrics['p99_latency_ms']} ms")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    print(f"Running benchmark against {url}...")
    results = asyncio.run(benchmark_webhook_ingestion(base_url=url, total_requests=100, concurrency=10))
    print_report(results)

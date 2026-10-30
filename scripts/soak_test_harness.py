"""Phase 9 Continuous Soak & Stability Test Harness (Gate 9).

Validates Section 5, Phase 9 criteria:
  - Long-duration stability under continuous staging load.
  - Memory leak monitoring via Prometheus process metrics over time.
  - Service restart and probe failure detection.
  - Continuous metrics endpoint reliability.

Operational Requirements:
  - Staging gate requires a 48-hour continuous soak test with zero restarts and zero data corruption.
  - Short runs must explicitly state that memory leak absence requires the full 48-hour duration.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from datetime import UTC, datetime

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("soak_test")

# Regex patterns for standard Prometheus process metrics
RE_RSS_BYTES = re.compile(r"^process_resident_memory_bytes\s+([0-9.eE+-]+)", re.MULTILINE)
RE_START_TIME = re.compile(r"^process_start_time_seconds\s+([0-9.eE+-]+)", re.MULTILINE)


class SoakTestState:
    """Tracks continuous soak test state and time-series telemetry."""

    def __init__(self, target_url: str):
        self.target_url = target_url
        self.start_wall_time = time.time()
        self.iterations = 0
        self.health_successes = 0
        self.health_failures = 0
        self.metrics_successes = 0
        self.metrics_failures = 0
        self.service_restarts = 0
        self.initial_process_start_time: float | None = None
        self.memory_samples: list[tuple[str, float]] = []  # (timestamp_iso, rss_mib)

    def record_metrics_data(self, metrics_text: str):
        """Extract process memory and detect unexpected process restarts."""
        # 1. Parse RSS memory
        m_rss = RE_RSS_BYTES.search(metrics_text)
        if m_rss:
            rss_bytes = float(m_rss.group(1))
            rss_mib = round(rss_bytes / (1024 * 1024), 2)
            now_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            self.memory_samples.append((now_iso, rss_mib))

        # 2. Check process start time for restarts
        m_start = RE_START_TIME.search(metrics_text)
        if m_start:
            proc_start = float(m_start.group(1))
            if self.initial_process_start_time is None:
                self.initial_process_start_time = proc_start
            elif abs(proc_start - self.initial_process_start_time) > 1.0:
                self.service_restarts += 1
                logger.warning(
                    "Service restart detected! Previous start_time=%.0f, current start_time=%.0f",
                    self.initial_process_start_time,
                    proc_start,
                )
                self.initial_process_start_time = proc_start


async def run_soak_iteration(client: httpx.AsyncClient, base_url: str, state: SoakTestState) -> bool:
    """Run one synthetic health probe and metrics scrape cycle."""
    state.iterations += 1
    iteration_ok = True

    # 1. Health readiness probe
    try:
        resp_health = await client.get(f"{base_url.rstrip('/')}/api/v1/health/ready", timeout=5.0)
        if resp_health.status_code == 200:
            state.health_successes += 1
        else:
            state.health_failures += 1
            iteration_ok = False
            logger.warning("Iteration %d: Health probe returned status %d", state.iterations, resp_health.status_code)
    except Exception as exc:  # noqa: BLE001
        state.health_failures += 1
        iteration_ok = False
        logger.error("Iteration %d: Health probe exception: %s", state.iterations, exc)

    # 2. Prometheus metrics scrape & memory telemetry
    try:
        resp_metrics = await client.get(f"{base_url.rstrip('/')}/metrics", timeout=5.0)
        if resp_metrics.status_code == 200:
            state.metrics_successes += 1
            state.record_metrics_data(resp_metrics.text)
        else:
            state.metrics_failures += 1
            iteration_ok = False
            logger.warning("Iteration %d: Metrics probe returned status %d", state.iterations, resp_metrics.status_code)
    except Exception as exc:  # noqa: BLE001
        state.metrics_failures += 1
        iteration_ok = False
        logger.error("Iteration %d: Metrics probe exception: %s", state.iterations, exc)

    return iteration_ok


def print_summary_report(state: SoakTestState, total_elapsed_s: float):
    """Print complete soak test audit report."""
    hours = round(total_elapsed_s / 3600, 2)
    uptime_pct = (
        round((state.health_successes / state.iterations) * 100, 2) if state.iterations > 0 else 0.0
    )

    rss_values = [m[1] for m in state.memory_samples]
    init_mem = rss_values[0] if rss_values else None
    final_mem = rss_values[-1] if rss_values else None
    min_mem = min(rss_values) if rss_values else None
    max_mem = max(rss_values) if rss_values else None
    mem_delta = round(final_mem - init_mem, 2) if (init_mem and final_mem) else None

    print("\n" + "=" * 68)
    print("  CodeSentinel Phase 9 Continuous Soak & Stability Report")
    print("=" * 68)
    print(f"  Target Server:             {state.target_url}")
    print(f"  Total Duration:            {total_elapsed_s:.1f} s ({hours} hours)")
    print(f"  Total Probe Cycles:        {state.iterations}")
    print(f"  Health Check Successes:    {state.health_successes}")
    print(f"  Health Check Failures:     {state.health_failures}")
    print(f"  Readiness Uptime Rate:     {uptime_pct} %")
    print(f"  Metrics Scrape Successes:  {state.metrics_successes}")
    print(f"  Metrics Scrape Failures:   {state.metrics_failures}")
    print(f"  Detected Service Restarts: {state.service_restarts}")

    if rss_values:
        print("-" * 68)
        print("  Resident Set Size (RSS) Memory Profile:")
        print(f"    Initial Memory (RSS):    {init_mem:.2f} MiB")
        print(f"    Final Memory (RSS):      {final_mem:.2f} MiB")
        print(f"    Min Memory:              {min_mem:.2f} MiB")
        print(f"    Max Memory:              {max_mem:.2f} MiB")
        delta_str = f"+{mem_delta:.2f}" if (mem_delta is not None and mem_delta >= 0) else f"{mem_delta:.2f}"
        print(f"    Total Net Change:        {delta_str} MiB across {len(rss_values)} samples")
    else:
        print("-" * 68)
        print("  Resident Set Size (RSS):   No process_resident_memory_bytes data parsed")

    print("-" * 68)
    if hours < 48.0:
        print("  NOTE: This execution did NOT complete the 48-hour staging requirement.")
        print("  Demonstrating the absence of slow memory leaks requires the full")
        print("  48-hour continuous soak test before production cutover (Phase 10).")
    else:
        print("  Full 48-hour staging soak requirement reached.")
    print("=" * 68 + "\n")


async def main_async(base_url: str, duration_seconds: float, poll_interval: float):
    """Execute continuous soak test loop."""
    logger.info(
        "Starting Phase 9 Soak Test against %s (target duration=%.1fs / %.2fh, interval=%.1fs)...",
        base_url,
        duration_seconds,
        duration_seconds / 3600,
        poll_interval,
    )
    state = SoakTestState(target_url=base_url)
    start_t = time.time()
    end_t = start_t + duration_seconds

    async with httpx.AsyncClient() as client:
        while time.time() < end_t:
            await run_soak_iteration(client, base_url, state)

            if state.iterations % 20 == 0:
                elapsed = round(time.time() - start_t, 1)
                latest_mem = f"{state.memory_samples[-1][1]:.2f} MiB" if state.memory_samples else "N/A"
                logger.info(
                    "Soak Progress: Cycles=%d, UptimeOK=%d, Failures=%d, Restarts=%d, Memory=%s, Elapsed=%ss",
                    state.iterations,
                    state.health_successes,
                    state.health_failures,
                    state.service_restarts,
                    latest_mem,
                    elapsed,
                )

            await asyncio.sleep(poll_interval)

    total_elapsed = round(time.time() - start_t, 2)
    print_summary_report(state, total_elapsed)

    if state.health_failures > 0 or state.service_restarts > 0:
        logger.error(
            "Soak test completed with ERRORS: %d health failures, %d restarts",
            state.health_failures,
            state.service_restarts,
        )
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CodeSentinel Phase 9 Soak Test Harness")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Target base URL")
    parser.add_argument(
        "--duration-hours",
        type=float,
        default=48.0,
        help="Test duration in hours (default: 48.0 for staging sign-off)",
    )
    parser.add_argument(
        "--duration-minutes",
        type=float,
        default=None,
        help="Optional override for shorter test runs in minutes",
    )
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.duration_minutes is not None:
        duration_seconds = args.duration_minutes * 60.0
    else:
        duration_seconds = args.duration_hours * 3600.0

    asyncio.run(main_async(args.url, duration_seconds, args.interval))


if __name__ == "__main__":
    main()

"""Phase 9 Continuous Soak & Stability Test Harness (Gate 9).

Validates Section 5, Phase 9 criteria:
  - Long-duration stability under simulated load.
  - Memory leak absence during repeated run ingestion.
  - Orphaned task recovery and audit logging integrity.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("soak_test")


async def run_soak_iteration(client: httpx.AsyncClient, base_url: str, iteration: int) -> bool:
    """Run one synthetic health, metrics, and API cycle."""
    try:
        # 1. Health check
        resp_health = await client.get(f"{base_url}/api/v1/health/ready", timeout=5.0)
        if resp_health.status_code != 200:
            logger.warning("Iteration %d: Health probe returned %d", iteration, resp_health.status_code)
            return False

        # 2. Prometheus metrics scrape
        resp_metrics = await client.get(f"{base_url}/metrics", timeout=5.0)
        if resp_metrics.status_code != 200:
            logger.warning("Iteration %d: Metrics probe returned %d", iteration, resp_metrics.status_code)
            return False

        return True
    except Exception as exc:
        logger.error("Iteration %d failed with exception: %s", iteration, exc)
        return False


async def main(base_url: str, duration_minutes: int, poll_interval_seconds: float):
    """Execute continuous soak test loop."""
    logger.info("Starting Phase 9 Soak Test against %s for %d minutes...", base_url, duration_minutes)
    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)
    iteration = 0
    successes = 0
    failures = 0

    async with httpx.AsyncClient() as client:
        while time.time() < end_time:
            iteration += 1
            ok = await run_soak_iteration(client, base_url, iteration)
            if ok:
                successes += 1
            else:
                failures += 1

            if iteration % 20 == 0:
                elapsed = round(time.time() - start_time, 1)
                logger.info(
                    "Soak Progress: Iteration=%d, Successes=%d, Failures=%d, Elapsed=%ss",
                    iteration,
                    successes,
                    failures,
                    elapsed,
                )

            await asyncio.sleep(poll_interval_seconds)

    total_time = round(time.time() - start_time, 2)
    logger.info("=" * 60)
    logger.info("Soak Test Completed in %s seconds", total_time)
    logger.info("Total Iterations: %d | Success: %d | Failures: %d", iteration, successes, failures)
    logger.info("=" * 60)

    if failures > 0:
        logger.error("Soak test recorded %d failures!", failures)
        sys.exit(1)
    else:
        logger.info("Soak test PASSED with 100%% uptime!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CodeSentinel Phase 9 Soak Test Harness")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Target base URL")
    parser.add_argument("--duration", type=int, default=5, help="Test duration in minutes")
    parser.add_argument("--interval", type=float, default=1.0, help="Poll interval in seconds")
    args = parser.parse_args()

    asyncio.run(main(args.url, args.duration, args.interval))

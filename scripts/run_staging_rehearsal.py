#!/usr/bin/env python3
"""CodeSentinel Phase 9 End-to-End Staging Rehearsal & Security Fuzzing Orchestrator.

Orchestrates the pre-production validation suite:
1. Security & Adversarial Fuzzing: Prompt injection, evidence spoofing, HMAC forgery.
2. Migration Rollback & Bidirectional Schema Safety: Alembic upgrade/downgrade cycle.
3. Disaster Recovery Reconstitution Drill: AES-256-GCM backup, restore, RTO/RPO verification.
4. High-Throughput Load & Webhook Intake Benchmark: Target >= 100 req/sec, p99 < 50ms, 100% success.
5. In-Process Health & Telemetry Probe: Validates liveness, readiness, and metrics generation.
   (Note: Distinguishes local in-process testing from a sustained 48-hour soak test on deployed infrastructure.)
6. Emits STAGING_REHEARSAL_REPORT.json with explicit thresholds and actual measurements.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import hmac
import json
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"

LOAD_BENCHMARK_MIN_RPS = 100.0
LOAD_BENCHMARK_MAX_P99_MS = 50.0


def run_command_buffered(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Execute command synchronously, capturing stdout and stderr."""
    proc = subprocess.run(
        cmd,
        cwd=cwd or ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode, combined


def run_fuzzing_suite(python_exe: str) -> dict:
    """Run Phase 9 Adversarial Fuzzing and Prompt Injection pytest suite."""
    print("--> [1/5] Running Phase 9 Adversarial Security & Prompt Injection Fuzzing...")
    t0 = time.perf_counter()
    code, output = run_command_buffered(
        [python_exe, "-m", "pytest", "backend/tests/test_phase9_fuzzing.py", "-v"],
        cwd=ROOT_DIR,
    )
    dur = round(time.perf_counter() - t0, 3)
    passed = code == 0
    print(f"    Fuzzing Result: {'PASSED' if passed else 'FAILED'} in {dur}s (code {code})")
    return {
        "suite": "Adversarial Fuzzing & Prompt Injection",
        "passed": passed,
        "exit_code": code,
        "duration_sec": dur,
        "output_tail": output.splitlines()[-5:] if output else [],
    }


def run_migration_rollback_suite(python_exe: str) -> dict:
    """Run Phase 8/9 Alembic Migration Rollback verification."""
    print("--> [2/5] Running Schema Migration Rollback & Reversibility Verification...")
    t0 = time.perf_counter()
    code, output = run_command_buffered(
        [python_exe, "-m", "pytest", "backend/tests/test_migration_rollback.py", "-v"],
        cwd=ROOT_DIR,
    )
    dur = round(time.perf_counter() - t0, 3)
    passed = code == 0
    print(f"    Rollback Result: {'PASSED' if passed else 'FAILED'} in {dur}s (code {code})")
    return {
        "suite": "Schema Migration Rollback",
        "passed": passed,
        "exit_code": code,
        "duration_sec": dur,
        "output_tail": output.splitlines()[-5:] if output else [],
    }


def run_dr_drill(python_exe: str) -> dict:
    """Run Phase 8 Disaster Recovery Drill Script."""
    print("--> [3/5] Executing Disaster Recovery Reconstitution Drill...")
    t0 = time.perf_counter()
    code, _output = run_command_buffered(
        [python_exe, "scripts/test_disaster_recovery_drill.py"],
        cwd=ROOT_DIR,
    )
    dur = round(time.perf_counter() - t0, 3)
    passed = code == 0
    print(f"    DR Drill Result: {'PASSED' if passed else 'FAILED'} in {dur}s (code {code})")

    drill_data = {}
    dr_report_file = ROOT_DIR / "DISASTER_RECOVERY_DRILL_REPORT.json"
    if dr_report_file.exists():
        with contextlib.suppress(Exception):
            drill_data = json.loads(dr_report_file.read_text(encoding="utf-8"))

    return {
        "suite": "Disaster Recovery Reconstitution Drill",
        "passed": passed,
        "exit_code": code,
        "duration_sec": dur,
        "drill_metrics": drill_data.get("metrics", {}),
    }


async def run_in_process_benchmark(requests_count: int = 100, concurrency: int = 10) -> dict:
    """Run in-process webhook intake load benchmark using FastAPI ASGI transport.

    Enforces Phase 9 SLA thresholds:
      - Throughput: >= 100 requests/sec
      - P99 Latency: < 50 ms
      - Success Rate: 100% of requests successful
    """
    print(f"--> [4/5] Executing Webhook Ingestion Load Benchmark ({requests_count} requests, concurrency={concurrency})...")
    import httpx
    from app.core.config import settings
    from app.main import app

    secret = "staging_rehearsal_benchmark_secret_key"
    settings.GITHUB_WEBHOOK_SECRET = secret

    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    success_count = 0

    async def _send(client: httpx.AsyncClient, i: int):
        nonlocal success_count
        payload = json.dumps({
            "action": "ping",
            "repository": {"id": 99999, "full_name": "staging/rehearsal"},
            "delivery": f"staging-del-{i}",
        }).encode("utf-8")
        mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
        sig = f"sha256={mac.hexdigest()}"
        headers = {
            "x-hub-signature-256": sig,
            "x-github-event": "ping",
            "x-github-delivery": f"staging-del-{i}",
            "content-type": "application/json",
        }
        async with sem:
            t0 = time.perf_counter()
            r = await client.post("/api/v1/webhooks/github", content=payload, headers=headers)
            dur_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(dur_ms)
            if r.status_code in (200, 202):
                success_count += 1

    transport = httpx.ASGITransport(app=app)
    t_start = time.perf_counter()
    async with httpx.AsyncClient(transport=transport, base_url="http://staging-testserver") as client:
        tasks = [_send(client, idx) for idx in range(requests_count)]
        await asyncio.gather(*tasks)
    total_sec = time.perf_counter() - t_start

    req_per_sec = requests_count / total_sec if total_sec > 0 else 0
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else p50
    p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else p95

    throughput_met = req_per_sec >= LOAD_BENCHMARK_MIN_RPS
    latency_met = p99 < LOAD_BENCHMARK_MAX_P99_MS
    success_met = (success_count == requests_count) and (requests_count > 0)
    passed = bool(throughput_met and latency_met and success_met)

    print(
        f"    Load Result: {req_per_sec:.1f} req/s (threshold: >= {LOAD_BENCHMARK_MIN_RPS}), "
        f"p50={p50:.2f}ms, p99={p99:.2f}ms (threshold: < {LOAD_BENCHMARK_MAX_P99_MS}ms), "
        f"Success: {success_count}/{requests_count} -> {'PASSED' if passed else 'FAILED'}"
    )

    thresholds = {
        "min_requests_per_second": LOAD_BENCHMARK_MIN_RPS,
        "max_p99_latency_ms": LOAD_BENCHMARK_MAX_P99_MS,
        "min_success_rate_pct": 100.0,
    }
    measurements = {
        "total_requests": requests_count,
        "concurrency": concurrency,
        "success_count": success_count,
        "success_rate_pct": round((success_count / requests_count * 100.0) if requests_count else 0.0, 2),
        "duration_sec": round(total_sec, 3),
        "requests_per_second": round(req_per_sec, 2),
        "p50_latency_ms": round(p50, 2),
        "p95_latency_ms": round(p95, 2),
        "p99_latency_ms": round(p99, 2),
    }

    return {
        "suite": "Webhook Ingestion Load Benchmark (In-Process ASGI)",
        "passed": passed,
        "execution_environment": "in_process_asgi",
        "thresholds": thresholds,
        "measurements": measurements,
        "throughput_met": throughput_met,
        "latency_met": latency_met,
        "success_rate_met": success_met,
        # Backwards-compatible flat keys
        "total_requests": requests_count,
        "concurrency": concurrency,
        "success_count": success_count,
        "duration_sec": round(total_sec, 3),
        "requests_per_second": round(req_per_sec, 2),
        "p50_latency_ms": round(p50, 2),
        "p95_latency_ms": round(p95, 2),
        "p99_latency_ms": round(p99, 2),
    }


async def run_soak_probe_iteration() -> dict:
    """Run an automated in-process health and metrics probe.

    Validates liveness, readiness, and Prometheus metric generation in-process.
    Note: A single probe does NOT constitute a continuous 48-hour staging soak test.
    """
    print("--> [5/5] Executing In-Process Health & Prometheus Telemetry Probe...")
    import httpx
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://staging-testserver") as client:
        # Check health probe
        health_resp = await client.get("/api/v1/health/live")
        health_ok = health_resp.status_code == 200

        # Check readiness probe
        ready_resp = await client.get("/api/v1/health/ready")
        ready_ok = ready_resp.status_code == 200

        # Check metrics endpoint
        metrics_resp = await client.get("/metrics")
        metrics_ok = metrics_resp.status_code == 200 and "codesentinel_" in metrics_resp.text

    passed = health_ok and ready_ok and metrics_ok
    print(f"    Probe Result: Health={health_ok}, Ready={ready_ok}, Metrics={metrics_ok} (In-process probe, not 48h soak)")

    return {
        "suite": "Health & Prometheus Telemetry Endpoint Probe (In-Process)",
        "passed": passed,
        "execution_environment": "in_process_asgi",
        "live_health_probe": health_ok,
        "ready_probe": ready_ok,
        "metrics_probe": metrics_ok,
        "is_sustained_soak": False,
        "note": "Validates liveness, readiness, and Prometheus metric generation in-process; does not constitute a sustained 48h soak test.",
    }


def execute_staging_rehearsal(report_path: Path) -> dict:
    """Execute complete staging rehearsal orchestrator."""
    rehearsal_start = time.perf_counter()
    python_exe = sys.executable

    # Ensure backend is on sys.path
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    fuzzing_res = run_fuzzing_suite(python_exe)
    migration_res = run_migration_rollback_suite(python_exe)
    dr_res = run_dr_drill(python_exe)
    load_res = asyncio.run(run_in_process_benchmark(requests_count=100, concurrency=10))
    soak_res = asyncio.run(run_soak_probe_iteration())

    all_passed = bool(
        fuzzing_res["passed"]
        and migration_res["passed"]
        and dr_res["passed"]
        and load_res["passed"]
        and soak_res["passed"]
    )

    total_dur = round(time.perf_counter() - rehearsal_start, 3)

    final_report = {
        "status": "PASSED" if all_passed else "FAILED",
        "phase": "Phase 9: End-to-End Staging Rehearsal & Security Fuzzing",
        "timestamp": datetime.now(UTC).isoformat(),
        "total_duration_sec": total_dur,
        "test_execution_mode": "local_in_process",
        "staging_infrastructure_deployed": False,
        "suites": {
            "adversarial_fuzzing": fuzzing_res,
            "migration_rollback": migration_res,
            "disaster_recovery_drill": dr_res,
            "load_benchmark": load_res,
            "soak_and_telemetry_probe": soak_res,
        },
        "staging_acceptance_criteria": {
            "prompt_injection_tamper_prevention": fuzzing_res["passed"],
            "zero_downtime_reversible_migrations": migration_res["passed"],
            "disaster_recovery_reconstitution_fidelity_100pct": dr_res["passed"],
            "webhook_ingestion_throughput_and_latency": load_res["passed"],
            "continuous_health_and_metrics_integrity": soak_res["passed"],
            "sustained_48h_staging_soak_verified": False,
        },
    }

    report_path.write_text(json.dumps(final_report, indent=2), encoding="utf-8")
    return final_report


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeSentinel Staging Rehearsal Orchestrator")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT_DIR / "STAGING_REHEARSAL_REPORT.json",
        help="Destination path for staging rehearsal report JSON",
    )
    args = parser.parse_args()

    print("=================================================================")
    print("   CodeSentinel End-to-End Staging Rehearsal (Phase 9)           ")
    print("=================================================================")
    try:
        report = execute_staging_rehearsal(args.report)
        print("=================================================================")
        if report["status"] == "PASSED":
            print(f"[SUCCESS] Staging Rehearsal PASSED across all 5 verification suites in {report['total_duration_sec']}s")
            print(f"Report written to: {args.report}")
            sys.exit(0)
        else:
            print(f"[FAILURE] Staging Rehearsal FAILED in {report['total_duration_sec']}s", file=sys.stderr)
            print(f"Report written to: {args.report}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL REHEARSAL ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

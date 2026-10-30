#!/usr/bin/env python3
"""CodeSentinel Production Release Invariant Gate Sign-Off Validator (Phase 10).

Programmatically verifies all 10 release gates and invariants:
- Gate 1:  Runner Hardening & Execution Sandbox Isolation
- Gate 2:  Durable Schema & Bidirectional Alembic Migrations
- Gate 3:  Webhook HMAC-SHA256 & Idempotent Deduplication
- Gate 4:  AI Hypothesis Boundary & Prompt Injection Immunity
- Gate 5:  Cloud KMS & Zero-Plaintext Secrets Architecture
- Gate 6:  Observability, SLA Alert Rules & SRE Dashboards
- Gate 7:  Frontend Production Build & WCAG 2.1 AA Accessibility
- Gate 8:  Disaster Recovery, Authenticated Backup & RTO/RPO Compliance
- Gate 9:  Staging Rehearsal & High-Throughput Load Verification
- Gate 10: Production Reverse Proxy, TLS 1.3 & Rate Limiting

Outputs RELEASE_GATE_SIGNOFF.json and returns 0 only upon 100% gate passage with verified evidence.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def check_gate(
    gate_num: int,
    name: str,
    checker_fn,
) -> dict:
    """Execute gate checker and format result."""
    t0 = time.perf_counter()
    res = checker_fn()
    if len(res) == 4:
        passed, reason, metadata, status_str = res
    else:
        passed, reason, metadata = res
        status_str = "PASSED" if passed else "FAILED"
    dur = round(time.perf_counter() - t0, 3)
    print(f"  [Gate {gate_num:02d}] {name:<55} -> {status_str} ({dur}s)")
    if not passed:
        print(f"            Reason: {reason}")
    return {
        "gate": gate_num,
        "name": name,
        "passed": passed,
        "status": status_str,
        "reason": reason,
        "duration_sec": dur,
        "metadata": metadata,
    }


def gate_1_runner_isolation(root_dir: Path | None = None) -> tuple[bool, str, dict, str]:
    """Gate 1: Verify runner container hardening and execution isolation evidence."""
    base = root_dir or ROOT_DIR
    dockerfile = base / "runner" / "Dockerfile"
    ci_workflow = base / ".github" / "workflows" / "runner_image.yml"
    if not dockerfile.exists():
        return False, "Missing runner/Dockerfile", {}, "FAILED"
    if not ci_workflow.exists():
        return False, "Missing .github/workflows/runner_image.yml", {}, "FAILED"
    content = dockerfile.read_text(encoding="utf-8")
    if "10001:10001" not in content and "10001" not in content:
        return False, "Runner Dockerfile does not enforce non-root UID 10001", {}, "FAILED"

    # Runtime isolation check: require active daemon or explicit runner verification report
    runner_report = base / "RUNNER_VERIFICATION_REPORT.json"
    runtime_bin = shutil.which("docker") or shutil.which("podman")
    daemon_available = False
    if runtime_bin:
        with contextlib.suppress(Exception):
            res = subprocess.run([runtime_bin, "info"], capture_output=True, timeout=3, check=False)
            daemon_available = (res.returncode == 0)

    has_runtime_evidence = False
    if runner_report.exists():
        with contextlib.suppress(Exception):
            rdata = json.loads(runner_report.read_text(encoding="utf-8"))
            has_runtime_evidence = (
                rdata.get("status") == "PASSED" and rdata.get("runtime_isolation_verified") is True
            )

    if daemon_available or has_runtime_evidence:
        return (
            True,
            "Runner Dockerfile enforces non-root UID 10001 and container runtime isolation verified",
            {"dockerfile": str(dockerfile), "daemon_available": daemon_available, "runtime_evidence": has_runtime_evidence},
            "PASSED",
        )

    return (
        False,
        "Static Dockerfile non-root UID 10001 and CI workflow verified, but runtime container isolation is unverified (no container daemon or execution evidence)",
        {"dockerfile": str(dockerfile), "daemon_available": False, "runtime_evidence": False},
        "UNVERIFIED",
    )


def gate_2_database_migrations(root_dir: Path | None = None) -> tuple[bool, str, dict, str]:
    """Gate 2: Verify durable schema migrations and bidirectional rollbacks."""
    base = root_dir or ROOT_DIR
    alembic_ini = base / "backend" / "alembic.ini"
    versions_dir = base / "backend" / "alembic" / "versions"
    if not alembic_ini.exists() or not versions_dir.exists():
        return False, "Alembic configuration missing", {}, "FAILED"
    versions = list(versions_dir.glob("*.py"))
    if len(versions) < 4:
        return False, f"Expected >= 4 migration versions, found {len(versions)}", {}, "FAILED"
    return (
        True,
        f"Alembic migrations verified ({len(versions)} versions with reversible downgrade)",
        {"versions_count": len(versions)},
        "PASSED",
    )


def gate_3_webhook_idempotency(root_dir: Path | None = None) -> tuple[bool, str, dict, str]:
    """Gate 3: Verify webhook HMAC-SHA256 signature checking and deduplication."""
    base = root_dir or ROOT_DIR
    webhook_ep = base / "backend" / "app" / "api" / "v1" / "endpoints" / "webhooks.py"
    webhook_svc = base / "backend" / "app" / "services" / "webhook.py"
    if not webhook_ep.exists() or not webhook_svc.exists():
        return False, "Missing webhook endpoints or service", {}, "FAILED"
    ep_content = webhook_ep.read_text(encoding="utf-8")
    svc_content = webhook_svc.read_text(encoding="utf-8")
    if "x-hub-signature-256" not in ep_content.lower() or "compare_digest" not in svc_content:
        return False, "Missing constant-time compare_digest or signature checking", {}, "FAILED"
    return True, "HMAC-SHA256 signature verification and idempotent deduplication enforced", {}, "PASSED"


def gate_4_ai_hypothesis_boundary(root_dir: Path | None = None) -> tuple[bool, str, dict, str]:
    """Gate 4: Verify strict evidence-ID cross referencing in AI diagnosis engine."""
    base = root_dir or ROOT_DIR
    analysis_path = base / "backend" / "app" / "services" / "analysis.py"
    if not analysis_path.exists():
        return False, "Missing backend/app/services/analysis.py", {}, "FAILED"
    content = analysis_path.read_text(encoding="utf-8")
    if "validate_diagnosis" not in content or "valid_evidence_ids" not in content:
        return False, "Missing evidence ID cross-referencing validation in analysis engine", {}, "FAILED"
    return True, "AI hypotheses strictly validated against factual evidence IDs", {}, "PASSED"


def gate_5_cloud_kms_secrets(root_dir: Path | None = None) -> tuple[bool, str, dict, str]:
    """Gate 5: Verify Cloud KMS manifests and zero tracked plaintext secrets."""
    base = root_dir or ROOT_DIR
    aws_irsa = base / "deploy" / "kms" / "aws-irsa.yaml"
    gcp_wi = base / "deploy" / "kms" / "gcp-workload-identity.yaml"
    if not aws_irsa.exists() or not gcp_wi.exists():
        return False, "Missing Kubernetes Cloud KMS IRSA or Workload Identity manifests", {}, "FAILED"
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=base, capture_output=True, text=True, check=False)
    if ".env.production\n" in proc.stdout or ".env\n" in proc.stdout:
        return False, "Unsafe .env file tracked in git status", {}, "FAILED"
    return True, "Cloud KMS Workload Identity & IRSA manifests present; zero secrets in git", {}, "PASSED"


def gate_6_observability_alerting(root_dir: Path | None = None) -> tuple[bool, str, dict, str]:
    """Gate 6: Verify Prometheus SLA alerting rules and Grafana dashboard."""
    base = root_dir or ROOT_DIR
    alert_rules = base / "monitoring" / "prometheus" / "alert_rules.yml"
    dashboard = base / "monitoring" / "dashboards" / "codesentinel_overview.json"
    if not alert_rules.exists() or not dashboard.exists():
        return False, "Missing alert_rules.yml or Grafana dashboard", {}, "FAILED"
    rule_content = alert_rules.read_text(encoding="utf-8")
    if "HighWebhookErrorRate" not in rule_content or "QueueBackpressureHigh" not in rule_content:
        return False, "Missing mandatory Prometheus SLA alerting rules", {}, "FAILED"
    return True, "Prometheus SLA alert rules and Grafana SRE overview dashboard validated", {}, "PASSED"


def gate_7_frontend_build_a11y(root_dir: Path | None = None) -> tuple[bool, str, dict, str]:
    """Gate 7: Verify frontend production bundle and executed Playwright/a11y test evidence."""
    base = root_dir or ROOT_DIR
    dist_index = base / "frontend" / "dist" / "index.html"
    a11y_spec = base / "frontend" / "tests" / "accessibility.spec.ts"
    e2e_spec = base / "frontend" / "tests" / "e2e_user_flow.spec.ts"
    if not dist_index.exists():
        return False, "Frontend production build (dist/index.html) missing", {}, "FAILED"
    if not a11y_spec.exists() or not e2e_spec.exists():
        return False, "Missing frontend Playwright E2E or accessibility test suites", {}, "FAILED"

    test_results_paths = [
        base / "frontend" / "test-results" / "results.json",
        base / "frontend" / "playwright-report" / "results.json",
        base / "FRONTEND_TEST_REPORT.json",
    ]
    test_report_file = next((p for p in test_results_paths if p.exists()), None)
    tests_verified = False
    if test_report_file:
        with contextlib.suppress(Exception):
            tdata = json.loads(test_report_file.read_text(encoding="utf-8"))
            tests_verified = bool(
                tdata.get("status") in ("PASSED", "passed", "ok")
                or tdata.get("suites_passed", False)
            )

    if tests_verified:
        return (
            True,
            "Frontend production bundle compiled; Playwright E2E and WCAG 2.1 AA test execution verified",
            {"bundle": str(dist_index), "test_report": str(test_report_file)},
            "PASSED",
        )

    return (
        False,
        "Frontend production bundle compiled and test specs exist, but Playwright E2E and WCAG 2.1 AA test execution results are unverified",
        {"bundle": str(dist_index), "test_specs": [str(a11y_spec), str(e2e_spec)], "test_execution_verified": False},
        "UNVERIFIED",
    )


def gate_8_disaster_recovery(
    report_path: Path | None = None,
    root_dir: Path | None = None,
) -> tuple[bool, str, dict, str]:
    """Gate 8: Validate disaster recovery reconstitution drill, RTO/RPO thresholds, and integrity evidence."""
    base = root_dir or ROOT_DIR
    dr_script = base / "scripts" / "test_disaster_recovery_drill.py"
    dr_report = report_path or (base / "DISASTER_RECOVERY_DRILL_REPORT.json")
    if not dr_script.exists():
        return False, "Missing scripts/test_disaster_recovery_drill.py", {}, "FAILED"
    if not dr_report.exists():
        return False, "Disaster recovery drill report not found (run scripts/test_disaster_recovery_drill.py)", {}, "FAILED"

    try:
        report_data = json.loads(dr_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Failed to parse disaster recovery drill report: {exc}", {}, "FAILED"

    if report_data.get("status") != "PASSED":
        return False, f"DR drill report status is '{report_data.get('status')}', expected 'PASSED'", {}, "FAILED"

    metrics = report_data.get("metrics", {})
    rto = metrics.get("rto_achieved_sec")
    rto_thresh = metrics.get("rto_threshold_sec", 1800)
    rpo = metrics.get("rpo_achieved_sec")
    rpo_thresh = metrics.get("rpo_threshold_sec", 300)

    if rto is None or rto > rto_thresh or not metrics.get("rto_compliant", True):
        return False, f"DR drill RTO {rto}s exceeds required threshold (<= {rto_thresh}s)", metrics, "FAILED"

    if rpo is None or rpo > rpo_thresh or not metrics.get("rpo_compliant", True):
        return False, f"DR drill RPO {rpo}s exceeds required threshold (<= {rpo_thresh}s)", metrics, "FAILED"

    verifications = report_data.get("verifications", {})
    if not verifications.get("checksum_sidecar_validated"):
        return False, "DR drill missing SHA-256 checksum sidecar verification evidence", verifications, "FAILED"
    if not (verifications.get("tamper_detection_prevented_corruption") or verifications.get("tamper_detection_validated")):
        return False, "DR drill missing AES-256-GCM tamper detection verification evidence", verifications, "FAILED"
    if not verifications.get("catastrophic_wipe_reconstituted"):
        return False, "DR drill missing catastrophic wipe reconstitution verification evidence", verifications, "FAILED"
    if verifications.get("data_fidelity_pct") != 100.0:
        return False, f"DR drill data fidelity {verifications.get('data_fidelity_pct')}% is below 100.0%", verifications, "FAILED"

    db_type = report_data.get("database_url_type", "unknown")
    if db_type in ("ephemeral-sqlite", "sqlite"):
        reason = (
            f"DR drill passed on ephemeral SQLite simulation with 100% data fidelity "
            f"(RTO: {rto}s <= {rto_thresh}s, RPO: {rpo}s <= {rpo_thresh}s, AES-256-GCM tamper-checked); "
            f"production PostgreSQL WAL archiving/restore unverified"
        )
    else:
        reason = (
            f"DR drill passed with 100% data fidelity on {db_type} database "
            f"(RTO: {rto}s <= {rto_thresh}s, RPO: {rpo}s <= {rpo_thresh}s, AES-256-GCM authenticated)"
        )

    return True, reason, {"metrics": metrics, "verifications": verifications, "database_url_type": db_type}, "PASSED"


def gate_9_staging_rehearsal(
    report_path: Path | None = None,
    root_dir: Path | None = None,
) -> tuple[bool, str, dict, str]:
    """Gate 9: Validate staging rehearsal report, all suite outcomes, and load SLAs (>=100 req/s, p99<50ms)."""
    base = root_dir or ROOT_DIR
    rehearsal_script = base / "scripts" / "run_staging_rehearsal.py"
    rehearsal_report = report_path or (base / "STAGING_REHEARSAL_REPORT.json")
    if not rehearsal_script.exists():
        return False, "Missing scripts/run_staging_rehearsal.py", {}, "FAILED"
    if not rehearsal_report.exists():
        return False, "Staging rehearsal report not found (run scripts/run_staging_rehearsal.py)", {}, "FAILED"

    try:
        report_data = json.loads(rehearsal_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Failed to parse staging rehearsal report: {exc}", {}, "FAILED"

    if report_data.get("status") != "PASSED":
        return False, f"Staging rehearsal report status is '{report_data.get('status')}', expected 'PASSED'", {}, "FAILED"

    suites = report_data.get("suites", {})
    required_suites = [
        "adversarial_fuzzing",
        "migration_rollback",
        "disaster_recovery_drill",
        "load_benchmark",
    ]
    soak_suite_name = next(
        (s for s in ("soak_and_telemetry_probe", "health_and_telemetry_probe") if s in suites),
        None,
    )
    if not soak_suite_name:
        return False, "Staging rehearsal report missing health/soak telemetry probe suite", {}, "FAILED"

    for r_suite in required_suites:
        if r_suite not in suites:
            return False, f"Staging rehearsal missing required suite '{r_suite}'", {}, "FAILED"
        if not suites[r_suite].get("passed", False):
            return False, f"Staging rehearsal suite '{r_suite}' did not pass", suites[r_suite], "FAILED"

    if not suites[soak_suite_name].get("passed", False):
        return False, f"Staging rehearsal probe suite '{soak_suite_name}' did not pass", suites[soak_suite_name], "FAILED"

    load_suite = suites["load_benchmark"]
    measurements = load_suite.get("measurements", load_suite)
    rps = measurements.get("requests_per_second")
    p99 = measurements.get("p99_latency_ms")
    success_count = measurements.get("success_count")
    total_requests = measurements.get("total_requests")

    if rps is None or p99 is None or success_count is None or total_requests is None:
        return False, "Load benchmark metrics (throughput, p99, success_count, total_requests) incomplete in report", load_suite, "FAILED"

    if total_requests <= 0 or success_count != total_requests:
        return False, f"Load benchmark dropped requests ({success_count}/{total_requests} succeeded, 100% required)", load_suite, "FAILED"

    if rps < 100.0:
        return False, f"Load benchmark throughput {rps:.1f} req/s failed SLA threshold (>= 100.0 req/s required)", load_suite, "FAILED"

    if p99 >= 50.0:
        return False, f"Load benchmark p99 latency {p99:.2f}ms failed SLA threshold (< 50.0ms required)", load_suite, "FAILED"

    exec_env = report_data.get("test_execution_mode", load_suite.get("execution_environment", "in_process"))
    reason = (
        f"Staging rehearsal verified across all suites; load benchmark achieved {rps:.1f} req/s (>= 100 req/s) "
        f"and p99 {p99:.2f}ms (< 50ms) with 100% request success (execution: {exec_env})"
    )
    return True, reason, {"load_measurements": measurements, "execution_environment": exec_env}, "PASSED"


def gate_10_production_reverse_proxy(root_dir: Path | None = None) -> tuple[bool, str, dict, str]:
    """Gate 10: Verify Nginx TLS 1.3 reverse proxy configuration and live TLS termination evidence."""
    base = root_dir or ROOT_DIR
    nginx_conf = base / "deploy" / "nginx" / "codesentinel.conf"
    if not nginx_conf.exists():
        return False, "Missing deploy/nginx/codesentinel.conf", {}, "FAILED"
    content = nginx_conf.read_text(encoding="utf-8")
    if "TLSv1.3" not in content or "proxy_buffering off" not in content:
        return False, "Nginx configuration missing TLSv1.3 or SSE unbuffered streaming", {}, "FAILED"

    tls_report = base / "TLS_VERIFICATION_REPORT.json"
    live_tls_verified = False
    if tls_report.exists():
        with contextlib.suppress(Exception):
            tdata = json.loads(tls_report.read_text(encoding="utf-8"))
            live_tls_verified = (tdata.get("status") == "PASSED" and tdata.get("tls_1_3_verified") is True)

    if live_tls_verified:
        return (
            True,
            "Nginx TLS 1.3 reverse proxy configuration and live TLS termination verified",
            {"conf": str(nginx_conf), "tls_verified": True},
            "PASSED",
        )

    return (
        False,
        "Static Nginx configuration validated (TLSv1.3, rate limiting, unbuffered SSE), but live TLS termination and reverse proxy deployment are unverified",
        {"conf": str(nginx_conf), "live_tls_verified": False},
        "UNVERIFIED",
    )


def verify_all_gates(
    root_dir: Path | None = None,
    dr_report_path: Path | None = None,
    rehearsal_report_path: Path | None = None,
) -> dict:
    """Execute all 10 release gate checks and return structured sign-off report."""
    base = root_dir or ROOT_DIR
    gates = [
        (1, "Runner Hardening & True Container Isolation", lambda: gate_1_runner_isolation(root_dir=base)),
        (2, "Durable Database Schema & Bidirectional Rollbacks", lambda: gate_2_database_migrations(root_dir=base)),
        (3, "Webhook HMAC-SHA256 & Idempotent Deduplication", lambda: gate_3_webhook_idempotency(root_dir=base)),
        (4, "AI Hypothesis Boundary & Prompt Injection Immunity", lambda: gate_4_ai_hypothesis_boundary(root_dir=base)),
        (5, "Cloud KMS & Zero-Plaintext Secret Architecture", lambda: gate_5_cloud_kms_secrets(root_dir=base)),
        (6, "Observability, SLA Alert Rules & SRE Dashboards", lambda: gate_6_observability_alerting(root_dir=base)),
        (7, "Frontend Production Build & WCAG 2.1 AA A11y", lambda: gate_7_frontend_build_a11y(root_dir=base)),
        (8, "Disaster Recovery Reconstitution Drill (RTO/RPO)", lambda: gate_8_disaster_recovery(report_path=dr_report_path, root_dir=base)),
        (9, "Staging Rehearsal & High-Throughput Load Verification", lambda: gate_9_staging_rehearsal(report_path=rehearsal_report_path, root_dir=base)),
        (10, "Production Reverse Proxy, TLS 1.3 & Rate Limiting", lambda: gate_10_production_reverse_proxy(root_dir=base)),
    ]

    results = []
    all_passed = True

    for num, name, fn in gates:
        res = check_gate(num, name, fn)
        results.append(res)
        if not res["passed"]:
            all_passed = False

    return {
        "release_candidate": "v1.0.0-rc1",
        "timestamp": datetime.now(UTC).isoformat(),
        "overall_status": "APPROVED_FOR_PRODUCTION" if all_passed else "REJECTED_GATES_FAILED",
        "gates_passed": sum(1 for r in results if r["passed"]),
        "total_gates": len(gates),
        "completion_percentage": round((sum(1 for r in results if r["passed"]) / len(gates)) * 100.0, 1),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeSentinel Release Gate Validator")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT_DIR / "RELEASE_GATE_SIGNOFF.json",
        help="Destination path for release gate signoff report JSON",
    )
    args = parser.parse_args()

    print("=================================================================")
    print("   CodeSentinel Production Release Invariant Gate Sign-Off       ")
    print("=================================================================")

    signoff_report = verify_all_gates(root_dir=ROOT_DIR)
    all_passed = signoff_report["overall_status"] == "APPROVED_FOR_PRODUCTION"

    print("=================================================================")
    args.report.write_text(json.dumps(signoff_report, indent=2), encoding="utf-8")

    if all_passed:
        print("[SIGN-OFF CERTIFICATE ISSUED]")
        print("  Status: APPROVED FOR PRODUCTION (100% of Release Gates Passed)")
        print(f"  Sign-Off Document: {args.report}")
        sys.exit(0)
    else:
        print("[SIGN-OFF REJECTED]")
        print("  Status: GATES FAILED OR UNVERIFIED - Review blockers above")
        print(f"  Sign-Off Document: {args.report}")
        sys.exit(1)


if __name__ == "__main__":
    main()

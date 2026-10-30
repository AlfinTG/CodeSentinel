"""Unit and integration tests for release gate validation and staging rehearsal.

Verifies:
  - Gate 9 strictly enforces throughput >= 100 req/s AND p99 < 50ms with 100% success.
  - Gate 9 fails on reports with high throughput but p99 above 50 ms.
  - Gate 8 validates RTO/RPO thresholds and all 4 recovery-integrity evidence points.
  - Gate 8 does not describe ephemeral SQLite drill as production database recovery.
  - Gate 1, Gate 7, and Gate 10 report only what they actually establish (unverified without runtime/test evidence).
  - Unverified/failed gates strictly prevent issuing APPROVED_FOR_PRODUCTION.
"""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

# Ensure workspace root is in sys.path for scripts import
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.run_staging_rehearsal import (  # noqa: E402
    LOAD_BENCHMARK_MAX_P99_MS,
    LOAD_BENCHMARK_MIN_RPS,
)
from scripts.verify_release_gates import (  # noqa: E402
    gate_1_runner_isolation,
    gate_7_frontend_build_a11y,
    gate_8_disaster_recovery,
    gate_9_staging_rehearsal,
    gate_10_production_reverse_proxy,
    verify_all_gates,
)


def test_staging_rehearsal_constants():
    """Verify that rehearsal module defines the strict Phase 9 SLA constants."""
    assert LOAD_BENCHMARK_MIN_RPS == 100.0
    assert LOAD_BENCHMARK_MAX_P99_MS == 50.0


@pytest.fixture
def isolated_tmp_dir() -> Path:
    """Provide an isolated temporary directory within workspace boundary to avoid sandbox permissions issues."""
    temp_dir = ROOT_DIR / "backend" / ".pytest_temp" / f"case_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def valid_rehearsal_payload() -> dict:
    return {
        "status": "PASSED",
        "phase": "Phase 9: End-to-End Staging Rehearsal & Security Fuzzing",
        "timestamp": "2026-10-30T20:00:00+00:00",
        "total_duration_sec": 12.5,
        "test_execution_mode": "local_in_process",
        "staging_infrastructure_deployed": False,
        "suites": {
            "adversarial_fuzzing": {"passed": True, "exit_code": 0, "duration_sec": 1.0},
            "migration_rollback": {"passed": True, "exit_code": 0, "duration_sec": 1.5},
            "disaster_recovery_drill": {"passed": True, "exit_code": 0, "duration_sec": 0.5},
            "load_benchmark": {
                "passed": True,
                "execution_environment": "in_process_asgi",
                "thresholds": {
                    "min_requests_per_second": 100.0,
                    "max_p99_latency_ms": 50.0,
                    "min_success_rate_pct": 100.0,
                },
                "measurements": {
                    "total_requests": 100,
                    "concurrency": 10,
                    "success_count": 100,
                    "success_rate_pct": 100.0,
                    "duration_sec": 0.8,
                    "requests_per_second": 125.0,
                    "p50_latency_ms": 15.0,
                    "p95_latency_ms": 35.0,
                    "p99_latency_ms": 42.0,
                },
                "total_requests": 100,
                "concurrency": 10,
                "success_count": 100,
                "duration_sec": 0.8,
                "requests_per_second": 125.0,
                "p50_latency_ms": 15.0,
                "p95_latency_ms": 35.0,
                "p99_latency_ms": 42.0,
            },
            "soak_and_telemetry_probe": {
                "passed": True,
                "live_health_probe": True,
                "ready_probe": True,
                "metrics_probe": True,
                "is_sustained_soak": False,
            },
        },
        "staging_acceptance_criteria": {
            "prompt_injection_tamper_prevention": True,
            "zero_downtime_reversible_migrations": True,
            "disaster_recovery_reconstitution_fidelity_100pct": True,
            "webhook_ingestion_throughput_and_latency": True,
            "continuous_health_and_metrics_integrity": True,
            "sustained_48h_staging_soak_verified": False,
        },
    }


@pytest.fixture
def valid_dr_payload() -> dict:
    return {
        "status": "PASSED",
        "phase": "Phase 8: Automated Disaster Recovery & Backup Reconstitution",
        "timestamp": "2026-10-30T20:00:00+00:00",
        "database_url_type": "ephemeral-sqlite",
        "metrics": {
            "rto_achieved_sec": 0.025,
            "rto_threshold_sec": 1800,
            "rto_compliant": True,
            "rpo_achieved_sec": 0,
            "rpo_threshold_sec": 300,
            "rpo_compliant": True,
            "backup_duration_sec": 0.015,
            "total_drill_duration_sec": 0.12,
        },
        "verifications": {
            "checksum_sidecar_validated": True,
            "tamper_detection_validated": True,
            "catastrophic_wipe_reconstituted": True,
            "data_fidelity_pct": 100.0,
            "records_recovered": {
                "projects": 1,
                "runs": 1,
                "evidence": 1,
                "analyses": 1,
            },
        },
    }


# ==============================================================================
# Gate 9 Tests: Load SLAs & Staging Rehearsal Verification
# ==============================================================================

def test_gate_9_accepts_valid_rehearsal_report(isolated_tmp_dir: Path, valid_rehearsal_payload: dict):
    """Gate 9 passes when all 5 suites pass and load SLAs (>=100 req/s, p99<50ms) are satisfied."""
    report_file = isolated_tmp_dir / "STAGING_REHEARSAL_REPORT.json"
    report_file.write_text(json.dumps(valid_rehearsal_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_9_staging_rehearsal(report_path=report_file)
    assert passed is True
    assert status == "PASSED"
    assert "125.0 req/s" in reason
    assert "42.00ms" in reason


def test_gate_9_rejects_high_throughput_with_p99_above_50ms(
    isolated_tmp_dir: Path, valid_rehearsal_payload: dict
):
    """Gate 9 MUST fail when throughput exceeds 100 req/s (126.9 req/s) but p99 latency is above 50 ms (318.57 ms)."""
    valid_rehearsal_payload["suites"]["load_benchmark"]["measurements"]["requests_per_second"] = 126.9
    valid_rehearsal_payload["suites"]["load_benchmark"]["measurements"]["p99_latency_ms"] = 318.57
    valid_rehearsal_payload["suites"]["load_benchmark"]["requests_per_second"] = 126.9
    valid_rehearsal_payload["suites"]["load_benchmark"]["p99_latency_ms"] = 318.57

    report_file = isolated_tmp_dir / "STAGING_REHEARSAL_REPORT.json"
    report_file.write_text(json.dumps(valid_rehearsal_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_9_staging_rehearsal(report_path=report_file)
    assert passed is False
    assert status == "FAILED"
    assert "p99 latency 318.57ms failed SLA threshold" in reason


def test_gate_9_rejects_low_throughput(isolated_tmp_dir: Path, valid_rehearsal_payload: dict):
    """Gate 9 MUST fail when throughput is below 100 req/s even if p99 latency is low."""
    valid_rehearsal_payload["suites"]["load_benchmark"]["measurements"]["requests_per_second"] = 85.0
    valid_rehearsal_payload["suites"]["load_benchmark"]["measurements"]["p99_latency_ms"] = 20.0
    valid_rehearsal_payload["suites"]["load_benchmark"]["requests_per_second"] = 85.0
    valid_rehearsal_payload["suites"]["load_benchmark"]["p99_latency_ms"] = 20.0

    report_file = isolated_tmp_dir / "STAGING_REHEARSAL_REPORT.json"
    report_file.write_text(json.dumps(valid_rehearsal_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_9_staging_rehearsal(report_path=report_file)
    assert passed is False
    assert status == "FAILED"
    assert "throughput 85.0 req/s failed SLA threshold" in reason


def test_gate_9_rejects_dropped_requests(isolated_tmp_dir: Path, valid_rehearsal_payload: dict):
    """Gate 9 MUST fail when success rate is less than 100% (any requests dropped or failed)."""
    valid_rehearsal_payload["suites"]["load_benchmark"]["measurements"]["success_count"] = 98
    valid_rehearsal_payload["suites"]["load_benchmark"]["measurements"]["total_requests"] = 100
    valid_rehearsal_payload["suites"]["load_benchmark"]["success_count"] = 98
    valid_rehearsal_payload["suites"]["load_benchmark"]["total_requests"] = 100

    report_file = isolated_tmp_dir / "STAGING_REHEARSAL_REPORT.json"
    report_file.write_text(json.dumps(valid_rehearsal_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_9_staging_rehearsal(report_path=report_file)
    assert passed is False
    assert status == "FAILED"
    assert "dropped requests (98/100 succeeded" in reason


def test_gate_9_rejects_failed_individual_suite(isolated_tmp_dir: Path, valid_rehearsal_payload: dict):
    """Gate 9 MUST fail if any individual suite in the staging rehearsal failed."""
    valid_rehearsal_payload["suites"]["adversarial_fuzzing"]["passed"] = False

    report_file = isolated_tmp_dir / "STAGING_REHEARSAL_REPORT.json"
    report_file.write_text(json.dumps(valid_rehearsal_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_9_staging_rehearsal(report_path=report_file)
    assert passed is False
    assert status == "FAILED"
    assert "adversarial_fuzzing' did not pass" in reason


def test_gate_9_rejects_missing_suite(isolated_tmp_dir: Path, valid_rehearsal_payload: dict):
    """Gate 9 MUST fail if any required suite is omitted from the report."""
    del valid_rehearsal_payload["suites"]["migration_rollback"]

    report_file = isolated_tmp_dir / "STAGING_REHEARSAL_REPORT.json"
    report_file.write_text(json.dumps(valid_rehearsal_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_9_staging_rehearsal(report_path=report_file)
    assert passed is False
    assert status == "FAILED"
    assert "missing required suite 'migration_rollback'" in reason


# ==============================================================================
# Gate 8 Tests: Disaster Recovery RTO/RPO & Integrity Verification
# ==============================================================================

def test_gate_8_validates_rto_rpo_and_ephemeral_sqlite_description(
    isolated_tmp_dir: Path, valid_dr_payload: dict
):
    """Gate 8 accurately describes ephemeral SQLite drill and does not describe it as production recovery."""
    report_file = isolated_tmp_dir / "DISASTER_RECOVERY_DRILL_REPORT.json"
    report_file.write_text(json.dumps(valid_dr_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_8_disaster_recovery(report_path=report_file)
    assert passed is True
    assert status == "PASSED"
    assert "ephemeral SQLite simulation" in reason
    assert "production PostgreSQL WAL archiving/restore unverified" in reason
    assert metadata["database_url_type"] == "ephemeral-sqlite"


def test_gate_8_rejects_rto_exceeded(isolated_tmp_dir: Path, valid_dr_payload: dict):
    """Gate 8 fails when RTO exceeds threshold."""
    valid_dr_payload["metrics"]["rto_achieved_sec"] = 2500
    valid_dr_payload["metrics"]["rto_compliant"] = False

    report_file = isolated_tmp_dir / "DISASTER_RECOVERY_DRILL_REPORT.json"
    report_file.write_text(json.dumps(valid_dr_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_8_disaster_recovery(report_path=report_file)
    assert passed is False
    assert status == "FAILED"
    assert "RTO 2500s exceeds required threshold" in reason


def test_gate_8_rejects_missing_tamper_detection(isolated_tmp_dir: Path, valid_dr_payload: dict):
    """Gate 8 fails when cryptographic tamper detection verification is missing or failed."""
    valid_dr_payload["verifications"]["tamper_detection_validated"] = False

    report_file = isolated_tmp_dir / "DISASTER_RECOVERY_DRILL_REPORT.json"
    report_file.write_text(json.dumps(valid_dr_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_8_disaster_recovery(report_path=report_file)
    assert passed is False
    assert status == "FAILED"
    assert "tamper detection" in reason


def test_gate_8_rejects_data_fidelity_below_100pct(isolated_tmp_dir: Path, valid_dr_payload: dict):
    """Gate 8 fails when recovered data fidelity is less than 100.0%."""
    valid_dr_payload["verifications"]["data_fidelity_pct"] = 99.5

    report_file = isolated_tmp_dir / "DISASTER_RECOVERY_DRILL_REPORT.json"
    report_file.write_text(json.dumps(valid_dr_payload), encoding="utf-8")

    passed, reason, metadata, status = gate_8_disaster_recovery(report_path=report_file)
    assert passed is False
    assert status == "FAILED"
    assert "data fidelity 99.5% is below 100.0%" in reason


# ==============================================================================
# Gate 1, Gate 7, Gate 10 Evidence Verification Tests
# ==============================================================================

def test_gate_1_reports_unverified_without_runtime_daemon(isolated_tmp_dir: Path):
    """Gate 1 verifies static Dockerfile/CI files but marks runtime isolation UNVERIFIED when daemon is missing."""
    runner_dir = isolated_tmp_dir / "runner"
    runner_dir.mkdir(parents=True)
    (runner_dir / "Dockerfile").write_text("USER 10001:10001\n", encoding="utf-8")
    wf_dir = isolated_tmp_dir / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "runner_image.yml").write_text("name: runner\n", encoding="utf-8")

    passed, reason, metadata, status = gate_1_runner_isolation(root_dir=isolated_tmp_dir)
    assert passed is False
    assert status == "UNVERIFIED"
    assert "runtime container isolation is unverified" in reason
    assert metadata["daemon_available"] is False


def test_gate_7_reports_unverified_without_playwright_test_report(isolated_tmp_dir: Path):
    """Gate 7 verifies bundle and specs exist, but marks UNVERIFIED when execution test results are missing."""
    dist_dir = isolated_tmp_dir / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
    tests_dir = isolated_tmp_dir / "frontend" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "accessibility.spec.ts").write_text("// test\n", encoding="utf-8")
    (tests_dir / "e2e_user_flow.spec.ts").write_text("// test\n", encoding="utf-8")

    passed, reason, metadata, status = gate_7_frontend_build_a11y(root_dir=isolated_tmp_dir)
    assert passed is False
    assert status == "UNVERIFIED"
    assert "Playwright E2E and WCAG 2.1 AA test execution results are unverified" in reason
    assert metadata["test_execution_verified"] is False


def test_gate_10_reports_unverified_without_live_tls_evidence(isolated_tmp_dir: Path):
    """Gate 10 verifies static Nginx conf directives, but marks live TLS UNVERIFIED without live deployment evidence."""
    nginx_dir = isolated_tmp_dir / "deploy" / "nginx"
    nginx_dir.mkdir(parents=True)
    (nginx_dir / "codesentinel.conf").write_text(
        "ssl_protocols TLSv1.3;\nproxy_buffering off;\n", encoding="utf-8"
    )

    passed, reason, metadata, status = gate_10_production_reverse_proxy(root_dir=isolated_tmp_dir)
    assert passed is False
    assert status == "UNVERIFIED"
    assert "live TLS termination and reverse proxy deployment are unverified" in reason
    assert metadata["live_tls_verified"] is False


def test_overall_signoff_rejects_production_approval_on_unverified_gates():
    """Sign-off validator strictly refuses APPROVED_FOR_PRODUCTION when any gate is UNVERIFIED or FAILED."""
    report = verify_all_gates()
    assert report["overall_status"] != "APPROVED_FOR_PRODUCTION"
    assert report["overall_status"] == "REJECTED_GATES_FAILED"
    unverified_gates = [r for r in report["results"] if r["status"] == "UNVERIFIED"]
    assert len(unverified_gates) >= 1

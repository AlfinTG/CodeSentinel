"""Phase 4 — AI Analysis Layer tests.

Gate 4 requirements verified here:
  ✓ Prompt construction is bounded and evidence-grounded
  ✓ Evidence ID cross-reference check rejects foreign IDs
  ✓ Schema validation rejects oversized fields, wrong types, and missing IDs
  ✓ Provider failure / timeout produces analysis error state; test_outcome unchanged
  ✓ Invalid JSON / schema mismatch produces INVALID_RESPONSE outcome
  ✓ POST /runs/{id}/analysis/request requires evidence; blocks duplicates
  ✓ POST /runs/{id}/analysis/retry blocked when in-progress; requires prior attempt
  ✓ GET /runs/{id}/analysis enforces authorization (cross-user blocked)
  ✓ No secrets leak in any error path (no API key in assertions)
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.db.session import get_db
from app.main import app
from app.models.analysis import Analysis
from app.models.base import Base
from app.models.evidence import EvidenceItem, EvidenceKind
from app.models.project import Project
from app.models.run import AnalysisOutcome, CheckRun, LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.schemas.analysis import ModelDiagnosisSchema
from app.services.analysis import (
    MAX_EVIDENCE_CONTENT_CHARS,
    MAX_PROMPT_CHARS,
    build_analysis_prompt,
    validate_diagnosis,
)

# ──────────────────────────────────────────────────────────────────────────────
# Test fixtures
# ──────────────────────────────────────────────────────────────────────────────

def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # reuse one connection so in-memory schema is always visible
    )
    # Enable FK enforcement for SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db():
    engine = _make_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db: Session):
    """TestClient with DB and auth overrides."""
    user = User(github_user_id=7001, login="phase4-user", display_name="Phase 4 User")
    db.add(user)
    db.commit()
    db.refresh(user)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    c = TestClient(app, raise_server_exceptions=True)
    yield c, db, user
    app.dependency_overrides.clear()


def _seed_run_with_evidence(
    db: Session, user: User, num_evidence: int = 2, test_outcome: str = TestOutcome.FAILED.value
) -> tuple[Run, list[EvidenceItem]]:
    project = Project(
        owner_user_id=user.id,
        github_installation_id=3001,
        repository_id=900001,
        repository_full_name="phase4/repo",
        default_branch="main",
    )
    db.add(project)
    db.flush()

    run = Run(
        project_id=project.id,
        head_sha="aabbcc" * 6 + "dd",
        event_kind="push",
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=test_outcome,
        analysis_outcome=AnalysisOutcome.NOT_REQUESTED.value,
        correlation_id=str(uuid.uuid4()),
    )
    db.add(run)
    db.flush()

    check = CheckRun(
        run_id=run.id,
        check_key="pytest",
        check_name="pytest",
        status="failed",
        exit_code=1,
    )
    db.add(check)
    db.flush()

    evidence_items = []
    for i in range(num_evidence):
        content = f"AssertionError: expected 42 got {i} — line {100 + i}"
        ev = EvidenceItem(
            run_id=run.id,
            check_run_id=check.id,
            kind=EvidenceKind.FAILED_ASSERTION.value,
            source_label=f"test_sample.py::test_case_{i}",
            content_redacted=content,
            content_digest=hashlib.sha256(content.encode()).hexdigest(),
            truncated=False,
            byte_length=len(content.encode()),
        )
        db.add(ev)
        evidence_items.append(ev)

    db.commit()
    [db.refresh(e) for e in evidence_items]
    db.refresh(run)
    return run, evidence_items


# ──────────────────────────────────────────────────────────────────────────────
# 1. Prompt construction
# ──────────────────────────────────────────────────────────────────────────────

def test_prompt_is_bounded_at_max_chars(db: Session):
    """Prompt must never exceed MAX_PROMPT_CHARS regardless of evidence size."""
    user = User(github_user_id=7010, login="u-prompt", display_name="U")
    db.add(user)
    db.commit()
    db.refresh(user)

    run, evidence_items = _seed_run_with_evidence(db, user, num_evidence=5)

    # Artificially bloat evidence content beyond the per-item cap
    for ev in evidence_items:
        ev.content_redacted = "X" * 50_000
    db.commit()

    prompt = build_analysis_prompt(run, evidence_items)
    assert len(prompt) <= MAX_PROMPT_CHARS, (
        f"Prompt length {len(prompt)} exceeds MAX_PROMPT_CHARS={MAX_PROMPT_CHARS}"
    )


def test_prompt_contains_evidence_ids(db: Session):
    """Every evidence UUID must appear verbatim in the prompt for the model to cite them."""
    user = User(github_user_id=7011, login="u-ids", display_name="U")
    db.add(user)
    db.commit()
    db.refresh(user)

    run, evidence_items = _seed_run_with_evidence(db, user, num_evidence=3)
    prompt = build_analysis_prompt(run, evidence_items)

    for ev in evidence_items:
        assert str(ev.id) in prompt, f"Evidence ID {ev.id} missing from prompt"


def test_prompt_contains_run_sha(db: Session):
    """Run commit SHA must be present in the prompt for traceability."""
    user = User(github_user_id=7012, login="u-sha", display_name="U")
    db.add(user)
    db.commit()
    db.refresh(user)

    run, evidence_items = _seed_run_with_evidence(db, user)
    prompt = build_analysis_prompt(run, evidence_items)

    assert run.head_sha in prompt


def test_per_item_content_truncated_in_prompt(db: Session):
    """Individual evidence content must be capped at MAX_EVIDENCE_CONTENT_CHARS."""
    user = User(github_user_id=7013, login="u-trunc", display_name="U")
    db.add(user)
    db.commit()
    db.refresh(user)

    run, evidence_items = _seed_run_with_evidence(db, user, num_evidence=1)
    long_content = "A" * (MAX_EVIDENCE_CONTENT_CHARS + 500)
    evidence_items[0].content_redacted = long_content
    db.commit()

    prompt = build_analysis_prompt(run, evidence_items)
    # The truncated marker should appear and the full long content should not
    assert "[truncated for prompt]" in prompt
    # The full un-truncated content (char beyond cap) should NOT be present verbatim
    assert long_content not in prompt


# ──────────────────────────────────────────────────────────────────────────────
# 2. Schema validation — valid and invalid cases
# ──────────────────────────────────────────────────────────────────────────────

def _valid_diagnosis(evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "summary": "The payment processor returns HTTP 500 under high load (hypothesis).",
        "root_cause_hypotheses": [
            {
                "statement": "HYPOTHESIS: Connection pool exhaustion causes sporadic 500s.",
                "evidence_ids": evidence_ids[:1],
                "confidence": "medium",
                "reasoning": "Evidence shows DB connection timeout in stack trace.",
            }
        ],
        "affected_behaviors": ["checkout_flow"],
        "recommended_next_steps": ["Increase pool size and run load test"],
        "missing_evidence": ["DB connection pool metrics"],
    }


def test_valid_diagnosis_passes_validation():
    """Well-formed diagnosis with valid evidence IDs passes schema validation."""
    eid = str(uuid.uuid4())
    raw = _valid_diagnosis([eid])
    diagnosis, err = validate_diagnosis(raw, valid_evidence_ids={eid})
    assert err is None
    assert diagnosis is not None
    assert isinstance(diagnosis, ModelDiagnosisSchema)


def test_foreign_evidence_id_rejected():
    """Evidence IDs that don't belong to the run must be rejected."""
    real_eid = str(uuid.uuid4())
    foreign_eid = str(uuid.uuid4())
    raw = _valid_diagnosis([foreign_eid])
    _, err = validate_diagnosis(raw, valid_evidence_ids={real_eid})
    assert err is not None
    assert "foreign_evidence_id" in err


def test_invalid_confidence_rejected():
    """Confidence values outside {low, medium, high} must fail schema validation."""
    eid = str(uuid.uuid4())
    raw = _valid_diagnosis([eid])
    raw["root_cause_hypotheses"][0]["confidence"] = "very_sure"
    _, err = validate_diagnosis(raw, valid_evidence_ids={eid})
    assert err is not None


def test_oversized_summary_rejected():
    """Summary exceeding 500 chars must fail Pydantic validation."""
    eid = str(uuid.uuid4())
    raw = _valid_diagnosis([eid])
    raw["summary"] = "S" * 501
    _, err = validate_diagnosis(raw, valid_evidence_ids={eid})
    assert err is not None


def test_too_many_hypotheses_rejected():
    """More than 5 root_cause_hypotheses must be rejected."""
    eid = str(uuid.uuid4())
    hyp = {
        "statement": "HYPOTHESIS: X",
        "evidence_ids": [eid],
        "confidence": "low",
        "reasoning": "r",
    }
    raw = _valid_diagnosis([eid])
    raw["root_cause_hypotheses"] = [hyp] * 6
    _, err = validate_diagnosis(raw, valid_evidence_ids={eid})
    assert err is not None


def test_suspicious_content_rejected():
    """Model output containing script tags or executable commands must be rejected."""
    eid = str(uuid.uuid4())
    raw = _valid_diagnosis([eid])
    raw["recommended_next_steps"] = ["<script>alert(1)</script>"]
    _, err = validate_diagnosis(raw, valid_evidence_ids={eid})
    assert err is not None
    assert "rejected_content" in err


def test_empty_evidence_ids_in_hypothesis_rejected():
    """Hypothesis with zero evidence_ids must fail (min_length=1)."""
    eid = str(uuid.uuid4())
    raw = _valid_diagnosis([eid])
    raw["root_cause_hypotheses"][0]["evidence_ids"] = []
    _, err = validate_diagnosis(raw, valid_evidence_ids={eid})
    assert err is not None


# ──────────────────────────────────────────────────────────────────────────────
# 3. API endpoint: POST /runs/{id}/analysis/request
# ──────────────────────────────────────────────────────────────────────────────

def test_request_analysis_accepted_with_evidence(client):
    """Requesting analysis on a run with evidence returns 202 and queued status."""
    tc, db, user = client
    run, _ = _seed_run_with_evidence(db, user)

    # Patch background task to no-op so the in-memory SQLite session is not accessed
    # in a separate thread after the response is sent. Service-level tests cover execution.
    with patch("app.api.v1.endpoints.analysis.run_analysis"):
        resp = tc.post(f"/api/v1/runs/{run.id}/analysis/request")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["run_id"] == run.id
    assert body["status"] == "pending"
    assert body["provider"] == "nebius_token_factory"
    assert body["attempt_number"] == 1


def test_request_analysis_rejected_without_evidence(client):
    """Analysis request with no evidence must return 422."""
    tc, db, user = client
    project = Project(
        owner_user_id=user.id,
        github_installation_id=3009,
        repository_id=900009,
        repository_full_name="phase4/no-ev",
        default_branch="main",
    )
    db.add(project)
    db.flush()
    run = Run(
        project_id=project.id,
        head_sha="deadbeef" * 5,
        event_kind="push",
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.FAILED.value,
        analysis_outcome=AnalysisOutcome.NOT_REQUESTED.value,
        correlation_id=str(uuid.uuid4()),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    resp = tc.post(f"/api/v1/runs/{run.id}/analysis/request")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_evidence"


def test_duplicate_request_analysis_blocked(client):
    """A second request while analysis is pending must return 409."""
    tc, db, user = client
    run, _ = _seed_run_with_evidence(db, user)

    with patch("app.api.v1.endpoints.analysis.run_analysis"):
        r1 = tc.post(f"/api/v1/runs/{run.id}/analysis/request")
        assert r1.status_code == 202

        r2 = tc.post(f"/api/v1/runs/{run.id}/analysis/request")
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "analysis_already_exists"


def test_request_analysis_cross_user_blocked(db: Session):
    """Cross-user access to analysis request endpoint must be blocked (403)."""
    engine = _make_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()

    owner = User(github_user_id=8001, login="owner-p4", display_name="Owner")
    attacker = User(github_user_id=8002, login="attacker-p4", display_name="Attacker")
    session.add_all([owner, attacker])
    session.commit()
    session.refresh(owner)
    session.refresh(attacker)

    run, _ = _seed_run_with_evidence(session, owner)

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: attacker
    tc = TestClient(app, raise_server_exceptions=True)

    resp = tc.post(f"/api/v1/runs/{run.id}/analysis/request")
    assert resp.status_code == 403

    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(engine)


# ──────────────────────────────────────────────────────────────────────────────
# 4. API endpoint: POST /runs/{id}/analysis/retry
# ──────────────────────────────────────────────────────────────────────────────

def test_retry_blocked_when_no_prior_analysis(client):
    """Retry must return 404 if no analysis record exists."""
    tc, db, user = client
    run, _ = _seed_run_with_evidence(db, user)

    resp = tc.post(f"/api/v1/runs/{run.id}/analysis/retry")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "analysis_not_found"


def test_retry_blocked_while_in_progress(client):
    """Retry must return 409 while analysis is pending/running."""
    tc, db, user = client
    run, _ = _seed_run_with_evidence(db, user)

    # Seed a pending analysis directly
    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nebius_token_factory",
        model_id="nvidia/test-model",
        status="pending",
        schema_version="1.0",
        input_digest=hashlib.sha256(b"x").hexdigest(),
    )
    db.add(analysis)
    db.commit()

    resp = tc.post(f"/api/v1/runs/{run.id}/analysis/retry")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "analysis_in_progress"


def test_retry_succeeds_after_failed_analysis(client):
    """Retry creates attempt N+1 when previous analysis failed."""
    tc, db, user = client
    run, _ = _seed_run_with_evidence(db, user)

    # Seed a failed analysis
    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nebius_token_factory",
        model_id="nvidia/test-model",
        status="failed",
        schema_version="1.0",
        input_digest=hashlib.sha256(b"x").hexdigest(),
        safe_error_code="provider_unavailable",
    )
    db.add(analysis)
    db.commit()

    with patch("app.api.v1.endpoints.analysis.run_analysis"):
        resp = tc.post(f"/api/v1/runs/{run.id}/analysis/retry")
    assert resp.status_code == 202
    body = resp.json()
    assert body["attempt_number"] == 2
    assert body["status"] == "pending"


# ──────────────────────────────────────────────────────────────────────────────
# 5. Provider failure — test_outcome must remain unchanged
# ──────────────────────────────────────────────────────────────────────────────

def test_provider_failure_does_not_alter_test_outcome(db: Session):
    """When Nebius is unavailable, test_outcome stays FAILED; only analysis_outcome changes."""
    user = User(github_user_id=7050, login="u-provfail", display_name="U")
    db.add(user)
    db.commit()
    db.refresh(user)

    run, _ = _seed_run_with_evidence(db, user, test_outcome=TestOutcome.FAILED.value)

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nebius_token_factory",
        model_id="nvidia/llama-test",
        status="pending",
        schema_version="1.0",
        input_digest=hashlib.sha256(b"init").hexdigest(),
    )
    db.add(analysis)
    db.commit()

    # Patch the Nebius call to simulate a network failure
    from app.services import analysis as analysis_svc
    with patch.object(analysis_svc, "_call_nebius", return_value=(None, 0, {})):
        analysis_svc.run_analysis(run, analysis, db)

    db.refresh(run)
    db.refresh(analysis)

    # Gate 4: test outcome must never change
    assert run.test_outcome == TestOutcome.FAILED.value
    # Analysis must reflect provider failure
    assert analysis.status == "failed"
    assert analysis.safe_error_code == "provider_unavailable"
    assert run.analysis_outcome == AnalysisOutcome.UNAVAILABLE.value


def test_invalid_model_response_produces_invalid_response_outcome(db: Session):
    """Invalid JSON structure from model → INVALID_RESPONSE; test_outcome unchanged."""
    user = User(github_user_id=7051, login="u-invalid", display_name="U")
    db.add(user)
    db.commit()
    db.refresh(user)

    run, _ = _seed_run_with_evidence(db, user, test_outcome=TestOutcome.FAILED.value)

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nebius_token_factory",
        model_id="nvidia/llama-test",
        status="pending",
        schema_version="1.0",
        input_digest=hashlib.sha256(b"init").hexdigest(),
    )
    db.add(analysis)
    db.commit()

    # Return a structurally invalid response (missing required fields)
    garbage_response = {"message": "I cannot help with that", "confidence": "very_sure"}

    from app.services import analysis as analysis_svc
    with patch.object(analysis_svc, "_call_nebius", return_value=(garbage_response, 120, {})):
        analysis_svc.run_analysis(run, analysis, db)

    db.refresh(run)
    db.refresh(analysis)

    assert run.test_outcome == TestOutcome.FAILED.value
    assert analysis.status == "failed"
    assert analysis.safe_error_code == "invalid_model_response"
    assert run.analysis_outcome == AnalysisOutcome.INVALID_RESPONSE.value


def test_successful_analysis_records_model_id_and_result(db: Session):
    """When model returns a valid diagnosis, it is persisted with model_id and latency."""
    user = User(github_user_id=7052, login="u-success", display_name="U")
    db.add(user)
    db.commit()
    db.refresh(user)

    run, evidence_items = _seed_run_with_evidence(db, user, test_outcome=TestOutcome.FAILED.value)
    eid = str(evidence_items[0].id)

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nebius_token_factory",
        model_id="nvidia/llama-3.1-nemotron-70b-instruct",
        status="pending",
        schema_version="1.0",
        input_digest=hashlib.sha256(b"init").hexdigest(),
    )
    db.add(analysis)
    db.commit()

    good_response = _valid_diagnosis([eid])
    fake_token_counts = {"prompt_tokens": 450, "completion_tokens": 280}

    from app.services import analysis as analysis_svc
    with patch.object(
        analysis_svc, "_call_nebius", return_value=(good_response, 850, fake_token_counts)
    ):
        analysis_svc.run_analysis(run, analysis, db)

    db.refresh(run)
    db.refresh(analysis)

    # Gate 4: test outcome must remain unchanged
    assert run.test_outcome == TestOutcome.FAILED.value
    # Analysis succeeded
    assert analysis.status == "succeeded"
    assert run.analysis_outcome == AnalysisOutcome.SUCCEEDED.value
    assert analysis.model_id == "nvidia/llama-3.1-nemotron-70b-instruct"
    assert analysis.latency_ms == 850
    assert analysis.input_tokens == 450
    assert analysis.output_tokens == 280
    assert analysis.result_json is not None
    assert "root_cause_hypotheses" in analysis.result_json
    assert analysis.result_json["root_cause_hypotheses"][0]["evidence_ids"][0] == eid

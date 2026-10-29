"""Opt-in live integration tests for Nebius Token Factory provider.

Gate 4 / Phase 4 Acceptance Criteria:
- Live network handshake against https://api.tokenfactory.nebius.com/v1
- Model: nvidia/llama-3.1-nemotron-70b-instruct
- Strict JSON schema validation against canonical ModelDiagnosisSchema
- Telemetry recording (latency_ms, input_tokens, output_tokens)
- Secret isolation: API key is never logged or leaked in error responses
- Skipped when NEBIUS_TOKEN_FACTORY_API_KEY is not configured
"""

import hashlib
import logging
import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem
from app.models.project import Project
from app.models.run import AnalysisOutcome, LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.schemas.analysis import ModelDiagnosisSchema
from app.services.analysis import run_analysis

logger = logging.getLogger(__name__)

# Condition for executing live provider test
LIVE_PROVIDER_ENABLED = bool(settings.NEBIUS_TOKEN_FACTORY_API_KEY)


@pytest.mark.live_provider
@pytest.mark.skipif(
    not LIVE_PROVIDER_ENABLED,
    reason="Live Nebius Token Factory integration test requires NEBIUS_TOKEN_FACTORY_API_KEY",
)
def test_nebius_live_handshake_and_schema_validation(db: Session, caplog: pytest.LogCaptureFixture):
    """Execute live call against Nebius Token Factory with real credentials and verify schema."""
    # 1. Setup user, project, run, and real evidence items
    user = User(
        github_user_id=9999,
        login="nebius-live-user",
        display_name="Nebius Live User",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    proj = Project(
        owner_user_id=user.id,
        github_installation_id=99999,
        repository_id=88888,
        repository_full_name="codesentinel-live/smoke-test",
        default_branch="main",
    )
    db.add(proj)
    db.commit()
    db.refresh(proj)

    run = Run(
        project_id=proj.id,
        head_sha="0123456789abcdef0123456789abcdef01234567",
        event_kind="check_suite",
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.FAILED.value,
        analysis_outcome=AnalysisOutcome.NOT_REQUESTED.value,
        correlation_id=str(uuid.uuid4()),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    evidence_id_1 = str(uuid.uuid4())
    evidence_id_2 = str(uuid.uuid4())
    c1 = "FAILED tests/test_auth.py::test_jwt_expired - TokenExpiredError: Signature has expired"
    c2 = "Traceback (most recent call last):\n  File 'app/auth.py', line 45, in verify_token\n    raise TokenExpiredError()"
    ev1 = EvidenceItem(
        id=evidence_id_1,
        run_id=run.id,
        kind="failure_summary",
        source_label="pytest",
        content_redacted=c1,
        content_digest=hashlib.sha256(c1.encode()).hexdigest(),
        truncated=False,
        byte_length=len(c1.encode()),
    )
    ev2 = EvidenceItem(
        id=evidence_id_2,
        run_id=run.id,
        kind="stack_trace",
        source_label="pytest",
        content_redacted=c2,
        content_digest=hashlib.sha256(c2.encode()).hexdigest(),
        truncated=False,
        byte_length=len(c2.encode()),
    )
    db.add_all([ev1, ev2])
    db.commit()

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        model_id=settings.NEBIUS_MODEL_ID,
        provider="nebius_token_factory",
        input_digest="live-handshake-digest",
        status="pending",
    )
    db.add(analysis)
    db.commit()

    # 2. Execute live analysis
    with caplog.at_level(logging.DEBUG):
        run_analysis(run, analysis, db)

    db.refresh(analysis)
    db.refresh(run)

    # 3. Verify execution outcome
    assert analysis.status == "succeeded", f"Analysis failed with code: {analysis.safe_error_code}"
    assert run.analysis_outcome == AnalysisOutcome.SUCCEEDED.value
    # Invariant: test_outcome remains FAILED
    assert run.test_outcome == TestOutcome.FAILED.value

    # 4. Telemetry assertions
    assert analysis.latency_ms is not None and analysis.latency_ms > 0
    assert analysis.input_tokens is not None and analysis.input_tokens > 0
    assert analysis.output_tokens is not None and analysis.output_tokens > 0

    # 5. Schema and hypothesis assertions
    assert analysis.result_json is not None
    diagnosis = ModelDiagnosisSchema.model_validate(analysis.result_json)
    assert diagnosis.summary
    assert len(diagnosis.root_cause_hypotheses) > 0

    # Evidence IDs in hypotheses must be valid and belong to the run
    valid_ids = {evidence_id_1, evidence_id_2}
    for hyp in diagnosis.root_cause_hypotheses:
        assert hyp.confidence in ("low", "medium", "high")
        for eid in hyp.evidence_ids:
            assert eid in valid_ids

    # 6. Secret isolation check: API key must never appear in log records
    for record in caplog.records:
        assert settings.NEBIUS_TOKEN_FACTORY_API_KEY not in record.message

"""Live integration and simulated network handshake tests for Nebius Token Factory provider.

Gate 4 / Phase 4 Acceptance Criteria:
- Network handshake against OpenAI-compatible endpoint
- Model: nvidia/llama-3.1-nemotron-70b-instruct
- Strict JSON schema validation against canonical ModelDiagnosisSchema
- Telemetry recording (latency_ms, input_tokens, output_tokens)
- Secret isolation: API key is never logged or leaked in error responses
- Rate limiting 429 backoff retry over network
- Circuit breaker trip over network errors
- Opt-in live testing against remote https://api.tokenfactory.nebius.com/v1 when NEBIUS_TOKEN_FACTORY_API_KEY is configured
"""

from __future__ import annotations

import hashlib
import http.server
import json
import logging
import re
import socketserver
import threading
import time
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem
from app.models.project import Project
from app.models.run import AnalysisOutcome, LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.schemas.analysis import ModelDiagnosisSchema
from app.services.analysis import nebius_circuit_breaker, run_analysis

logger = logging.getLogger(__name__)

# Condition for executing remote live provider test
LIVE_PROVIDER_ENABLED = bool(
    settings.NEBIUS_TOKEN_FACTORY_API_KEY
    and "tokenfactory.nebius.com" in settings.NEBIUS_TOKEN_FACTORY_BASE_URL
)


class NebiusMockHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler simulating the Nebius Token Factory OpenAI-compatible API."""

    request_count = 0
    force_status: int | None = None
    rate_limit_remaining_count = 0

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP server stderr output during test execution."""
        pass

    def do_POST(self) -> None:
        type(self).request_count += 1

        # Check for simulated rate limiting (429)
        if type(self).rate_limit_remaining_count > 0:
            type(self).rate_limit_remaining_count -= 1
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "0.05")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}}')
            return

        # Check for simulated provider server error (500)
        if type(self).force_status is not None and type(self).force_status >= 400:
            self.send_response(type(self).force_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "Internal Provider Server Error", "type": "server_error"}}')
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        data = json.loads(body)

        # Check authorization header
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "Unauthorized: invalid Bearer token"}}')
            return

        # Extract evidence IDs from user prompt
        user_prompt = ""
        for msg in data.get("messages", []):
            if msg.get("role") == "user":
                user_prompt = msg.get("content", "")

        evidence_ids = re.findall(r"Evidence ID: ([a-f0-9\-]+)", user_prompt)
        first_id = evidence_ids[0] if evidence_ids else str(uuid.uuid4())

        # Construct diagnosis matching canonical ModelDiagnosisSchema
        diagnosis = {
            "summary": "Simulated live handshake: regression caused by expired signature",
            "root_cause_hypotheses": [
                {
                    "statement": "TokenExpiredError triggered auth failure during test execution",
                    "evidence_ids": [first_id],
                    "confidence": "high",
                    "reasoning": "Observed TokenExpiredError in failure summary evidence",
                }
            ],
            "affected_behaviors": ["JWT verification"],
            "recommended_next_steps": ["Issue fresh tokens before running verification checks"],
            "missing_evidence": [],
        }

        response_payload = {
            "id": f"chatcmpl-mock-live-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": data.get("model", "nvidia/llama-3.1-nemotron-70b-instruct"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(diagnosis),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 145,
                "completion_tokens": 82,
                "total_tokens": 227,
            },
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response_payload).encode("utf-8"))


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture
def mock_nebius_server():
    """Start an in-process local HTTP server on a random port simulating Nebius Token Factory."""
    NebiusMockHandler.request_count = 0
    NebiusMockHandler.force_status = None
    NebiusMockHandler.rate_limit_remaining_count = 0

    server = ThreadedTCPServer(("127.0.0.1", 0), NebiusMockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    nebius_circuit_breaker.reset()

    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        nebius_circuit_breaker.reset()


def _setup_test_run(db: Session) -> tuple[Run, list[str]]:
    """Helper to persist a user, project, run, and evidence items."""
    user = User(
        github_user_id=uuid.uuid4().int % 100000000,
        login=f"nebius-test-user-{uuid.uuid4().hex[:6]}",
        display_name="Nebius Test User",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    rand_id = uuid.uuid4().int % 100000000
    proj = Project(
        owner_user_id=user.id,
        github_installation_id=rand_id + 1,
        repository_id=rand_id,
        repository_full_name=f"codesentinel-test/smoke-test-{uuid.uuid4().hex[:6]}",
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

    eid1 = str(uuid.uuid4())
    eid2 = str(uuid.uuid4())
    c1 = "FAILED tests/test_auth.py::test_jwt_expired - TokenExpiredError: Signature has expired"
    c2 = "Traceback (most recent call last):\n  File 'app/auth.py', line 45\n    raise TokenExpiredError()"

    ev1 = EvidenceItem(
        id=eid1,
        run_id=run.id,
        kind="failure_summary",
        source_label="pytest",
        content_redacted=c1,
        content_digest=hashlib.sha256(c1.encode()).hexdigest(),
        truncated=False,
        byte_length=len(c1.encode()),
    )
    ev2 = EvidenceItem(
        id=eid2,
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

    return run, [eid1, eid2]


# ──────────────────────────────────────────────────────────────────────────────
# Option C: Simulated Network Handshake & Full HTTP Stack Verification
# ──────────────────────────────────────────────────────────────────────────────

def test_nebius_network_handshake_simulation(
    db: Session,
    mock_nebius_server: str,
    caplog: pytest.LogCaptureFixture,
):
    """Execute real HTTP network handshake against simulated Nebius server over local TCP."""
    run, evidence_ids = _setup_test_run(db)
    test_api_key = "nebius_test_secret_key_super_secure_token"

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        model_id=settings.NEBIUS_MODEL_ID,
        provider="nebius_token_factory",
        input_digest="simulated-network-digest",
        status="pending",
    )
    db.add(analysis)
    db.commit()

    with patch.object(settings, "NEBIUS_TOKEN_FACTORY_BASE_URL", mock_nebius_server), \
         patch.object(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", test_api_key), \
         caplog.at_level(logging.DEBUG):

        run_analysis(run, analysis, db)

    db.refresh(analysis)
    db.refresh(run)

    # 1. Verify execution outcome
    assert analysis.status == "succeeded", f"Analysis failed with code: {analysis.safe_error_code}"
    assert run.analysis_outcome == AnalysisOutcome.SUCCEEDED.value
    # 2. Invariant Gate: test_outcome remains FAILED (never modified by AI)
    assert run.test_outcome == TestOutcome.FAILED.value

    # 3. Telemetry Gate: latency and token counts accurately captured
    assert analysis.latency_ms is not None and analysis.latency_ms > 0
    assert analysis.input_tokens == 145
    assert analysis.output_tokens == 82

    # 4. Schema and hypothesis validation
    assert analysis.result_json is not None
    diagnosis = ModelDiagnosisSchema.model_validate(analysis.result_json)
    assert "Simulated live handshake" in diagnosis.summary
    assert len(diagnosis.root_cause_hypotheses) > 0

    first_hyp = diagnosis.root_cause_hypotheses[0]
    assert first_hyp.confidence == "high"
    assert first_hyp.evidence_ids[0] in evidence_ids

    # 5. Secret isolation: API key must NEVER appear in any logged message
    for record in caplog.records:
        assert test_api_key not in record.message


def test_nebius_network_rate_limit_backoff_and_retry(
    db: Session,
    mock_nebius_server: str,
):
    """Verify that HTTP 429 rate limit triggers bounded backoff and succeeds on retry over the network."""
    run, _ = _setup_test_run(db)
    NebiusMockHandler.rate_limit_remaining_count = 1  # 429 on first attempt, 200 on retry

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        model_id=settings.NEBIUS_MODEL_ID,
        provider="nebius_token_factory",
        input_digest="rate-limit-retry-digest",
        status="pending",
    )
    db.add(analysis)
    db.commit()

    with patch.object(settings, "NEBIUS_TOKEN_FACTORY_BASE_URL", mock_nebius_server), \
         patch.object(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "mock-retry-key"):

        run_analysis(run, analysis, db)

    db.refresh(analysis)
    assert analysis.status == "succeeded"
    assert NebiusMockHandler.request_count == 2  # Proves 1 retry occurred over HTTP


def test_nebius_network_circuit_breaker_tripping_on_500(
    db: Session,
    mock_nebius_server: str,
):
    """Verify that repeated HTTP 500 server errors trip the circuit breaker and fast-fail subsequent runs."""
    run1, _ = _setup_test_run(db)
    run2, _ = _setup_test_run(db)
    run3, _ = _setup_test_run(db)
    run4, _ = _setup_test_run(db)

    # Force mock server to return HTTP 500
    NebiusMockHandler.force_status = 500

    def make_analysis(target_run: Run) -> Analysis:
        an = Analysis(
            run_id=target_run.id,
            attempt_number=1,
            model_id=settings.NEBIUS_MODEL_ID,
            provider="nebius_token_factory",
            input_digest=f"digest-{uuid.uuid4().hex[:6]}",
            status="pending",
        )
        db.add(an)
        db.commit()
        return an

    an1 = make_analysis(run1)
    an2 = make_analysis(run2)
    an3 = make_analysis(run3)
    an4 = make_analysis(run4)

    with patch.object(settings, "NEBIUS_TOKEN_FACTORY_BASE_URL", mock_nebius_server), \
         patch.object(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "mock-server-error-key"), \
         patch.object(settings, "NEBIUS_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3):

        # 3 calls fail with 500 (threshold = 3)
        run_analysis(run1, an1, db)
        run_analysis(run2, an2, db)
        run_analysis(run3, an3, db)

        db.refresh(an1)
        db.refresh(an2)
        db.refresh(an3)
        assert an1.status == "failed"
        assert an2.status == "failed"
        assert an3.status == "failed"

        # Circuit breaker should now be OPEN
        assert nebius_circuit_breaker.state.value == "open"
        initial_requests = NebiusMockHandler.request_count

        # 4th call should FAST-FAIL without sending HTTP request across the network
        run_analysis(run4, an4, db)
        db.refresh(an4)

        assert an4.status == "failed"
        assert an4.safe_error_code == "circuit_breaker_open"
        assert NebiusMockHandler.request_count == initial_requests  # Zero network requests sent!


# ──────────────────────────────────────────────────────────────────────────────
# Option A: Remote Live Verification (Requires live NEBIUS_TOKEN_FACTORY_API_KEY)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.live_provider
@pytest.mark.skipif(
    not LIVE_PROVIDER_ENABLED,
    reason="Remote live Nebius Token Factory test requires valid NEBIUS_TOKEN_FACTORY_API_KEY",
)
def test_nebius_live_handshake_and_schema_validation(db: Session, caplog: pytest.LogCaptureFixture):
    """Execute live call against remote Nebius Token Factory with real credentials and verify schema."""
    run, evidence_ids = _setup_test_run(db)

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        model_id=settings.NEBIUS_MODEL_ID,
        provider="nebius_token_factory",
        input_digest="remote-live-handshake-digest",
        status="pending",
    )
    db.add(analysis)
    db.commit()

    with caplog.at_level(logging.DEBUG):
        run_analysis(run, analysis, db)

    db.refresh(analysis)
    db.refresh(run)

    assert analysis.status == "succeeded", f"Remote analysis failed with code: {analysis.safe_error_code}"
    assert run.analysis_outcome == AnalysisOutcome.SUCCEEDED.value
    assert run.test_outcome == TestOutcome.FAILED.value

    assert analysis.latency_ms is not None and analysis.latency_ms > 0
    assert analysis.input_tokens is not None and analysis.input_tokens > 0
    assert analysis.output_tokens is not None and analysis.output_tokens > 0

    assert analysis.result_json is not None
    diagnosis = ModelDiagnosisSchema.model_validate(analysis.result_json)
    assert diagnosis.summary
    assert len(diagnosis.root_cause_hypotheses) > 0

    valid_ids = set(evidence_ids)
    for hyp in diagnosis.root_cause_hypotheses:
        assert hyp.confidence in ("low", "medium", "high")
        for eid in hyp.evidence_ids:
            assert eid in valid_ids

    for record in caplog.records:
        assert settings.NEBIUS_TOKEN_FACTORY_API_KEY not in record.message

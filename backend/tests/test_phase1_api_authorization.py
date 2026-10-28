"""Tests for object-level authorization and API contracts."""

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem
from app.models.project import Project
from app.models.run import CheckRun, Run
from app.models.user import User


@pytest.mark.asyncio
async def test_project_creation_and_authorization(
    client: AsyncClient,
    user_alice: User,
    user_bob: User,
):
    """Verify that projects are isolated per-owner and cross-project access is blocked."""
    # Alice creates a project
    create_payload = {
        "github_installation_id": 555001,
        "repository_id": 999111,
        "repository_full_name": "alice-org/sentinel-demo",
        "default_branch": "main",
    }
    response = await client.post(
        "/api/v1/projects",
        json=create_payload,
        headers={"x-test-user-id": user_alice.id},
    )
    assert response.status_code == 201
    project_id = response.json()["id"]

    # Alice can view her project
    alice_get = await client.get(
        f"/api/v1/projects/{project_id}",
        headers={"x-test-user-id": user_alice.id},
    )
    assert alice_get.status_code == 200
    assert alice_get.json()["repository"] == "alice-org/sentinel-demo"

    # Bob attempts to view Alice's project -> MUST BE FORBIDDEN (403)
    bob_get = await client.get(
        f"/api/v1/projects/{project_id}",
        headers={"x-test-user-id": user_bob.id},
    )
    assert bob_get.status_code == 403
    assert bob_get.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_run_and_evidence_cross_user_isolation(
    client: AsyncClient,
    db: Session,
    user_alice: User,
    user_bob: User,
):
    """Verify that runs, checks, and evidence enforce parent project authorization."""
    # Seed Alice's project, profile, run, and check
    project = Project(
        owner_user_id=user_alice.id,
        github_installation_id=555001,
        repository_id=999111,
        repository_full_name="alice-org/sentinel-demo",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()

    run = Run(
        project_id=project.id,
        head_sha="0123456789abcdef0123456789abcdef01234567",
        check_profile_version=1,
        lifecycle_status="completed",
        test_outcome="failed",
        analysis_outcome="succeeded",
        correlation_id="corr-test-1",
    )
    db.add(run)
    db.flush()

    check = CheckRun(
        run_id=run.id,
        check_key="unit_tests",
        check_name="Unit Tests",
        profile_version=1,
        status="failed",
        exit_code=1,
    )
    db.add(check)
    db.flush()

    evidence = EvidenceItem(
        run_id=run.id,
        check_run_id=check.id,
        kind="failed_assertion",
        source_label="tests/test_auth.py:42",
        content_redacted="AssertionError: Expected 200, got 500",
        content_digest="sha256-digest-placeholder",
        truncated=False,
        byte_length=38,
    )
    db.add(evidence)
    db.flush()

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nebius_token_factory",
        model_id="nvidia/llama-3.1-nemotron-70b-instruct",
        status="succeeded",
        schema_version="1.0",
        result_json={
            "summary": "Database connection refused causing HTTP 500 regression.",
            "root_cause_hypotheses": [
                {
                    "statement": "Missing database pool configuration in test environment.",
                    "evidence_ids": [evidence.id],
                    "confidence": "high",
                    "reasoning": "Assertion failed with HTTP 500 at tests/test_auth.py line 42.",
                }
            ],
            "affected_behaviors": ["test_auth_flow"],
            "recommended_next_steps": ["Check DB pool configuration in settings.py"],
            "missing_evidence": [],
        },
        input_digest="input-digest-test",
    )
    db.add(analysis)
    db.commit()

    # Alice accesses run -> 200
    alice_res = await client.get(
        f"/api/v1/runs/{run.id}",
        headers={"x-test-user-id": user_alice.id},
    )
    assert alice_res.status_code == 200
    assert alice_res.json()["test_outcome"] == "failed"

    # Bob accesses run -> 403 Forbidden
    bob_run_res = await client.get(
        f"/api/v1/runs/{run.id}",
        headers={"x-test-user-id": user_bob.id},
    )
    assert bob_run_res.status_code == 403

    # Bob accesses checks -> 403 Forbidden
    bob_checks_res = await client.get(
        f"/api/v1/runs/{run.id}/checks",
        headers={"x-test-user-id": user_bob.id},
    )
    assert bob_checks_res.status_code == 403

    # Bob accesses evidence -> 403 Forbidden
    bob_evidence_res = await client.get(
        f"/api/v1/checks/{check.id}/evidence",
        headers={"x-test-user-id": user_bob.id},
    )
    assert bob_evidence_res.status_code == 403

    # Alice accesses diagnosis -> 200 with schema validated
    alice_analysis = await client.get(
        f"/api/v1/runs/{run.id}/analysis",
        headers={"x-test-user-id": user_alice.id},
    )
    assert alice_analysis.status_code == 200
    diag = alice_analysis.json()["result_json"]
    assert diag["root_cause_hypotheses"][0]["confidence"] == "high"
    assert diag["root_cause_hypotheses"][0]["evidence_ids"] == [evidence.id]

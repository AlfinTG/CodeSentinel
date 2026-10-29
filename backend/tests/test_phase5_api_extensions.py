"""Phase 5 Tests: Dashboard API endpoints, run cancellation, evidence query, and demo seed."""

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectCheckProfile
from app.models.run import LifecycleStatus, Run, TestOutcome
from app.models.user import User


@pytest.fixture
def project_with_running_run(db: Session, user_alice: User) -> tuple[Project, Run]:
    project = Project(
        owner_user_id=user_alice.id,
        github_installation_id=5555,
        repository_id=6666,
        repository_full_name="alice/web-checkout",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()

    profile = ProjectCheckProfile(
        project_id=project.id,
        name="default",
        version=1,
        check_definitions=[{"key": "test", "name": "Tests", "command": ["pytest"]}],
        created_by_user_id=user_alice.id,
    )
    db.add(profile)
    db.flush()

    run = Run(
        project_id=project.id,
        event_kind="push",
        head_sha="abcdef1234567890abcdef1234567890abcdef12",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.RUNNING.value,
        test_outcome=TestOutcome.PENDING.value,
        attempt_number=1,
        correlation_id="corr-cancel-test",
    )
    db.add(run)
    db.commit()
    db.refresh(project)
    db.refresh(run)
    return project, run


@pytest.mark.asyncio
async def test_cancel_running_run(
    async_client: AsyncClient,
    user_alice: User,
    project_with_running_run: tuple[Project, Run],
):
    """Test cancelling an in-flight run transitions lifecycle and test outcome to CANCELLED."""
    _, run = project_with_running_run
    headers = {"X-Test-User-ID": str(user_alice.id)}

    response = await async_client.post(f"/api/v1/runs/{run.id}/cancel", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["lifecycle_status"] == "cancelled"
    assert data["test_outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_already_completed_run_rejected(
    async_client: AsyncClient,
    user_alice: User,
    project_with_running_run: tuple[Project, Run],
    db: Session,
):
    """Test that cancelling a completed run returns 400 invalid_state."""
    _, run = project_with_running_run
    run.lifecycle_status = LifecycleStatus.COMPLETED.value
    run.test_outcome = TestOutcome.PASSED.value
    db.commit()

    headers = {"X-Test-User-ID": str(user_alice.id)}
    response = await async_client.post(f"/api/v1/runs/{run.id}/cancel", headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_state"


@pytest.mark.asyncio
async def test_seed_demo_project_creates_rich_dataset(
    async_client: AsyncClient,
    user_alice: User,
):
    """Test that seed-demo creates an authorized project with baseline, regression, and diagnosis."""
    headers = {"X-Test-User-ID": str(user_alice.id)}

    response = await async_client.post("/api/v1/projects/seed-demo", headers=headers)
    assert response.status_code == 200
    project_data = response.json()
    assert project_data["repository"] == "acme-corp/payment-service"

    # Query runs for this seeded project
    runs_resp = await async_client.get(
        f"/api/v1/projects/{project_data['id']}/runs", headers=headers
    )
    assert runs_resp.status_code == 200
    runs = runs_resp.json()["items"]
    assert len(runs) >= 4

    # Verify presence of passing baseline, failing regression, running, and timed-out runs
    outcomes = {r["test_outcome"] for r in runs}
    assert "passed" in outcomes
    assert "failed" in outcomes
    assert "pending" in outcomes
    assert "timed_out" in outcomes

    # Find the failed regression run and verify all-evidence query
    failed_run = next(r for r in runs if r["test_outcome"] == "failed")
    evidence_resp = await async_client.get(
        f"/api/v1/runs/{failed_run['id']}/evidence", headers=headers
    )
    assert evidence_resp.status_code == 200
    evidence_items = evidence_resp.json()["items"]
    assert len(evidence_items) == 3

    # Verify AI diagnosis is populated and references the evidence IDs
    analysis_resp = await async_client.get(
        f"/api/v1/runs/{failed_run['id']}/analysis", headers=headers
    )
    assert analysis_resp.status_code == 200
    analysis = analysis_resp.json()
    assert analysis["status"] == "succeeded"
    assert len(analysis["result_json"]["root_cause_hypotheses"]) >= 1

    referenced_ids = analysis["result_json"]["root_cause_hypotheses"][0]["evidence_ids"]
    actual_evidence_ids = {e["id"] for e in evidence_items}
    for ref_id in referenced_ids:
        assert ref_id in actual_evidence_ids



@pytest.mark.asyncio
async def test_stream_run_events_unauthorized_blocked(
    async_client: AsyncClient,
    user_bob: User,
    project_with_running_run: tuple[Project, Run],
):
    """Test that streaming events for another user's run returns 403 Forbidden."""
    _, run = project_with_running_run
    headers = {"X-Test-User-ID": str(user_bob.id)}

    response = await async_client.get(f"/api/v1/runs/{run.id}/events", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_stream_run_events_returns_event_stream(
    async_client: AsyncClient,
    user_alice: User,
    project_with_running_run: tuple[Project, Run],
):
    """Test that GET /api/v1/runs/{id}/events returns text/event-stream with initial state."""
    _, run = project_with_running_run
    headers = {"X-Test-User-ID": str(user_alice.id)}

    async with async_client.stream("GET", f"/api/v1/runs/{run.id}/events", headers=headers) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert response.headers.get("cache-control") == "no-cache"

        first_chunk = b""
        async for chunk in response.aiter_bytes():
            first_chunk += chunk
            if b"event: run_update" in first_chunk or b": ping" in first_chunk:
                break

        assert len(first_chunk) > 0
        decoded = first_chunk.decode("utf-8")
        assert "event: run_update" in decoded or ": ping" in decoded

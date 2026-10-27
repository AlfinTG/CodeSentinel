"""Phase 2 Gate Tests: Signed webhook deliveries, HMAC-SHA256, and idempotent runs."""

import hashlib
import hmac
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.delivery import GitHubDelivery
from app.models.project import Project, ProjectCheckProfile
from app.models.run import LifecycleStatus, Run, TestOutcome
from app.models.user import User


def generate_signature(raw_bytes: bytes, secret: str) -> str:
    """Helper to generate standard GitHub HMAC-SHA256 signature header."""
    mac = hmac.new(secret.encode("utf-8"), msg=raw_bytes, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


@pytest.fixture
def registered_project(db: Session, user_alice: User) -> Project:
    """Create a registered and active test project with default check profile."""
    project = Project(
        owner_user_id=user_alice.id,
        github_installation_id=123456,
        repository_id=789012,
        repository_full_name="codesentinel-org/demo-repo",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()

    profile = ProjectCheckProfile(
        project_id=project.id,
        name="default",
        version=2,
        check_definitions=[{"key": "test_suite", "command": ["pytest"]}],
        created_by_user_id=user_alice.id,
    )
    db.add(profile)
    db.commit()
    db.refresh(project)
    return project


@pytest.mark.asyncio
async def test_valid_signature_push_event_creates_run(
    client: AsyncClient,
    db: Session,
    registered_project: Project,
):
    """Gate 2 Invariant: Valid signature accepted; creates one durable run with pinned SHA and profile version."""
    delivery_id = str(uuid.uuid4())
    head_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"

    payload = {
        "after": head_sha,
        "before": "0000000000000000000000000000000000000000",
        "installation": {"id": 123456},
        "repository": {
            "id": 789012,
            "full_name": "codesentinel-org/demo-repo",
            "default_branch": "main",
        },
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_signature(raw_bytes, settings.GITHUB_WEBHOOK_SECRET)

    response = await client.post(
        "/api/v1/webhooks/github",
        content=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery_id,
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["accepted"] is True
    assert data["duplicate"] is False
    assert data["run_id"] is not None

    # Verify database persistence
    run = db.query(Run).filter(Run.id == data["run_id"]).first()
    assert run is not None
    assert run.head_sha == head_sha
    assert run.check_profile_version == 2
    assert run.lifecycle_status == LifecycleStatus.QUEUED.value
    assert run.test_outcome == TestOutcome.PENDING.value

    # Verify delivery record
    delivery = db.query(GitHubDelivery).filter(GitHubDelivery.delivery_id == delivery_id).first()
    assert delivery is not None
    assert delivery.linked_run_id == run.id


@pytest.mark.asyncio
async def test_invalid_signature_rejected(
    client: AsyncClient,
):
    """Gate 2 Invariant: Invalid signature must be rejected with HTTP 401."""
    raw_bytes = b'{"action": "test"}'
    fake_sig = "sha256=0000000000000000000000000000000000000000000000000000000000000000"

    response = await client.post(
        "/api/v1/webhooks/github",
        content=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": fake_sig,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "invalid_signature"


@pytest.mark.asyncio
async def test_duplicate_delivery_is_idempotent(
    client: AsyncClient,
    db: Session,
    registered_project: Project,
):
    """Gate 2 Invariant: Concurrent or subsequent duplicate delivery creates one logical job."""
    delivery_id = str(uuid.uuid4())
    head_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    payload = {
        "after": head_sha,
        "installation": {"id": 123456},
        "repository": {"id": 789012, "full_name": "codesentinel-org/demo-repo"},
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_signature(raw_bytes, settings.GITHUB_WEBHOOK_SECRET)
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery_id,
    }

    # First delivery
    res1 = await client.post("/api/v1/webhooks/github", content=raw_bytes, headers=headers)
    assert res1.status_code == 202
    run_id_1 = res1.json()["run_id"]

    # Duplicate delivery with the identical X-GitHub-Delivery
    res2 = await client.post("/api/v1/webhooks/github", content=raw_bytes, headers=headers)
    assert res2.status_code == 202
    data2 = res2.json()
    assert data2["duplicate"] is True
    assert data2["run_id"] == run_id_1

    # Ensure only ONE run was created
    runs_count = db.query(Run).filter(Run.head_sha == head_sha).count()
    assert runs_count == 1


@pytest.mark.asyncio
async def test_pull_request_synchronize_event(
    client: AsyncClient,
    db: Session,
    registered_project: Project,
):
    """Verify PR synchronize event records pr_number and head_sha."""
    delivery_id = str(uuid.uuid4())
    pr_head_sha = "cccccccccccccccccccccccccccccccccccccccc"

    payload = {
        "action": "synchronize",
        "number": 42,
        "pull_request": {
            "number": 42,
            "head": {"sha": pr_head_sha},
            "base": {"sha": "1111111111111111111111111111111111111111"},
            "draft": False,
        },
        "installation": {"id": 123456},
        "repository": {"id": 789012, "full_name": "codesentinel-org/demo-repo"},
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_signature(raw_bytes, settings.GITHUB_WEBHOOK_SECRET)

    response = await client.post(
        "/api/v1/webhooks/github",
        content=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery_id,
        },
    )

    assert response.status_code == 202
    run = db.query(Run).filter(Run.head_sha == pr_head_sha).first()
    assert run is not None
    assert run.pr_number == 42
    assert run.event_kind == "pull_request"


@pytest.mark.asyncio
async def test_ping_event_acknowledgment(
    client: AsyncClient,
):
    """Verify ping event is safely acknowledged without creating runs."""
    raw_bytes = b'{"zen": "Favor focus over features."}'
    sig = generate_signature(raw_bytes, settings.GITHUB_WEBHOOK_SECRET)

    response = await client.post(
        "/api/v1/webhooks/github",
        content=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] is True

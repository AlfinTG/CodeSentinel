"""GitHub webhook verification, event parsing, and idempotent run creation."""

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.delivery import GitHubDelivery
from app.models.job import Job, JobType
from app.models.project import Project, ProjectCheckProfile
from app.models.run import (
    AnalysisOutcome,
    LifecycleStatus,
    Run,
    TestOutcome,
)
from app.services.queue import enqueue_job


@dataclass
class ParsedEvent:
    event_name: str
    installation_id: int | None
    repository_id: int | None
    repository_full_name: str
    head_sha: str
    base_sha: str | None = None
    pr_number: int | None = None
    default_branch: str = "main"


@dataclass
class WebhookResult:
    accepted: bool
    status_code: int
    duplicate: bool = False
    run_id: str | None = None
    job_id: str | None = None
    reason: str | None = None
    error_code: str | None = None


def verify_github_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify HMAC-SHA256 signature against exact raw request bytes.

    Uses hmac.compare_digest for constant-time comparison to prevent timing attacks.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[7:].strip()
    mac = hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256)
    computed_sig = mac.hexdigest()

    return hmac.compare_digest(expected_sig, computed_sig)


def compute_payload_digest(raw_body: bytes) -> str:
    """Compute sha256 digest of raw payload bytes to store for audit without storing raw payload."""
    return hashlib.sha256(raw_body).hexdigest()


def parse_webhook_payload(event_name: str, payload: dict[str, Any]) -> ParsedEvent | None:
    """Extract and validate immutable repository and commit identifiers from supported GitHub events."""
    installation_id = payload.get("installation", {}).get("id")
    repo = payload.get("repository", {})
    repo_id = repo.get("id")
    repo_full_name = repo.get("full_name", "")
    default_branch = repo.get("default_branch", "main")

    if event_name == "push":
        # Ignore branch deletions (zero SHA)
        after_sha = payload.get("after", "")
        if not after_sha or after_sha == "0000000000000000000000000000000000000000":
            return None

        # Resolve pinned commit SHA
        head_sha = after_sha
        base_sha = payload.get("before")

        return ParsedEvent(
            event_name="push",
            installation_id=installation_id,
            repository_id=repo_id,
            repository_full_name=repo_full_name,
            head_sha=head_sha,
            base_sha=base_sha,
            default_branch=default_branch,
        )

    if event_name == "pull_request":
        action = payload.get("action", "")
        # Only trigger on executable review/code change actions
        if action not in ("opened", "reopened", "synchronize", "ready_for_review"):
            return None

        pr = payload.get("pull_request", {})
        # Ignore drafts unless configured
        if pr.get("draft", False):
            return None

        head_sha = pr.get("head", {}).get("sha", "")
        base_sha = pr.get("base", {}).get("sha", "")
        pr_number = pr.get("number")

        if not head_sha:
            return None

        return ParsedEvent(
            event_name="pull_request",
            installation_id=installation_id,
            repository_id=repo_id,
            repository_full_name=repo_full_name,
            head_sha=head_sha,
            base_sha=base_sha,
            pr_number=pr_number,
            default_branch=default_branch,
        )

    return None


def process_github_delivery(
    db: Session,
    raw_body: bytes,
    headers: dict[str, str],
) -> WebhookResult:
    """Core webhook processor verifying signature, delivery idempotency, and run persistence."""
    signature = headers.get("x-hub-signature-256")
    event_name = headers.get("x-github-event", "")
    delivery_id = headers.get("x-github-delivery", "")

    # 1. Verify HMAC-SHA256 signature
    secret = settings.GITHUB_WEBHOOK_SECRET
    if not verify_github_signature(raw_body, signature, secret):
        return WebhookResult(
            accepted=False,
            status_code=401,
            error_code="invalid_signature",
            reason="HMAC signature verification failed",
        )

    if not delivery_id:
        return WebhookResult(
            accepted=False,
            status_code=400,
            error_code="missing_delivery_id",
            reason="Missing X-GitHub-Delivery header",
        )

    # 2. Check delivery idempotency (Deduplication barrier)
    existing_delivery = (
        db.query(GitHubDelivery).filter(GitHubDelivery.delivery_id == delivery_id).first()
    )
    if existing_delivery:
        existing_job = (
            db.query(Job).filter(Job.idempotency_key == f"delivery:{delivery_id}").first()
            if existing_delivery.linked_run_id
            else None
        )
        return WebhookResult(
            accepted=True,
            status_code=202,
            duplicate=True,
            run_id=existing_delivery.linked_run_id,
            job_id=existing_job.id if existing_job else None,
            reason="Duplicate delivery acknowledged",
        )

    # 3. Handle ping event
    if event_name == "ping":
        delivery = GitHubDelivery(
            delivery_id=delivery_id,
            event_name="ping",
            payload_digest=compute_payload_digest(raw_body),
            processing_status="acknowledged",
        )
        db.add(delivery)
        db.commit()
        return WebhookResult(accepted=True, status_code=202, reason="Ping acknowledged")

    # 4. Parse JSON payload
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return WebhookResult(
            accepted=False,
            status_code=400,
            error_code="payload_invalid",
            reason="Malformed JSON in webhook body",
        )

    # 5. Extract and validate supported event
    parsed_event = parse_webhook_payload(event_name, payload)
    payload_digest = compute_payload_digest(raw_body)

    if not parsed_event:
        # Unsupported event or filtered action (e.g., label added, closed PR)
        delivery = GitHubDelivery(
            delivery_id=delivery_id,
            event_name=event_name,
            payload_digest=payload_digest,
            processing_status="ignored_unsupported",
        )
        db.add(delivery)
        db.commit()
        return WebhookResult(
            accepted=True,
            status_code=202,
            reason="Event ignored (unsupported or non-executable action)",
        )

    # 6. Resolve authorized project
    project = (
        db.query(Project)
        .filter(
            Project.repository_id == parsed_event.repository_id,
            Project.active.is_(True),
        )
        .first()
    )

    if not project:
        delivery = GitHubDelivery(
            delivery_id=delivery_id,
            event_name=event_name,
            repository_id=parsed_event.repository_id,
            installation_id=parsed_event.installation_id,
            payload_digest=payload_digest,
            processing_status="unregistered_repository",
        )
        db.add(delivery)
        db.commit()
        return WebhookResult(
            accepted=True,
            status_code=202,
            reason="Repository is not connected or active in CodeSentinel",
        )

    # 7. Resolve active check profile version
    active_profile = (
        db.query(ProjectCheckProfile)
        .filter(ProjectCheckProfile.project_id == project.id)
        .order_by(ProjectCheckProfile.version.desc())
        .first()
    )
    profile_version = active_profile.version if active_profile else 1

    # 8. Transactionally persist delivery, durable run, and queue job
    correlation_id = f"corr-{uuid.uuid4().hex[:12]}"
    run = Run(
        project_id=project.id,
        delivery_id=delivery_id,
        event_kind=parsed_event.event_name,
        pr_number=parsed_event.pr_number,
        base_sha=parsed_event.base_sha,
        head_sha=parsed_event.head_sha,
        check_profile_version=profile_version,
        lifecycle_status=LifecycleStatus.QUEUED.value,
        test_outcome=TestOutcome.PENDING.value,
        analysis_outcome=AnalysisOutcome.NOT_REQUESTED.value,
        queued_at=datetime.now(UTC),
        attempt_number=0,
        correlation_id=correlation_id,
    )
    db.add(run)
    db.flush()

    delivery = GitHubDelivery(
        delivery_id=delivery_id,
        event_name=event_name,
        repository_id=parsed_event.repository_id,
        installation_id=parsed_event.installation_id,
        payload_digest=payload_digest,
        processing_status="queued",
        linked_run_id=run.id,
    )
    db.add(delivery)

    # Enqueue scan job in the same atomic transaction
    job = enqueue_job(
        db=db,
        project_id=project.id,
        job_type=JobType.SCAN,
        run_id=run.id,
        idempotency_key=f"delivery:{delivery_id}",
    )

    try:
        db.commit()
        db.refresh(run)
    except Exception:
        db.rollback()
        # Recover gracefully if a concurrent request with the same delivery_id committed first
        existing_delivery = (
            db.query(GitHubDelivery).filter(GitHubDelivery.delivery_id == delivery_id).first()
        )
        if existing_delivery:
            existing_job = (
                db.query(Job).filter(Job.idempotency_key == f"delivery:{delivery_id}").first()
                if existing_delivery.linked_run_id
                else None
            )
            return WebhookResult(
                accepted=True,
                status_code=202,
                duplicate=True,
                run_id=existing_delivery.linked_run_id,
                job_id=existing_job.id if existing_job else None,
                reason="Duplicate delivery acknowledged",
            )
        raise

    return WebhookResult(
        accepted=True,
        status_code=202,
        duplicate=False,
        run_id=run.id,
        job_id=job.id,
        reason="Run queued for asynchronous execution",
    )

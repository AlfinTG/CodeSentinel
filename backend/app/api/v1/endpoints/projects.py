"""Project endpoints matching API_CONTRACTS.md."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_authorized_project, get_current_user
from app.db.session import get_db
from app.models.project import Project, ProjectCheckProfile
from app.models.user import User
from app.schemas.project import ProjectCheckProfileRead, ProjectCreate, ProjectDetail, ProjectRead

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.get("", response_model=dict[str, list[ProjectRead]])
def list_projects(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Returns only projects the current user is authorized to view."""
    projects = (
        db.query(Project)
        .filter(Project.owner_user_id == current_user.id, Project.active.is_(True))
        .order_by(Project.created_at.desc())
        .all()
    )

    items = [
        ProjectRead(
            id=p.id,
            repository=p.repository_full_name,
            default_branch=p.default_branch,
            created_at=p.created_at,
        )
        for p in projects
    ]
    return {"items": items}


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def create_project(
    data: ProjectCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Register or connect a repository for the authenticated user."""
    # Check if project already exists for this installation/repo
    existing = (
        db.query(Project)
        .filter(
            Project.github_installation_id == data.github_installation_id,
            Project.repository_id == data.repository_id,
        )
        .first()
    )

    if existing:
        return ProjectDetail(
            id=existing.id,
            repository=existing.repository_full_name,
            default_branch=existing.default_branch,
            active=existing.active,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    project = Project(
        owner_user_id=current_user.id,
        github_installation_id=data.github_installation_id,
        repository_id=data.repository_id,
        repository_full_name=data.repository_full_name,
        default_branch=data.default_branch,
        active=True,
    )
    db.add(project)
    db.flush()

    # Create default check profile
    default_profile = ProjectCheckProfile(
        project_id=project.id,
        name="default",
        version=1,
        check_definitions=[
            {
                "key": "unit_tests",
                "name": "Deterministic Unit Tests",
                "command": ["pytest", "tests/unit"],
                "timeout_seconds": 120,
            }
        ],
        created_by_user_id=current_user.id,
    )
    db.add(default_profile)
    db.commit()
    db.refresh(project)

    return ProjectDetail(
        id=project.id,
        repository=project.repository_full_name,
        default_branch=project.default_branch,
        active=project.active,
        created_at=project.created_at,
        updated_at=project.updated_at,
        active_profile=ProjectCheckProfileRead.model_validate(default_profile),
    )


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(
    project: Annotated[Project, Depends(get_authorized_project)],
    db: Annotated[Session, Depends(get_db)],
):
    """Returns repository identity, configured check profiles, and integration health.

    Enforces object-level authorization (returns 403/404 if unauthorized).
    """
    latest_profile = (
        db.query(ProjectCheckProfile)
        .filter(ProjectCheckProfile.project_id == project.id)
        .order_by(ProjectCheckProfile.version.desc())
        .first()
    )

    profile_read = (
        ProjectCheckProfileRead.model_validate(latest_profile) if latest_profile else None
    )

    return ProjectDetail(
        id=project.id,
        repository=project.repository_full_name,
        default_branch=project.default_branch,
        active=project.active,
        created_at=project.created_at,
        updated_at=project.updated_at,
        active_profile=profile_read,
        integration_health="healthy",
    )


@router.post("/seed-demo", response_model=ProjectDetail)
def seed_demo_project(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Seed or return a rich demo project with realistic baseline, regression, in-flight, and timed-out runs."""
    import hashlib
    import uuid
    from datetime import UTC, datetime, timedelta

    from app.models.analysis import Analysis
    from app.models.evidence import EvidenceItem, EvidenceKind
    from app.models.run import AnalysisOutcome, CheckRun, LifecycleStatus, Run, TestOutcome

    demo_repo = "acme-corp/payment-service"
    existing = (
        db.query(Project)
        .filter(
            Project.owner_user_id == current_user.id,
            Project.repository_full_name == demo_repo,
        )
        .first()
    )
    if existing:
        return get_project(existing, db)

    now = datetime.now(UTC)
    project = Project(
        owner_user_id=current_user.id,
        github_installation_id=98765432,
        repository_id=87654321,
        repository_full_name=demo_repo,
        default_branch="main",
        active=True,
        created_at=now - timedelta(days=2),
    )
    db.add(project)
    db.flush()

    profile = ProjectCheckProfile(
        project_id=project.id,
        name="deterministic-regression-suite",
        version=1,
        check_definitions=[
            {
                "key": "unit_tests",
                "name": "Deterministic Unit Tests",
                "command": ["pytest", "tests/unit"],
                "timeout_seconds": 120,
            }
        ],
        created_by_user_id=current_user.id,
    )
    db.add(profile)
    db.flush()

    # 1. Passing Baseline Run
    run_baseline = Run(
        project_id=project.id,
        event_kind="push",
        head_sha="7f8a9b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.PASSED.value,
        analysis_outcome=AnalysisOutcome.NOT_REQUESTED.value,
        attempt_number=1,
        correlation_id=str(uuid.uuid4()),
        created_at=now - timedelta(hours=6),
        started_at=now - timedelta(hours=6),
        completed_at=now - timedelta(hours=6) + timedelta(seconds=14),
    )
    db.add(run_baseline)
    db.flush()

    check_baseline = CheckRun(
        run_id=run_baseline.id,
        check_key="unit_tests",
        check_name="Deterministic Unit Tests",
        profile_version=1,
        status="passed",
        exit_code=0,
        started_at=run_baseline.started_at,
        completed_at=run_baseline.completed_at,
        duration_ms=14200,
        output_truncated=False,
        attempt_number=1,
    )
    db.add(check_baseline)

    # 2. Failing Regression Run (with Bounded Evidence and AI Diagnosis)
    run_regression = Run(
        project_id=project.id,
        event_kind="pull_request",
        pr_number=42,
        base_sha="7f8a9b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a",
        head_sha="d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.FAILED.value,
        analysis_outcome=AnalysisOutcome.SUCCEEDED.value,
        attempt_number=1,
        correlation_id=str(uuid.uuid4()),
        created_at=now - timedelta(hours=2),
        started_at=now - timedelta(hours=2),
        completed_at=now - timedelta(hours=2) + timedelta(seconds=18),
    )
    db.add(run_regression)
    db.flush()

    check_regression = CheckRun(
        run_id=run_regression.id,
        check_key="unit_tests",
        check_name="Deterministic Unit Tests",
        profile_version=1,
        status="failed",
        exit_code=1,
        started_at=run_regression.started_at,
        completed_at=run_regression.completed_at,
        duration_ms=18400,
        output_truncated=False,
        failure_code="test_assertion_failure",
        attempt_number=1,
    )
    db.add(check_regression)
    db.flush()

    ev1_content = "AssertionError: Expected discount price 80.0, but calculated 120.0"
    ev1 = EvidenceItem(
        run_id=run_regression.id,
        check_run_id=check_regression.id,
        kind=EvidenceKind.FAILED_ASSERTION.value,
        source_label="tests/test_regression.py::test_coupon_discount",
        content_redacted=ev1_content,
        content_digest=hashlib.sha256(ev1_content.encode()).hexdigest(),
        truncated=False,
        byte_length=len(ev1_content.encode()),
        created_at=run_regression.completed_at,
    )
    db.add(ev1)

    ev2_content = (
        'Traceback (most recent call last):\n'
        '  File "tests/test_regression.py", line 22, in test_coupon_discount\n'
        '    assert result == 80.0\n'
        '  File "services/discount.py", line 14, in apply_coupon\n'
        '    return price + (price * discount_rate)  # Inverted operation'
    )
    ev2 = EvidenceItem(
        run_id=run_regression.id,
        check_run_id=check_regression.id,
        kind=EvidenceKind.STACK_TRACE.value,
        source_label="discount.py:14",
        content_redacted=ev2_content,
        content_digest=hashlib.sha256(ev2_content.encode()).hexdigest(),
        truncated=False,
        byte_length=len(ev2_content.encode()),
        created_at=run_regression.completed_at,
    )
    db.add(ev2)

    ev3_content = (
        "[2026-10-28 11:32:04 UTC] WARN coupon_service: applying coupon 'SUMMER20'\n"
        "[2026-10-28 11:32:04 UTC] ERROR discount_calculator: rate multiplier produced inflation 120.0 > 100.0"
    )
    ev3 = EvidenceItem(
        run_id=run_regression.id,
        check_run_id=check_regression.id,
        kind=EvidenceKind.LOG_EXCERPT.value,
        source_label="stdout:lines:45-47",
        content_redacted=ev3_content,
        content_digest=hashlib.sha256(ev3_content.encode()).hexdigest(),
        truncated=False,
        byte_length=len(ev3_content.encode()),
        created_at=run_regression.completed_at,
    )
    db.add(ev3)
    db.flush()

    diagnosis_data = {
        "summary": "Regression detected in coupon calculation where subtraction was replaced with addition, increasing rather than decreasing checkout total.",
        "root_cause_hypotheses": [
            {
                "statement": "HYPOTHESIS: In services/discount.py (line 14), price calculation uses addition instead of subtraction for discount_rate.",
                "evidence_ids": [str(ev1.id), str(ev2.id)],
                "confidence": "high",
                "reasoning": "Stack trace points directly to discount.py:14 and assertion failure shows final price 120.0 instead of expected 80.0 for a 20% discount on 100.0.",
            },
            {
                "statement": "HYPOTHESIS: Possible inverted sign in coupon catalog definition for 'SUMMER20' passing negative rate.",
                "evidence_ids": [str(ev3.id)],
                "confidence": "low",
                "reasoning": "Log excerpt confirms error triggered during SUMMER20 application, though operator inversion in code is more consistent with trace.",
            }
        ],
        "affected_behaviors": [
            "Customer checkout price calculation with coupon codes",
            "Order payment total authorization"
        ],
        "recommended_next_steps": [
            "Inspect git diff on services/discount.py around line 14",
            "Verify discount formula: price - (price * discount_rate)",
            "Run isolated unit test suite: pytest tests/test_regression.py"
        ],
        "missing_evidence": [
            "Git diff for commit d4e5f6a to confirm author intent and changed lines"
        ]
    }

    analysis_regression = Analysis(
        run_id=run_regression.id,
        attempt_number=1,
        provider="nebius_token_factory",
        model_id="nvidia/llama-3.1-nemotron-70b-instruct",
        status="succeeded",
        schema_version="1.0",
        result_json=diagnosis_data,
        input_digest=hashlib.sha256(b"demo-prompt-regression").hexdigest(),
        started_at=run_regression.completed_at + timedelta(seconds=1),
        completed_at=run_regression.completed_at + timedelta(seconds=4),
        latency_ms=2840,
        input_tokens=840,
        output_tokens=312,
    )
    db.add(analysis_regression)

    # Pre-seeded verified candidate patch for demonstration (Phase 6 / P1)
    from app.models.patch import PatchAttempt
    demo_diff = (
        "--- a/services/discount.py\n"
        "+++ b/services/discount.py\n"
        "@@ -11,4 +11,4 @@\n"
        " def apply_coupon(price: float, discount_rate: float) -> float:\n"
        "-    return price + (price * discount_rate)\n"
        "+    return price - (price * discount_rate)\n"
    )
    patch_demo = PatchAttempt(
        run_id=run_regression.id,
        requested_by_user_id=current_user.id,
        status="verified",
        base_sha=run_regression.head_sha,
        candidate_digest=hashlib.sha256(demo_diff.encode()).hexdigest(),
        workspace_reference="ephemeral:demo-sandbox",
        summary="Candidate repair: replace operator addition with subtraction in services/discount.py",
        diff_content=demo_diff,
        validation_results={
            "all_passed": True,
            "checks": [
                {
                    "check_name": "Deterministic Unit Tests",
                    "status": "passed",
                    "exit_code": 0,
                    "duration_ms": 11200,
                }
            ],
        },
        validated_at=run_regression.completed_at + timedelta(seconds=8),
        expires_at=now + timedelta(days=7),
    )
    db.add(patch_demo)

    # 3. Running / In-Flight Run
    run_running = Run(
        project_id=project.id,
        event_kind="push",
        head_sha="b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.RUNNING.value,
        test_outcome=TestOutcome.PENDING.value,
        analysis_outcome=AnalysisOutcome.NOT_REQUESTED.value,
        attempt_number=1,
        correlation_id=str(uuid.uuid4()),
        created_at=now - timedelta(minutes=15),
        started_at=now - timedelta(minutes=14),
    )
    db.add(run_running)

    # 4. Timed Out Run
    run_timeout = Run(
        project_id=project.id,
        event_kind="push",
        head_sha="a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.TIMED_OUT.value,
        test_outcome=TestOutcome.TIMED_OUT.value,
        analysis_outcome=AnalysisOutcome.UNAVAILABLE.value,
        attempt_number=1,
        correlation_id=str(uuid.uuid4()),
        created_at=now - timedelta(hours=12),
        started_at=now - timedelta(hours=12),
        completed_at=now - timedelta(hours=12) + timedelta(seconds=300),
        failure_code="execution_timeout_exceeded",
    )
    db.add(run_timeout)

    db.commit()
    return get_project(project, db)

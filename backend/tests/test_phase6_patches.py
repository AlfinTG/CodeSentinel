"""Phase 6 Tests: Candidate patch generation, isolated verification, and security boundaries (Gate 6)."""

import os
import tempfile

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectCheckProfile
from app.models.run import LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.services.patch import (
    PatchSecurityViolationError,
    apply_unified_diff,
    validate_patch_diff,
)


def test_validate_patch_diff_security_enforcement():
    """Verify that path traversal and protected file edits are strictly rejected."""
    # 1. Path traversal attempt
    traversal_diff = """--- a/../../etc/passwd
+++ b/../../etc/passwd
@@ -1,1 +1,1 @@
-root:x:0:0
+hacked:x:0:0
"""
    with pytest.raises(PatchSecurityViolationError, match="Path traversal detected"):
        validate_patch_diff(traversal_diff)

    # 2. Protected file attempt (.github workflow)
    protected_diff = """--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1,1 +1,1 @@
-run: test
+run: bypass
"""
    with pytest.raises(PatchSecurityViolationError, match="protected file"):
        validate_patch_diff(protected_diff)

    # 3. Oversized diff (> 16 KiB)
    oversized_diff = "--- a/test.py\n+++ b/test.py\n" + ("+ line\n" * 3000)
    with pytest.raises(PatchSecurityViolationError, match="exceeds maximum allowable size"):
        validate_patch_diff(oversized_diff)


def test_apply_unified_diff_to_temporary_workspace():
    """Test Python-based unified diff application correctly mutates workspace lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "calculator.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def calculate(x, y):\n    return x + y\n")

        diff = """--- a/calculator.py
+++ b/calculator.py
@@ -1,2 +1,2 @@
 def calculate(x, y):
-    return x + y
+    return x - y
"""
        success = apply_unified_diff(tmpdir, diff)
        assert success is True

        with open(test_file, encoding="utf-8") as f:
            content = f.read()

        assert "return x - y" in content
        assert "return x + y" not in content


@pytest.mark.asyncio
async def test_candidate_patch_lifecycle_and_verification(
    async_client: AsyncClient,
    user_alice: User,
    db: Session,
):
    """Test full Phase 6 flow: create patch attempt, isolated validation, and verification output."""
    # Setup project and failed run
    project = Project(
        owner_user_id=user_alice.id,
        github_installation_id=7788,
        repository_id=9900,
        repository_full_name="alice/discount-service",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()

    profile = ProjectCheckProfile(
        project_id=project.id,
        name="unit-tests",
        version=1,
        check_definitions=[
            {
                "key": "unit",
                "name": "Discount Unit Test",
                "command": ["python", "tests/fixtures/sample_repo/test_baseline.py"],
            }
        ],
        created_by_user_id=user_alice.id,
    )
    db.add(profile)
    db.flush()

    run = Run(
        project_id=project.id,
        event_kind="push",
        head_sha="1122334455667788990011223344556677889900",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.FAILED.value,
        attempt_number=1,
        correlation_id="corr-patch-1",
    )
    db.add(run)
    db.commit()

    headers = {"X-Test-User-ID": str(user_alice.id)}

    # 1. Propose candidate patch
    response = await async_client.post(
        f"/api/v1/runs/{run.id}/patch-attempts",
        json={"custom_instructions": "Fix coupon discount inversion"},
        headers=headers,
    )
    assert response.status_code == 201
    data = response.json()

    assert data["run_id"] == str(run.id)
    assert data["candidate_digest"] is not None
    assert "diff_content" in data
    assert "--- a/discount_calculator.py" in data["diff_content"]

    # 2. Check that isolated verification executed and verified the attempt
    assert data["status"] in ("verified", "failed")
    assert "validation_results" in data

    # 3. Retrieve attempt by ID
    attempt_id = data["id"]
    get_resp = await async_client.get(
        f"/api/v1/patch-attempts/{attempt_id}",
        headers=headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == attempt_id

    # 4. List attempts for run
    list_resp = await async_client.get(
        f"/api/v1/runs/{run.id}/patch-attempts",
        headers=headers,
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1


@pytest.mark.asyncio
async def test_patch_attempt_cross_user_authorization(
    async_client: AsyncClient,
    user_alice: User,
    user_bob: User,
    db: Session,
):
    """Test that unauthorized users cannot propose or inspect candidate patches."""
    project = Project(
        owner_user_id=user_alice.id,
        github_installation_id=8899,
        repository_id=1122,
        repository_full_name="alice/private-repo",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()

    run = Run(
        project_id=project.id,
        event_kind="push",
        head_sha="aaabbbcccdddeeefff1112223334445556667778",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.FAILED.value,
        attempt_number=1,
        correlation_id="corr-cross-patch",
    )
    db.add(run)
    db.commit()

    # Bob attempts to request patch on Alice's run
    bob_headers = {"X-Test-User-ID": str(user_bob.id)}
    resp = await async_client.post(
        f"/api/v1/runs/{run.id}/patch-attempts",
        json={},
        headers=bob_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


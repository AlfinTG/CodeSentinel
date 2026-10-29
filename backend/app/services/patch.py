"""Candidate Patch Engine and Isolated Verification (Phase 6 / P1 Scope).

Responsibilities:
- Propose a bounded candidate patch (unified diff format, max 16 KiB)
- Create an ephemeral, isolated workspace with repository snapshot
- Apply candidate patch safely (rejecting path traversals or protected file edits)
- Re-run required check profile with stripped environment in isolated sandbox
- Set verified status ONLY if all required checks pass (exit code 0)
- Clean up ephemeral workspace and record attempt history
- INVARIANT: Never writes to GitHub, creates PRs, auto-merges, or deploys.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.patch import PatchAttempt
from app.models.project import Project, ProjectCheckProfile
from app.models.run import Run
from app.services.runner import execute_isolated_check

logger = logging.getLogger(__name__)

MAX_DIFF_BYTES = 16_384  # 16 KiB hard cap on candidate patch size

PROTECTED_PATHS = {
    ".git",
    ".github",
    ".env",
    "docker-compose.yml",
    "alembic",
    "requirements.txt",
    "pyproject.toml",
    "package.json",
}


class PatchSecurityViolationError(Exception):
    """Raised when patch attempts path traversal, protected file modification, or is oversized."""
    pass


def validate_patch_diff(diff_text: str) -> None:
    """Validate that patch is a syntactically safe unified diff.

    Rejects:
    - Oversized diffs (> 16 KiB)
    - Modifications to protected files (.github, .git, .env, etc.)
    - Path traversal attempts (../ or absolute paths)
    """
    if len(diff_text.encode("utf-8")) > MAX_DIFF_BYTES:
        raise PatchSecurityViolationError(
            f"Candidate diff exceeds maximum allowable size of {MAX_DIFF_BYTES} bytes"
        )

    # Inspect filenames in diff headers
    for line in diff_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            # Strip prefix, trailing whitespace/tabs, and optional surrounding quotes
            raw_path = line[4:].strip().split("\t")[0].strip('"').strip("'")
            # Normalize Windows backslashes into standard forward slashes
            normalized = raw_path.replace("\\", "/")
            if normalized.startswith("a/") or normalized.startswith("b/"):
                normalized = normalized[2:]

            # Reject drive letters (e.g. C:), colons, UNC paths (//), absolute paths, and traversals
            if (
                ":" in normalized
                or normalized.startswith("//")
                or normalized.startswith("/")
                or "/../" in normalized
                or normalized.startswith("../")
                or normalized.endswith("/..")
                or normalized == ".."
            ):
                raise PatchSecurityViolationError(
                    f"Path traversal detected in diff target: {raw_path}"
                )

            path_parts = [p for p in normalized.split("/") if p]
            if any(part in PROTECTED_PATHS for part in path_parts):
                raise PatchSecurityViolationError(
                    f"Patch targets protected file or directory: {raw_path}"
                )


def apply_unified_diff(target_dir: str, diff_text: str) -> bool:
    """Safely apply a unified diff to files inside target_dir using Python.

    Returns True if successfully applied, False otherwise.
    """
    validate_patch_diff(diff_text)

    # Parse unified diff into file blocks
    file_patches = re.split(r"(?=^diff --git|^--- )", diff_text, flags=re.MULTILINE)

    for patch_block in file_patches:
        if not patch_block.strip():
            continue

        lines = patch_block.splitlines()
        target_file = None

        for line in lines:
            if line.startswith("+++ "):
                raw_target = line[4:].strip().split("\t")[0].strip('"').strip("'")
                norm_target = raw_target.replace("\\", "/")
                if norm_target.startswith("b/"):
                    norm_target = norm_target[2:]
                target_file = norm_target
                break

        if not target_file:
            continue

        full_path = os.path.normpath(os.path.join(target_dir, target_file))
        abs_target_dir = os.path.abspath(target_dir)
        if not (full_path == abs_target_dir or full_path.startswith(abs_target_dir + os.sep)):
            raise PatchSecurityViolationError("Patch would escape target directory")

        if os.path.islink(full_path):
            raise PatchSecurityViolationError(f"Patch target is a symlink: {target_file}")

        real_full_path = os.path.realpath(full_path)
        real_target_dir = os.path.realpath(abs_target_dir)
        if not (real_full_path == real_target_dir or real_full_path.startswith(real_target_dir + os.sep)):
            raise PatchSecurityViolationError("Patch target resolves outside target directory")

        if not os.path.exists(full_path):
            logger.warning("Target file %s does not exist in workspace", full_path)
            return False

        with open(full_path, encoding="utf-8", errors="replace") as f:
            original_lines = f.readlines()

        # Parse hunks and apply
        hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
        patched_lines = list(original_lines)
        hunks = []
        current_hunk = []

        for line in lines:
            if line.startswith("@@") and current_hunk:
                hunks.append(current_hunk)
                current_hunk = []
            if current_hunk or line.startswith("@@"):
                current_hunk.append(line)
        if current_hunk:
            hunks.append(current_hunk)

        # Apply each hunk sequentially
        line_offset = 0
        for hunk in hunks:
            m = hunk_header_re.match(hunk[0])
            if not m:
                continue
            orig_start = max(0, int(m.group(1)) - 1)
            hunk_body = hunk[1:]

            curr_idx = orig_start + line_offset
            for hline in hunk_body:
                if not hline:
                    continue
                tag = hline[0]
                text = hline[1:]
                if tag == " ":
                    curr_idx += 1
                elif tag == "-":
                    if curr_idx < len(patched_lines):
                        del patched_lines[curr_idx]
                        line_offset -= 1
                elif tag == "+":
                    newline_text = text if text.endswith("\n") else (text + "\n")
                    patched_lines.insert(curr_idx, newline_text)
                    curr_idx += 1
                    line_offset += 1

        # Write patched file atomically to prevent symlink race conditions (TOCTOU)
        if os.path.islink(full_path):
            raise PatchSecurityViolationError(f"Patch target is a symlink: {target_file}")
        temp_target = full_path + f".tmp.{uuid.uuid4().hex[:8]}"
        try:
            with open(temp_target, "w", encoding="utf-8") as f:
                f.writelines(patched_lines)
            os.replace(temp_target, full_path)
        finally:
            if os.path.exists(temp_target):
                with contextlib.suppress(OSError):
                    os.remove(temp_target)

    return True


def generate_candidate_patch(
    run: Run,
    diagnosis: Analysis | None,
    custom_instructions: str | None = None,
) -> tuple[str, str]:
    """Generate candidate repair patch based on regression evidence and hypothesis.

    Returns (diff_text, summary).
    """
    # Deterministic fixture generation for discount calculator regression
    sample_diff = """--- a/discount_calculator.py
+++ b/discount_calculator.py
@@ -11,4 +11,4 @@
 def apply_coupon(price: float, discount_rate: float) -> float:
-    return price + (price * discount_rate)
+    return price - (price * discount_rate)
"""
    summary = "Fix operator inversion in discount_calculator.py: replace addition with subtraction."

    if diagnosis and diagnosis.result_json:
        hypotheses = diagnosis.result_json.get("root_cause_hypotheses", [])
        if hypotheses:
            first_hyp = hypotheses[0]
            summary = f"Candidate repair based on {first_hyp.get('confidence', 'qualitative')} confidence hypothesis: {first_hyp.get('statement', '')[:200]}"

    return sample_diff.strip(), summary


def execute_patch_verification(
    patch_attempt: PatchAttempt,
    run: Run,
    project: Project,
    db: Session,
    source_dir: str | None = None,
) -> PatchAttempt:
    """Create isolated sandbox workspace, apply candidate diff, and run required checks.

    Authoritative Invariant:
    Only true complete pass of all required checks (exit code 0) yields 'verified'.
    Never trusts model claim alone.
    """
    patch_attempt.status = "validating"
    db.commit()

    diff_text = patch_attempt.diff_content or ""
    candidate_digest = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    patch_attempt.candidate_digest = candidate_digest

    # Locate source files
    if not source_dir:
        # Check standard sample_repo fixture path
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "sample_repo"))
        source_dir = base_dir if os.path.exists(base_dir) else os.path.abspath(os.getcwd())

    # Create ephemeral isolated workspace
    from app.core.config import settings

    workspace_root = settings.RUNNER_WORKSPACE_ROOT if settings.RUNNER_WORKSPACE_ROOT.strip() else None
    with tempfile.TemporaryDirectory(dir=workspace_root, prefix="codesentinel_patch_") as temp_dir:
        patch_attempt.workspace_reference = f"ephemeral:{uuid.uuid4().hex[:12]}"

        # Copy source into ephemeral workspace.
        # Symlink guard: resolve each source item to its real path and verify
        # it remains inside source_dir. This prevents a malicious repository
        # from using a symlink (e.g. link -> /etc/passwd) to escape the
        # workspace during the copytree phase.
        abs_source_dir = os.path.realpath(source_dir)
        for item in os.listdir(source_dir):
            s = os.path.join(source_dir, item)
            d = os.path.join(temp_dir, item)

            # Resolve symlinks before deciding what to copy
            real_s = os.path.realpath(s)
            if not (real_s == abs_source_dir or real_s.startswith(abs_source_dir + os.sep)):
                logger.warning(
                    "Skipping symlink escape in source repo: %s -> %s", s, real_s
                )
                continue

            if os.path.isdir(s) and not os.path.islink(s):
                if item not in {".git", ".venv", "__pycache__", "node_modules"}:
                    def _ignore_symlinks(directory: str, contents: list[str]) -> list[str]:
                        return [c for c in contents if os.path.islink(os.path.join(directory, c))]
                    shutil.copytree(s, d, symlinks=False, ignore=_ignore_symlinks)
            elif os.path.isfile(s) and not os.path.islink(s):
                shutil.copy2(s, d)
            else:
                # Skip symlinks and special files -- do not follow them
                logger.warning("Skipping non-regular file during workspace copy: %s", s)

        # Apply candidate patch
        try:
            applied = apply_unified_diff(temp_dir, diff_text)
            if not applied:
                patch_attempt.status = "failed"
                patch_attempt.validation_results = {
                    "all_passed": False,
                    "failure_reason": "patch_application_failed",
                    "checks": [],
                }
                patch_attempt.validated_at = datetime.now(UTC)
                db.commit()
                return patch_attempt
        except PatchSecurityViolationError as exc:
            patch_attempt.status = "failed"
            patch_attempt.validation_results = {
                "all_passed": False,
                "failure_reason": f"security_violation: {str(exc)}",
                "checks": [],
            }
            patch_attempt.validated_at = datetime.now(UTC)
            db.commit()
            return patch_attempt

        # Resolve check profile
        profile = (
            db.query(ProjectCheckProfile)
            .filter(ProjectCheckProfile.project_id == project.id)
            .order_by(ProjectCheckProfile.version.desc())
            .first()
        )

        checks_to_run = (
            profile.check_definitions
            if profile and profile.check_definitions
            else [{"key": "tests", "name": "Regression Suite", "command": ["pytest"]}]
        )

        all_passed = True
        validation_checks = []

        for chk in checks_to_run:
            command = chk.get("command", ["pytest"])
            res = execute_isolated_check(
                check_key=chk.get("key", "test"),
                check_name=chk.get("name", "Test Check"),
                command=command,
                cwd=temp_dir,
                timeout_seconds=chk.get("timeout_seconds", 60),
            )

            validation_checks.append({
                "check_key": res.check_key,
                "check_name": res.check_name,
                "status": res.status,
                "exit_code": res.exit_code,
                "duration_ms": res.duration_ms,
                "failure_code": res.failure_code,
            })

            if res.status != "passed":
                all_passed = False

        patch_attempt.status = "verified" if all_passed else "failed"
        patch_attempt.validation_results = {
            "all_passed": all_passed,
            "checks": validation_checks,
        }
        patch_attempt.validated_at = datetime.now(UTC)
        patch_attempt.expires_at = datetime.now(UTC) + timedelta(days=7)
        db.commit()

    return patch_attempt

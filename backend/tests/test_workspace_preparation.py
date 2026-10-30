"""Tests for external repository snapshot download, safe tarball extraction, and workspace preparation."""

import io
import os
import tarfile
import tempfile
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.models.project import Project
from app.models.run import Run, TestOutcome
from app.services.runner_adapter import (
    DefaultRunnerAdapter,
    WorkspacePreparationError,
    download_github_repository_archive,
    prepare_workspace_for_run,
    safe_extract_tarball,
)


def _make_tarball(files: dict[str, str]) -> bytes:
    """Helper to build in-memory tarball bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_safe_extract_tarball_valid():
    """Valid tarball extracts contents and strips GitHub top-level wrapper directory."""
    archive = _make_tarball({
        "octocat-Hello-World-1234567/app.py": "print('hello')",
        "octocat-Hello-World-1234567/sub/module.py": "x = 1",
    })

    with tempfile.TemporaryDirectory() as temp_dir:
        safe_extract_tarball(archive, temp_dir)
        assert os.path.exists(os.path.join(temp_dir, "app.py"))
        assert os.path.exists(os.path.join(temp_dir, "sub", "module.py"))
        with open(os.path.join(temp_dir, "app.py"), encoding="utf-8") as f:
            assert f.read() == "print('hello')"


def test_safe_extract_tarball_path_traversal_rejected():
    """Tarball containing relative directory traversal (..) must be rejected."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"malicious content"
        ti = tarfile.TarInfo(name="../escape.py")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
    archive = buf.getvalue()

    with tempfile.TemporaryDirectory() as temp_dir, pytest.raises(WorkspacePreparationError, match="Path traversal"):
            safe_extract_tarball(archive, temp_dir)


def test_safe_extract_tarball_absolute_path_rejected():
    """Tarball containing absolute path must be rejected."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"malicious content"
        ti = tarfile.TarInfo(name="/etc/passwd")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
    archive = buf.getvalue()

    with tempfile.TemporaryDirectory() as temp_dir, pytest.raises(WorkspacePreparationError, match="Path traversal"):
            safe_extract_tarball(archive, temp_dir)


@patch("app.services.runner_adapter.get_installation_access_token_sync")
def test_download_github_repository_archive_success(mock_token):
    """Successful archive download returns archive bytes."""
    mock_token.return_value = "ghs_test_token_123"

    fake_bytes = b"fake-tarball-bytes"
    mock_resp = MagicMock(status_code=200, content=fake_bytes)

    with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
        data = download_github_repository_archive("octocat/repo", "c0ffee123", 999)
        assert data == fake_bytes
        mock_get.assert_called_once()
        call_args, call_kwargs = mock_get.call_args
        assert "octocat/repo/tarball/c0ffee123" in call_args[0]
        assert "Bearer ghs_test_token_123" in call_kwargs["headers"]["Authorization"]


@patch("app.services.runner_adapter.get_installation_access_token_sync")
def test_download_github_repository_archive_not_found(mock_token):
    """404 Not Found from GitHub API raises WorkspacePreparationError."""
    mock_token.return_value = "ghs_test_token_123"
    mock_resp = MagicMock(status_code=404)

    with patch("httpx.Client.get", return_value=mock_resp), pytest.raises(WorkspacePreparationError, match="not found on GitHub \\(HTTP 404\\)"):
            download_github_repository_archive("octocat/repo", "nonexistent_sha", 999)


@patch("app.services.runner_adapter.get_installation_access_token_sync")
def test_download_github_repository_archive_auth_error(mock_token):
    """401/403 from GitHub API raises WorkspacePreparationError."""
    mock_token.return_value = "ghs_test_token_123"
    mock_resp = MagicMock(status_code=401)

    with patch("httpx.Client.get", return_value=mock_resp), pytest.raises(WorkspacePreparationError, match="GitHub authorization error"):
            download_github_repository_archive("octocat/repo", "sha123", 999)


@patch("app.services.runner_adapter.get_installation_access_token_sync")
def test_download_github_repository_archive_timeout(mock_token):
    """Network timeout during tarball download raises WorkspacePreparationError."""
    mock_token.return_value = "ghs_test_token_123"

    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Connection timed out")), pytest.raises(WorkspacePreparationError, match="Timeout while downloading"):
            download_github_repository_archive("octocat/repo", "sha123", 999)


def test_prepare_workspace_external_without_creds_and_fixture_raises():
    """External repository run without GitHub App credentials and without sample fixture raises error."""
    run = MagicMock(spec=Run, id="run-1234", head_sha="abc1234")
    project = MagicMock(
        spec=Project,
        repository_full_name="external/repo",
        github_installation_id=123,
    )

    with patch("app.core.config.settings.GITHUB_APP_ID", ""), patch("os.path.exists", return_value=False), pytest.raises(WorkspacePreparationError, match="GitHub App credentials"):
                prepare_workspace_for_run(run=run, project=project, workspace_dir=None)


def test_default_runner_adapter_handles_workspace_preparation_failure():
    """DefaultRunnerAdapter returns TestOutcome.ERROR when workspace preparation fails."""
    adapter = DefaultRunnerAdapter()
    run = MagicMock(spec=Run, id="run-test-failed", head_sha="deadbeef", project_id="proj-1", checks=[])
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch(
        "app.services.runner_adapter.prepare_workspace_for_run",
        side_effect=WorkspacePreparationError("Failed to fetch repository"),
    ):
        summary = adapter.execute_run(db=db, run=run)
        assert summary.outcome == TestOutcome.ERROR
        assert summary.failure_code == "repository_workspace_failed"
        assert "Failed to fetch repository" in str(summary.error_message)


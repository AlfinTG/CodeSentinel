"""Live integration tests for Cloudflare R2 (S3‑compatible).

These tests are **disabled by default**; they run only when the environment
variable ``RUN_R2_LIVE_TESTS=true`` is present.  All operations are confined
to the ``codesentinel-test/`` prefix inside the bucket defined by the
environment variables ``BACKUP_S3_BUCKET``, ``BACKUP_S3_ACCESS_KEY``,
``BACKUP_S3_SECRET_KEY``, ``BACKUP_S3_ENDPOINT_URL`` (and optionally
``BACKUP_S3_REGION`` and ``BACKUP_S3_TIMEOUT_SECONDS``).

Never commit real credentials – the ``.env`` file is ignored by ``.gitignore``.
"""

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from dotenv import find_dotenv, load_dotenv

from app.services.backup import crypto
from app.services.backup.storage import S3BackupStorage

# Load local .env file if available (never overrides explicit shell environment)
load_dotenv(find_dotenv(usecwd=True))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def storage():
    """Create a configured ``S3BackupStorage`` instance.

    The required environment variables are validated here so the test fails
    early if the configuration is incomplete.
    """
    required = [
        "BACKUP_S3_BUCKET",
        "BACKUP_S3_ACCESS_KEY",
        "BACKUP_S3_SECRET_KEY",
        "BACKUP_S3_ENDPOINT_URL",
    ]
    missing = [v for v in required if not os.getenv(v)]
    assert not missing, f"Missing required env vars for R2 live test: {missing}"

    return S3BackupStorage(
        bucket=os.getenv("BACKUP_S3_BUCKET"),
        access_key_id=os.getenv("BACKUP_S3_ACCESS_KEY"),
        secret_access_key=os.getenv("BACKUP_S3_SECRET_KEY"),
        region=os.getenv("BACKUP_S3_REGION", "us-east-1"),
        endpoint_url=os.getenv("BACKUP_S3_ENDPOINT_URL"),
        timeout_seconds=float(os.getenv("BACKUP_S3_TIMEOUT_SECONDS", "30")),
    )

@pytest.fixture
def test_key():
    """Unique object key inside the dedicated ``codesentinel-test/`` prefix.
    The ``finally`` block in each test ensures it is removed.
    """
    return f"codesentinel-test/{uuid.uuid4()}.dump"

# ----------------------------------------------------------------------
# Live tests – skipped unless RUN_R2_LIVE_TESTS=true
# ----------------------------------------------------------------------
@pytest.mark.skipif(
    os.getenv("RUN_R2_LIVE_TESTS") != "true",
    reason="Live R2 tests disabled – set RUN_R2_LIVE_TESTS=true to enable",
)
def test_single_part_upload_download_and_cleanup(storage, test_key):
    # Create a small temporary file (≈1 MiB) and encrypt it
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "plain.dump"
        src.write_bytes(os.urandom(1_024 * 1024))  # 1 MiB random data

        enc_key = crypto.generate_backup_key()
        enc_path = Path(td) / "plain.dump.enc"
        crypto.encrypt_file(src, enc_path, enc_key)

        local_sha = sha256_file(enc_path)
        # ---- Upload ---------------------------------------------------
        result = storage.upload_file(enc_path, test_key)
        assert result.success, f"Upload failed: {result.error_message}"
        assert result.sha256 == local_sha

        # ---- Download ------------------------------------------------
        download_path = Path(td) / "download.dump.enc"
        try:
            ok = storage.download_file(test_key, download_path)
            assert ok, "Download returned False"
            assert sha256_file(download_path) == local_sha

            # ---- Decrypt & verify content ---------------------------
            dec_path = Path(td) / "download.dump"
            crypto.decrypt_file(download_path, dec_path, enc_key)
            assert dec_path.read_bytes() == src.read_bytes()
        finally:
            # ---- Cleanup ------------------------------------------------
            storage.delete_object(test_key)


@pytest.mark.skipif(
    os.getenv("RUN_R2_LIVE_TESTS") != "true",
    reason="Live R2 tests disabled – set RUN_R2_LIVE_TESTS=true to enable",
)
def test_multipart_upload_download_and_cleanup(storage, test_key):
    # Force multipart upload by using a file larger than the default threshold.
    # We lower the threshold to 2 MiB to keep the test fast.
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "large.dump"
        src.write_bytes(os.urandom(5 * 1024 * 1024))  # 5 MiB random data

        enc_key = crypto.generate_backup_key()
        enc_path = Path(td) / "large.dump.enc"
        crypto.encrypt_file(src, enc_path, enc_key)
        local_sha = sha256_file(enc_path)

        # ---- Multipart upload ---------------------------------------
        result = storage.upload_file(
            enc_path,
            test_key,
            multipart_threshold=2 * 1024 * 1024,  # 2 MiB → forces multipart
            part_size=2 * 1024 * 1024,
        )
        # If the upload fails the test should abort immediately.
        assert result.success, f"Multipart upload failed: {result.error_message}"
        assert result.sha256 == local_sha

        # ---- Download & verify --------------------------------------
        download_path = Path(td) / "download.dump.enc"
        try:
            ok = storage.download_file(test_key, download_path)
            assert ok, "Download returned False"
            assert sha256_file(download_path) == local_sha

            # Decrypt back to original data and compare
            dec_path = Path(td) / "download.dump"
            crypto.decrypt_file(download_path, dec_path, enc_key)
            assert dec_path.read_bytes() == src.read_bytes()
        finally:
            # ---- Cleanup -------------------------------------------
            storage.delete_object(test_key)


@pytest.mark.skipif(
    os.getenv("RUN_R2_LIVE_TESTS") != "true",
    reason="Live R2 tests disabled – set RUN_R2_LIVE_TESTS=true to enable",
)
def test_nonexistent_key_download_returns_false(storage):
    nonexistent_key = f"codesentinel-test/nonexistent-{uuid.uuid4()}.dump"
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "missing.dump"
        ok = storage.download_file(nonexistent_key, dest)
        assert ok is False, "Expected download of nonexistent object to return False"
        assert not dest.exists()


@pytest.mark.skipif(
    os.getenv("RUN_R2_LIVE_TESTS") != "true",
    reason="Live R2 tests disabled – set RUN_R2_LIVE_TESTS=true to enable",
)
def test_explicit_deletion_and_verification(storage, test_key):
    with tempfile.TemporaryDirectory() as td:
        temp_file = Path(td) / "temp.dump"
        temp_file.write_bytes(b"TEMPORARY_OBJECT_TO_DELETE")

        upload_res = storage.upload_file(temp_file, test_key)
        assert upload_res.success is True, f"Failed to upload test object: {upload_res.error_message}"

        # Delete the object and verify success response
        deleted = storage.delete_object(test_key)
        assert deleted is True, f"Failed to delete {test_key}"

        # Verify that downloading the deleted object fails
        verify_path = Path(td) / "after_del.dump"
        download_ok = storage.download_file(test_key, verify_path)
        assert download_ok is False, "Object should not be downloadable after deletion"



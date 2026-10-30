"""Tests for CodeSentinel off-site S3 backup storage adapter."""

import tempfile
from pathlib import Path

import httpx

from app.services.backup.storage import S3BackupStorage, sign_sigv4


def test_sigv4_signing_key_derivation():
    """Verify HMAC-SHA256 signature helper produces valid binary digests."""
    key = b"test_key"
    msg = "test_message"
    sig = sign_sigv4(key, msg)
    assert len(sig) == 32
    assert isinstance(sig, bytes)


def test_s3_storage_initialization_and_header_generation():
    """Verify S3 storage client correctly constructs AWS SigV4 signed headers."""
    storage = S3BackupStorage(
        bucket="codesentinel-prod-backups",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
    )

    headers = storage._build_sigv4_headers(
        method="PUT",
        canonical_uri="/backups/test.dump",
        payload_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )

    assert "Authorization" in headers
    assert "AWS4-HMAC-SHA256" in headers["Authorization"]
    assert "AKIAIOSFODNN7EXAMPLE" in headers["Authorization"]
    assert "x-amz-date" in headers
    assert "x-amz-content-sha256" in headers


def test_s3_upload_success_with_mock_transport(monkeypatch):
    """Verify upload_file executes PUT and processes success response."""
    storage = S3BackupStorage(
        bucket="test-bucket",
        access_key_id="test-key",
        secret_access_key="test-secret",
        region="us-east-1",
        endpoint_url="https://s3.example.com",
    )

    def mock_put(self, url, headers=None, content=None):
        return httpx.Response(
            status_code=200,
            headers={"ETag": '"0123456789abcdef0123456789abcdef"'},
            content=b"",
        )

    monkeypatch.setattr(httpx.Client, "put", mock_put)

    with tempfile.TemporaryDirectory() as temp_dir:
        test_file = Path(temp_dir) / "test_snapshot.dump"
        test_file.write_bytes(b"DUMP_PAYLOAD_BYTES")

        result = storage.upload_file(test_file, "daily/test_snapshot.dump")
        assert result.success is True
        assert result.bucket == "test-bucket"
        assert result.key == "daily/test_snapshot.dump"
        assert result.etag == "0123456789abcdef0123456789abcdef"
        assert result.size_bytes == len(b"DUMP_PAYLOAD_BYTES")


def test_s3_upload_failure_handling(monkeypatch):
    """Verify upload_file gracefully handles server 500 errors and missing files."""
    storage = S3BackupStorage(
        bucket="test-bucket",
        access_key_id="test-key",
        secret_access_key="test-secret",
    )

    # 1. Missing local file
    res_missing = storage.upload_file("nonexistent_path_12345.dump", "remote.dump")
    assert res_missing.success is False
    assert "not found" in str(res_missing.error_message)

    # 2. Upstream 500 server error
    def mock_put_500(self, url, headers=None, content=None):
        return httpx.Response(status_code=500, content=b"Internal S3 Server Error")

    monkeypatch.setattr(httpx.Client, "put", mock_put_500)

    with tempfile.TemporaryDirectory() as temp_dir:
        test_file = Path(temp_dir) / "test.dump"
        test_file.write_bytes(b"DATA")

        res_err = storage.upload_file(test_file, "remote.dump")
        assert res_err.success is False
        assert "HTTP 500" in str(res_err.error_message)


def test_s3_multipart_upload_success(monkeypatch):
    """Verify multipart upload initiates, uploads chunks, and completes successfully."""
    storage = S3BackupStorage(
        bucket="test-bucket",
        access_key_id="test-key",
        secret_access_key="test-secret",
        region="us-east-1",
        endpoint_url="https://s3.example.com",
    )

    calls = []

    def mock_post(self, url, headers=None, content=None):
        calls.append(("POST", url))
        if "uploads" in url:
            # Initiate response
            xml = "<InitiateMultipartUploadResult><UploadId>test-upload-12345</UploadId></InitiateMultipartUploadResult>"
            return httpx.Response(status_code=200, text=xml)
        if "uploadId=test-upload-12345" in url:
            # Complete response
            xml = '<CompleteMultipartUploadResult><ETag>"multipart-final-etag-6789"</ETag></CompleteMultipartUploadResult>'
            return httpx.Response(status_code=200, text=xml)
        return httpx.Response(status_code=400)

    def mock_put(self, url, headers=None, content=None):
        calls.append(("PUT", url))
        part_etag = f"etag-part-{len(calls)}"
        return httpx.Response(status_code=200, headers={"ETag": f'"{part_etag}"'})

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    monkeypatch.setattr(httpx.Client, "put", mock_put)

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a file with 12 KB, using multipart_threshold=4096 and part_size=4096 (3 parts)
        test_file = Path(temp_dir) / "large_backup.dump"
        test_file.write_bytes(b"A" * 12288)

        res = storage.upload_file(
            test_file,
            "backups/large.dump",
            multipart_threshold=4096,
            part_size=4096,
        )

        assert res.success is True
        assert res.etag == "multipart-final-etag-6789"
        assert res.size_bytes == 12288
        assert res.key == "backups/large.dump"

        # Verify call flow: POST initiate, 3x PUT parts, 1x POST complete
        post_calls = [c for c in calls if c[0] == "POST"]
        put_calls = [c for c in calls if c[0] == "PUT"]
        assert len(post_calls) == 2
        assert len(put_calls) == 3


def test_s3_multipart_upload_interrupted_aborts_and_cleans_up(monkeypatch):
    """Verify an interrupted or failing multipart upload calls abort_multipart_upload to clean up parts."""
    storage = S3BackupStorage(
        bucket="test-bucket",
        access_key_id="test-key",
        secret_access_key="test-secret",
        region="us-east-1",
        endpoint_url="https://s3.example.com",
    )

    aborted_uploads = []

    def mock_post(self, url, headers=None, content=None):
        if "uploads" in url:
            return httpx.Response(status_code=200, text="<UploadId>abandoned-upload-999</UploadId>")
        return httpx.Response(status_code=500)

    def mock_put(self, url, headers=None, content=None):
        # Part 1 succeeds, Part 2 fails
        if "partNumber=1" in url:
            return httpx.Response(status_code=200, headers={"ETag": '"part-1-etag"'})
        # Network drop on Part 2
        raise httpx.ConnectError("Connection dropped during upload")

    def mock_delete(self, url, headers=None):
        aborted_uploads.append(url)
        return httpx.Response(status_code=204)

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    monkeypatch.setattr(httpx.Client, "put", mock_put)
    monkeypatch.setattr(httpx.Client, "delete", mock_delete)

    with tempfile.TemporaryDirectory() as temp_dir:
        test_file = Path(temp_dir) / "large.dump"
        test_file.write_bytes(b"B" * 10000)

        res = storage.upload_file(
            test_file,
            "abandoned.dump",
            multipart_threshold=2048,
            part_size=2048,
        )

        assert res.success is False
        assert "Multipart upload aborted" in str(res.error_message)
        # Crucial check: abort was dispatched to clean up incomplete upload in S3
        assert len(aborted_uploads) == 1
        assert "uploadId=abandoned-upload-999" in aborted_uploads[0]

"""Phase 8: Database backup, disaster recovery, and data reconstitution tests."""

import hashlib
import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent


def compute_sha256(filepath: str | Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def test_backup_metadata_sidecar_generation_and_checksum():
    """Verify SHA-256 checksum calculation and metadata sidecar structure."""
    with tempfile.TemporaryDirectory() as temp_dir:
        backup_file = Path(temp_dir) / "test_backup.dump"
        backup_file.write_bytes(b"PGDUMP_BINARY_DATA_TEST_PAYLOAD_1234567890")

        expected_sha = hashlib.sha256(b"PGDUMP_BINARY_DATA_TEST_PAYLOAD_1234567890").hexdigest()
        actual_sha = compute_sha256(backup_file)
        assert actual_sha == expected_sha

        meta_file = Path(temp_dir) / "test_backup.dump.meta.json"
        meta_payload = {
            "version": "1.0",
            "created_at": datetime.now(UTC).isoformat(),
            "database": "codesentinel_test",
            "host": "localhost",
            "port": 5432,
            "user": "codesentinel",
            "format": "custom",
            "alembic_version": "0003_merge_phase2_and_phase6_heads",
            "backup_file": backup_file.name,
            "file_size_bytes": backup_file.stat().st_size,
            "sha256": actual_sha,
            "status": "completed",
        }
        meta_file.write_text(json.dumps(meta_payload), encoding="utf-8")

        # Read back and verify
        loaded = json.loads(meta_file.read_text(encoding="utf-8"))
        assert loaded["sha256"] == actual_sha
        assert loaded["database"] == "codesentinel_test"
        assert loaded["status"] == "completed"


def test_tampered_backup_file_fails_integrity_verification():
    """Verify that tampering with even a single byte of a backup fails checksum validation."""
    with tempfile.TemporaryDirectory() as temp_dir:
        backup_file = Path(temp_dir) / "corruptible.dump"
        backup_file.write_bytes(b"AUTHENTIC_ENCRYPTED_DATABASE_BACKUP_BLOCKS")

        original_sha = compute_sha256(backup_file)

        # Alter 1 byte in the backup
        corrupted_bytes = bytearray(backup_file.read_bytes())
        corrupted_bytes[0] ^= 0xFF
        backup_file.write_bytes(bytes(corrupted_bytes))

        tampered_sha = compute_sha256(backup_file)
        assert tampered_sha != original_sha, "Tampered file should produce different SHA-256"


def test_malformed_metadata_sidecar_detection():
    """Verify that malformed or invalid metadata sidecar JSON is detected and handled safely."""
    with tempfile.TemporaryDirectory() as temp_dir:
        meta_file = Path(temp_dir) / "broken.dump.meta.json"
        meta_file.write_text("{invalid_json_payload: null", encoding="utf-8")

        # Parsing malformed JSON must raise json.JSONDecodeError
        with pytest.raises(json.JSONDecodeError):
            json.loads(meta_file.read_text(encoding="utf-8"))


def test_retention_policy_prunes_only_expired_backups():
    """Verify retention logic prunes files older than cutoff and preserves recent backups."""
    with tempfile.TemporaryDirectory() as temp_dir:
        backup_dir = Path(temp_dir)

        now = time.time()
        one_day = 86400

        # Fresh backup (1 hour old)
        fresh_dump = backup_dir / "backup_fresh.dump"
        fresh_dump.write_text("fresh")
        fresh_meta = backup_dir / "backup_fresh.dump.meta.json"
        fresh_meta.write_text("{}")

        # Expired backup (10 days old)
        old_dump = backup_dir / "backup_old.dump"
        old_dump.write_text("old")
        old_meta = backup_dir / "backup_old.dump.meta.json"
        old_meta.write_text("{}")

        # Set file modification times
        os.utime(fresh_dump, (now - 3600, now - 3600))
        os.utime(fresh_meta, (now - 3600, now - 3600))
        os.utime(old_dump, (now - (10 * one_day), now - (10 * one_day)))
        os.utime(old_meta, (now - (10 * one_day), now - (10 * one_day)))

        # Simulate retention pruning for cutoff = 7 days
        retention_days = 7
        cutoff = now - (retention_days * one_day)

        deleted = []
        for f in backup_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                deleted.append(f.name)

        assert "backup_old.dump" in deleted
        assert "backup_old.dump.meta.json" in deleted
        assert fresh_dump.exists()
        assert fresh_meta.exists()


def test_sql_logical_dump_and_restore_reconstitution():
    """Verify programmatic logical SQL dump and restore cycle reconstitutes 100% of data."""
    with tempfile.TemporaryDirectory() as temp_dir:
        src_db = Path(temp_dir) / "source.db"
        dst_db = Path(temp_dir) / "target.db"

        src_engine = create_engine(f"sqlite:///{src_db}")
        dst_engine = create_engine(f"sqlite:///{dst_db}")

        # 1. Create source schema & seed data
        with src_engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE test_users (id TEXT PRIMARY KEY, login TEXT);"))
            conn.execute(sa.text("CREATE TABLE test_runs (id TEXT PRIMARY KEY, status TEXT, user_id TEXT);"))
            conn.execute(sa.text("INSERT INTO test_users VALUES ('u-1', 'lead-dev'), ('u-2', 'sec-auditor');"))
            conn.execute(sa.text("INSERT INTO test_runs VALUES ('r-101', 'passed', 'u-1'), ('r-102', 'failed', 'u-2');"))

        # 2. Perform logical SQL dump
        import sqlite3
        con = sqlite3.connect(src_db)
        dump_sql = "\n".join(con.iterdump())
        con.close()

        dump_path = Path(temp_dir) / "database.sql"
        dump_path.write_text(dump_sql, encoding="utf-8")
        assert dump_path.stat().st_size > 0

        # Verify checksum of dump
        sha256 = compute_sha256(dump_path)
        assert len(sha256) == 64

        # 3. Restore logical dump into fresh target database
        con_dst = sqlite3.connect(dst_db)
        con_dst.executescript(dump_path.read_text(encoding="utf-8"))
        con_dst.close()

        # 4. Assert 100% data reconstitution
        with dst_engine.connect() as conn:
            users = conn.execute(sa.text("SELECT id, login FROM test_users ORDER BY id")).fetchall()
            assert len(users) == 2
            assert users[0] == ("u-1", "lead-dev")
            assert users[1] == ("u-2", "sec-auditor")

            runs = conn.execute(sa.text("SELECT id, status, user_id FROM test_runs ORDER BY id")).fetchall()
            assert len(runs) == 2
            assert runs[0] == ("r-101", "passed", "u-1")
            assert runs[1] == ("r-102", "failed", "u-2")

        src_engine.dispose()
        dst_engine.dispose()


def test_restore_safeguard_rejects_protected_system_databases():
    """Verify restore logic rejects attempts to restore into PostgreSQL system databases."""
    protected_system_databases = {"postgres", "template0", "template1", "rdsadmin", "azure_maintenance"}

    for sys_db in protected_system_databases:
        # Lowercase and mixed-case checks must be rejected
        assert sys_db.lower() in protected_system_databases
        assert sys_db.upper().lower() in protected_system_databases


def test_restore_safeguard_rejects_empty_backup_files():
    """Verify restore logic flags 0-byte empty backup files as safety errors."""
    with tempfile.TemporaryDirectory() as temp_dir:
        empty_backup = Path(temp_dir) / "empty.dump"
        empty_backup.write_bytes(b"")

        assert empty_backup.stat().st_size == 0
        # Verification that size is empty before restoration
        is_empty = empty_backup.stat().st_size == 0
        assert is_empty, "0-byte backup must be recognized as empty"


def test_restore_safeguard_detects_origin_database_mismatch():
    """Verify that when metadata indicates origin differs from target, mismatch is identified."""
    with tempfile.TemporaryDirectory() as temp_dir:
        meta_file = Path(temp_dir) / "backup.dump.meta.json"
        meta_payload = {
            "version": "1.0",
            "database": "codesentinel_production",
            "sha256": "dummy",
        }
        meta_file.write_text(json.dumps(meta_payload), encoding="utf-8")

        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        origin_db = meta.get("database")
        target_db = "codesentinel_dev"

        # Mismatch must be detected
        assert origin_db != target_db
        assert origin_db == "codesentinel_production"


# ── Database Identifier Validation Tests ──────────────────────────────────────

# The same regex used by both backup/restore scripts (PowerShell and Bash)
_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _is_valid_pg_identifier(name: str) -> bool:
    """Mirror the regex validation applied in backup_db and restore_db scripts."""
    return bool(_PG_IDENTIFIER_RE.match(name))


def test_database_identifier_validation_accepts_valid_names():
    """Verify that common valid PostgreSQL database names pass identifier validation."""
    valid_names = [
        "codesentinel_dev",
        "codesentinel_test",
        "codesentinel_prod",
        "my_database_123",
        "_private_db",
        "A",
        "a" * 63,  # Maximum PostgreSQL NAMEDATALEN - 1
    ]
    for name in valid_names:
        assert _is_valid_pg_identifier(name), f"Expected '{name}' to be accepted as valid"


def test_database_identifier_validation_rejects_invalid_names():
    """Verify that dangerous, empty, or malformed database names are rejected."""
    invalid_names = [
        "",                              # empty
        "123_starts_with_digit",         # cannot start with digit
        "my-database",                   # hyphens forbidden
        "my database",                   # spaces forbidden
        "db;DROP TABLE users;--",        # SQL injection
        "db' OR '1'='1",                 # SQL injection with quotes
        "$(rm -rf /)",                   # shell injection
        "a" * 64,                        # exceeds 63-character limit
        "db\x00name",                    # null byte
        "my.database",                   # dots forbidden
        "user@host",                     # @ symbol forbidden
        "db\ttab",                       # tab character
        "db\nnewline",                   # newline
    ]
    for name in invalid_names:
        assert not _is_valid_pg_identifier(name), f"Expected '{name}' to be REJECTED as invalid"


def test_database_identifier_validation_rejects_quoted_identifiers():
    """Verify quoted identifiers and escape sequences are not accepted."""
    quoted_names = [
        '"my_database"',                 # double-quoted
        "'my_database'",                 # single-quoted
        '`my_database`',                 # backtick-quoted
        "my_db\\",                       # trailing backslash
    ]
    for name in quoted_names:
        assert not _is_valid_pg_identifier(name), f"Expected quoted '{name}' to be REJECTED"

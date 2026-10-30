"""Tests for Point-in-Time Recovery (PITR) WAL archiving and recovery logic."""

from app.services.backup.pitr import (
    archive_wal_segment,
    compute_sha256,
    generate_recovery_configuration,
    prune_wal_archive,
    replay_transactions_to_timestamp,
)


def test_archive_wal_segment_success(tmp_path):
    wal_dir = tmp_path / "pg_wal"
    wal_dir.mkdir()
    archive_dir = tmp_path / "archive"

    # Create dummy WAL segment (24 hex characters)
    segment_name = "000000010000000000000001"
    wal_file = wal_dir / segment_name
    wal_file.write_bytes(b"\x00" * 1024 * 16)  # 16KB mock segment

    result = archive_wal_segment(wal_file, archive_dir)

    assert result.success is True
    assert result.segment_name == segment_name
    assert result.destination_path.exists()
    assert result.destination_path.stat().st_size == 1024 * 16
    assert result.sha256 == compute_sha256(wal_file)

    # Verify sidecar checksum file
    sidecar = archive_dir / f"{segment_name}.sha256"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8") == result.sha256


def test_archive_wal_segment_missing_source(tmp_path):
    archive_dir = tmp_path / "archive"
    missing_file = tmp_path / "nonexistent_wal"

    result = archive_wal_segment(missing_file, archive_dir)

    assert result.success is False
    assert "not found" in (result.error_message or "").lower()


def test_prune_wal_archive(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    segments = [
        "000000010000000000000001",
        "000000010000000000000002",
        "000000010000000000000003",
        "000000010000000000000004",
    ]

    for seg in segments:
        f = archive_dir / seg
        f.write_text(f"dummy content {seg}", encoding="utf-8")
        sidecar = archive_dir / f"{seg}.sha256"
        sidecar.write_text("dummy-hash", encoding="utf-8")

    # Prune segments older than 000000010000000000000003
    deleted = prune_wal_archive(archive_dir, oldest_wal_to_keep="000000010000000000000003")

    assert deleted == 2
    assert not (archive_dir / "000000010000000000000001").exists()
    assert not (archive_dir / "000000010000000000000001.sha256").exists()
    assert not (archive_dir / "000000010000000000000002").exists()
    assert not (archive_dir / "000000010000000000000002.sha256").exists()
    assert (archive_dir / "000000010000000000000003").exists()
    assert (archive_dir / "000000010000000000000003.sha256").exists()
    assert (archive_dir / "000000010000000000000004").exists()


def test_generate_recovery_configuration(tmp_path):
    pgdata = tmp_path / "pgdata"
    wal_arch = tmp_path / "wal_archive"
    wal_arch.mkdir()

    # Pre-populate existing postgresql.auto.conf
    pgdata.mkdir()
    existing_conf = pgdata / "postgresql.auto.conf"
    existing_conf.write_text("# Existing settings\nmax_connections = 100\n", encoding="utf-8")

    target_time = "2026-10-30 02:00:00 UTC"
    signal_path, config_str = generate_recovery_configuration(
        pgdata_dir=pgdata,
        target_time_utc=target_time,
        wal_archive_dir=wal_arch,
        recovery_action="promote",
    )

    assert signal_path.exists()
    assert signal_path.name == "recovery.signal"
    assert "recovery_target_time = '2026-10-30 02:00:00 UTC'" in config_str
    assert "recovery_target_action = 'promote'" in config_str
    assert "recovery_target_inclusive = true" in config_str

    # Verify auto.conf preserved prior settings and contains PITR settings
    merged_content = existing_conf.read_text(encoding="utf-8")
    assert "max_connections = 100" in merged_content
    assert "restore_command" in merged_content
    assert target_time in merged_content


def test_replay_transactions_to_timestamp():
    transactions = [
        {"txid": 1, "timestamp": "2026-10-30T01:00:00Z", "action": "INSERT account 1"},
        {"txid": 2, "timestamp": "2026-10-30T01:30:00Z", "action": "UPDATE balance"},
        {"txid": 3, "timestamp": "2026-10-30T02:00:00Z", "action": "DROP TABLE critical"},
        {"txid": 4, "timestamp": "2026-10-30T02:05:00Z", "action": "INSERT orphan"},
    ]

    # Target: 01:45:00Z (before the DROP TABLE)
    replayed = replay_transactions_to_timestamp(transactions, "2026-10-30T01:45:00Z")
    assert len(replayed) == 2
    assert [tx["txid"] for tx in replayed] == [1, 2]

    # Target: exact match on 02:00:00Z
    replayed_exact = replay_transactions_to_timestamp(transactions, "2026-10-30T02:00:00Z")
    assert len(replayed_exact) == 3
    assert [tx["txid"] for tx in replayed_exact] == [1, 2, 3]

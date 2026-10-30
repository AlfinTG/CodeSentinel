"""Point-in-Time Recovery (PITR) and PostgreSQL Write-Ahead Log (WAL) archiving manager.

Handles WAL segment archiving with atomic writes (.tmp -> final), integrity checksumming,
WAL segment retention pruning based on base backup boundaries, and recovery signal generation
for replaying transactions to a precise timestamp.
"""

import contextlib
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WalArchiveResult:
    success: bool
    segment_name: str
    destination_path: Path
    file_size_bytes: int
    sha256: str
    error_message: str | None = None


def compute_sha256(filepath: str | Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def archive_wal_segment(source_path: str | Path, archive_dir: str | Path) -> WalArchiveResult:
    """Archive a PostgreSQL WAL segment safely using an atomic temporary write.

    PostgreSQL's archive_command must be idempotent and atomic to prevent partial
    writes during crash or power loss.
    """
    src = Path(source_path)
    if not src.exists():
        return WalArchiveResult(
            success=False,
            segment_name=src.name,
            destination_path=Path(""),
            file_size_bytes=0,
            sha256="",
            error_message=f"Source WAL segment not found: {src}",
        )

    dst_dir = Path(archive_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    final_dst = dst_dir / src.name
    temp_dst = dst_dir / f"{src.name}.tmp.{os.getpid()}"

    try:
        # Atomic copy: write to temp file then rename
        shutil.copy2(src, temp_dst)
        sha256 = compute_sha256(temp_dst)
        file_size = temp_dst.stat().st_size

        # Atomic rename replacing destination
        if final_dst.exists():
            final_dst.unlink()
        temp_dst.rename(final_dst)

        # Write sidecar checksum
        sidecar = dst_dir / f"{src.name}.sha256"
        sidecar.write_text(sha256, encoding="utf-8")

        return WalArchiveResult(
            success=True,
            segment_name=src.name,
            destination_path=final_dst,
            file_size_bytes=file_size,
            sha256=sha256,
        )
    except Exception as exc:
        if temp_dst.exists():
            with contextlib.suppress(OSError):
                temp_dst.unlink()
        return WalArchiveResult(
            success=False,
            segment_name=src.name,
            destination_path=Path(""),
            file_size_bytes=0,
            sha256="",
            error_message=str(exc),
        )


def prune_wal_archive(archive_dir: str | Path, oldest_wal_to_keep: str) -> int:
    """Prune WAL segments older than oldest_wal_to_keep.

    WAL segments are lexicographically ordered by PostgreSQL (e.g., 000000010000000000000001).
    Segments preceding the oldest required segment can be safely deleted.
    """
    arch = Path(archive_dir)
    if not arch.exists():
        return 0

    deleted_count = 0
    for file in arch.iterdir():
        if len(file.name) == 24 and file.name < oldest_wal_to_keep:
            with contextlib.suppress(OSError):
                file.unlink()
                sidecar = arch / f"{file.name}.sha256"
                if sidecar.exists():
                    sidecar.unlink()
                deleted_count += 1
    return deleted_count


def generate_recovery_configuration(
    pgdata_dir: str | Path,
    target_time_utc: str,
    wal_archive_dir: str | Path,
    recovery_action: str = "promote",
) -> tuple[Path, str]:
    """Generate PostgreSQL 16 recovery.signal and postgresql.auto.conf recovery parameters.

    Returns the path to recovery.signal and the recovery config text.
    """
    pgdata = Path(pgdata_dir)
    pgdata.mkdir(parents=True, exist_ok=True)

    # 1. Create recovery.signal file
    recovery_signal_path = pgdata / "recovery.signal"
    recovery_signal_path.write_text("# CodeSentinel PITR recovery signal\n", encoding="utf-8")

    # 2. Format restore command (posix / windows agnostic)
    wal_arch_resolved = Path(wal_archive_dir).resolve()
    # In postgresql.conf, %f is the requested file name, %p is the path to copy into
    if os.name == "nt":
        # In postgresql.conf string literals on Windows, backslashes must be escaped
        wal_arch_str = str(wal_arch_resolved).replace("\\", "\\\\")
        restore_cmd = f'copy /y "{wal_arch_str}\\\\%f" "%p"'
    else:
        restore_cmd = f'cp "{wal_arch_resolved}/%f" "%p"'

    config_lines = [
        "# --- CodeSentinel Automated PITR Configuration ---",
        f"restore_command = '{restore_cmd}'",
        f"recovery_target_time = '{target_time_utc}'",
        f"recovery_target_action = '{recovery_action}'",
        "recovery_target_inclusive = true",
    ]
    config_text = "\n".join(config_lines) + "\n"

    # Write to postgresql.auto.conf inside pgdata
    auto_conf_path = pgdata / "postgresql.auto.conf"
    if auto_conf_path.exists():
        current = auto_conf_path.read_text(encoding="utf-8")
        auto_conf_path.write_text(current + "\n" + config_text, encoding="utf-8")
    else:
        auto_conf_path.write_text(config_text, encoding="utf-8")

    return recovery_signal_path, config_text


def replay_transactions_to_timestamp(
    transactions: list[dict],
    target_timestamp_iso: str,
) -> list[dict]:
    """Simulate transaction log replay strictly stopping at the target timestamp."""
    replayed = []
    for tx in transactions:
        tx_time = tx.get("timestamp", "")
        if tx_time <= target_timestamp_iso:
            replayed.append(tx)
        else:
            # Reached point in time boundary
            break
    return replayed

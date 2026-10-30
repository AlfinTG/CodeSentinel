"""Phase 8: Database migration rollback and bidirectional upgrade integrity tests."""

import contextlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_DIR = BACKEND_DIR / "alembic"


@pytest.fixture
def temp_alembic_db() -> Generator[tuple[Config, str, sa.Engine], None, None]:
    """Create an isolated SQLite database file and configured Alembic Config object."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_alembic_")
    os.close(fd)
    db_url = f"sqlite:///{db_path}"

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)

    engine = create_engine(db_url)

    try:
        yield cfg, db_url, engine
    finally:
        engine.dispose()
        if os.path.exists(db_path):
            with contextlib.suppress(OSError):
                os.remove(db_path)


def test_all_migration_revisions_have_reversible_downgrade():
    """Verify that every revision script in alembic/versions defines both upgrade and downgrade functions."""
    versions_dir = ALEMBIC_DIR / "versions"
    assert versions_dir.exists(), f"Versions directory not found: {versions_dir}"

    revision_files = [f for f in versions_dir.glob("*.py") if not f.name.startswith("__")]
    assert len(revision_files) >= 4, f"Expected at least 4 migration scripts, found {len(revision_files)}"

    for rev_file in revision_files:
        content = rev_file.read_text(encoding="utf-8")
        assert "def upgrade()" in content, f"Missing upgrade() function in {rev_file.name}"
        assert "def downgrade()" in content, f"Missing downgrade() function in {rev_file.name}"
        assert "revision:" in content, f"Missing revision identifier in {rev_file.name}"


def test_alembic_full_upgrade_and_downgrade_cycle(temp_alembic_db):
    """Verify full bidirectional cycle: upgrade to head -> downgrade to base -> upgrade to head."""
    cfg, db_url, engine = temp_alembic_db

    # 1. Upgrade cleanly to HEAD
    command.upgrade(cfg, "head")

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    # Verify all expected tables exist at HEAD
    expected_tables = {
        "users",
        "projects",
        "project_check_profiles",
        "github_deliveries",
        "runs",
        "check_runs",
        "evidence_items",
        "analyses",
        "patch_attempts",
        "audit_events",
        "jobs",
        "alembic_version",
    }
    assert expected_tables.issubset(tables), f"Missing tables after upgrade: {expected_tables - tables}"

    # Verify Phase 6 columns exist on patch_attempts
    columns = {col["name"] for col in inspector.get_columns("patch_attempts")}
    assert {"diff_content", "summary", "validation_results"}.issubset(columns)

    # 2. Downgrade from head to 0001_initial_schema
    command.downgrade(cfg, "0001_initial_schema")

    inspector = inspect(engine)
    tables_after_mid_downgrade = set(inspector.get_table_names())

    # 'jobs' table must be dropped
    assert "jobs" not in tables_after_mid_downgrade
    # Core tables must still exist at 0001
    assert "runs" in tables_after_mid_downgrade
    assert "users" in tables_after_mid_downgrade

    # Phase 6 columns on patch_attempts must be dropped
    columns_mid = {col["name"] for col in inspector.get_columns("patch_attempts")}
    assert "diff_content" not in columns_mid
    assert "summary" not in columns_mid
    assert "validation_results" not in columns_mid

    # 3. Downgrade from 0001_initial_schema to base
    command.downgrade(cfg, "base")

    inspector = inspect(engine)
    remaining_tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert remaining_tables == set(), f"Expected all tables to be dropped, found: {remaining_tables}"

    # 4. Re-upgrade from base to head
    command.upgrade(cfg, "head")

    inspector = inspect(engine)
    tables_re_upgraded = set(inspector.get_table_names())
    assert expected_tables.issubset(tables_re_upgraded)


def test_migration_data_persistence_across_schema_upgrades(temp_alembic_db):
    """Verify that existing data in core tables is preserved when migrations are upgraded."""
    cfg, db_url, engine = temp_alembic_db

    # Apply initial schema
    command.upgrade(cfg, "0001_initial_schema")

    # Insert test data at revision 0001
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, github_user_id, login, display_name, created_at, updated_at) "
                "VALUES ('user-1', 12345, 'octocat', 'The Octocat', '2026-10-30T00:00:00Z', '2026-10-30T00:00:00Z')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO projects (id, owner_user_id, github_installation_id, repository_id, "
                "repository_full_name, default_branch, active, created_at, updated_at) "
                "VALUES ('proj-1', 'user-1', 999, 8888, 'octocat/hello-world', 'main', 1, "
                "'2026-10-30T00:00:00Z', '2026-10-30T00:00:00Z')"
            )
        )

    # Upgrade across remaining revisions to HEAD
    command.upgrade(cfg, "head")

    # Verify data survived the upgrade intact
    with engine.connect() as conn:
        user_row = conn.execute(sa.text("SELECT login, display_name FROM users WHERE id = 'user-1'")).fetchone()
        assert user_row is not None
        assert user_row[0] == "octocat"
        assert user_row[1] == "The Octocat"

        proj_row = conn.execute(
            sa.text("SELECT repository_full_name, default_branch FROM projects WHERE id = 'proj-1'")
        ).fetchone()
        assert proj_row is not None
        assert proj_row[0] == "octocat/hello-world"
        assert proj_row[1] == "main"


def test_alembic_single_head_invariant(temp_alembic_db):
    """Verify that Alembic migration history strictly resolves to a single head without divergent branches."""
    from alembic.script import ScriptDirectory

    cfg, _, _ = temp_alembic_db
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()

    assert len(heads) == 1, f"Expected exactly 1 migration head, found multiple: {heads}"
    assert heads[0] == "0003_merge_phase2_and_phase6_heads"


def test_ensure_version_num_length_skips_when_already_64(temp_alembic_db):
    """Verify the version_num widening is idempotent and does not error when column is wide enough."""
    cfg, db_url, engine = temp_alembic_db

    # Upgrade to head to create alembic_version with version_num_length=64
    command.upgrade(cfg, "head")

    # Verify the database is healthy after HEAD migration
    with engine.connect() as conn:
        insp = inspect(conn)
        cols = {c["name"]: c for c in insp.get_columns("alembic_version")}
        assert "version_num" in cols

    # Run downgrade + re-upgrade (exercises _ensure_version_num_length again indirectly)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        insp = inspect(conn)
        tables = set(insp.get_table_names())
        assert "alembic_version" in tables


def test_ensure_version_num_length_is_logged_not_silenced():
    """Verify the env.py ALTER logic logs warnings on failure rather than silently swallowing."""
    # Since env.py can't be imported as a normal module, we verify the code directly.
    env_path = ALEMBIC_DIR / "env.py"
    source = env_path.read_text(encoding="utf-8")

    # The old code had `except Exception:` with `contextlib.suppress(Exception)` and no logging.
    # The new code must log the failure.

    # Verify structure: must have _ensure_version_num_length function
    assert "def _ensure_version_num_length" in source, "Missing _ensure_version_num_length function"

    # Verify it logs on OperationalError instead of silently suppressing
    assert "_logger.warning" in source or "_logger.error" in source, \
        "ALTER failure must be logged, not silently suppressed"

    # Verify it does NOT have a bare `except Exception:` that suppresses the ALTER
    # (the rollback suppress is fine, but the ALTER itself should be caught specifically)
    assert "sa.exc.OperationalError" in source, \
        "ALTER failures should catch OperationalError specifically, not bare Exception"

    # Verify the function is called in run_migrations_online
    assert "_ensure_version_num_length(connection)" in source, \
        "run_migrations_online must call _ensure_version_num_length"

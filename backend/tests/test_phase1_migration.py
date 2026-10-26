"""Migration and metadata contract tests for Phase 1 Gate 1."""

from app.models.base import Base


def test_all_ten_entities_registered_in_metadata():
    """Verify that all 10 domain models defined in DATABASE.md are registered in Base.metadata."""
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
    }

    registered_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(
        registered_tables
    ), f"Missing tables: {expected_tables - registered_tables}"


def test_foreign_key_and_unique_constraints():
    """Verify foreign key relationships and unique constraints exist on core tables."""
    projects_table = Base.metadata.tables["projects"]
    runs_table = Base.metadata.tables["runs"]
    checks_table = Base.metadata.tables["check_runs"]
    analyses_table = Base.metadata.tables["analyses"]

    # Check project -> user FK
    fk_targets = {fk.target_fullname for fk in projects_table.foreign_keys}
    assert "users.id" in fk_targets

    # Check runs -> project FK
    fk_targets = {fk.target_fullname for fk in runs_table.foreign_keys}
    assert "projects.id" in fk_targets

    # Check checks -> run FK
    fk_targets = {fk.target_fullname for fk in checks_table.foreign_keys}
    assert "runs.id" in fk_targets

    # Check analyses -> run FK
    fk_targets = {fk.target_fullname for fk in analyses_table.foreign_keys}
    assert "runs.id" in fk_targets

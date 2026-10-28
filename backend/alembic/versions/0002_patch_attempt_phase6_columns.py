"""Add Phase 6 columns to patch_attempts table.

Revision ID: 0002_patch_attempt_phase6_columns
Revises: 0001_initial_schema
Create Date: 2026-10-28 00:00:00.000000

Adds diff_content (Text), summary (String 500), and validation_results (JSON)
columns that were added to the PatchAttempt model in Phase 6 but were missing
from the initial schema migration (0001).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_patch_attempt_phase6_columns"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patch_attempts",
        sa.Column("diff_content", sa.Text(), nullable=True),
    )
    op.add_column(
        "patch_attempts",
        sa.Column("summary", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "patch_attempts",
        sa.Column("validation_results", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("patch_attempts", "validation_results")
    op.drop_column("patch_attempts", "summary")
    op.drop_column("patch_attempts", "diff_content")


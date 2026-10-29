"""merge_phase2_and_phase6_heads

Revision ID: 0003_merge_phase2_and_phase6_heads
Revises: 0002_patch_attempt_phase6_columns, 0002_phase2_distributed_queue
Create Date: 2026-10-29 21:58:28.300902

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0003_merge_phase2_and_phase6_heads"
down_revision: tuple[str, str] = (
    "0002_patch_attempt_phase6_columns",
    "0002_phase2_distributed_queue",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

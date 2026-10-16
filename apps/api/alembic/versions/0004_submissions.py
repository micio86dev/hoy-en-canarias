"""Visitor submissions awaiting or passing moderation.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE submissions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            status text NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
            reason text,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            decided_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX submissions_status_idx ON submissions (status, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS submissions")

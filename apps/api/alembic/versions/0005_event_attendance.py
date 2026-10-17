"""Guest attendance: one hashed guest identifier per event.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE event_attendances (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            event_id uuid NOT NULL REFERENCES events (id) ON DELETE CASCADE,
            guest_hash text NOT NULL CHECK (length(guest_hash) = 64),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT event_attendances_event_guest_key UNIQUE (event_id, guest_hash)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS event_attendances")

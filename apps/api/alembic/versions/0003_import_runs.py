"""Record scheduled and manual importer runs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE import_runs (
            id bigserial PRIMARY KEY,
            trigger text NOT NULL CHECK (trigger IN ('schedule', 'manual', 'cli')),
            status text NOT NULL CHECK (
                status IN ('running', 'succeeded', 'failed', 'skipped_locked')
            ),
            window_start date NOT NULL,
            window_end date NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            started_at timestamptz NOT NULL,
            finished_at timestamptz,
            duration_ms integer,
            events_inserted integer,
            events_updated integer,
            events_stale integer,
            events_skipped integer,
            error_summary text
        )
        """
    )
    op.execute("CREATE INDEX import_runs_started_idx ON import_runs (started_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS import_runs")

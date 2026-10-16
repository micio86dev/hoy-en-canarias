"""Enable PostgreSQL extensions used by the portal.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# vector: future semantic search (no embeddings written yet).
# pg_trgm: fuzzy title/venue matching. unaccent: accent-insensitive search.
EXTENSIONS: tuple[str, ...] = ("vector", "pg_trgm", "unaccent")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')


def downgrade() -> None:
    for extension in reversed(EXTENSIONS):
        op.execute(f'DROP EXTENSION IF EXISTS "{extension}"')

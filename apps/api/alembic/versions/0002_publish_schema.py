"""Create the published events schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ISLANDS_SQL = (
    "'Tenerife', 'Gran Canaria', 'Lanzarote', 'Fuerteventura', "
    "'La Palma', 'La Gomera', 'El Hierro', 'La Graciosa'"
)
STATUS_SQL = "'scheduled', 'cancelled', 'postponed', 'rescheduled', 'moved_online', 'unknown'"

# unaccent() is STABLE, so generated columns need an IMMUTABLE wrapper. unaccent turns
# guillemets into "<<" and ">>", which the text parser reads as HTML tags and drops; the
# angle brackets are replaced with spaces so the words inside them stay searchable.
UNACCENT_WRAPPER = """
CREATE OR REPLACE FUNCTION hoy_unaccent(input text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT regexp_replace(public.unaccent('public.unaccent', input), '[<>]', ' ', 'g')
$$
"""

EVENTS_TABLE = f"""
CREATE TABLE events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    importer_id text NOT NULL UNIQUE,
    slug text NOT NULL UNIQUE,
    title text NOT NULL,
    description text,
    category text NOT NULL DEFAULT 'other',
    category_raw text,
    tags text[] NOT NULL DEFAULT '{{}}',
    start_at timestamptz,
    end_at timestamptz,
    start_date date,
    end_date date,
    all_day boolean NOT NULL DEFAULT false,
    timezone text NOT NULL DEFAULT 'Atlantic/Canary',
    venue_name text,
    address text,
    municipality text,
    island text CHECK (island IN ({ISLANDS_SQL})),
    latitude double precision CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision CHECK (longitude BETWEEN -180 AND 180),
    price_min numeric(10, 2) CHECK (price_min >= 0),
    price_max numeric(10, 2) CHECK (price_max >= 0),
    currency text,
    is_free boolean,
    booking_url text,
    ticket_url text,
    official_event_url text,
    image_url text,
    organizer_name text,
    organizer_url text,
    status text NOT NULL DEFAULT 'unknown' CHECK (status IN ({STATUS_SQL})),
    stale boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    last_verified_at timestamptz,
    published_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    search_tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', hoy_unaccent(coalesce(title, ''))), 'A') ||
        setweight(to_tsvector('simple', hoy_unaccent(
            coalesce(venue_name, '') || ' ' || coalesce(municipality, ''))), 'B') ||
        setweight(to_tsvector('simple', hoy_unaccent(coalesce(description, ''))), 'C')
    ) STORED,
    CHECK (start_at IS NOT NULL OR start_date IS NOT NULL)
)
"""

EVENT_SOURCES_TABLE = """
CREATE TABLE event_sources (
    id bigserial PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    source_name text NOT NULL,
    source_event_id text NOT NULL,
    source_url text,
    official_url text,
    UNIQUE (event_id, source_name, source_event_id)
)
"""

INDEXES = (
    "CREATE INDEX events_start_at_idx ON events (start_at)",
    "CREATE INDEX events_start_date_idx ON events (start_date)",
    "CREATE INDEX events_island_idx ON events (island)",
    "CREATE INDEX events_category_idx ON events (category)",
    "CREATE INDEX events_search_idx ON events USING gin (search_tsv)",
    "CREATE INDEX events_title_trgm_idx ON events USING gin (title gin_trgm_ops)",
    "CREATE INDEX event_sources_event_idx ON event_sources (event_id)",
)


def upgrade() -> None:
    op.execute(UNACCENT_WRAPPER)
    op.execute(EVENTS_TABLE)
    op.execute(EVENT_SOURCES_TABLE)
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS event_sources")
    op.execute("DROP TABLE IF EXISTS events")
    op.execute("DROP FUNCTION IF EXISTS hoy_unaccent(text)")

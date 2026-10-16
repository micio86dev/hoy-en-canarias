import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

TEST_DATABASE_URL = os.environ.get(
    "HOY_TEST_DATABASE_URL",
    "postgresql+psycopg://hoy:hoy_test_password@localhost:55433/hoy_test",
)


@pytest.fixture(scope="module")
def engine() -> Generator[Engine, None, None]:
    eng = create_engine(TEST_DATABASE_URL)
    try:
        with eng.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"PostgreSQL test database unavailable: {exc}")
    yield eng
    eng.dispose()


@pytest.mark.parametrize("extension", ["vector", "pg_trgm", "unaccent"])
def test_required_extension_is_available(engine: Engine, extension: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"'))
        conn.commit()
        row = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = :name"),
            {"name": extension},
        ).first()
    assert row is not None

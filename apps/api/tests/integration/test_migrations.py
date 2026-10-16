import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests.conftest import TEST_DATABASE_URL, require_test_database_name

API_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def clean_engine() -> Generator[Engine, None, None]:
    require_test_database_name(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect():
            pass
    except Exception as exc:
        if os.environ.get("HOY_SKIP_DB_TESTS") == "1":
            pytest.skip(f"PostgreSQL test database unavailable: {exc}")
        pytest.fail(f"PostgreSQL test database unavailable: {exc}")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def _installed_extensions(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT extname FROM pg_extension")).all()
    return {row[0] for row in rows}


def test_upgrade_from_clean_database_enables_extensions(clean_engine: Engine) -> None:
    command.upgrade(_alembic_config(TEST_DATABASE_URL), "head")

    installed = _installed_extensions(clean_engine)
    assert {"vector", "pg_trgm", "unaccent"} <= installed


def test_downgrade_removes_extensions_and_upgrade_is_repeatable(clean_engine: Engine) -> None:
    config = _alembic_config(TEST_DATABASE_URL)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert not {"vector", "pg_trgm", "unaccent"} & _installed_extensions(clean_engine)

    command.upgrade(config, "head")
    assert {"vector", "pg_trgm", "unaccent"} <= _installed_extensions(clean_engine)

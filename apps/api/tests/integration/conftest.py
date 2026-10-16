from collections.abc import Generator

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import TEST_DATABASE_URL, require_test_database_name
from tests.integration.test_migrations import _alembic_config


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session], None, None]:
    """A session factory over a freshly migrated, empty test schema."""
    require_test_database_name(TEST_DATABASE_URL)
    reset: Engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with reset.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    reset.dispose()
    command.upgrade(_alembic_config(TEST_DATABASE_URL), "head")
    engine = create_engine(TEST_DATABASE_URL)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()

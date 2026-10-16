import os

import pytest

TEST_DATABASE_URL = os.environ.get(
    "HOY_TEST_DATABASE_URL",
    "postgresql+psycopg://hoy:hoy_test_password@localhost:55433/hoy_test",
)


def require_test_database_name(url: str) -> None:
    """Refuse to run destructive tests against anything but a database named *_test."""
    database = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not database.endswith("_test"):
        pytest.fail(f"Refusing destructive tests on non-test database {database!r}")

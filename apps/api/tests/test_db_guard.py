import pytest

from tests.conftest import require_test_database_name


def test_guard_accepts_test_database() -> None:
    require_test_database_name("postgresql+psycopg://hoy:pw@localhost:55433/hoy_test")
    require_test_database_name(
        "postgresql+psycopg://hoy:pw@localhost:55433/hoy_test?sslmode=disable"
    )


def test_guard_refuses_non_test_database() -> None:
    with pytest.raises(pytest.fail.Exception):
        require_test_database_name("postgresql+psycopg://hoy:pw@localhost:5432/hoy")

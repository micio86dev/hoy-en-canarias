import os

TEST_DATABASE_URL = os.environ.get(
    "HOY_TEST_DATABASE_URL",
    "postgresql+psycopg://hoy:hoy_test_password@localhost:55433/hoy_test",
)

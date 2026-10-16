// Shared E2E settings. The database is the disposable Postgres from docker compose's "test" profile.
export const API_PORT = 8766
export const WEB_PORT = 3210
export const E2E_DATABASE_URL = 'postgresql+psycopg://hoy:hoy_test_password@127.0.0.1:55433/hoy_test'
export const E2E_BASE_URL = `http://127.0.0.1:${WEB_PORT}`

import { defineConfig, devices } from '@playwright/test'
import { API_PORT, E2E_BASE_URL, E2E_DATABASE_URL, WEB_PORT } from './tests/e2e/env'

// The API resets and seeds its disposable test database before it starts, so no test
// runs against a half-prepared schema. The web app serves the production build.
export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: E2E_BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: [
    {
      command: `uv run --package hoy-api python scripts/e2e_prepare.py && uv run --package hoy-api uvicorn hoy_api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: '../api',
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { HOY_DATABASE_URL: E2E_DATABASE_URL, HOY_RATE_LIMIT_PER_MINUTE: '6000' },
    },
    {
      command: 'node .output/server/index.mjs',
      url: `${E2E_BASE_URL}/`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { PORT: String(WEB_PORT), NUXT_API_BASE: `http://127.0.0.1:${API_PORT}` },
    },
  ],
})

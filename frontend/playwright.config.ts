import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  workers: 1,
  timeout: 40000,
  use: {
    baseURL: 'http://127.0.0.1:18765',
    browserName: 'chromium',
    launchOptions: process.env.SLEEPY_CHROMIUM
      ? { executablePath: process.env.SLEEPY_CHROMIUM }
      : {},
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'uv run python tests/e2e_server.py --port 18765',
    url: 'http://127.0.0.1:18765',
    reuseExistingServer: false,
    timeout: 30000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10000 },
  },
})

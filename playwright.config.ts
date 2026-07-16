import { defineConfig, devices } from '@playwright/test';

const port = 8765;
const baseURL = `http://127.0.0.1:${port}`;

function project(name: string, colorScheme: 'light' | 'dark', mobile = false) {
  return {
    name,
    use: {
      ...(mobile
        ? {
            ...devices['Pixel 7'],
            viewport: { width: 390, height: 844 },
          }
        : {
            browserName: 'chromium' as const,
            viewport: { width: 1440, height: 1000 },
          }),
      baseURL,
      colorScheme,
      locale: 'en-US',
      timezoneId: 'UTC',
      reducedMotion: 'reduce' as const,
    },
  };
}

export default defineConfig({
  testDir: './tests/ui',
  outputDir: 'test-results',
  workers: 1,
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      maxDiffPixelRatio: 0.01,
    },
  },
  use: {
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `.venv\\Scripts\\python.exe tests\\ui\\server.py --port ${port}`,
    url: `${baseURL}/newsletter/`,
    timeout: 60_000,
    reuseExistingServer: false,
    stdout: 'pipe',
    stderr: 'pipe',
  },
  projects: [
    project('desktop-light', 'light'),
    project('desktop-dark', 'dark'),
    project('mobile-light', 'light', true),
    project('mobile-dark', 'dark', true),
  ],
});
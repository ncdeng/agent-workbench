import { defineConfig, devices } from '@playwright/test';

// Ensure localhost bypasses any active HTTP proxy (e.g. Clash on :7890).
// Without this, Playwright's webServer readiness probe gets a proxy 502 for
// http://127.0.0.1:5173, mistakes it for a live server, skips starting Vite,
// and the browser then hits ERR_CONNECTION_REFUSED. Forcing NO_PROXY here makes
// the e2e run self-contained regardless of the shell's proxy env.
const NO_PROXY_HOSTS = '127.0.0.1,localhost';
const mergeNoProxy = (existing?: string) => {
  const parts = new Set((existing ?? '').split(',').map((s) => s.trim()).filter(Boolean));
  for (const host of NO_PROXY_HOSTS.split(',')) parts.add(host);
  return Array.from(parts).join(',');
};
process.env.NO_PROXY = mergeNoProxy(process.env.NO_PROXY);
process.env.no_proxy = mergeNoProxy(process.env.no_proxy);

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    stdout: 'pipe',
    stderr: 'pipe',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        ...(process.env.PW_USE_SYSTEM_CHROME === '1' ? { channel: 'chrome' as const } : {}),
      },
    },
  ],
});

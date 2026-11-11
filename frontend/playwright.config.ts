import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8001;
const WEB_PORT = 3100;

/** Test-only values for the throwaway e2e database; never real secrets. */
const E2E_API_ENV = {
  JWT_SECRET: "e2e-only-jwt-secret-not-a-real-secret-0123456789",
  DEID_HMAC_KEY: "e2e-only-deid-key-not-a-real-secret-0123456789",
  REFRESH_COOKIE_PATH: "/api/auth",
  TRUSTED_PROXY_IPS: "127.0.0.1",
  COOKIE_SECURE: "true",
};

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1, // one shared database; review tests change it
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://localhost:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Rebuilds the e2e database from the synthetic generator, then serves the API on it.
      command: `uv run python backend/tests/e2e_seed.py --serve ${API_PORT}`,
      cwd: "..",
      url: `http://127.0.0.1:${API_PORT}/health`,
      env: E2E_API_ENV,
      timeout: 300_000,
      reuseExistingServer: false,
      stdout: "pipe",
    },
    {
      command: `pnpm build && pnpm start -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/login`,
      env: { BACKEND_URL: `http://127.0.0.1:${API_PORT}` },
      timeout: 300_000,
      reuseExistingServer: false, // always test a fresh build
    },
  ],
});

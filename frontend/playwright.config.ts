import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  retries: 0,
  // E2E projects share the seeded demo accounts, and the API rate limit is
  // enforced per user. Keep browser workflows serial to avoid test cross-talk.
  workers: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"] },
    },
  ],
});

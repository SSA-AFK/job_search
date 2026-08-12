import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5174",
    trace: "on-first-retry",
  },
  webServer: [
    {
      command: "npm run test:e2e:backend",
      url: "http://127.0.0.1:8011/api/v1/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5174 --strictPort",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [
    {
      name: "desktop",
      testIgnore: /seeded-flow\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile",
      testIgnore: /seeded-flow\.spec\.ts/,
      use: { ...devices["iPhone 13"], viewport: { width: 390, height: 844 } },
    },
    {
      name: "seeded",
      testMatch: /seeded-flow\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
});

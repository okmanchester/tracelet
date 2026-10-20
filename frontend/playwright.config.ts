import { defineConfig } from "@playwright/test";

for (const key of ["TRACELET_PILOT_REPORT_DIR", "TRACELET_PILOT_FRONT_URL", "TRACELET_PILOT_CONTROL_URL",
  "TRACELET_PILOT_CONTROL_SECRET", "TRACELET_PILOT_LOGIN_PASSWORD"]) {
  if (!process.env[key]) throw new Error("浏览器准入须通过 python -m scripts.verify_pilot 启动独立环境");
}

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  retries: 0,
  timeout: 360_000,
  expect: { timeout: 15_000 },
  forbidOnly: true,
  outputDir: process.env.TRACELET_PILOT_REPORT_DIR + "/browser",
  reporter: [["line"], ["junit", { outputFile: process.env.TRACELET_PILOT_REPORT_DIR + "/browser.xml" }]],
  use: {
    baseURL: process.env.TRACELET_PILOT_FRONT_URL,
    browserName: "chromium",
    trace: "off",
    screenshot: "off",
    video: "off",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
});

/**
 * e2e консоли. API подменяется в браузере (см. e2e/api.ts): сценарии
 * проверяют, что экраны и визарды доводят человека до цели, а не что
 * оркестратор считает деньги — это делают pytest-тесты бэкенда.
 *
 * Запуск: `npm run e2e` (поднимет vite сам) или `npm run e2e -- --ui`.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = 5177;

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    locale: "ru-RU",
    colorScheme: "light",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `npm run dev -w @looma/console -- --port ${PORT} --strictPort`,
    port: PORT,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});

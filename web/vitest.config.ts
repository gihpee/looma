import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["packages/**/*.test.{ts,tsx}", "apps/**/*.test.{ts,tsx}"],
    setupFiles: ["./vitest.setup.ts"],
    css: false,
  },
});

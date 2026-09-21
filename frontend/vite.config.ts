import { defineConfig } from "vitest/config";

export default defineConfig({
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});

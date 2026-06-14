import { defineConfig } from "vitest/config";

// Standalone Vitest config so vite.config.ts keeps pure Vite types: the project's Vite and
// Vitest's bundled Vite are distinct instances, and sharing one `defineConfig` clashes on
// plugin types. 5a unit tests are logic-only (store / stream / contract), so no React plugin
// is needed here; rendered-UI checks run under Playwright (e2e/).
export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Vitest owns logic tests under src/; the e2e/ Playwright specs (which import
    // @playwright/test) are run by `npm run e2e`, never collected here.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});

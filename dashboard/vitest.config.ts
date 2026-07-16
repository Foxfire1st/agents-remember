import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Standalone Vitest config so vite.config.ts keeps pure Vite types: the project's Vite and
// Vitest's bundled Vite are distinct instances, and sharing one `defineConfig` clashes on
// plugin types. 5a unit tests are logic-only (store / stream / contract), so no React plugin
// is needed here; rendered-UI checks run under Playwright (e2e/).
export default defineConfig({
  resolve: {
    alias: {
      // Under vitest, the package's `node` export condition resolves to an "edge-light" build
      // that skips layout effects — panels then never receive their initial layout and the
      // imperative collapse/expand API asserts. Alias to the browser development build: the
      // code path the real app runs (FEUI-L1: the sessions view drives collapse via commands).
      "react-resizable-panels": fileURLToPath(
        new URL(
          "./node_modules/react-resizable-panels/dist/react-resizable-panels.browser.development.js",
          import.meta.url,
        ),
      ),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Vitest owns logic tests under src/; the e2e/ Playwright specs (which import
    // @playwright/test) are run by `npm run e2e`, never collected here.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});

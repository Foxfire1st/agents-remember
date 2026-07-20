import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "styled-system", "styled-system-studio"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
  },
  {
    files: ["*.config.ts", "*.config.cjs", "e2e/**/*.ts"],
    languageOptions: { ecmaVersion: 2022, globals: globals.node },
  },
  {
    // e2e benchmark scripts run node-side but drive page.evaluate bodies that reference `window`.
    files: ["e2e/**/*.mjs"],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.node, ...globals.browser } },
  },
);

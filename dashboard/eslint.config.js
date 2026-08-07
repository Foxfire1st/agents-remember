import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import importPlugin from "eslint-plugin-import";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "styled-system", "styled-system-studio"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  jsxA11y.flatConfigs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    settings: {
      // eslint-plugin-import parses TS itself via the project parser and resolves relative
      // imports with the node resolver; without these, import/no-cycle never sees a graph.
      "import/parsers": { "@typescript-eslint/parser": [".ts", ".tsx"] },
      "import/resolver": {
        typescript: { project: "tsconfig.json" },
        node: true,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      import: importPlugin,
    },
    rules: {
      // The rail's first rule set, landed alone: hook extraction is only safe when every
      // dependency array moves across a function boundary under these two checks.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      "import/no-cycle": "error",
      complexity: ["error", { max: 10 }],
      // The repository File Size Budget, enforced by ESLint at the same 1,200-line hard limit as
      // the Python detector. No baseline, grandfathering, or per-file exemption list.
      "max-lines": ["error", { max: 1200, skipBlankLines: true, skipComments: true }],
      "max-lines-per-function": [
        "error",
        { max: 80, skipBlankLines: true, skipComments: true },
      ],
    },
  },
  {
    // Tests are behavior scripts, not shipped units: they stay file-size-gated by max-lines
    // (R3/R4) but are not re-factored into helper units just to satisfy per-function limits.
    files: ["src/**/*.test.{ts,tsx}", "src/test/**/*.{ts,tsx}"],
    rules: {
      "max-lines-per-function": "off",
      complexity: "off",
    },
  },
  {
    files: ["*.config.ts", "*.config.cjs", "e2e/**/*.ts", "scripts/**/*.mjs"],
    languageOptions: { ecmaVersion: 2022, globals: globals.node },
  },
  {
    // e2e benchmark scripts run node-side but drive page.evaluate bodies that reference `window`.
    files: ["e2e/**/*.mjs"],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.node, ...globals.browser } },
  },
);

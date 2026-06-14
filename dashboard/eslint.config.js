import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
  },
  {
    files: ["*.config.ts", "e2e/**/*.ts"],
    languageOptions: { ecmaVersion: 2022, globals: globals.node },
  },
);

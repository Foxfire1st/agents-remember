# Install Guides

These pages explain how to copy the Agents Remember starter package for each
agent harness.

The normal first-run path is:

1. Copy the harness-native package files from this repo into your workspace.
2. Replace every placeholder, including `<PATH/TO/YOUR/PROJECTS_FOLDER>` and
   `<YOUR_REPOSITORY_FOLDER_NAME>`.
3. Register the Agents Remember MCP server, usually:

   ```text
   uvx agents-remember-mcp@latest --config <absolute path to agents-remember-settings.json>
   ```

4. Restart the harness once.
5. Invoke `c-13-install-and-onboard`; it runs or verifies `runtime_install()` and
   then handles memory, onboarding, and providers.

Initial skills and hooks/rules/instructions come from the copied package. Do not
run `skills_install()` for first-run setup; that MCP tool remains available for
manual maintenance and non-package installs.

Then choose the guide for your tool:

- [Codex](codex.md)
- [Claude Code](claude-code.md)
- [Cursor](cursor.md)
- [Antigravity](antigravity.md)
- [VS Code + GitHub Copilot](vscode-copilot.md)
- [Hermes.md](hermes.md)
- [Pi.dev](pi.md)
- [OpenClaw](openclaw.md)

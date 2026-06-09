# Install Guides

These pages explain how to copy the Agents Remember starter package for each
agent harness.

The normal first-run path is:

1. Copy the harness-native package files from this repo into your workspace.
2. Render the copied package. The `render-starter` script is a convenience for
   replacing placeholders in the copied files: it infers the workspace root from
   the copied harness folder, fills repository names and hook/context commands,
   and validates that each requested repository folder exists. Pass every
   repository folder name after one `--repo`, for example:

   ```text
   python .codex/render-starter.py --repo my-repo shared-lib
   ```

   On POSIX systems you can use `render-starter.sh`; on native Windows you can
   use `render-starter.ps1`.

   If you prefer not to run the renderer, manually replace every placeholder in
   the copied package and validate that no `<PATH/TO/YOUR/PROJECTS_FOLDER>`,
   `<YOUR_REPOSITORY_FOLDER_NAME>`, or hook-command placeholder remains.
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

**Native Windows note:** enable long paths before working with worktree-backed
tasks — worktree folders nest deeply enough that repos with deep trees exceed
the legacy 260-character `MAX_PATH`, which breaks Python tooling, pip installs,
and test fixtures mid-task. Run in an elevated PowerShell, then restart your
harness:

```text
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -Type DWord
git config --global core.longpaths true
```

`worktree_start` checks this up front and refuses with the projected path
length when the host cannot represent the worktree's deepest files. WSL is not
affected.

Then choose the guide for your tool:

- [Codex](codex.md)
- [Claude Code](claude-code.md)
- [Cursor](cursor.md)
- [Antigravity](antigravity.md)
- [VS Code + GitHub Copilot](vscode-copilot.md)
- [Hermes.md](hermes.md)
- [Pi.dev](pi.md)
- [OpenClaw](openclaw.md)

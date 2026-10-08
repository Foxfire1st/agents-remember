# Install Guides

These pages explain how to copy the Agents Remember starter package for each
agent harness.

The normal first-run path is:

1. Copy the harness-native package files from this repo into your workspace.
2. Render the copied package. The `render-starter` script is a convenience for
   replacing placeholders in the copied files: it infers the workspace root from
   the copied harness folder, fills repository names and hook/context commands,
   and validates that each requested repository folder exists. It also creates
   the one shared host settings file when absent; manual placeholder replacement
   needs the complete shared block described in the install skill. Pass every
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
5. Invoke `c-13-install-and-onboard`; it runs or verifies `runtime_install()`, which
   provisions the build-pinned Paseo host and its plugin on the product's checked
   Node, then handles memory, onboarding, and providers. No separate host command
   is needed during install.
6. Start both with the same package installation and actual MCP config file.
   For the uvx registration above, take the served version from server_info and run:

   ```text
   uvx --from "agents-remember-mcp==<served-version>" agents-remember dashboard --daemon --config "<absolute active MCP settings file>"
   ```

   Replace both placeholders; a local-wheel registration uses that exact wheel
   for --from. [uv's tool guide](https://docs.astral.sh/uv/guides/tools/#commands-with-different-package-names)
   documents this separate executable name. uvx does not place a bare command on
   PATH. A uv tool-installed package can use its agents-remember executable with
   the same explicit --config. Repeat this one start after a reboot. A start
   uses the installed host and never installs or upgrades. Dashboard stop/restart
   leaves the host and agent sessions running; `paseo stop` is explicit and ends
   running turns and permission prompts.

The renderer creates one shared `<coordinationRoot>/system/settings.json`
`paseoRuntime` block for all harnesses, with absolute data/state paths and fresh
listen `127.0.0.1:8766`; it never overwrites an existing file. The host currently
supports Linux x86_64 with `/proc` only. The build carries one exact host, a whole
npm lock, and Node 22.23.2 plus its official archive digest. A missing shared block
is reported as configured=false by install. During approved setup, add the
complete five-key block from c-13 to that one shared file, preserving other
families; retry runtime_install afterwards. Existing per-harness blocks
are ignored with a migration notice; move one to the shared file, preserving
explicit listen/home/prefix and other settings. The renderer also accepts
--coordination-root, --host-port and --dashboard-port; omitting them retains
workspace/ar-coordination, 8766 and 8765. Sandbox tooling supplies its recorded
root and two ports through that public invocation and does not edit the rendered
file. The developer chooses the real
settings migration and any terminal provision needed to switch a running host
from another Node. `runtime_install` only reads this authority and defers a restart.

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

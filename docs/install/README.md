# Install Guides

One path starts Agents Remember on every harness: register the MCP tool server, then make one
tool call. The call prepares the runtime scaffold, provisions and starts the Paseo host, starts
one instructed Bootstrap seat, and returns that seat's dashboard address. Setup then continues in
the dashboard.

Nothing is copied into a harness: no skill folder, no starter package, no rendered settings. The
tool server serves the canonical skills to a harness as MCP resources (MIK-R196), so the skills a
session reads belong to exactly one installed build.

The steps, once per workspace:

1. **Write the MCP authority settings.** Copy `examples/mcp/settings.example.json` to a path of
   your own and replace its paths with absolute ones: `coordinationRoot` (the installation's
   coordination folder — it may not exist yet), `workspaceRoot`, `transcriptRoot`, and one entry
   per code repository under `repositories`. The server reads this file at boot and resolves
   workspace paths from it, not from where a directory happens to sit.
2. **Register the MCP server** in your harness's own MCP configuration, pointing `--config` at
   that file:

   ```text
   uvx agents-remember-mcp@latest --config <absolute path to your settings file>
   ```

3. **Restart or reload the harness once** so it starts the server.
4. **Make one call** in a chat of that harness, naming no task, sprint or repository:

   ```text
   role_start(role="bootstrap", request_id="<a UUID you generate>")
   ```

   Bootstrap is parentless and taskless: it invents no sprint, master, leaf, task document or
   worktree. The call creates the coordination scaffold when the configured root does not exist
   yet, provisions and starts the host when it is not running, verifies the Agents Remember
   plug-in, and starts one seat with its compiled instructions. Its answer names that seat and the
   dashboard address of its chat. A retry with the same `request_id` reconciles to the same seat.
5. **Continue setup in the dashboard.** Choose the repository and the memory root, initialize or
   read the memory baseline, adopt its content after review, and choose the harness each role
   should use. Harness-only choices are valid: leave model and effort unset and the harness picks
   its own defaults. Bootstrap writes the user settings file
   (`~/.config/agents-remember/settings.json`, or under `XDG_CONFIG_HOME`) during installation, and
   it reports an existing file instead of overwriting it.
6. **Start the Architect.** Bootstrap hands the knowledge foundation to you: it tells you to start
   the Architect, which organises memory and knowledge bootstrapping as ordinary task, master or
   sprint work. Bootstrap authors no onboarding and no knowledge, and it starts no role.

The skills themselves — `c-00-initialize-memory-repo` to `c-14-knowledge-bootstrap`,
`l-01-agent-lifecycles` and `w-02-light-task-workflow` — are served by the server. A harness that
reads MCP resources finds them without any install step, and no project or home skill folder has to
be kept in sync.

## Host notes

The build-pinned Paseo host supports Linux x86_64 with `/proc`. The build carries one exact host,
a whole npm lock, and Node 26.11.1 plus its official archive digest. The one call prepares a
missing `paseoRuntime` block in the active MCP authority settings file with absolute data/state paths
and listen `127.0.0.1:8766`; an existing block is read as it stands, never rewritten. A host whose
settings are invalid, or a host the call may not touch because it holds live sessions, is reported
with the stage that failed and the daemon state it left behind — never as a successful launch.

**Native Windows note:** enable long paths before working with worktree-backed tasks — worktree
folders nest deeply enough that repos with deep trees exceed the legacy 260-character `MAX_PATH`,
which breaks Python tooling, pip installs, and test fixtures mid-task. Run in an elevated
PowerShell, then restart your harness:

```text
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -Type DWord
git config --global core.longpaths true
```

`worktree_start` checks this up front and refuses with the projected path length when the host
cannot represent the worktree's deepest files. WSL is not affected.

## Optional per-harness instruction files

Each guide below names the instruction file, hook or rule that makes a harness's free chat load the
Agents Remember first-action notice at session start. Those files are conveniences for a chat the
product did not start: they carry a directive and nothing else, they hold no skill folder, and
copying one is optional. Replace their `<PATH/TO/YOUR/PROJECTS_FOLDER>` markers with your absolute
path by hand; there is no renderer.

Then choose the guide for your tool:

- [Codex](codex.md)
- [Claude Code](claude-code.md)
- [Cursor](cursor.md)
- [Antigravity](antigravity.md)
- [VS Code + GitHub Copilot](vscode-copilot.md)
- [Hermes.md](hermes.md)
- [Pi.dev](pi.md)
- [OpenClaw](openclaw.md)

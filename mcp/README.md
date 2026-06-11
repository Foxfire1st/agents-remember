# Agents Remember MCP

`agents-remember-mcp` is the installable Model Context Protocol server for
Agents Remember. It lets an MCP-capable coding harness call Agents Remember
operations from the host instead of asking the model to edit or execute
coordinator scripts directly.

Source: [github.com/Foxfire1st/agents-remember-md](https://github.com/Foxfire1st/agents-remember-md)

## Quickstart

Setup is agent-driven. Ask your agent to:

1. **Copy the harness package** — Pick your harness guide under
   [docs/install](https://github.com/Foxfire1st/agents-remember-md/tree/main/docs/install),
   copy that harness's native starter files from the source repo into the
   workspace, then render the copied package. The `render-starter` script is a
   convenience: it infers the workspace root from the copied harness folder and
   fills the copied package's path, repository, and hook-command placeholders
   from a single `--repo` list such as `--repo my-app shared-lib`. You can also
   do those replacements by hand. These packages include the harness-visible skills,
   hooks/rules/instructions, MCP settings templates, and local renderers.
2. **Wire the MCP server** — Register Agents Remember MCP with `uvx` and the
   copied settings file:

   ```text
   uvx agents-remember-mcp@latest --config /absolute/path/to/agents-remember-settings.json
   ```

   Use the `agents-remember-settings.json` path from the copied harness package.
   Then **restart the harness once** so it loads the MCP server, native skills,
   and package hooks/rules/instructions.
3. **Onboard your project** — Invoke the copied skill
   `c-13-install-and-onboard`. It runs or verifies `runtime_install()`, asks
   whether to scaffold a new memory repo or use an existing one, bootstraps
   onboarding when needed, and starts provider indexing when providers are
   enabled.

That is the normal first-run path. `skills_install()` remains available as a
maintenance/manual MCP tool, but the starter packages already provide the
initial skills and harness files. Your only required first-run restart is after
copying the harness package and wiring the MCP server.

## Requirements

- Python **3.11 or newer** (the package declares `requires-python >=3.11`; on a
  multi-version host, `uvx` selects a compatible interpreter automatically).
- an MCP-capable coding harness
- [uv](https://docs.astral.sh/uv/) (for `uvx`) or pip
- Git for repository and memory ledger operations (configure `user.name` /
  `user.email`; without them, memory/worktree commits fall back to a placeholder
  identity so work can proceed).
- Docker (running) when provider tools are enabled. The grepai embedder runs
  Ollama as a Docker container and pulls an embedding model (`nomic-embed-text`)
  on first setup — no host Ollama install is required.

Claude Code hooks do not require `jq`. Older starter packages used a `jq`
one-liner to encode hook output; the current starter packages use Python
renderers and Python hook scripts.

## Install And Run

The simplest path is `uvx`, which fetches and runs the server on demand — no
manual virtualenv or PATH setup:

```text
uvx agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
```

Or install with pip and use the console command:

```text
python -m pip install agents-remember-mcp
agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
```

The config path must be **absolute**, the settings file must live **outside the
`ar-coordination/` runtime folder**, and it should live **under your harness's
registration folder in an `mcp/` subdirectory** (see
[Settings file location](#settings-file-location)). The server reads this file
**only at startup** — if you edit it later (enable providers, add repos, change
`timeoutCaps`), **restart the harness** for the change to take effect; run
`server_info()` to confirm what the running server actually loaded.

## Settings

A minimal starter `agents-remember-settings.json` (your agent can fill this in):

```json
{
  "version": 1,
  "coordinationRoot": "/absolute/path/to/ar-coordination",
  "workspaceRoot": "/absolute/path/to/workspace",
  "repositories": {
    "<your-repo-name>": {}
  },
  "providers": {
    "codegraphcontext-code": {},
    "grepai-memory": {}
  }
}
```

`coordinationRoot` is where the runtime and memory repos live (populated by
the `runtime_install` MCP tool); **default it to `<workspace>/ar-coordination/`** — inside the
workspace, never the user's home directory. `workspaceRoot` is the workspace itself (it holds your
code repos). The `c-13-install-and-onboard` skill treats the workspace as the first assumption for
every install location and shows each resolved default for you to accept or override, so placement
is never silent or guessed. List each repo you
want Agents Remember to manage under `repositories`. Omit or empty the
`providers` block if you do not want the Docker-backed providers. Full field
reference:
[settings-json.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/reference/settings-json.md).

> **Upgrading?** `timeoutCaps.providerSeconds` was renamed to
> `providerSetupSeconds`. The old key is rejected with a fail-loud `ConfigError`
> at startup, so rename it in any existing settings file. `providerSetupSeconds`
> caps only provider **image build / dependency install**; indexing and database
> seed/clone are never time-capped. A cap value of `0` means unlimited.

### Settings file location

Place the settings file where the copied starter package expects it. Keep it
under the harness registration folder, not loose in the workspace root and not
inside `ar-coordination/`.

| Harness | Starter package | Settings path after copy |
| --- | --- | --- |
| Claude Code | `.claude/` | `.claude/mcp/agents-remember-settings.json` |
| Codex | `.codex/` | `.codex/mcp/agents-remember-settings.json` |
| Cursor | `.cursor/` | `.cursor/mcp/agents-remember-settings.json` |
| Antigravity | `.agents/` | `.agents/mcp/agents-remember-settings.json` |
| VS Code + Copilot | `.github-vscode/` + `.vscode/` | `.vscode/mcp/agents-remember-settings.json` |
| Hermes | `.hermes/` | `.hermes/mcp/agents-remember-settings.json` |
| Pi.dev | `.pi/` | `.pi/mcp/agents-remember-settings.json` |
| OpenClaw | `.openclaw/` | `.openclaw/mcp/agents-remember-settings.json` |

See your harness page under
[docs/install/](https://github.com/Foxfire1st/agents-remember-md/tree/main/docs/install)
for the exact registration folder.

## Harness Setup

Register the MCP server with your harness by pointing it at `uvx` (or the
installed console command) and the absolute settings path:

```json
{
  "command": "uvx",
  "args": [
    "agents-remember-mcp",
    "--config",
    "/absolute/path/to/agents-remember-settings.json"
  ]
}
```

After installing or changing the MCP server registration, restart the harness so
it reloads the server and discovers the tool list. Use the starter package for
your harness whenever possible; it already carries the matching skills,
hooks/rules/instructions, and settings template.

### Per-harness setup pages

Harnesses differ in where settings and skills go — so follow your harness's
page, don't guess:

| Harness | Setup guide |
| --- | --- |
| Claude Code | [docs/install/claude-code.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/install/claude-code.md) |
| Codex | [docs/install/codex.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/install/codex.md) |
| Cursor | [docs/install/cursor.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/install/cursor.md) |
| Antigravity | [docs/install/antigravity.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/install/antigravity.md) |
| VS Code + Copilot | [docs/install/vscode-copilot.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/install/vscode-copilot.md) |
| Hermes | [docs/install/hermes.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/install/hermes.md) |
| Pi.dev | [docs/install/pi.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/install/pi.md) |
| OpenClaw | [docs/install/openclaw.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/install/openclaw.md) |

**One flat folder per skill.** The copied starter package already includes the
skills in the harness-native skill root. `skills_install()` remains available for
manual maintenance and non-package setups; it copies packaged skills into a
skill root as `<skill-root>/<name>/` (matching the skill's lowercase frontmatter
`name`).

## Install Order And First Operations

With starter packages, the strict first-run order is **package + MCP wiring →
one harness restart → runtime and onboarding**.

```text
server_info()                      # confirm resolved roots / allowed providers
runtime_install(dry_run=true)      # preview, then apply:
runtime_install(dry_run=false)     # scaffold coordinator; build provider images if enabled
context_packet(repo_id="<repo-id>", include_providers=true)
```

The copied `c-13-install-and-onboard` skill owns this post-restart phase. It
runs or verifies `runtime_install()`, sets up the memory repo, bootstraps
onboarding, and **starts provider indexing** (`provider_watchers(action="start")`)
when providers are enabled.

Why this order:

1. **Harness-native files first.** Skills, hooks/rules/instructions, and MCP
   settings are loaded by the harness, so the copied starter package must be in
   place before restart.
2. **Runtime scaffolding after restart.** The MCP server must be loaded before
   the agent can run `runtime_install()`. The runtime tool creates the
   coordinator directory and records the provider-runner integrity manifest.
3. **Providers last.** They are heavy (Docker, plus Ollama for grepai),
   per-repo, and optional. Note the split: `runtime_install()` builds provider
   images when `install_provider_deps=true`, but indexing only starts later via
   `c-13-install-and-onboard`. Pass `install_provider_deps=false` to refresh
   scaffold/docs without rebuilding images or disturbing running watchers; pass
   `no_cache=true` to force a from-scratch image rebuild (it otherwise skips
   images whose tag already exists). If providers report `degraded`, check that
   Docker is running and (for grepai) that the Ollama model pulled, then
   `provider_watchers(action="refresh")`; `provider_diagnostics()` shows the gap.

## Troubleshooting

**`uvx` can't find a just-published version.** PyPI's simple index (what `uvx`
resolves against) lags a few minutes behind a release, so
`uvx agents-remember-mcp==X.Y.Z` may briefly fail with "no version found" right
after that version is published. Wait 2–5 minutes and retry, run
`uvx --refresh …` to bypass uv's cache, or drop the `==X.Y.Z` pin to take the
latest the index currently serves.

**Providers report `degraded` and indexing/search returns nothing.** Both
providers need Docker running; grepai additionally needs its Ollama container and
embedding model. Check and recover:

- Docker: `docker ps` — if the daemon is down, start it (`sudo systemctl start
  docker` on Linux, or Docker Desktop), then `provider_watchers(action="refresh")`.
- grepai/Ollama: `docker logs ar-grepai-ollama` and
  `docker exec ar-grepai-ollama ollama list` to confirm the model is present. The
  model (`nomic-embed-text`) is pulled automatically on first setup; if that
  timed out, re-run `runtime_install()` or pull it manually with
  `docker exec ar-grepai-ollama ollama pull nomic-embed-text`.
- `provider_diagnostics()` shows the precise failing resource (backend, embedder,
  watcher) for either provider.

Providers are optional — core by-path memory and onboarding work without them, so
you can defer this and the rest of setup continues.

**Memory/worktree commits and git identity.** Closeout and carryover operations
commit to the memory repo and ledger, so git needs an author identity. Configure
your own with `git config --global user.name "…"` and
`git config --global user.email "…"`. If none is set, Agents Remember writes a
repo-local placeholder (`Agents Remember <agents-remember@example.invalid>`) so
work can still proceed — commits just won't carry your identity until you set it.

## Tool Surface

The server exposes tools for:

- startup context and drift checks
- runtime and skill installation
- memory initialization, memory quality checks, and route index refresh
- provider status, watcher control, GrepAI search, and CodeGraphContext queries
- worktree-backed closeout and task workflows
- benchmark preparation and execution (opt-in; see the note below)

Provider tools only work when the MCP settings enable the provider and the
required Docker services are available. Full tool list:
[MCP Tool Reference](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/reference/mcp-tools.md).

> **Benchmark execution is opt-in and runs untrusted code.** The `codex_benchmark_prepare`
> and `codex_benchmark_run` MCP tools are refused unless the MCP settings set
> `"benchmarksEnabled": true`. A real run (`dry_run=false`) clones third-party
> repositories and executes the Codex CLI against them. `codex_sandbox` defaults to
> Codex's own `default` sandbox; pass `"danger-full-access"` only for trusted local
> runs — it grants the benchmark agent full host access.

## More

- [Project README](https://github.com/Foxfire1st/agents-remember-md/blob/main/README.md)
- [Getting Started](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/getting-started.md)
- [Settings Reference](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/reference/settings-json.md)

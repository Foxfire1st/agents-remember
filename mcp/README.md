# Agents Remember MCP

`agents-remember-mcp` is the installable Model Context Protocol server for
Agents Remember. It lets an MCP-capable coding harness call Agents Remember
operations from the host instead of asking the model to edit or execute
coordinator scripts directly.

Source: [github.com/Foxfire1st/agents-remember-md](https://github.com/Foxfire1st/agents-remember-md)

## Quickstart

Setup is agent-driven. Ask your agent to:

1. **Install and wire Agents Remember MCP** — set it to run via
   `uvx agents-remember-mcp --config <absolute-path>/agents-remember-settings.json`,
   help you fill in that settings file (starter below), and register it with this
   harness. **Place the settings file under your harness's registration folder in
   an `mcp/` subdirectory** — e.g. `.claude/mcp/agents-remember-settings.json` for
   Claude Code, `.codex/mcp/…` for Codex — not loose in the workspace root (see
   [Settings file location](#settings-file-location) for why). **Then restart the
   harness** so it loads the server.
2. **Install the scaffolding, then the skills** — run `runtime_install` first
   (it scaffolds the coordinator and, when providers are enabled and Docker is
   running, builds the provider images), then `skills_install` (copies skills into
   the harness skill folder). **Restart the harness again** so it discovers the
   newly-installed skills. Order matters: scaffolding → skills → providers last
   (see [Install order](#install-order-and-first-operations)).
3. **Onboard your project** — run the `C-13-install-and-onboard` skill: it
   pre-checks the setup, installs the start hook (or places the directive for
   harnesses without one), sets up the memory repo (it will ask: scaffold a new
   one or use an existing one), bootstraps onboarding, and **starts the providers
   indexing last** (this is when indexing begins; image builds already happened in
   step 2). If it installs a session-start hook, **restart once more** so the hook
   activates — hooks are loaded at session start, not mid-session.

The hands-on steps for you: ask your agent for the steps above, **restart three
times** (after step 1 so the harness loads the server, after step 2 so it
discovers the installed skills, and after step 3 so a newly-installed session
hook activates), and answer the new-vs-existing memory question in step 3.

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
- `jq` for the Claude Code `SessionStart` hook. Install it with your distro's
  package manager: `apt install jq` (Debian/Ubuntu), `brew install jq` (macOS),
  `pacman -S jq` (Arch), `dnf install jq` (Fedora), or `apk add jq` (Alpine). If
  `jq` is missing the hook installs but silently injects nothing.

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
`runtime_install`). `workspaceRoot` holds your code repos. List each repo you
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

Place the settings file under your **harness registration folder** in an `mcp/`
subdirectory. This is not cosmetic: `skills_install` infers where to install
skills from the settings path — it uses the sibling `skills/` folder **only when
the settings file's parent directory is named `mcp`**. Put the file elsewhere
(e.g. loose in the workspace root) and `skills_install` has no target and fails.

| Harness | Put settings at | `skills_install` then targets |
| --- | --- | --- |
| Claude Code | `.claude/mcp/agents-remember-settings.json` | `.claude/skills/` |
| Codex | `.codex/mcp/agents-remember-settings.json` | `.codex/skills/` |
| Cursor | `.cursor/mcp/…` (or `.agents/mcp/…`) | `.cursor/skills/` (or `.agents/skills/`) |
| VS Code + Copilot | `.agents/mcp/agents-remember-settings.json` | `.agents/skills/` |

Do **not** place the settings file at the workspace root or inside
`ar-coordination/`. If your harness needs a non-standard layout you can set
`harnessSkillRoot` explicitly in the settings — but point it at a folder the
harness actually discovers (e.g. `.claude/skills`), or the skills install but
never load. See your harness page under
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
it reloads the server and discovers the tool list. Register the server under the
harness folder described in [Settings file location](#settings-file-location) so
`skills_install` can infer the skill target.

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

**One flat folder per skill.** `skills_install` copies the packaged skills —
already flat — into the skill root, so each lands at `<skill-root>/<name>/`
(matching the skill's lowercase frontmatter `name`). There is no layout option.
If your harness discovers skills somewhere other than `<harness-root>/skills/`,
set `harnessSkillRoot` explicitly (see your harness page).

## Install Order And First Operations

Setup runs in a strict order: **scaffolding → skills → providers last.** Preview
each step with `dry_run=true` before applying (`dry_run=false`, the default).

```text
server_info()                      # confirm resolved roots / allowed providers
runtime_install(dry_run=true)      # preview, then apply:
runtime_install(dry_run=false)     # scaffold coordinator; build provider images if enabled
skills_install(dry_run=true)       # preview
skills_install(dry_run=false)      # copy skills into the harness skill folder
# --- restart the harness here so it discovers the installed skills ---
context_packet(repo_id="<repo-id>", include_providers=true)
```

Then run the installed `C-13-install-and-onboard` skill (Quickstart step 3): it
sets up the memory repo, installs the start hook, bootstraps onboarding, and
**starts the providers indexing** (`provider_watchers(action="start")`).

Why this order:

1. **Scaffolding first.** `runtime_install` creates the coordinator directory and
   records a provider-runner integrity manifest. Provider operations check that
   manifest, so `provider_watchers` run **before** `runtime_install` fails fast
   with `runnerIntegrityFailed`.
2. **Skills second**, so `C-13` and the rest are available for the final step.
   (Most harnesses only discover newly-installed skills after a restart.)
   `skills_install` copies one flat folder per skill; there is no layout option.
3. **Providers last.** They are heavy (Docker, plus Ollama for grepai),
   per-repo, and optional. Note the split: `runtime_install` *builds* provider
   images during step 2 (with `install_provider_deps=true`, the default), but
   indexing only *starts* in step 3 — so "providers last" means indexing, not
   image builds. Pass `install_provider_deps=false` to refresh scaffold/docs
   without rebuilding images or disturbing running watchers; pass `no_cache=true`
   to force a from-scratch image rebuild (it otherwise skips images whose tag
   already exists). If providers report `degraded`, check that Docker is running
   and (for grepai) that the Ollama model pulled, then
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
- chat/direct closeout and worktree-backed task workflows
- benchmark preparation and execution (opt-in; see the note below)

Provider tools only work when the MCP settings enable the provider and the
required Docker services are available. Full tool list:
[MCP Tool Reference](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/reference/mcp-tools.md).

> **Benchmark execution is opt-in and runs untrusted code.** `codex_benchmark_prepare`
> and `codex_benchmark_run` are refused unless the MCP settings set
> `"benchmarksEnabled": true`. A real run (`dry_run=false`) clones third-party
> repositories and executes the Codex CLI against them. `codex_sandbox` defaults to
> Codex's own `default` sandbox; pass `"danger-full-access"` only for trusted local
> runs — it grants the benchmark agent full host access.

## More

- [Project README](https://github.com/Foxfire1st/agents-remember-md/blob/main/README.md)
- [Getting Started](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/getting-started.md)
- [Settings Reference](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/reference/settings-json.md)

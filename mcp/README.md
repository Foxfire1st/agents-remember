<h1 align="center">Agents Remember MCP</h1>

<p align="center"> 
📖 <b>Current docs:</b> https://foxfire1st.github.io/agents-remember/</br>
🤖 <b>Machine-readable summary:</b> https://foxfire1st.github.io/agents-remember/llms.txt</br>
📦 <b>Repository:</b> https://github.com/Foxfire1st/agents-remember</br>
<i>Note: caches and search snippets may serve an outdated copy of this README — the docs site above is canonical and always current.</i>
</p>

`agents-remember-mcp` is the installable Model Context Protocol server for
Agents Remember. It lets an MCP-capable coding harness call Agents Remember
operations from the host instead of asking the model to edit or execute
coordinator scripts directly.

## Quickstart

Setup is agent-driven. Ask your agent to:

1. **Copy the harness package** — Pick your harness guide under
   [docs/install](https://github.com/Foxfire1st/agents-remember/tree/main/docs/install),
   copy that harness's native starter files from the source repo into the
   workspace, then render the copied package. The `render-starter` script is a
   convenience: it infers the workspace root from the copied harness folder and
   fills the copied package's path, repository, and hook-command placeholders
   from a single `--repo` list such as `--repo my-app shared-lib`. You can also
   do those replacements by hand. These packages include the harness-visible skills,
   hooks/rules/instructions, MCP settings templates, and local renderers.
2. **Wire the MCP server** — Build a wheel from this checkout (`uv build --wheel mcp`
   at the repository root), then register it with `uvx` and the copied settings file:

   ```text
   uvx --python 3.14 --from /absolute/path/to/built-wheel.whl agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
   ```

   Use the `agents-remember-settings.json` path from the copied harness package.
   Then **restart the harness once** so it loads the MCP server, native skills,
   and package hooks/rules/instructions.
3. **Onboard your project** — Invoke the copied skill
   `c-13-install-and-onboard`. It runs or verifies `runtime_install()`, asks
   whether to scaffold a new memory repo or use an existing one, and bootstraps
   onboarding when needed. (Provider indexing is retired: no provider install,
   index, or watch step runs.)

That is the normal first-run path. The starter packages already provide the
initial skills and harness files. Your only required first-run restart is after
copying the harness package and wiring the MCP server.

## Requirements

The requirements here describe the current checkout and wheels built from it: Python 3.14 (`>=3.14,<3.15`), with 3.14.8 pinned only for managed development and CI. The published `3.0.0rc8` release is historical and requires Python 3.13 (`>=3.13,<3.14`). An official release of the Python 3.14 build is pending; use a locally built wheel for this checkout.

- Python **3.14** (the package declares `requires-python >=3.14,<3.15`).
- an MCP-capable coding harness
- [uv](https://docs.astral.sh/uv/) (for `uvx`) or pip
- Git for repository and memory ledger operations (configure `user.name` /
  `user.email`; without them, memory/worktree commits fall back to a placeholder
  identity so work can proceed).
- Docker (running) only for leftover pre-retirement provider containers. The two
  providers (`codegraphcontext-code`, `grepai-memory`) are retired and never
  count as enabled: no provider install, index, or watch runs, and old enabled
  keys are reported once as unused. The grepai embedder used to run
  Ollama as a Docker container and pull an embedding model (`nomic-embed-text`)
  on first setup — no host Ollama install is required.

Claude Code hooks do not require `jq`. Older starter packages used a `jq`
one-liner to encode hook output; the current starter packages use Python
renderers and Python hook scripts.

### Canonical Linux/WSL development runtime

Repository development, the MCP registration, detached lifecycle workers, and
Dagger acceptance use the contract in `scripts/python-runtime-contract.env`.
The current managed runtime is official source-built CPython 3.14.8. Its
installer verifies the Python.org source digest and the pinned `python-build`
definition before compilation; uv installs the frozen project dependencies but
is forbidden from substituting a managed Python distribution.

From the repository root on Linux or WSL:

```text
scripts/bootstrap-mcp-venv.sh
```

To replace an existing development environment explicitly:

```text
scripts/bootstrap-mcp-venv.sh --replace
```

The bootstrap writes the venv only to `mcp/.venv`, proves its exact interpreter,
standard-library extensions, native pidfd APIs, and source-build base prefix,
then runs the locked dependency compatibility check. It never replaces the
system Python and installs the interpreter outside the Git repository under the
Agents Remember data root.

It builds with the `uv` on the `PATH` when it is the contract's version
(`AR_UV_VERSION` in `scripts/python-runtime-contract.env`) or newer, and
installs none itself: an older `uv`, or none, is refused before anything is
moved or created. A `uv` that updates by itself therefore works.

## Install And Run

Build a wheel from the current checkout, then run that artifact on Python 3.14:

```text
uv build --wheel mcp
uvx --python 3.14 --from /absolute/path/to/built-wheel.whl agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
```

Or install the same local wheel into an existing Python 3.14 environment:

```text
python -m pip install /absolute/path/to/built-wheel.whl
agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
```

The source and published artifacts currently share the version string `3.0.0rc8`;
the published artifact has the historical Python 3.13 range. The local wheel path
identifies the current Python 3.14 build without claiming that it is published.

For the mission-control dashboard, build and sync its bundle before building the
wheel (run `npm --prefix dashboard run build`, then `python3 scripts/sync-dashboard.py`), then install the local artifact:

```text
uv tool install --python 3.14 /absolute/path/to/built-wheel.whl
agents-remember dashboard --daemon --config /absolute/path/to/active-harness-settings.json
```

The install brings the build-pinned Paseo host and its whole locked tree on the
product's checked Node (Linux x86_64). One shared paseoRuntime block in
<coordinationRoot>/system/settings.json supplies its facts. Each dashboard start
ensures the installed host without installing/upgrading; dashboard stop/restart
keeps host sessions running. Explicit paseo stop ends running turns and pending
prompts, with sessions resumable after a new start. --status prints both states
with a five-second host limit. Both --status and --daemon exit 0 when the
dashboard and host are up (or host unconfigured), 1 when the dashboard is down,
and 3 when a configured host is down.

`dashboard` finds its `--config` on its own: it walks up from the current
directory to the nearest `.claude/mcp/agents-remember-settings.json`, or the
`--config` recorded in an `.mcp.json` `agents-remember` entry. `--daemon`
detaches it so it survives the terminal that started it (`--status` /
`--stop` manage it; state and log live under
`<coordinationRoot>/logs/dashboard/`), and the
`"dashboard": {"autoStart": true}` settings key makes every MCP server boot
ensure the daemon — adopting a healthy one, starting a missing one, and
restarting on version mismatch.

> Published pre-releases use their own release metadata. The current Python 3.14
> checkout build uses the local wheel until an official release is available.

The config path must be **absolute**, the settings file must live **outside the
`ar-coordination/` runtime folder**, and it should live **under your harness's
registration folder in an `mcp/` subdirectory** (see
[Settings file location](#settings-file-location)). The server reads this file
**only at startup** — if you edit it later (add repos, change
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

(The `providers` block is retired: both providers never count as enabled, old
keys are reported once as unused, and the block may be omitted or emptied.)

`coordinationRoot` is where the runtime and memory repos live (populated by
the `runtime_install` MCP tool); **default it to `<workspace>/ar-coordination/`** — inside the
workspace, never the user's home directory. `workspaceRoot` is the workspace itself (it holds your
code repos). The `c-13-install-and-onboard` skill treats the workspace as the first assumption for
every install location and shows each resolved default for you to accept or override, so placement
is never silent or guessed. List each repo you
want Agents Remember to manage under `repositories`. The `providers` block is
unused and may be omitted or emptied: both former providers are retired, old
enabled keys are reported once as unused, and no provider install, index, or
watch runs. Full field
reference:
[settings-json.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/reference/settings-json.md).

> **Upgrading?** `timeoutCaps.providerSeconds` was renamed to
> `providerSetupSeconds`. The old key is rejected with a fail-loud `ConfigError`
> at startup, so rename it in any existing settings file. `providerSetupSeconds`
caps only provider **image build / dependency install**; indexing and database
seed/clone are never time-capped. A cap value of `0` means unlimited.
(Retired in practice: no provider image build runs anymore; the install only
stops leftover watchers and reports enabled provider keys once as unused.)

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
[docs/install/](https://github.com/Foxfire1st/agents-remember/tree/main/docs/install)
for the exact registration folder.

## Harness Setup

Register the MCP server with your harness by pointing it at `uvx` (or the
installed console command) and the absolute settings path:

```json
{
  "command": "uvx",
  "args": [
    "--python",
    "3.14",
    "--from",
    "/absolute/path/to/built-wheel.whl",
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
| Claude Code | [docs/install/claude-code.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/claude-code.md) |
| Codex | [docs/install/codex.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/codex.md) |
| Cursor | [docs/install/cursor.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/cursor.md) |
| Antigravity | [docs/install/antigravity.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/antigravity.md) |
| VS Code + Copilot | [docs/install/vscode-copilot.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/vscode-copilot.md) |
| Hermes | [docs/install/hermes.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/hermes.md) |
| Pi.dev | [docs/install/pi.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/pi.md) |
| OpenClaw | [docs/install/openclaw.md](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/openclaw.md) |

**One flat folder per skill.** The copied starter package already includes the
skills in the harness-native skill root.

## Install Order And First Operations

With starter packages, the strict first-run order is **package + MCP wiring →
one harness restart → runtime and onboarding**.

```text
server_info()                      # confirm resolved roots / allowed providers
runtime_install(dry_run=true)      # preview, then apply:
runtime_install(dry_run=false)     # scaffold coordinator (no provider image
                                 # build remains: the install only stops leftover
                                 # watchers and reports enabled provider keys
                                 # once as unused)
context_packet(repo_id="<repo-id>")
```

The copied `c-13-install-and-onboard` skill owns this post-restart phase. It
runs or verifies `runtime_install()`, sets up the memory repo, and bootstraps
onboarding. (Provider indexing is retired: it starts nothing.)

Why this order:

1. **Harness-native files first.** Skills, hooks/rules/instructions, and MCP
   settings are loaded by the harness, so the copied starter package must be in
   place before restart.
2. **Runtime scaffolding after restart.** The MCP server must be loaded before
   the agent can run `runtime_install()`. The runtime tool creates the
   coordinator directory and records the provider-runner integrity manifest.
3. **Providers retired.** The two former providers were heavy (Docker, plus
   Ollama for grepai), per-repo, and optional; both are retired and never count
   as enabled, so nothing in this section runs anymore. Note the retained
   parameters: `install_provider_deps` now only scopes the providers-tree copy
   (dependency-skipped copies preserve live runner state), and `no_cache`
   rebuilds nothing provider-side — neither starts any provider install, index,
   or watch. Old enabled keys are reported once as unused, and leftover
   watchers are stopped; remove leftover Docker containers only if asked.

## Troubleshooting

**`uvx` can't find a just-published version.** PyPI's simple index (what `uvx`
resolves against) lags a few minutes behind a release, so
`uvx agents-remember-mcp==X.Y.Z` may briefly fail with "no version found" right
after that version is published. Wait 2–5 minutes and retry, run
`uvx --refresh …` to bypass uv's cache, or drop the `==X.Y.Z` pin to take the
latest the index currently serves.

**Leftover pre-retirement provider containers (historical; both providers are retired).** Both
providers needed Docker running; grepai additionally needed its Ollama container and
embedding model. Read-only inspection only — nothing below starts or recovers anything:

- Docker: `docker ps` shows leftover containers (start the daemon only to inspect them for asked-only removal).
- grepai/Ollama: `docker logs ar-grepai-ollama` and
  `docker exec ar-grepai-ollama ollama list` shows whether the old model is present. The
  model (`nomic-embed-text`) was pulled automatically on pre-retirement first setup; nothing pulls it now. To clear leftovers, re-run `runtime_install()`, which stops leftover watchers and
  reports the old enabled keys once as unused; remove containers only if asked. (The former provider-diagnostics
  tool is removed; `server_info()` confirms what the running server loaded.)

Providers are retired — core by-path memory and onboarding work without them, so
nothing here blocks setup.

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
- worktree-backed closeout and task workflows
- benchmark preparation and execution (opt-in; see the note below)

The provider tools (provider status, watcher control, GrepAI search,
CodeGraphContext queries) were removed (MIK-R112) and are no longer served;
old enabled provider keys are reported once as unused. Full tool list:
[MCP Tool Reference](https://github.com/Foxfire1st/agents-remember/blob/main/docs/reference/mcp-tools.md).

> **Benchmark execution is opt-in and runs untrusted code.** The benchmark runner
> refuses unless the MCP settings set
> `"benchmarksEnabled": true`. A real run (`dry_run=false`) clones third-party
> repositories and executes the Codex CLI against them. `codex_sandbox` defaults to
> Codex's own `default` sandbox; pass `"danger-full-access"` only for trusted local
> runs — it grants the benchmark agent full host access.

## More

- [Project README](https://github.com/Foxfire1st/agents-remember/blob/main/README.md)
- [Getting Started](https://github.com/Foxfire1st/agents-remember/blob/main/docs/getting-started.md)
- [Settings Reference](https://github.com/Foxfire1st/agents-remember/blob/main/docs/reference/settings-json.md)


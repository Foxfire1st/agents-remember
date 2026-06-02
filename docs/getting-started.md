# Getting Started

This guide sets up Agents Remember in a workspace that contains one or more code repositories.

Setup is agent-driven. Once the MCP server is wired in, you ask your agent to do the work and answer one question along the way.

## The Short Version

Ask your agent to:

1. **Wire the MCP server** — Register Agents Remember MCP with this harness using `uvx`, help you author the settings file, then **restart the harness** so it loads the server.
2. **Install Agents Remember** — Run `runtime_install`, then `skills_install` (scaffolding, skills, and provider images when providers are enabled).
3. **Onboard your project** — Run `C-13-install-and-onboard`. It pre-checks the setup, installs the start hook (or places the directive for harnesses without one), sets up the memory repo (it asks: scaffold a new one or use an existing one), bootstraps onboarding, and starts the providers indexing.

The only hands-on steps for you is to restart once after step 1, and then continue from there.

## Example Workspace

```text
projects/
  AGENTS.md
  agents-remember-md/
  my-app/
  ar-coordination/
```

`agents-remember-md` is the source checkout. `ar-coordination` is the installed runtime and local coordination area. `my-app` is the repository you want agents to work on.

## Manual Wire Of The MCP Server

The agent should now how to setup the mcp. But if it doesn't work out here is how to do it manually. The simplest path is `uvx`, which fetches and runs the server on demand — no manual virtualenv or PATH setup. Register it with your harness by pointing the command at `uvx` and an **absolute** settings path:

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

A minimal starter `agents-remember-settings.json`:

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

The settings file must be absolute and must live **outside** the `ar-coordination/` runtime folder. See the [settings.json reference](reference/settings-json.md) for every field. After registering or changing the server, **restart the harness** so it discovers the tool list.

## Install The Runtime

With the server loaded, request:

```text
runtime_install()
```

The runtime installer reconciles package-owned runtime files into `ar-coordination`: installed `AGENTS.md` templates, skills, provider defaults, and runtime folders. It does not create memory repos, run onboarding bootstrap, overwrite live settings, or modify tasks, notes, worktrees, memory content, or temporary artifacts. Preview with `runtime_install(dry_run=true)`.

When providers are enabled in the settings, `runtime_install` also builds or pulls their Docker images. It does **not** start indexing on its own — `C-13` (or the [Providers guide](guides/providers.md)) does that.

Benchmark fixtures are optional and not installed by default. Install or refresh them with `runtime_install(include_benchmarks=true)`. The benchmark package is idempotent and preserves local outputs under `ar-coordination/benchmarks/user-runs/`.

## Choose Your Agent

Different tools discover instructions and skills differently. Use the install page for your harness:

| Harness | Setup guide |
| --- | --- |
| Codex | [docs/install/codex.md](docs/install/codex.md) |
| Claude Code | [docs/install/claude-code.md](docs/install/claude-code.md) |
| Cursor | [docs/install/cursor.md](docs/install/cursor.md) |
| Antigravity | [docs/install/antigravity.md](docs/install/antigravity.md) |
| VS Code + GitHub Copilot | [docs/install/vscode-copilot.md](docs/install/vscode-copilot.md) |
| Hermes.md | [docs/install/hermes.md](docs/install/hermes.md) |
| Pi.dev | [docs/install/pi.md](docs/install/pi.md) |
| OpenClaw | [docs/install/openclaw.md](docs/install/openclaw.md) |

## Expose Skills To Your Harness

Some agent tools read skills from a folder in the workspace; others require skills in a specific registration folder. Use the MCP `skills_install` tool instead of copying skill folders by hand:

```text
skills_install()
```

The install target is normally inferred from the MCP settings location: settings under `<registration-root>/mcp/<settings>.json` install into `<registration-root>/skills/`. For recursive skill scanners such as Codex and Claude Code, this creates one copied namespace tree:

```text
.codex/skills/agents-remember-md/
```

For harnesses that require the folder containing `SKILL.md` to match the skill's lowercase `name` (direct skill-folder scanners), use the flat layout:

```text
skills_install(layout="flat")
```

See the harness-specific pages under [install](install/README.md) for exact locations, and the [Skills reference](reference/skills.md) for the full skill list.

## Install The Hook Or Workspace Instructions

Agents Remember works best when its coordinator directive is loaded authoritatively at the start of every session. `C-13-install-and-onboard` does this for you, choosing per harness:

- **Harnesses with a session/chat start hook** (Claude Code, Codex, Pi.dev, Antigravity, OpenClaw) — install a start hook that injects `ar-coordination/AGENTS.md` as authoritative context. Start hooks are first-class here because instruction-following is far more reliable when the directive is injected than when it merely sits in an optional import.
- **Harnesses without a start hook** (Cursor, GitHub Copilot, Hermes) — place the directive in the harness's native instruction location (a Cursor project rule, a Copilot instructions file, Hermes priority context).

If you set this up by hand instead, the workspace-root instruction file most harnesses read is `AGENTS.md`:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

For Claude Code specifically, prefer the SessionStart hook so the directive is authoritative; a `CLAUDE.md` import works only as a degraded optional fallback. See [Install for Claude Code](install/claude-code.md) and the other [install pages](install/README.md).

## Set Up Memory

`C-13` asks whether to **scaffold a new memory repo** or **use an existing one** — it does not assume you always want a fresh one. If you answer "scaffold," it runs `C-00-initialize-memory-repo` for the target repository.

By default `C-00` creates repo-local internal memory:

```text
my-app/
  ar-memory/
    onboarding/
    docs/
    system/
      settings.md
      settings.json
      sources.md
      tools.md
```

Use external memory only when you intentionally want a separate memory repo under `ar-coordination/memory-repos/ar-<repo>/`. See [Use External Memory](guides/use-external-memory.md).

## Bootstrap Onboarding

For a new memory repo, `C-13` runs `C-03-repo-bootstrap` for the target repository. A thin `overview.md` is enough to start. Larger repositories can grow route-local overviews and file-level onboarding as work touches new areas. For token-conscious bootstraps of large repos, see [Cost-aware Bootstrap](guides/cost-aware-bootstrap.md).

## Start Providers (Optional)

If you enabled `codegraphcontext-code` or `grepai-memory`, `C-13` starts the watchers so the providers index your configured code and memory. You can also do this directly:

```text
provider_watchers(action="start")
provider_status()
```

Providers are optional — memory, onboarding, drift, and task workflows all work without them. See [Providers](guides/providers.md).

## Start Working

Normal tasks run through the L-01 session job lifecycle (orient → ground → frame → decide → build → close). The agent should:

1. resolve the repository context with `C-08-ar-coordination-context-resolver`
2. run `C-02-memory-quality-control` before planning against onboarding
3. read the relevant onboarding beside the code
4. propose changes and wait for approval
5. implement approved work
6. update onboarding through `C-05-create-or-update-onboarding-files`

Escalate to a [durable W-02 task or master series](workflows.md) when the work needs a durable plan that survives the session.

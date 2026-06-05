<h1 align="center">
  Agents Remember
</h1>  
<h3 align="center">
  Durable, git-verified repo memory for coding agents.
</h3>

<p align="center">
  <img alt="NPM License" src="https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FFoxfire1st%2Fagents-remember-md%2Fmain%2Fmcp%2Fpyproject.toml">
</p>

## Table of Contents

1. [Why It Exists](#why-it-exists)
2. [Core Model](#core-model)
3. [What It Looks Like In Practice](#what-it-looks-like-in-practice)
4. [Live Demo](#live-demo)
5. [Requirements](#requirements)
6. [Quickstart](#quickstart)
7. [Documentation](#documentation)
8. [Repository Layout](#repository-layout)
9. [Status](#status)
10. [Stability](#stability)
11. [Contributing](#contributing)

## Why It Exists

Modern coding agents can make clean, plausible edits while missing the project-specific rules that make those edits safe. A top-level instruction file can help, but it does not naturally reappear when the agent is deep in a file and deciding what to change.

Agents Remember fixes that: the matching note is reachable at the moment of the edit — most often by the very path the agent is already working in — so project rules surface exactly when a change is being made, not buried in a top-level file.

## Core Model

**Agents Remember makes hard-earned lessons first-class infrastructure** — the local invariants, naming rules, migration scars, cross-repo contracts, and "this looks safe but is not" facts that live in people's heads, old PRs, and team habits, exactly where coding agents miss them. It keeps that knowledge as versioned Markdown beside the code, drift-checked against Git and updated only after approved work lands.

```text
src/orchestrator/core_editor.py
ar-memory/onboarding/src/orchestrator/core_editor.py.md
```

Agents reach that memory three ways:

- **By path** — a source file's own note, found directly from its path (as above). Needs nothing extra.
- **By meaning** — semantic search across the memory when you know the concept but not the file.
- **By relationship** — a code graph for callers, callees, and dependencies.

The by-path notes are the core; meaning and relationship are opt-in providers (see [Concepts](docs/concepts.md) and [Providers](docs/guides/providers.md)).

The memory layer rests on a small, strict discipline:

- **Onboarding units:** Markdown notes derived from source paths. A file such as `src/foo/bar.ts` maps to `ar-memory/onboarding/src/foo/bar.ts.md` in the default repo-local mode.
- **Memory quality control:** Before an agent trusts onboarding, `c-02-memory-quality-control` checks whether the source changed since the onboarding was verified. During closeout it also covers new-file onboarding and final memory quality checks.
- **Approval-gated updates:** Onboarding records approved current state, not guesses or plans. Task-local notes stay task-local until the developer approves implementation.

The default setup stores durable memory in the target repository under `ar-memory/`. Teams that need separate memory repositories can use external memory under `ar-coordination/memory-repos/ar-<repo>/`.

## What It Looks Like In Practice

A source file has an onboarding note beside it, reached by path:

```text
mcp/src/agents_remember/mcp/server.py
ar-memory/onboarding/mcp/src/agents_remember/mcp/server.py.md
```

At task start the agent orients and checks memory health:

```text
context_packet(repo_id="my-app")
memory_quality_check(repo_id="my-app")
```

It then reads the source file and its onboarding note together before proposing a change. After the change is approved and lands, the onboarding is refreshed and re-verified against the new commit — so the note stays true to the code.

## Live Demo

Agents Remember runs on itself. The companion memory repo is:
https://github.com/Foxfire1st/ar-agents-remember-md

That repo contains the live onboarding layer, so you can inspect how by-path memory, drift-aware updates, and contribution-time onboarding look in practice.

## Requirements

Before the Quickstart, make sure the host has:

- **[uv](https://docs.astral.sh/uv/)** (for `uvx`) or pip, and **Python 3.11+** — the agent runs the MCP server with `uvx`, which picks a compatible interpreter.
- **Git**, with `user.name` / `user.email` configured (memory and worktree commits need an author; otherwise a placeholder identity is used).
- **Docker** running, only if you enable the optional providers. The semantic-memory provider (grepai) also uses a Dockerized Ollama and pulls an embedding model (`nomic-embed-text`) on first setup — no host Ollama install needed.
- **`jq`**, only when using the Claude Code starter package's `SessionStart`
  hook: `apt install jq` (Debian/Ubuntu), `brew install jq` (macOS),
  `pacman -S jq` (Arch), `dnf install jq` (Fedora), or `apk add jq` (Alpine).
  Without it the copied Claude hook injects nothing.

Providers, Docker, Ollama, and `jq` are only needed for the optional Docker-backed providers and the Claude Code package hook; the core by-path memory works without them. Full detail and troubleshooting live in the [MCP package README](https://pypi.org/project/agents-remember-mcp/).

## Quickstart

This is the short path for a new workspace. The detailed walkthrough lives in [Getting Started](docs/getting-started.md).

Ask your agent to:

1. **Copy the harness package** — Pick your harness guide under
   [docs/install](docs/install/README.md), copy that harness's native starter
   files from this repo into the workspace, and replace every
   placeholder, including `<PATH/TO/YOUR/PROJECTS_FOLDER>` and
   `<YOUR_REPOSITORY_FOLDER_NAME>`. These packages include the harness-visible
   skills, hooks/rules/instructions, and MCP settings templates.
2. **Wire the MCP server** — Register Agents Remember MCP from
   [PyPI](https://pypi.org/project/agents-remember-mcp/) with `uvx`:

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
initial skills and harness files.

After that, normal work runs through the `l-01-session-job-lifecycle` skill. The agent resolves the active context with `c-08-ar-coordination-context-resolver`, checks memory quality with `c-02-memory-quality-control`, reads relevant onboarding beside code, and updates onboarding after approved changes.

## Documentation

- [Getting Started](docs/getting-started.md) - a fuller first-run setup.
- [Concepts](docs/concepts.md) - onboarding units, memory roots, drift, and approval gates.
- [Architecture](docs/architecture.md) - runtime, coordination, internal memory, and external memory.
- [Workflows](docs/workflows.md) - the `l-01-session-job-lifecycle` skill and its build modes (read-only / chat build / `w-02-light-task-workflow` skill), and when to use each.
- [Benchmark Methodology](docs/benchmarks-methodology.md) - how paired `codex exec --json` runs are captured and compared.
- [FAQ](docs/FAQ.md) - design principles, objections, and comparisons.
- [External Memory Guide](docs/guides/use-external-memory.md) - separate memory repos for selected code repos.
- [Cost-aware Bootstrap](docs/guides/cost-aware-bootstrap.md) - model and wave-sizing choices for token-heavy repository bootstrap.
- [Settings Reference](docs/reference/settings-json.md) - memory-layer `system/settings.json` and MCP authority settings.
- [Skills Reference](docs/reference/skills.md) - the installed skill families.

## Repository Layout

```text
agents-remember-md/
  AGENTS.md                         # source checkout instructions
  README.md                         # public front door
  skills/                           # canonical skill source tree
  scripts/sync-skills.py            # sync skills into package/harness copies
  mcp/                              # package-local MCP server and services
    src/agents_remember/package_data/
      runtime/
        agents-md-files/            # installed AGENTS.md templates
        skills/                     # generated package copy of root skills/
        providers/                  # provider runtime assets (images, runners)
        system/defaults/examples/   # scaffold examples used by initialization
      benchmarks/                   # optional benchmark package source
  docs/                             # user-facing documentation
```

Edit skills in root `skills/`, then run `python3 scripts/sync-skills.py` to
refresh the MCP package data and every harness starter package. The pre-push
hook runs `python3 scripts/sync-skills.py --check`.

The installed runtime lives in `ar-coordination/` — by default `<workspace>/ar-coordination/`,
inside the workspace (never your home directory) — not in the source checkout. The
`c-13-install-and-onboard` skill shows this and every other install path as a workspace-first
default you can accept or override:

```text
ar-coordination/
  AGENTS.md
  skills/
  system/
  memory-repos/
  providers/                        # provider runtimes (images, runners, indexes)
  benchmarks/                       # optional, installed with --include-benchmarks
  tasks/
  notes/
  worktrees/
  temp/
```

## Status

Agents Remember is at `2.3.3` and actively developed. The `2.0.0` major release reshaped the session job lifecycle: every session now enters the `l-01-session-job-lifecycle` skill, the chat (W-03) and heavy (W-01) workflows were retired in favor of the light task plus master + light sub-task series composition, the skill tree went flat, and some public contracts changed (removed skill IDs, the `skills_install` MCP tool's `layout` input, and heavy `workflow_kind` values). `2.1.0` builds on that with workspace-first install-location defaults, a consistent skill-name/MCP-tool reference convention, and provider/quality fixes; `2.2.0` refreshes the lifecycle collaboration loop around context-packet grounding, developer-facing reframes, deeper-research evidence, and clearer C-09 source-branch discipline; `2.3.0` adds harness-native starter packages, package-first skills/hooks/instructions, root-level canonical skill sources, and a one-restart first-run path, `2.3.1` corrects the MCP package README shown on PyPI to match that setup flow, `2.3.2` packages the refreshed runtime skills with the C-09 worktree intent approval gate and integration checkout prerequisite reminder, and `2.3.3` makes runtime reinstalls stop and restart enabled provider watchers around managed runner refreshes and normalizes dotted worktree names for Docker-backed provider setup — all backward-compatible. The core path — by-path onboarding, drift checks, and approval-gated updates — is in real use and stable enough to rely on. The public contracts listed under [Stability](#stability) are held stable across minor releases and change only on a major bump; the internals beneath them and the optional semantic/relationship providers may still evolve, so pin a version and read the release notes before upgrading. The Claude Code path is the most exercised; other harnesses are supported but less battle-tested.

## Stability

Following semantic versioning from `1.0.0`, these public contracts will not change without a **major** version bump: **skill IDs** (e.g. the `c-08-ar-coordination-context-resolver` and `w-02-light-task-workflow` skills), **MCP tool names and their inputs/outputs**, the **`ar-coordination/` and `ar-memory/` layout**, and the **settings schema**. Internal modules, provider internals, and prompt wording are not part of this promise and may change in minor releases.

## Contributing

Contributions should make the memory layer clearer, safer, and easier to apply consistently. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and keep the core rules intact: drift check before planning, approval before implementation, and onboarding updates only after approved changes.

Agents Remember runs on itself, so the best way to contribute is with the memory layer active. Download or clone this project's own memory at [Foxfire1st/ar-agents-remember-md](https://github.com/Foxfire1st/ar-agents-remember-md) and use it as the Agents Remember memory for your checkout: you get the project's by-path onboarding at the moment you edit, and your onboarding updates land alongside your code changes — the same loop this repo asks of every contribution.

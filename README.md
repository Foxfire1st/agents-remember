# Agents Remember

Durable, git-verified repo memory for coding agents.

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

## Why It Exists

Modern coding agents can make clean, plausible edits while missing the project-specific rules that make those edits safe. A top-level instruction file can help, but it does not naturally reappear when the agent is deep in a file and deciding what to change.

Agents Remember fixes that: the matching note is reachable at the moment of the edit — most often by the very path the agent is already working in — so project rules surface exactly when a change is being made, not buried in a top-level file.

## Core Model

The memory layer rests on a small, strict discipline:

- **Onboarding units:** Markdown notes derived from source paths. A file such as `src/foo/bar.ts` maps to `ar-memory/onboarding/src/foo/bar.ts.md` in the default repo-local mode.
- **Memory quality control:** Before an agent trusts onboarding, `C-02-memory-quality-control` checks whether the source changed since the onboarding was verified. During closeout it also covers new-file onboarding and final memory quality checks.
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

## Live Demo: This Repo Uses Agents Remember

Agents Remember runs on itself. The companion memory repo is:
https://github.com/Foxfire1st/ar-agents-remember-md

That repo contains the live onboarding layer, so you can inspect how by-path memory, drift-aware updates, and contribution-time onboarding look in practice.

## Requirements

Before the Quickstart, make sure the host has:

- **[uv](https://docs.astral.sh/uv/)** (for `uvx`) or pip, and **Python 3.11+** — the agent runs the MCP server with `uvx`, which picks a compatible interpreter.
- **Git**, with `user.name` / `user.email` configured (memory and worktree commits need an author; otherwise a placeholder identity is used).
- **Docker** running, only if you enable the optional providers. The semantic-memory provider (grepai) also uses a Dockerized Ollama and pulls an embedding model (`nomic-embed-text`) on first setup — no host Ollama install needed.
- **`jq`**, only for the Claude Code `SessionStart` hook: `apt install jq` (Debian/Ubuntu), `brew install jq` (macOS), `pacman -S jq` (Arch), `dnf install jq` (Fedora), or `apk add jq` (Alpine). Without it the hook installs but silently injects nothing.

Providers, Docker, Ollama, and `jq` are only needed for the optional Docker-backed providers and the Claude Code hook; the core by-path memory works without them. Full detail and troubleshooting live in the [MCP package README](https://pypi.org/project/agents-remember-mcp/).

## Quickstart

This is the short path for a new workspace. The detailed walkthrough lives in [Getting Started](docs/getting-started.md).

Ask your agent to:

1. **Wire the MCP server** — Install Agents Remember MCP from [PyPI](https://pypi.org/project/agents-remember-mcp/) with `uvx`:

   ```text
   uvx agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
   ```

   Have your agent follow the PyPI link for setup details and help you author the settings file. Then **restart the harness** so it loads the server.
2. **Install Agents Remember** — Run the mcp tool `runtime_install`, then `skills_install` (scaffolding, skills, and provider images when providers are enabled). Then **restart the harness** again so it discovers the skills `skills_install` just wrote (some harnesses hot-reload skills and skip this; restarting is the safe default).
3. **Onboard your project** — Run the skill `C-13-install-and-onboard`. It pre-checks the setup, installs the start hook (or places the directive for harnesses without one), sets up the memory repo (it asks: scaffold a new one or use an existing one), bootstraps onboarding, and starts the providers indexing. If it installed a session-start hook, **restart the harness once more** so the hook activates (hooks load at session start).

Those three restarts (load the server, discover the skills, activate the hook) are the only hands-on steps; between and after them, your agent continues on its own.

After that, normal work runs through the L-01 session job lifecycle. The agent resolves the active context with `C-08-ar-coordination-context-resolver`, checks memory quality with `C-02-memory-quality-control`, reads relevant onboarding beside code, and updates onboarding after approved changes.

## Documentation

- [Getting Started](docs/getting-started.md) - a fuller first-run setup.
- [Concepts](docs/concepts.md) - onboarding units, memory roots, drift, and approval gates.
- [Architecture](docs/architecture.md) - runtime, coordination, internal memory, and external memory.
- [Workflows](docs/workflows.md) - the L-01 lifecycle and its build modes (read-only / chat build / W-02), and when to use each.
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
  mcp/                              # package-local MCP server and services
    src/agents_remember/package_data/
      runtime/
        agents-md-files/            # installed AGENTS.md templates
        skills/                     # installed skill source tree
        providers/                  # provider runtime assets (images, runners)
        system/defaults/examples/   # scaffold examples used by initialization
      benchmarks/                   # optional benchmark package source
  docs/                             # user-facing documentation
```

The installed runtime lives in `ar-coordination/`, not in the source checkout:

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

Agents Remember is at `2.0.0` and actively developed. **2.0.0 is a major, breaking release** — the session job lifecycle reshape: every session now enters the `L-01` lifecycle, the chat (W-03) and heavy (W-01) workflows are retired in favor of the light task plus master + light sub-task series composition, the skill tree is flat, and some public contracts changed (removed skill IDs, the `skills_install` `layout` input, and heavy `workflow_kind` values). The core path — by-path onboarding, drift checks, and approval-gated updates — is in real use and stable enough to rely on. The public contracts listed under [Stability](#stability) are held stable across minor releases and change only on a major bump like this one; the internals beneath them and the optional semantic/relationship providers may still evolve, so pin a version and read the release notes before upgrading. The Claude Code path is the most exercised; other harnesses are supported but less battle-tested.

## Stability

Following semantic versioning from `1.0.0`, these public contracts will not change without a **major** version bump: **skill IDs** (e.g. `C-08`, `W-02`), **MCP tool names and their inputs/outputs**, the **`ar-coordination/` and `ar-memory/` layout**, and the **settings schema**. Internal modules, provider internals, and prompt wording are not part of this promise and may change in minor releases.

## Contributing

Contributions should make the memory layer clearer, safer, and easier to apply consistently. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and keep the core rules intact: drift check before planning, approval before implementation, and onboarding updates only after approved changes.

Agents Remember runs on itself, so the best way to contribute is with the memory layer active. Download or clone this project's own memory at [Foxfire1st/ar-agents-remember-md](https://github.com/Foxfire1st/ar-agents-remember-md) and use it as the Agents Remember memory for your checkout: you get the project's by-path onboarding at the moment you edit, and your onboarding updates land alongside your code changes — the same loop this repo asks of every contribution.

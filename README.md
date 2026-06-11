<h1 align="center">
  Agents Remember
</h1>  
<h3 align="center">
  Durable, git-verified repo memory for coding agents.
</h3>

<p align="center">
  <img alt="NPM License" src="https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FFoxfire1st%2Fagents-remember%2Fmain%2Fmcp%2Fpyproject.toml">
</p>

<p align="center"> 
  📖 <b>Current docs:</b> https://foxfire1st.github.io/agents-remember/</br>
  🤖 <b>Machine-readable summary:</b> https://foxfire1st.github.io/agents-remember/llms.txt</br>
  <i>Note: caches and search snippets may serve an outdated copy of this README — the docs site above is canonical and always current.</i>
</p>

##

## Table of Contents

1. [Why It Exists](#why-it-exists)
2. [Core Features](#core-features)
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

## Core Features

**Agents Remember gives coding agents project memory they can verify and act on.** It turns local invariants, naming rules, migration scars, cross-repo contracts, and "this looks safe but is not" facts into versioned Markdown beside the code, checks that memory against Git before use, and updates it only after approved work lands.

```text
src/orchestrator/core_editor.py
ar-memory/onboarding/src/orchestrator/core_editor.py.md
```

- **Path-addressed memory:** A source file's note lives at a deterministic mirror path, so an agent holding a file can reach the right context without search, ranking, or guesswork.
- **Git-proven freshness:** File notes, route overviews, and entity catalogs are drift-checked against source commits, route scopes, or deterministic fingerprints before they are trusted.
- **Search that finds, not decides:** Optional semantic memory and code-graph providers help locate relevant files, callers, dependencies, and concepts, but verified Markdown and source code remain the truth.
- **Memory that lands with code:** External memory repos use a `memory.md` ledger, isolated dual worktrees, preview/apply closeout, and all-or-nothing integration so code and memory stay synchronized.
- **Repo-owned agent behavior:** Each memory repo carries `system/` files for path rules, tools, coding guidelines, documentation sources, branch policy, and reporting shape, so the same project rules load across harnesses.
- **Harness-ready first run:** Starter packages for Claude Code, Codex, Cursor, Antigravity, VS Code Copilot, Hermes, Pi.dev, and OpenClaw carry the native MCP, skills, hooks, rules, and instruction files each harness needs.

The default setup stores durable memory in the target repository under `ar-memory/`. Teams that need separate memory repositories can use external memory under `ar-coordination/memory-repos/ar-<repo>/`. For the full tour, see [Features](docs/features.md).

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
https://github.com/Foxfire1st/ar-agents-remember

That repo contains the live onboarding layer, so you can inspect how by-path memory, drift-aware updates, and contribution-time onboarding look in practice.

## Requirements

Before the Quickstart, make sure the host has:

- **[uv](https://docs.astral.sh/uv/)** (for `uvx`) or pip, and **Python 3.11+** — the agent runs the MCP server with `uvx`, which picks a compatible interpreter.
- **Git**, with `user.name` / `user.email` configured (memory and worktree commits need an author; otherwise a placeholder identity is used).
- **Docker** running, only if you enable the optional providers. The semantic-memory provider (grepai) also uses a Dockerized Ollama and pulls an embedding model (`nomic-embed-text`) on first setup — no host Ollama install needed.

Providers, Docker, and Ollama are only needed for the optional Docker-backed
providers; the core by-path memory works without them. Claude Code hooks do not
require `jq`; the current starter package uses a Python hook. Full detail and
troubleshooting live in the [MCP package README](https://pypi.org/project/agents-remember-mcp/).

## Quickstart

This is the short path for a new workspace. The detailed walkthrough lives in [Getting Started](docs/getting-started.md).

Ask your agent to:

1. **Copy the harness package** — Pick your harness guide under
   [docs/install](docs/install/README.md), copy that harness's native starter
   files from this repo into the workspace, then render the copied package.
   The `render-starter` script is a convenience: it infers the workspace root
   from the copied harness folder and fills the copied package's path,
   repository, and hook-command placeholders from a single `--repo` list such as
   `--repo my-app shared-lib`. You can also do those replacements by hand. These
   packages include the harness-visible skills, hooks/rules/instructions, and
   MCP settings templates.
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

- [Features](docs/features.md) - the concentrated tour of what Agents Remember gives users.
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
agents-remember/
  AGENTS.md                         # source checkout instructions
  README.md                         # public front door
  skills/                           # canonical skill source tree
  scripts/sync-skills.py            # sync skills into package/harness copies
  scripts/sync-runtime.py           # sync runtime assets into package data
  agents-md-files/                  # canonical installed AGENTS.md templates
  benchmarks/                       # canonical optional benchmark package source
  providers/                        # canonical provider runtime assets
  system/defaults/examples/         # canonical scaffold examples
  mcp/                              # package-local MCP server and services
    src/agents_remember/package_data/
      runtime/
        agents-md-files/            # generated copy of root agents-md-files/
        skills/                     # generated package copy of root skills/
        providers/                  # generated copy of root providers/
        system/defaults/examples/   # generated copy of root system/defaults/examples/
      benchmarks/                   # generated copy of root benchmarks/
  docs/                             # user-facing documentation
```

Edit skills in root `skills/`, then run `python3 scripts/sync-skills.py` to
refresh the MCP package data and every harness starter package. The pre-commit
and pre-push hooks run `python3 scripts/sync-skills.py --check`.

Edit runtime assets in root `agents-md-files/`, `benchmarks/`, `providers/`,
and `system/`, then run `python3 scripts/sync-runtime.py` to refresh MCP package
data only. The pre-commit and pre-push hooks run
`python3 scripts/sync-runtime.py --check`.

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

Agents Remember is at `2.8.0` and actively developed. The `2.0.0` major release reshaped the session job lifecycle: every session now enters the `l-01-session-job-lifecycle` skill, the chat (W-03) and heavy (W-01) workflows were retired in favor of the light task plus master + light sub-task series composition, the skill tree went flat, and some public contracts changed (removed skill IDs, the `skills_install` MCP tool's `layout` input, and heavy `workflow_kind` values). `2.1.0` builds on that with workspace-first install-location defaults, a consistent skill-name/MCP-tool reference convention, and provider/quality fixes; `2.2.0` refreshes the lifecycle collaboration loop around context-packet grounding, developer-facing reframes, deeper-research evidence, and clearer C-09 source-branch discipline; `2.3.0` adds harness-native starter packages, package-first skills/hooks/instructions, root-level canonical skill sources, and a one-restart first-run path, `2.3.1` corrects the MCP package README shown on PyPI to match that setup flow, `2.3.2` packages the refreshed runtime skills with the C-09 worktree intent approval gate and integration checkout prerequisite reminder, `2.3.3` makes runtime reinstalls stop and restart enabled provider watchers around managed runner refreshes and normalizes dotted worktree names for Docker-backed provider setup, `2.4.0` adds harness-local starter renderers, Python hook command rendering, and clearer manual placeholder-replacement install docs that remove the legacy Claude Code `jq` misconception, `2.4.1` tightens runtime asset packaging/source sync and preserves provider validation on skipped-provider context packets, `2.4.2` packages the consolidated L-01 lifecycle skill so the full request-to-close doctrine is available from `SKILL.md` without a skipped companion file, and `2.5.0` makes the CodeGraphContext provider durable and honest about readiness: FalkorDB graph data now persists to the mounted host path (previously lost on container recreate), provider status probes actual graph content (`indexed` / `indexing` / `empty` / `backend-unreachable`) instead of trusting container liveness, empty-graph targets degrade the global context packet, the compact providers summary lists busy `indexing` targets, the watcher self-heals poisoned empty graphs at startup, and managed compose runs clean up watchers of de-configured repos, and `2.5.1` makes the tool surface reliable and context-affordable: MCP subprocesses can no longer inherit the stdio protocol pipes (the proven root cause of multi-minute tool-call hangs, now fenced by an AST guard test and an end-to-end stdio transport test), worktree provider seeding detects wedges by stalled progress instead of capping clone time, GrepAI gains real `indexed`/`indexing` states from its own scan markers, crash-looping containers no longer count as ready watchers, the CGC runner image tag has a single derivation, and the verbose tools (`runtime_install`, `provider_diagnostics`, `provider_watchers`) file their bulk diagnostics under `temp/tool-reports/` (keep-5/7-day retention) and return compact outcomes with a `reportPath` — all backward-compatible, and `2.5.2` extends that response-budget discipline to the last verbose pair: `memory_carryover_plan`/`memory_carryover_apply` now return per-decision source-path digests with the full candidate records filed under `temp/tool-reports/` (the apply response previously repeated every record twice), and `2.6.0` closes the memory-integrity gap (GitHub #56): branch-memory carryover plans route-overview candidates beside file sidecars (route-keyed, auto-carried only when content is identical) and regenerates official-side route indexes after a carry, while closeout gains body/history gates that reject header-only or unmarked history-only onboarding refreshes for changed sources and their nearest-governing route overviews — with explicit `No content impact:` / `No route impact:` Update History markers as reviewed-no-impact attestations surfaced in closeout payloads, and `2.7.0` makes worktree provider setup observable and Windows-honest (GitHub #53, #58): `worktree_start` returns within seconds while the provider chain runs in the background writing a heartbeat-stamped `setup-progress.json` (polled via `worktree_status`, with the seed-refused→full-reindex transition flagged as `seedFallback` and `retry_provider_setup` as the recovery path), and CGC seed export/load argv is rendered in container form so Windows hosts stop failing every seed into the silent full reindex, and `2.8.0` makes base-currency a lifecycle-long guarantee (GitHub #54): `context_packet` gains an opt-in `include_freshness` section (code/memory upstream ahead-behind plus a `ledgerMapsCodeHead` check) for the lifecycle-start trust checkpoint, `worktree_start` refuses stale or diverged source branches with `stale_base_choice` fast-forward/override recoveries and auto-creates a missing external-memory source branch from the official tip, carryover fast-forwards memory `main` to the official checkout tip (`memory_main_advance`) so non-main cycles no longer leave it behind, and the new `worktree_sync` tool pulls a moved official line into a live worktree atomically (consistent ledger-mapped base pair, parked-memory fast-forward, `merge-memory`/`skip-memory` recoveries, contract `sync_log`), with `worktree_status` carrying a fetch-free freshness block that recommends it. The core path — by-path onboarding, drift checks, and approval-gated updates — is in real use and stable enough to rely on. The public contracts listed under [Stability](#stability) are held stable across minor releases and change only on a major bump; the internals beneath them and the optional semantic/relationship providers may still evolve, so pin a version and read the release notes before upgrading. The Claude Code path is the most exercised; other harnesses are supported but less battle-tested.

## Stability

Following semantic versioning from `1.0.0`, these public contracts will not change without a **major** version bump: **skill IDs** (e.g. the `c-08-ar-coordination-context-resolver` and `w-02-light-task-workflow` skills), **MCP tool names and their inputs/outputs**, the **`ar-coordination/` and `ar-memory/` layout**, and the **settings schema**. Internal modules, provider internals, and prompt wording are not part of this promise and may change in minor releases.

## Contributing

Contributions should make the memory layer clearer, safer, and easier to apply consistently. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and keep the core rules intact: drift check before planning, approval before implementation, and onboarding updates only after approved changes.

Agents Remember runs on itself, so the best way to contribute is with the memory layer active. Download or clone this project's own memory at [Foxfire1st/ar-agents-remember](https://github.com/Foxfire1st/ar-agents-remember) and use it as the Agents Remember memory for your checkout: you get the project's by-path onboarding at the moment you edit, and your onboarding updates land alongside your code changes — the same loop this repo asks of every contribution.

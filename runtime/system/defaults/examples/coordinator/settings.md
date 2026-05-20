# Coordinator Settings Example

Use this file as the human-facing `ar-coordination/system/settings.md` starter
for a local coordinator.

The coordinator owns workspace-wide instructions, tools, workflow state, and
routing that can apply across multiple code repositories. It does not replace a
per-repository memory layer's `system/settings.md` or `system/settings.json`.

## Scope

The coordinator may store:

- global agent instructions that apply across repositories
- shared tool and command conventions
- workspace-wide source registries
- task roots
- worktree roots
- local notes and scratch paths
- selected external memory repo locations
- local operator conventions that are not durable repo policy

Rules that are valid only for one code repository belong in that repository's
selected memory layer, usually `ar-coordination/memory-repos/ar-<repo>/system/`
for external memory repos.

## Local Layout

```text
ar-coordination/
├── system/
│   ├── settings.md
│   └── settings.json
├── memory-repos/
├── tasks/
├── notes/
└── worktrees/
```

## Memory Repo Routing

Agents should still invoke C-08 for the target code repository. C-08 resolves
the active memory root and returns the memory layer's settings, tools, sources,
onboarding, and ledger paths.

Coordinator guidance may provide global defaults. Repository-specific memory
guidance is more specific and should win when the target repository has its own
rule.

## Context Providers

The coordinator may define optional `contextProviders` in `settings.json`.
Providers are local discovery accelerators. They do not replace source files,
verified onboarding, drift checks, branch validity, or memory promotion rules.

Use providers by retrieval substrate:

- `semantic`: use when a concept is known but the route or file location is
  unknown. GrepAI can serve this role over `memory-repos`.
- `relationship`: use when an anchor is known but relationships, callers,
  callees, dependencies, or impact paths are unknown. CodeGraphContext can serve
  this role per code repository.
- `intent`: use onboarding and bounded source confirmation when the anchor or
  location is known but hidden contracts, invariants, and code truths are
  unknown.

Provider settings should stay declarative: roots, runtime locations, watch
mode, freshness hooks, and transport policy. Start/stop/status/refresh behavior
belongs to provider lifecycle tooling, not to this prose file.

Provider installs should be coordination-owned. Use pinned requirements under
`providers/requirements/`, one reusable virtual environment per provider type
under `providers/_venvs/`, and version-checked patches under
`providers/patches/`.

Relationship providers must keep runtime artifacts out of code repositories
unless a repository-local config file is explicitly approved. For
CodeGraphContext, prefer one runtime root per code repo with config, ignore
rules, KuzuDB data, logs, and process state under
`providers/codegraphcontext/<repo-id>/.codegraphcontext/`.

Treat CGC runtime environment and persisted CGC config as separate surfaces.
`CGC_RUNTIME_DB_TYPE`, `KUZUDB_PATH`, and `CGC_RUNTIME_DB_PATH` are useful
process env controls, but CGC v0.4.10 reports them as invalid if they are
written into `<runtimeRoot>/.codegraphcontext/.env`. Persist only CGC-recognized
keys in `.env`.

CGC versions that create `.cgcignore` inside the indexed source repo are not
acceptable for managed provider mode until patched or fixed upstream. The
provider may read source repos, but discovery should not dirty them.

When a provider is missing, unhealthy, stale, or noisy, agents should degrade to
onboarding-only routing and bounded source search.

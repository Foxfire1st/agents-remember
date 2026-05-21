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
  this role across configured code repository roots.
- `intent`: use onboarding and bounded source confirmation when the anchor or
  location is known but hidden contracts, invariants, and code truths are
  unknown.

Provider settings should stay declarative: roots, runtime locations, watch
mode, freshness hooks, and transport policy. Start/stop/status/refresh behavior
belongs to provider lifecycle tooling, not to this prose file.

Installers, benchmark preparation, and worktree preparation should enter
provider setup through `scripts/provider-setup.py`. That script centralizes
dependency installation, watcher startup, and CGC seed import so benchmark and
worktree flows do not reimplement the same provider logic. These flows should
skip provider work entirely when the relevant `settings.json` does not enable
providers.

Provider installs should be coordination-owned. Use pinned requirements under
`providers/requirements/`, one reusable virtual environment per provider type
under `providers/_venvs/`, and version-checked patches under
`providers/patches/`.

Relationship providers must keep runtime artifacts out of code repositories
unless a repository-local config file is explicitly approved. For
CodeGraphContext, configure one `codegraphcontext-code` provider with a `roots`
array of `{ repoId, path }` entries. The lifecycle manager expands those entries
into per-repo runtime roots under
`providers/codegraphcontext/<repo-id>/.codegraphcontext/`, while all repos share
one lifecycle-owned FalkorDB Docker DBMS with persistent data under
`provider-data/codegraphcontext/falkordb/`.

Treat CGC runtime environment and persisted CGC config as separate surfaces.
`processEnvTemplate` is applied when launching CGC commands and should not be
blindly written into `<instanceRoot>/.codegraphcontext/.env`. Persist only keys
accepted by the installed CGC version.

Provider reinstall/update is non-destructive to provider data by default.
`providers/` is disposable scaffolding and may be deleted and recreated from
source during install. Runtime reinstall then installs dependencies for
providers enabled in the live coordinator settings, using the copied provider
pins and patches. Durable provider databases live under `provider-data/`;
deleting FalkorDB data, graph namespaces, or repository indexes still requires
an explicit destructive lifecycle command.

CGC versions that create `.cgcignore` inside the indexed source repo are not
acceptable for managed provider mode until patched or fixed upstream. The
provider may read source repos, but discovery should not dirty them.

When a provider is missing, unhealthy, stale, or noisy, agents should degrade to
onboarding-only routing and bounded source search.

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
│   └── settings.md
├── memory-repos/
├── tasks/
├── notes/
└── worktrees/
```

## Memory Repo Routing

Agents should still invoke the `c-08-ar-coordination-context-resolver` skill for the target code repository. The `c-08-ar-coordination-context-resolver` skill resolves
the active memory root and returns the memory layer's settings, tools, sources,
onboarding, and ledger paths.

Coordinator guidance may provide global defaults. Repository-specific memory
guidance is more specific and should win when the target repository has its own
rule.

## Context Providers

The coordinator does not own machine-readable provider authority. Provider
allow-lists, repository roots, workspace roots, and generated provider lifecycle
settings belong to the Agents Remember MCP settings file outside this
coordinator root. Providers are local discovery accelerators. They do not
replace source files, verified onboarding, drift checks, branch validity, or
memory promotion rules.

Use providers by retrieval substrate:

- `semantic`: use when a concept is known but the route or file location is
  unknown. GrepAI can serve this role over external memory repos and
  repo-internal `ar-memory/` roots.
- `relationship`: use when an anchor is known but relationships, callers,
  callees, dependencies, or impact paths are unknown. CodeGraphContext can serve
  this role across configured code repository roots.
- `intent`: use onboarding and bounded source confirmation when the anchor or
  location is known but hidden contracts, invariants, and code truths are
  unknown.

Provider settings should stay declarative in MCP settings. Start/stop/status,
refresh, install, and integrity behavior belongs to MCP/package-owned lifecycle
tooling, not coordinator-owned scripts.

Provider installs should be coordination-owned. Use pinned requirements under
`providers/requirements/`, version-checked patches under `providers/patches/`,
and Docker runner image locks beside those pins. Do not use `providers/_bin/`
or `providers/_venvs/` as managed provider contracts. Prefer Docker-wrapped
providers and backend services when the provider needs native binaries, a
database, or daemonized infrastructure. Do not require host-level PostgreSQL,
FalkorDB, Ollama, OS services, launch agents, package-manager services, Python
virtual environments, or global user daemons for normal managed provider mode.
Stable provider Dockerfiles, base Compose files, and override templates are
package-owned assets; MCP lifecycle code renders dynamic override YAML from
authority settings at command time and feeds it to Compose through trusted
execution input. Rendered overrides are not durable coordination or model
workspace files.

Semantic providers must keep generated config, index, logs, and state out of
source repositories and durable memory roots. For GrepAI, configure one
`grepai-memory` provider in workspace mode with explicit `{ projectId, path }`
roots for both external memory repos and repo-internal memory roots. The
managed default mirrors those roots into provider-owned index roots before
launching GrepAI, because GrepAI still keeps per-project symbol/config artifacts
beside each configured project path. The lifecycle manager writes GrepAI
workspace config, logs, provider state, and mirrored index roots under
`providers/runners/grepai/`, while all memory roots share one Docker network,
one lifecycle-owned PostgreSQL/pgvector container with persistent data under
`providers/data/grepai/postgres/`, and one lifecycle-owned Ollama container for
embedding. GrepAI itself runs from the lifecycle-owned runner container; managed
mode must not install GrepAI or Ollama into host user space. A `.grepai/`
directory inside any indexed memory root is a containment failure, not durable
memory.

Relationship providers must keep runtime artifacts out of code repositories
unless a repository-local config file is explicitly approved. For
CodeGraphContext, configure one `codegraphcontext-code` provider with a `roots`
array of `{ repoId, path }` entries. The lifecycle manager expands those entries
into per-repo runtime roots under
`providers/runners/codegraphcontext/<repo-id>/.codegraphcontext/`, while all repos share
one lifecycle-owned FalkorDB Docker DBMS on the shared CGC Docker network with
persistent data under `providers/data/codegraphcontext/falkordb/`.
CodeGraphContext itself runs from the lifecycle-owned Docker runner
image/container, launched as the host user when supported so mounted runtime
files stay user-owned; managed mode must not create or use a host Python virtual
environment for CGC commands.

Treat CGC runtime environment and persisted CGC config as separate surfaces.
`processEnvTemplate` is applied when launching CGC commands and should not be
blindly written into `<instanceRoot>/.codegraphcontext/.env`. Persist only keys
accepted by the installed CGC version.

Provider reinstall/update is non-destructive to provider data by default.
`providers/` contains a mix of package-owned defaults and live provider runtime
state. MCP runtime reinstall may recreate Docker runner instances, image build
roots, copied pins, and patches, while preserving `providers/data/`.
Generated MCP/provider operator logs live under `logs/`, with provider setup
summaries under `logs/providers/setup/`. Durable provider databases live under
`providers/data/`; deleting FalkorDB data, graph namespaces, or repository
indexes still requires an explicit destructive lifecycle command.

CGC versions that create `.cgcignore` inside the indexed source repo are not
acceptable for managed provider mode until patched or fixed upstream. The
provider may read source repos, but discovery should not dirty them.

When a provider is missing, unhealthy, stale, or noisy, agents should degrade to
onboarding-only routing and bounded source search.

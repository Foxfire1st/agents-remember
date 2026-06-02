# Architecture

Agents Remember separates four surfaces that are easy to confuse:

- the `agents-remember-md` source checkout
- the installed `ar-coordination` runtime
- a target code repository
- the target repository's memory root

## Source Checkout

```text
agents-remember-md/
  mcp/
    src/agents_remember/
      install/
      package_data/
        runtime/
        benchmarks/
  docs/
  roadmap/
```

The source checkout packages runtime and benchmark assets as Python package data, alongside the MCP server and public documentation. Agents working on this repository itself follow the root `AGENTS.md` in the checkout. Users of the runtime normally point their agent at the installed `ar-coordination/AGENTS.md`.

## Installed Runtime

```text
ar-coordination/
  AGENTS.md
  providers/
    requirements/
    patches/
    runners/
    data/
    logs/
  skills/
  system/
  memory-repos/
  tasks/
  notes/
  worktrees/
  temp/
```

The installer copies package-owned assets from `agents_remember/package_data/runtime/` into this tree. The MCP settings own the coordination root, so normal users configure that path in the MCP settings JSON rather than through source-checkout environment files.
The installed runtime does not keep a parallel `scripts/` execution route; MCP tools and package-local modules own runtime install, provider lifecycle, worktree, memory, and benchmark operations.

## Target Code Repository

This is the repository the agent is actually changing. It may contain internal memory:

```text
my-app/
  src/
  ar-memory/
```

When internal memory exists, the `c-08-ar-coordination-context-resolver` skill resolves it before checking for external memory.

## Internal Memory

Internal memory is the default. Durable memory lives inside the code repository:

```text
my-app/ar-memory/
  onboarding/
  docs/
  system/
    settings.md
    settings.json
    sources.md
    tools.md
```

The `c-00-initialize-memory-repo` skill creates this scaffold. The `c-03-repo-bootstrap` skill bootstraps onboarding content. The `c-05-create-or-update-onboarding-files` skill maintains file-level onboarding and repo entity catalogs.

## External Memory

External memory stores durable memory in one repo per selected code repository:

```text
ar-coordination/memory-repos/ar-my-app/
  memory.md
  onboarding/
  docs/
  system/
```

Use external memory when teams need a separate memory repository, branch-specific memory movement, or memory review outside the code repository.

## Resolution Order

The `c-08-ar-coordination-context-resolver` skill resolves a target repository by checking:

1. explicit inputs such as `code_repository_root`, `coordination_root`, or task contract
2. repo-local internal memory at `<repo>/ar-memory/`
3. external memory at `<coordination-root>/memory-repos/ar-<repo>/`

If neither supported memory location exists, the `c-08-ar-coordination-context-resolver` skill fails and asks the caller to initialize memory instead of inventing an empty context.

## Retrieval Substrates

Once memory is resolved, agents reach into memory and code through three substrates, routed by the `c-04-retrieval-strategy-router` skill:

- **By path (Intent)** — a known file's onboarding note, located directly from its path. Always available; needs no provider.
- **By meaning (Semantics)** — semantic search over the memory, for when the concept is known but the file is not.
- **By relationship (Relationship)** — a code-relationship graph for callers, callees, and dependencies.

The meaning and relationship substrates are served by **opt-in providers** — GrepAI for semantic memory search, CodeGraphContext for the code graph. They run as local Docker services under `ar-coordination/providers/` (shown above) and index code and memory in the background.

These providers are accelerators for *finding* knowledge; they are not the source of truth. Durable memory stays Markdown + Git, drift-checked and approval-gated, regardless of which substrate surfaced it. `runtime_install` builds the provider images and `provider_watchers` drive indexing — see the [Providers guide](guides/providers.md) and [MCP tool reference](reference/mcp-tools.md).

## Ownership Boundaries

Runtime install owns package assets under `ar-coordination`.

The `c-00-initialize-memory-repo` skill owns memory-root creation or repair.

The `c-03-repo-bootstrap` skill owns repo onboarding bootstrap and route/slice maintenance.

The `c-05-create-or-update-onboarding-files` skill owns file-level onboarding and repo entity catalog maintenance.

The `c-08-ar-coordination-context-resolver` skill owns context resolution facts only.

The `c-09-git-worktree-manager` skill owns worktree lifecycle, integration, and cleanup.

The `c-12-closeout` skill owns the closeout approval gate and the code → memory → ledger commit sequence, for both direct edits and worktree-backed tasks.

Provider lifecycle — building images, watchers, and indexing — is owned by the MCP provider tools and `runtime_install`, not by the memory or workflow skills.

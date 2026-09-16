# Architecture

Agents Remember separates four surfaces that are easy to confuse:

- the `agents-remember` source checkout
- the installed `ar-coordination` runtime
- a target code repository
- the target repository's memory root

## Source Checkout

```text
agents-remember/
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

This is the repository the agent is actually changing. It carries no memory of its own:
durable memory lives beside it, under the coordination root.

A repository that still carries the removed repo-local `ar-memory/` root is reported by exact
path and refused, not migrated. The route out is to re-point the repository at its external
memory root and record `memory_mode: external` on the worktree contracts, or to re-initialize
with the `c-00-initialize-memory-repo` skill.

## External Memory

External memory is the only supported topology. It stores durable memory in one repo per
selected code repository:

```text
ar-coordination/memory-repos/ar-my-app/
  memory.md
  onboarding/
  docs/
  system/
```

## Resolution Order

The `c-08-ar-coordination-context-resolver` skill resolves a target repository by checking:

1. explicit inputs such as `code_repository_root`, `coordination_root`, or task contract
2. the external memory repo at `<coordination-root>/memory-repos/ar-<repo>/`

If that memory location does not exist, the `c-08-ar-coordination-context-resolver` skill fails
and asks the caller to initialize memory instead of inventing an empty context. A repository
still carrying the removed repo-local `ar-memory/` root is refused by name with that exact path.

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

The `c-12-closeout` skill owns closeout approval and the code → memory-content commit sequence for
worktree-backed tasks and the sanctioned branch-direct landing route. Memory commit messages carry
`Code-Commit` attribution. `memory.md` is an ignored consumer cache computed from that history,
outside staging, commits, and transaction admission or recovery. Regenerating the cache does not
rewrite historical commits; historical attribution rewrites require explicit deployment work.

Provider lifecycle — building images, watchers, and indexing — is owned by the MCP provider tools and `runtime_install`, not by the memory or workflow skills.

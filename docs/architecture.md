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

When internal memory exists, C-08 resolves it before checking for external memory.

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

C-00 creates this scaffold. C-03 bootstraps onboarding content. C-05 maintains file-level onboarding and repo entity catalogs.

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

C-08 resolves a target repository by checking:

1. explicit inputs such as `code_repository_root`, `coordination_root`, or task contract
2. repo-local internal memory at `<repo>/ar-memory/`
3. external memory at `<coordination-root>/memory-repos/ar-<repo>/`

If neither supported memory location exists, C-08 fails and asks the caller to initialize memory instead of inventing an empty context.

## Ownership Boundaries

Runtime install owns package assets under `ar-coordination`.

C-00 owns memory-root creation or repair.

C-03 owns repo onboarding bootstrap and route/slice maintenance.

C-05 owns file-level onboarding and repo entity catalog maintenance.

C-08 owns context resolution facts only.

C-09 owns worktree lifecycle, direct closeout, and approved commit sequencing.

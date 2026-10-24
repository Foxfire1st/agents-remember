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
The installed runtime does not keep a parallel `scripts/` execution route; MCP tools and package-local modules own runtime install, provider-leftover cleanup, worktree, memory, and benchmark operations.

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
- **By meaning (Semantics)** — semantic search over the memory, for when the concept is known but the file is not. Its provider is retired; the route has no engine now.
- **By relationship (Relationship)** — a code-relationship graph for callers, callees, and dependencies. Its provider is retired as well.

The meaning and relationship substrates were served by two providers — GrepAI for semantic memory search, CodeGraphContext for the code graph. Both are retired (MIK-R112): no product path installs, enables, starts, indexes, or queries them, and the `providers/` tree shown above holds only what an older installation left behind.

These providers were accelerators for *finding* knowledge; they were never the source of truth. Durable memory stays Markdown + Git, drift-checked and approval-gated, whatever surfaced a candidate. `runtime_install` stops a leftover provider watcher and reports old provider keys once as unused; deleting leftover folders takes the explicit `providers-remove` command — see the [Providers guide](guides/providers.md), now a retirement and old-install cleanup account, and [MCP tool reference](reference/mcp-tools.md).

## Ownership Boundaries

Runtime install owns package assets under `ar-coordination`.

The `c-00-initialize-memory-repo` skill owns memory-root creation or repair.

The `c-03-repo-bootstrap` skill owns repo onboarding bootstrap and route/slice maintenance.

The `c-05-create-or-update-onboarding-files` skill owns file-level onboarding and repo entity catalog maintenance.

The `c-08-ar-coordination-context-resolver` skill owns context resolution facts only.

The `w-02-light-task-workflow` skill owns worktree lifecycle, integration, and cleanup.

The `w-02-light-task-workflow` skill owns closeout approval and the code → memory-content commit sequence for
worktree-backed tasks and the sanctioned branch-direct landing route. Memory commit messages carry
`Code-Commit` attribution. `memory.md` is an ignored consumer cache computed from that history,
outside staging, commits, and transaction admission or recovery. Regenerating the cache does not
rewrite historical commits; historical attribution rewrites require explicit deployment work.

Provider leftovers — a stale watcher, old settings keys, leftover runtime folders — are handled by `runtime_install` and the explicit `providers-remove` command, not by the memory or workflow skills. No provider lifecycle (images, watchers, indexing) belongs to anything now: it is retired.

## What a Read May Write

A read is every registered tool that its description calls a read, every `GET` route of the
dashboard, `worktree_status`, and every preview and dry run. A read changes no branch, tag or
`HEAD`; no file of a working tree; no Git index (reads run Git with `GIT_OPTIONAL_LOCKS=0`); no
task document and no requirement packet. A read may write only:

- (a) cache files under `<coordination root>/runtime/`;
- (b) when the reviewer records a comparison: the comparison's record in the task's reports
  folder, the captured objects, and one pin ref under `refs/ar/review/` (the archive hook
  removes them);
- (c) in a preview or dry run of a closeout: the leaf's worklist in its enclosure folder, its
  quality reports, and the objects of the tree the gate judged.
- (d) reads: when the caller of a context read asks for freshness, the writes of one `git fetch` of the configured upstream, all inside the Git directory: the fetched objects, the remote-tracking refs and their reflogs, `FETCH_HEAD`, a tag that Git's fetch follows by its default, and Git's own housekeeping after a fetch. It changes no local branch, no HEAD, no index and no file of a working tree. Without the argument the read fetches nothing.

A read creates no directory except to hold a file of (a) to (d). A capture that nothing records
(the index's capture, a currentness read) writes its objects into a scratch object directory,
never into the repository. A read that cannot keep this boundary (for example a cache directory
that cannot be written) is refused by name; it never falls back to writing somewhere else.

Reads, previews and dry runs file no per-call report under `temp/tool-reports`: the answer
carries the content and omits the filing pointer when nothing is filed (the codebase's own
drift-check idiom). Only an executed mutating action files its report; mutating apply modes
are not adjudicated here.

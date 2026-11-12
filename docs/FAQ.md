# FAQ

Common questions and objections about Agents Remember. [Concepts](concepts.md) defines the terms
the answers use.

## Design Principles

### What is the product: memory or workflow?

The memory is the product: cards and knowledge records in a Git repository of their own. The role
agents, the task documents and the checks exist to keep stale or speculative content out of that
memory.

The core rules are:

1. the card of a source file lies at a path derived from the path of the source file
2. the tools measure the anchors of a card against the code, so staleness is reported and not
   guessed
3. the build of a task waits for the user's approval
4. cards and knowledge records hold approved current state, not plans

A question that changes no code is answered in a chat and needs no task. Every code change belongs
to a task under a master; the Architect writes the task from the
[leaf-task](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/templates/leaf-task.md)
template, which names the parts of a task document: objective, requirements, design, acceptance,
steps and proposed code examples.

### How does an agent find the right memory?

Three ways, matched to what the agent already knows:

- **By path**: when it has the file in hand, the card is at a deterministic path (`src/foo/bar.ts` → `<memory-root>/onboarding/src/foo/bar.ts.md`). Nothing is searched or ranked: the path is the address, so reads stay predictable.
- **By tool**: `read_ar_files` names the governing card and the knowledge records of up to 20
  paths, and `knowledge_read` names the invariants and families attributed to one source file.
  Both return names and locations; the agent opens the named files itself.
- **By text search**: when it knows a concept but no file, the agent searches the code and the
  cards as text.

See [Concepts](concepts.md#how-an-agent-finds-memory).

### Is this just documentation?

It is documentation with stricter placement, verification, and promotion rules.

READMEs and architecture docs orient people. A card answers a different question: what will an agent probably miss if it changes this file? That includes invariants, naming contracts, operational scars, edges to other repositories, and intent that code alone does not expose.

## Memory Layer

### Does memory get slower as it grows?

A card is loaded by path, so the cost of *reading* memory is proportional to the files in scope for a task, not the size of the repository. The [knowledge index](concepts.md#knowledge-index) is a cache that Agents Remember derives from one memory tree and rebuilds when it is absent; it helps the tools find records, and the cards themselves are still read by path.

A large code repository can still accumulate a lot of memory, but the agent does not need to read all of it. It reads the root overview of the repository, the [route overview](concepts.md#route-overview) of the folder when one exists, and the cards for the files it is touching.

### What stops cards from bloating?

Scope. A card should capture what code cannot say clearly on its own. It should not restate the implementation, duplicate type signatures, or become a dumping ground for task notes.

When a card grows too large, that is often a signal that the source file has too much coupling or that a route overview should hold shared context while file-specific notes stay small.

### How does the agent know memory is not stale?

The JSON sidecar of a card records an [anchor](concepts.md#anchor) for each reference,
[realization](concepts.md#realization-and-proof) and proof. These tools compare those anchors with
a selected code tree or repair them; the [MCP tool reference](reference/mcp-tools.md) describes
each:

- `context_packet` with `include_drift` counts the reference anchors as `current` or `stale` and
  names unreadable sidecars and validator findings. Without the argument nothing is measured.
- `knowledge_read` reports the currentness of an invariant as `current`, `stale`, `unverifiable`
  or `unrealized`.
- `citation_fix` repairs an anchor whose content only moved. The Curator writes a stale anchor
  anew.

Staleness alone blocks nothing. A stale card can still be read as a direction, but the agent must
not treat it as verified current behavior. At closeout the [knowledge gate](concepts.md#knowledge-gate)
judges the exact code and memory trees of the task, and open findings block the commit.

### What happens when a file moves?

The card moves with the source path. When a task renames or deletes a source file, the Curator of
that task moves or retires the card and re-points or removes every reference to the old path. The
Curator follows the [card procedure](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md), the skill's rules for the layout of cards and for checking their citations, for the move. A reference that still names the old path is
reported as `stale`, because its path is absent from the code tree.

### How does this handle cross-file or cross-repository coupling?

A card can include explicit docs references and references to other repositories. The agent follows those edges when they matter instead of eagerly loading a transitive closure.

Context for the whole repository belongs in the root `overview.md`. Larger repositories can use route overviews in the mirrored folders under `onboarding/`. Recurring entities and naming contracts can live in `entities.md`. A truth that several files share stands once in a knowledge record.

The context of one code repository can include another repository. The memory settings file lists
the other repositories under `crossRepo.allow`, each with the branch it must be on in the key
`expectedBranch`; the [settings reference](reference/settings-json.md#memory-settings-file-keys)
lists the keys of an entry.

## Workflow Layer

### Do I need a task?

Yes, for every code change. Chat is never a build route: every code change belongs to a task
document under a master, also a change of one file. The Architect writes the task from the
[leaf-task](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/templates/leaf-task.md)
template with the tool `task_doc`; the template names the parts of the task document, from the
objective to the proposed code examples. A question that changes no code needs no task; a chat
answers it.

### Is this overengineered?

The memory itself is small: Markdown files, JSON files, Git metadata, and deterministic paths. The
work around it is heavier. A code change is a task, a Worker builds it, a Reviewer reviews it, a
Curator updates the memory, and the knowledge gate checks the result before the commit.

The weight has one adjustable part. A task normally runs in an enclosure of its own; with a
direct-execution grant it runs in the checkouts of the master's branch.
[Workflows](workflows.md) describes both routes.

### What happens when the agent discovers something during implementation?

The agent routes the discovery to the right place instead of quietly changing requirements or memory.

The Worker does not widen the requirements and does not repair memory. It names what it met in its
[hand-off list](concepts.md#hand-off-list) for the Curator, including weaknesses of code outside
its task. The Curator writes durable current-state findings into memory through the
[curation operation](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/operations/curation.md),
the skill's instructions for reconciling a task's intent, code and memory. Requirement or
architecture changes need the user's explicit approval: the Architect revises the requirement
packets and briefs the affected tasks again.

### Why keep task documents separate from cards?

Task documents contain plans, decisions, proposed examples, and in-progress notes. Cards and knowledge records contain approved current-state knowledge.

Mixing them would pollute memory with speculation. The separation is what lets agents reason from memory without wondering whether it describes reality or merely a plan.

## The Memory Repository

### Why is the memory a repository of its own?

The memory of each code repository lies in a Git repository of its own. It is branched, committed
and reviewed separately from the code, and the memory commit of a closeout names the code commit
that it describes. This adds commit discipline, and that discipline is what makes a memory update
attributable.

### What is `memory.md`?

`memory.md` is a file at the root of the memory repository. Agents Remember computes it from the
`Code-Commit` trailers of the memory commits: it lists each pair of code commit and memory commit,
newest first. Git ignores the file, because Agents Remember adds the line `/memory.md` to the
memory repository's `.gitignore`, and no memory commit holds it.

Worktree start, closeout and landing take the pairs from the commits. A missing or outdated
`memory.md` does not block them. Baseline adoption, closeout, `worktree_sync` and `direct_landing`
write the file anew after their memory commit.

These reads use the content of the file:

- `context_packet` with `include_freshness` reports whether the file maps the checked-out code
  commit.
- The [cross-repository context](concepts.md#cross-repository-context) includes the memory of
  another repository only when, among the other conditions of its entry, that repository's
  `memory.md` can be read; an unreadable `memory.md` leaves that repository's code only.
- Closeout reports in `memory_ledger_repair` how the file differed from the pairs it computed.
- The dashboard's server reads the newest pairs of the file.

The Manager or the Orchestrator performs the closeout of a task ([closeout operation](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/operations/closeout.md)); at the landing, the worktree tools take the pairs of code commit and memory commit from the `Code-Commit` trailers of the memory commits, so code and memory do not drift apart silently.

### How is a repository's memory located?

The tool server resolves it per code repository: each
listed code repository binds to `<coordination-root>/memory-repos/ar-<id>/`, and one
repository's memory never moves another's.

## Comparisons

### How is this different from task workflow systems?

Task workflow systems organize work. Agents Remember primarily preserves codebase knowledge across work. Its role agents and task documents organize work too, and they exist to keep that knowledge current.

### How is this different from vector memory or graph memory?

Vector memory and graph memory are ways to *find* text. Agents Remember defines what the memory *is*: cards and knowledge records that are versioned, reviewable, diffable, measured against the source, and updated only after approved work has passed review. An agent finds a card by the path of its source file, by the tools that name the knowledge of a path, or by text search.

## What This Is Not

The durable memory of record is not:

- a hosted service
- a database you query instead of reading files
- a general knowledge base
- a replacement for READMEs, docstrings, or architecture docs
- a reason to skip the user's approval

It is memory as Markdown and JSON files in Git, with a tool server, role agents and checks that keep it current.

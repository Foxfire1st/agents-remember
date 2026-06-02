# FAQ

Common questions and objections about Agents Remember.

## Design Principles

### What is the product: memory or workflow?

The memory layer is the product. The workflow modes exist to protect that memory layer from stale or speculative content.

The core rules are:

1. onboarding is path-derived
2. onboarding is checked for drift before planning
3. implementation waits for developer approval
4. onboarding records approved current state, not plans

Read-only answers, chat builds, and durable W-02 tasks are different weights of the same discipline.

### How does an agent find the right memory?

Three ways, matched to what the agent already knows:

- **By path** — when it has the file in hand, the note is at a deterministic path (`src/foo/bar.ts` → `ar-memory/onboarding/src/foo/bar.ts.md`). No ranking, embedding threshold, or index step — the path is the address, so reads stay predictable.
- **By meaning** — when it knows the concept but not the file, semantic search over the memory returns candidate notes.
- **By relationship** — when it knows an anchor but not its connections, a code graph answers callers, callees, and dependencies.

By-path needs nothing extra; meaning and relationship are opt-in providers. See [Concepts](concepts.md) and [Providers](guides/providers.md).

### Is this just documentation?

It is documentation with stricter placement, verification, and promotion rules.

READMEs and architecture docs orient people. File-level onboarding answers a different question: what will an agent probably miss if it changes this file? That includes invariants, naming contracts, operational scars, cross-repo edges, and intent that code alone does not expose.

## Memory Layer

### Does memory get slower as it grows?

File-level onboarding is loaded by path, so the cost of *reading* it is proportional to the files in scope for a task, not the size of the repository. The optional semantic index grows with the memory like any corpus, but it only helps *find* notes — the notes themselves are still read by path.

Large repos can still accumulate a lot of memory, but the agent does not need to read all of it. It reads the repo overview, relevant route-local overview when one exists, and onboarding for the files it is touching.

### What stops onboarding files from bloating?

Scope. Onboarding should capture what code cannot say clearly on its own. It should not restate the implementation, duplicate type signatures, or become a dumping ground for task notes.

When an onboarding unit grows too large, that is often a signal that the source file has too much coupling or that a route-local overview should hold shared context while file-specific notes stay small.

### How does the agent know memory is not stale?

File-level sidecar onboarding records verification metadata. `C-02-memory-quality-control` compares the source file against that verification point before the agent plans against onboarding.

Typical outcomes are `up to date`, `drifted`, `missing verification`, `missing`, `orphaned`, `disabled`, or `unsupported`.

Drifted onboarding can still be useful historical context, but the agent must treat it as directional rather than verified current behavior.

### What happens when a file moves?

The onboarding should move with the source path. If it does not, drift detection can classify the old onboarding as orphaned. C-05 owns file-level onboarding relocation, while structural route changes can route through C-03.

### How does this handle cross-file or cross-repo coupling?

File-level onboarding can include explicit docs references and cross-repo references. The agent follows those edges when they matter instead of eagerly loading a transitive closure.

Repo-wide context belongs in `overview.md`. Larger repos can use route-local overviews in the mirrored onboarding hierarchy. Recurring entities and naming contracts can live in `entities.md`.

## Workflow Layer

### Do I need a durable task?

Often no. A chat build is the default for changes that fit in one session.

Use a W-02 light task when the work needs a durable task file or checklist. When it outgrows a single-page plan — broad, high-risk, or many distinct slices — escalate to a master + light sub-task series.

### Is this overengineered?

The memory layer is intentionally small: Markdown files, Git metadata, and deterministic paths. The workflow layer can be heavier, but it is optional and should match task risk.

If a change fits in one session, use a chat build. If it needs a durable plan, use a W-02 light task. If it outgrows one page or spans several slices, use a master + light sub-task series.

### What happens when the agent discovers something during implementation?

The agent should route the discovery to the right artifact instead of quietly changing requirements or memory.

Durable current-state findings can go through C-05. Requirement or architecture changes need explicit developer approval. C-01 findings capture keeps that routing visible.

### Why keep task files separate from onboarding?

Task files contain plans, decisions, proposed examples, and in-progress notes. Onboarding contains approved current-state knowledge.

Mixing them would pollute memory with speculation. The separation is what lets agents reason from onboarding without wondering whether it describes reality or merely a plan.

## External Memory

### Why use external memory?

External memory is useful when code and memory should live in separate repositories or when branch-specific code and memory need ledgered alignment.

Most users should start with internal memory under `<repo>/ar-memory/`. External memory adds operational power but also adds commit and ledger discipline.

### What is `memory.md`?

`memory.md` is the external-memory ledger. It records which memory commit was verified against which code commit. C-12 writes it during closeout and C-09 uses it during worktree integration, so code and memory do not drift apart silently.

### Can a workspace mix internal and external memory?

Yes. C-08 resolves topology per target repository. A repo with `<repo>/ar-memory/` uses internal memory. A repo with only `ar-coordination/memory-repos/ar-<repo>/` uses external memory. One repository does not force the choice onto its siblings.

## Comparisons

### How is this different from task workflow systems?

Task workflow systems organize work. Agents Remember primarily preserves codebase knowledge across work. The workflows in this repository are useful, but they sit on top of the memory layer.

### How is this different from vector memory or graph memory?

It isn't an alternative to them — it uses them. Semantic search over the memory and a code-relationship graph are first-class (opt-in) substrates for *finding* memory. What's distinctive is what memory *is*: durable notes that are versioned, reviewable, diffable, drift-checked against source, and updated only after approved work lands — independent of how they were found.

## What This Is Not

The durable memory of record is not:

- a hosted service
- a database you query instead of reading files
- a general knowledge base
- a replacement for READMEs, docstrings, or architecture docs
- a reason to skip human approval

It is a Markdown and Git based memory convention with skills that help agents follow it consistently. The optional providers (semantic search, code graph) build derived indexes *over* that memory and code to help find it — they do not replace it as the source of truth.

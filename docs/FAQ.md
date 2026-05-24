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

Chat, light task, and heavy task are different weights of the same discipline.

### Why path-derived memory instead of retrieval?

Retrieval systems are useful when the question is "what might be related?" Agents Remember is built for a narrower question: "what context applies to this file?"

When an agent opens `src/foo/bar.ts`, the matching onboarding path is deterministic:

```text
ar-memory/onboarding/src/foo/bar.ts.md
```

No ranking, embedding threshold, top-k cutoff, or index is needed. That makes reads predictable and keeps unrelated but semantically similar material out of context.

### Is this just documentation?

It is documentation with stricter placement, verification, and promotion rules.

READMEs and architecture docs orient people. File-level onboarding answers a different question: what will an agent probably miss if it changes this file? That includes invariants, naming contracts, operational scars, cross-repo edges, and intent that code alone does not expose.

## Memory Layer

### Does memory get slower as it grows?

Not in the same way as a retrieval corpus. File-level onboarding is loaded by path, so the cost is proportional to the files in scope for the task, not the size of the repository.

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

### Do I need the heavy workflow?

Usually no. Chat mode is the default.

Use light task when the work needs a durable task file or checklist. Use heavy task when the developer asks for it or when the risk justifies full phased research, design, planning, implementation, and review gates.

### Is this overengineered?

The memory layer is intentionally small: Markdown files, Git metadata, and deterministic paths. The workflow layer can be heavier, but it is optional and should match task risk.

If a change fits in one session, use chat. If it needs a durable plan, use light task. If a plausible mistake would be expensive, use heavy task.

### What happens when the agent discovers something during implementation?

The agent should route the discovery to the right artifact instead of quietly changing requirements or memory.

Durable current-state findings can go through C-05. Requirement or architecture changes need explicit developer approval. Heavy tasks use C-01 findings capture and review gates to keep that routing visible.

### Why keep task files separate from onboarding?

Task files contain plans, decisions, proposed examples, and in-progress notes. Onboarding contains approved current-state knowledge.

Mixing them would pollute memory with speculation. The separation is what lets agents reason from onboarding without wondering whether it describes reality or merely a plan.

## External Memory

### Why use external memory?

External memory is useful when code and memory should live in separate repositories or when branch-specific code and memory need ledgered alignment.

Most users should start with internal memory under `<repo>/ar-memory/`. External memory adds operational power but also adds commit and ledger discipline.

### What is `memory.md`?

`memory.md` is the external-memory ledger. It records which memory commit was verified against which code commit. C-09 uses it during worktree closeout and integration so code and memory do not drift apart silently.

### Can a workspace mix internal and external memory?

Yes. C-08 resolves topology per target repository. A repo with `<repo>/ar-memory/` uses internal memory. A repo with only `ar-coordination/memory-repos/ar-<repo>/` uses external memory. One repository does not force the choice onto its siblings.

## Comparisons

### How is this different from task workflow systems?

Task workflow systems organize work. Agents Remember primarily preserves codebase knowledge across work. The workflows in this repository are useful, but they sit on top of the memory layer.

### How is this different from vector memory or graph memory?

Vector and graph memory usually answer "what is related?" Agents Remember answers "what applies to this path, and has it been verified against current source?"

That tradeoff is deliberate. It is less flexible than broad retrieval, but it is easier to audit, review, diff, and trust.

## What This Is Not

Agents Remember is not:

- a hosted service
- a database
- a general knowledge base
- a replacement for READMEs, docstrings, or architecture docs
- a semantic retrieval engine
- a reason to skip human approval

It is a Markdown and Git based memory convention with skills that help agents follow it consistently.

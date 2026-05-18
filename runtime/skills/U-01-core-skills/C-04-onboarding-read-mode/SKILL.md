---
name: c-04-onboarding-read-mode
description: "Use existing onboarding as the primary read path for repository source work: navigate from repo overview to route overview to candidate source/sidecar pairs, then use scoped source confirmation."
---

# C-04 Onboarding Read Mode

Use this skill when a task requires understanding repository source with Agents
Remember memory.

C-04 turns onboarding into the navigation layer for code reading:

```text
repo overview -> route overview -> candidate files -> source+sidecar paired read
-> targeted source confirmation
```

## Onboarding Read Protocol

Treat the read as a state machine, not as background advice. Move through these
states in order and do not skip ahead because a search feels convenient.

State 1: onboarding root known

- Action: read `<onboarding_root>/overview.md`.
- If `memory_root` is known and `onboarding_root` is not explicitly named, use
  `<memory_root>/onboarding`.
- Forbidden: locating, listing, or inventorying the memory or onboarding tree.

State 2: route overview selected

- Action: read the smallest relevant route overview(s).
- Use repo overview evidence and the task domain to choose the route.
- Forbidden: repository-root source search or multi-subsystem source search.

State 3: candidate source set built

- Action: read each candidate source file with its deterministic sidecar:
  `<onboarding_root>/<repo-relative-source-path>.md`.
- Before relying on a source file, carry this evidence format:
  - source: `<repo-relative-source-path>`
  - sidecar: `<onboarding_root>/<repo-relative-source-path>.md`
  - sidecar status: `read` or `absent`
  - reason: why this pair is relevant
- Forbidden: source-only interpretation when the sidecar exists and has not
  been read.

State 4: named unresolved question remains

- Action: run one targeted source `rg` or `find` for that named question,
  scoped to the smallest selected route.
- Read the resulting source/sidecar pairs before searching again.
- Forbidden: a second or broader search until you can state what the first
  scoped search failed to answer.

Operational order:

1. Read the repository `onboarding/overview.md`.
2. Select the smallest relevant route overview(s) from the repo overview and the
   task domain.
3. Read those route overview(s), with attention to:
   - load-bearing files
   - file-level onboarding maps
   - local invariants and traps
   - child overview links
4. Build a small candidate source set from the route overview evidence. For
   each candidate, identify:
   - source path
   - deterministic sidecar path:
     `<onboarding_root>/<repo-relative-source-path>.md`
   - reason the candidate is relevant
5. Read candidate source files with their sidecar onboarding in paired batches.
6. When a candidate sidecar is absent, read the nearest governing `overview.md`
   and carry the gap as context.
7. Use source `rg` or `find` as targeted confirmation for one named unresolved
   question, scoped to the smallest selected route first.
8. Expand search one route at a time only after explaining what the previous
   scoped search and paired reads failed to answer.

## Hard Boundaries

Use C-04 to read existing onboarding as a navigational layer for source work.

Do not start with:

1. `rg --files`
2. broad `find`
3. onboarding-tree inventory
4. repository-root source search
5. multi-subsystem source search
6. cross-repo search

These are fallback actions only after the overview chain and candidate
source/onboarding pairs fail to answer a stated question.

If `onboarding_root` is known, onboarding-tree inventory is never a normal
setup step. Start at `<onboarding_root>/overview.md`.

Fallback source search has a budget: one named question, one smallest-route
search, then read the resulting source/sidecar pairs before searching again.

Do not perform cross-repo discovery unless the task evidence, repo overview, or
route overview explicitly identifies a cross-repo boundary. Vendored or
third-party directories are not cross-repo targets by default.

# Concepts

Agents Remember is built around a small set of concepts. The names matter because agents use them to decide what to read, what to trust, and what they are allowed to change.

## Onboarding Unit

An onboarding unit is durable repository knowledge that an agent can retrieve deterministically.

In the default repo-local mode, a source file maps to a mirrored Markdown file:

```text
src/foo/bar.ts
ar-memory/onboarding/src/foo/bar.ts.md
```

The onboarding unit should explain what the code does not make obvious: invariants, intent, boundaries, conventions, cross-repo edges, and risky assumptions. It should not duplicate code, type signatures, or obvious implementation details.

## Memory Root

The memory root is where durable memory for one code repository lives.

Internal memory:

```text
<repo>/ar-memory/
```

External memory:

```text
<ar-coordination>/memory-repos/ar-<repo>/
```

Internal memory is the default. External memory is for teams that intentionally want memory in a separate repository.

## Coordination Root

The coordination root is the installed runtime and local work area:

```text
ar-coordination/
```

It owns installed skills, installed `AGENTS.md` templates, task files, notes, worktrees, temporary drift reports, and external memory repo checkouts.

## Path-Derived Memory

Agents Remember does not use embeddings, semantic search, or top-k retrieval for file-level onboarding. The path of the source file determines the path of the onboarding file. That keeps the read surface small and avoids pulling in related-looking but irrelevant material.

## Retrieval Strategy Substrates

File-level onboarding remains path-derived and verifiable, but agents often need
different retrieval strategies before they know which file-level memory to read.
C-04 is the retrieval strategy router for that job.

The router starts from the model's current intent: what kind of missing context
does the agent need next, and in what shape should that context arrive? It then
chooses the memory substrate that best fits that intent.

- `Semantics`: use when the concept is known, but the structure or location is
  unknown. A semantic memory provider can search memory repos for candidate
  routes or files.
- `Relationship`: use when an anchor is known, but the surrounding
  relationships, impact paths, callers, callees, or dependencies are unknown. A
  relationship provider can search code structure for candidate connections.
- `Intent`: use when an anchor or location is known, but the hidden contracts,
  invariants, behavioral expectations, branch-valid truths, or code intent are
  unknown. Onboarding plus bounded source confirmation is the default substrate
  for this layer.

These substrates are complementary. A triage task may start from anchors in a
ticket, use the Relationship substrate to understand nearby structure, then move
to the Intent substrate to learn the code truths that make a change safe.

Provider output is candidate routing evidence, not proof. Source files,
verified onboarding, drift checks, branch validity, and approved memory
promotion remain the truth controls.

## Drift Detection

Each file-level sidecar onboarding unit records verification metadata, including the source commit it was checked against. Before planning against onboarding, `C-02-memory-quality-control` compares the source file with that verification point.

Common outcomes:

- `up to date`: no source diff since verification
- `drifted`: source changed and onboarding needs review
- `missing verification`: the onboarding unit lacks enough metadata to trust
- `orphaned`: the source file no longer exists
- `unsupported`: the storage or file shape cannot be verified safely

Stale onboarding can still be read directionally when that trust level is explicit, but it should not silently become current truth.

## Approval-Gated Memory

Onboarding records approved current state. It does not record speculation.

That means:

- chat work waits for developer approval before implementation
- light tasks wait for task approval before implementation
- heavy tasks promote durable findings only after their review gates
- onboarding updates happen after approved changes, not before

## Route-Local Overviews

The root onboarding overview gives repo-level orientation. Larger repositories can also use route-local overviews in the mirrored onboarding hierarchy when a package, module, or source slice needs its own durable context.

File-level onboarding remains one-to-one with source files. Route-local overviews add structure; they do not replace file-specific onboarding when file-specific facts matter.

## Memory Ledger

External memory repos use `memory.md` as a ledger that maps code commits to memory commits. This lets a branch carry the memory version that matches its code state.

The ledger matters for worktree-backed tasks and external-memory closeout because code and memory may move together but still live in separate repositories.

# Concepts

Agents Remember is built around a small set of concepts. The names matter because agents use them to decide what to read, what to trust, and what they are allowed to change.

## Onboarding Unit

An onboarding unit is durable repository knowledge that an agent can retrieve deterministically.

A source file maps to a mirrored Markdown file inside its repository's memory repo:

```text
src/foo/bar.ts
ar-coordination/memory-repos/ar-my-app/onboarding/src/foo/bar.ts.md
```

The onboarding unit explains what the code does not make obvious: invariants, intent,
boundaries, conventions, cross-repo edges and risky assumptions. In converted memory,
its Markdown is paired with `onboarding/src/foo/bar.ts.json`: a structured sidecar
holding references, realization claims and, for tests, proofs. The prose describes
current truth; shared invariants, families and decisions live in records under
`knowledge/`, rather than being copied into each card. See the [knowledge-system
reference](reference/knowledge-system.md).

## Memory Root

The memory root is where durable memory for one code repository lives:

```text
<ar-coordination>/memory-repos/ar-<repo>/
```

External memory is the only supported topology: memory is a repository of its own, so it can be
branched, committed and reviewed separately from the code it describes. Repo-local internal
memory under `<repo>/ar-memory/` was removed from the product; a repository that still carries
that layout is refused by name with its exact path.

## Coordination Root

The coordination root is the installed runtime and local work area:

```text
ar-coordination/
```

It owns installed skills, installed `AGENTS.md` templates, task files, notes, worktrees, temporary drift reports, and external memory repo checkouts.

## How Agents Find Memory

Agents reach memory through three substrates, matched to what they already know.

- **By path** — when an agent has a file in hand, its note is located directly from the path: `src/foo/bar.ts` → `<memory-root>/onboarding/src/foo/bar.ts.md`. No lookup, ranking, or index step — the path *is* the address. Reads stay predictable, and unrelated-but-similar material stays out of context.
- **By meaning** — when the concept is known but the file is not, semantic search over the memory found candidate notes. Its provider is retired, so this route has no engine now. This is the reverse of `code → onboarding`: a vague description led to an onboarding note, whose path then named the code file, with pointers to the surrounding logic.
- **By relationship** — when an anchor is known but its connections are not, a code-relationship graph answered callers, callees, and dependencies. That provider is retired too.

By-path notes are the core and need nothing extra. Meaning and relationship were served by two opt-in providers that are now retired, so by-path is the route with an engine today. The router that chooses among them is described next (it names the three *Intent*, *Semantics*, and *Relationship*). Whatever the route, what becomes *durable* memory is still drift-checked and approval-gated — finding is not the same as trusting.

## Retrieval Strategy Substrates

File-level onboarding remains path-derived and verifiable, but agents often need
different retrieval strategies before they know which file-level memory to read.
The `c-04-retrieval-strategy-router` skill is the retrieval strategy router for that job.

The router starts from the model's current intent: what kind of missing context
does the agent need next, and in what shape should that context arrive? It then
chooses the memory substrate that best fits that intent.

- `Semantics`: use when the concept is known, but the structure or location is
  unknown. A semantic memory provider searched memory repos for candidate
  routes or files; that provider is retired, so this route has no engine now.
- `Relationship`: use when an anchor is known, but the surrounding
  relationships, impact paths, callers, callees, or dependencies are unknown. A
  relationship provider searched code structure for candidate connections; that
  provider is retired too.
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

In converted memory, references, realization claims and proofs carry code anchors
in JSON sidecars. Currentness compares those recorded anchors with the selected
code tree. Read the card beside its sidecar and source; a card’s prose alone does
not establish that an anchor still names the current code.

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

- `w-02-light-task-workflow` tasks wait for task approval before implementation
- a master + light sub-task series gates each slice's approval and commit in turn
- onboarding updates happen after approved changes, not before

## Route-Local Overviews

The root onboarding overview gives repo-level orientation. Larger repositories can also use route-local overviews in the mirrored onboarding hierarchy when a package, module, or source slice needs its own durable context.

File-level onboarding remains one-to-one with source files. Route-local overviews add structure; they do not replace file-specific onboarding when file-specific facts matter.

## Knowledge Judgments and Publication

`knowledge/layout.json` marks a converted memory tree. A record's Git history
records changes to its meaning; Curator-authored rows in
`knowledge/history/<owner>.json` record judgments about the changes a leaf, wave or
crossing made. Closed history files remain frozen; a reopened leaf uses an
attempt-qualified successor file.

A worklist compares base and candidate code and memory trees. Its items identify
knowledge the changes reach; a current satisfying history row answers each item.
The mandatory knowledge gate recomputes the worklist and validates the exact
candidate trees. Open items, unreadable inputs and refusing validator violations
block publication. These checks do not invent the Curator's judgment.

## Memory Attribution Cache

Memory-content commits carry `Code-Commit` attribution. `memory.md` is an ignored,
computed cache of that history, outside staging, commits and transaction authority.
The code and memory commits remain the durable pair; a missing cache does not
block closeout or landing.

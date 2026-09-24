---
name: c-14-knowledge-bootstrap
description: "Build or resume a repository's first knowledge foundation through the existing curator: resolve the repository and its code/memory line, inventory bounded sources, hand the evidence to the curator, author invariants/families through the curator's own writer, publish through the taskless bootstrap, read the result back, and report coverage and unresolved work."
---

# c-14-knowledge-bootstrap Knowledge Bootstrap

A repository's memory has two halves that are created in different ways. Onboarding is **Markdown a
seat writes**: overviews and file cards, scaffolded by the `c-03-repo-bootstrap` skill and maintained
by `c-05-create-or-update-onboarding-files`. The knowledge foundation is **authored records in the
knowledge database**: invariants and their facets, the families and guarantees that hold obligations
together, the exact source realizations, and the external sources they rest on. Nothing scaffolds
that half. It is authored, and this skill is the procedure that authors it — for a repository that
has none, and for one resuming a foundation that is already part-built.

**This is a procedure, not a role and not a second orchestration system.** There is no
knowledge-bootstrap agent, no parallel onboarding track and no second database. The semantic owner is
the **existing curator**, the writer is the **existing admitted knowledge writer**, and the
publication lands at the **one location the ordinary read route already selects**. This skill states
the order those existing owners are used in, and the states a run must distinguish.

## When To Use

| Situation | Use this skill? |
| --- | --- |
| A new project has a memory root but no knowledge foundation | yes — the first-foundation entry |
| The knowledge location holds something that is not a readable dataset of this repository | yes — read the state first: reporting it is this run's job, and moving, deleting or migrating the object standing there is the developer's decision, not this run's |
| An existing project has Markdown memory only, and no knowledge database | yes — this is the ordinary existing-project entry |
| A previous bootstrap was interrupted, or stopped after publishing part of its scope | yes — resume; the retained record and the destination name what is still owed |
| The repository's foundation exists and the requested work extends one route | yes, as an extend/refresh run over that bounded scope |
| A leaf's own change set needs its requirement-shaped items authored | no — the curator's ordinary route is `agents-remember knowledge-ingest` inside that leaf; this skill is for the repository's foundation, not a leaf's delta |
| Only onboarding is missing | no — `c-03-repo-bootstrap` owns that, and this skill does not require onboarding to exist |

Onboarding is **optional input, never a precondition**. A repository with no onboarding file at all
is a supported starting state: the source inventory in step 2 is what the run reads, and a thick
corpus of onboarding is one of the sources available to it rather than a gate in front of it.

## Who Runs It

**This procedure is the curator's work. Which session carries it is the product's decision, and the
product decides by the task document.** A session is opened for a role, and the opener admits a role
with **no** task document only for the taskless seats — `chat`, a plain `terminal` pane, `bootstrap`,
and (since the developer's 2026-09-24 ruling) `curator`. Every other role is refused until a task
document is named:

```text
400  {"status": "task-binding-required", "detail": "named role scope is required"}
```

There are therefore three real entries, and no longer a fourth that does not exist:

- **A task exists** — open the curator on that task's document, through the ordinary launch route.
  That is the curator's own seat, and step 4's **leaf entry** is its writer: the foundation it builds
  is published onto the line that task's own readers resolve.
- **No task exists, first hour** — the **bootstrap** seat carries this procedure. It is the seat the
  product admits without a task document *and* instructs about the repository's knowledge foundation:
  it reads the state at the declared location and reports it (see the `l-01-agent-lifecycles`
  bootstrap role and operation). It does **not** author the records — it reaches the step and hands
  it to the curator.
- **No task exists, the foundation is to be built now** — the taskless writer in step 4 is run by an
  instructed session that holds this procedure, from a workspace with **no enclosure in scope**: that
  writer refuses one (`enclosure_in_scope`) so a bootstrap can never publish onto a task's memory line.
  **The write plane does not gate on role:** its admission is the repository entry, the resolved memory
  line and the real revisions, and its authorship envelope names the actor from `--authorization-ref`.
  That is exactly why the semantic ownership matters here — the reconciliation, the record actions, the
  family guarantees and the memberships are authored under the curator's rules, and a session that is
  not a curator seat follows them rather than inventing its own.
- **A taskless *curator* seat exists, and this is the session that authors the foundation when no task
  does.** The developer's **2026-09-24 ruling** admitted `curator` to the taskless seat roles, taken
  when the missing route was put to them; before it, the only taskless carriers were the bootstrap seat
  and this procedure held by an instructed session. So a repository that must build its foundation
  before any task exists has two carriers: the bootstrap seat, which reads the state and hands the step
  on, and a **curator-labelled session with no task document**, which holds the curator's own rules and
  this procedure and can author. Which one actually carried a run is what the run's retained record
  says — the ruling widened the seats that may open, and did not change what a run records about its
  own actor.

**Collection and search assistance may be delegated; the authored result may not.** A curator may
spawn read-only sub-agents to read source at exact revisions, search providers, or summarize declared
external documents, and may accept evidence collected by another seat — but the semantic
reconciliation, the record actions, the family guarantees and the memberships are authored by the
curator, and the curator's hand-off list is the only thing handed to the writer.

## Inputs

- **The repository id the MCP authority settings declare**, and the resolved coordination context
  (`context_packet`, `resolve_context`, `server_info`). There is no default repository.
- **The code and memory revision the foundation is being built against.** The taskless entry derives
  both from the real checkouts; a caller does not type them. Both lines must resolve `HEAD`: a
  checkout that answers no commit is refused by name (`code_revision_unavailable`,
  `memory_revision_unavailable`) with its own next action, because an admission with no commit would
  make every later snapshot and candidate reference meaningless while still looking admitted.
- **The requested scope** — the whole repository, or a named area, route or obligation set.
- **The available sources** — code at exact revisions; declared external specifications and
  documentation; existing onboarding when it is present.
- **The current knowledge state**, read before anything is authored (step 1).
- **The developer's commit word**, when the run is to write. A run without it plans and writes
  nothing.

## The Procedure

### 1. Resolve the entry and name the current state

Resolve the repository and the memory line first, then read what the knowledge location holds **now**.
Four states are four different facts, and a run that merges them invents history:

| State | Where it is observed | What it means, and the move |
| --- | --- | --- |
| **Uninitialized** — `not-recorded` | the `knowledge-bootstrap --status` report's `destinationNow.state`, or the `knowledge.state` block `memory_init` returns | No publication is recorded at the declared location. This is the first-foundation entry: continue to step 2. |
| **Populated** — `recorded` | the same two surfaces, plus `destinationContents` on a run | A dataset bound to this repository stands there. Read it before extending it: never reinitialize what exists. |
| **Corrupt or unavailable** — `unusable` | `destinationNow.state` / `memory_init`'s `knowledge.state`, with the shipped refusal code (`selected_input_unavailable` when there is no file to open, `snapshot_unavailable` when there are bytes that are not the expected dataset) | Something is there that is not a dataset this route can answer from, or it belongs to another repository's authority home. **Report the exact state and path; do not delete, overwrite or migrate it**, and do not report the repository as uninitialized. |
| **Context not admitted** — `context-not-admitted` | `memory_init`'s `knowledge` block | The bootstrap admission itself refused (see the refusal table below). The route is the refusal's own `nextAction`; it is not a knowledge state at all. |

A location with no file-system entry is `not-recorded`; a location holding a directory where the
dataset belongs is `unusable`. They are never reported as each other, and a null or missing count is
never rendered as a measured zero.

### 2. Build a bounded source inventory and a coverage plan

Inventory the scope's source areas at exact revisions, and say what the plan will cover and what it
will not. Read code; consult the declared external sources that actually govern the scope; use
existing onboarding when it is present, as one input among several.

- Cover **root and area contracts and their realizations**, not one mandatory record per file. A
  foundation is a small set of load-bearing obligations with real anchors, not a transcription of the
  source tree.
- Record each external source with its document identity, its version or retrieval time, the digest of
  what was inspected when one was taken, and the location that was read.
- A missing optional source is **absent, not failed**, and it produces no invented fact.
- Keep the plan resumable: a partial run that stops here must be able to name the areas it did not
  reach.

### 3. Hand the evidence to the curator

The inventory is evidence, and the curator reconciles it. Assemble the hand-off list in the shape the
`skills/l-01-agent-lifecycles/templates/curator-handoff-list.md` template owns — one JSON document
per list, its entry fields carrying what is true and where it was evidenced — and reconcile it into
the knowledge vocabulary before authoring anything.

The reconciliation is the substance of the run, and it is the curator's:

- supported **invariants and facets**, with their applicability, essential conditions and exclusions;
- **families**: where the evidence justifies a joint obligation, the family's own guarantee text and
  the exact memberships that place exact invariant revisions in it; where it does not, the deliberate
  `no_family` outcome **with its basis**. An obligation that was not examined is left with neither key
  and is reported as **unexamined** — never as family-free;
- **source realizations** that anchor actual code or memory bytes, at an exact revision;
- **external sources** declared under the list's own external-source key, so a document identity is
  never written as a repository path with a Git blob;
- **existing records are reused or revised, not duplicated.** Text that matches is not an identity
  rule; a correction is a successor that names the stored identity and the revision it supersedes.

Where code contradicts accepted intent, **preserve the intent and record the contradiction** rather
than quietly rewriting either half.

### 4. Author through the curator's own writer

The writer is the existing admitted knowledge batch writer, reached by the shipped command line. The
taskless entry is:

```text
agents-remember knowledge-bootstrap --repo <repo_id> --list <hand-off list>
    --authorization-ref <ref> --commit
```

- **Planning is the default, and planning is the dry run.** Without `--commit` the list is read, the
  candidate is planned, the destination is read and **nothing is written**: no batch, no publication
  and no retained progress record. Run it that way first and put its report in front of the
  developer.
- **`--commit` is the developer's commit word**, and it is the whole of the write act. The curator
  does not give itself that word.
- **`--authorization-ref` is the authorization this run is admitted under, and it is also the actor
  the authorship envelope names.** It must not be blank and must not be invented. One reference, so
  "who authorized this" and "who authored it" stay one recorded fact.
- **No enclosure is fabricated and no boolean grants admission.** The taskless entry resolves its own
  real admission — the repository entry the settings declare, the memory layer the ordinary read route
  resolves, and the exact code and memory revisions the real checkouts stand at — and its report
  carries that provenance. A repository whose only knowledge is its first knowledge therefore needs
  no development leaf, no worktree and no synthetic enclosure, and creating one to satisfy an argument
  list is exactly what this entry exists to make unnecessary.
- The destination is **derived, never accepted**: it is the location the ordinary read route itself
  selects. No argument on this surface aims a bootstrap at another location.

The leaf route — for a repository that already has a leaf, authoring that leaf's own change set — is
the curator's ordinary one:

```text
agents-remember knowledge-ingest --contract <this leaf's enclosure contract> --list <list>
    --authorization-ref <ref> --baseline <the published dataset this task forked from>
    --publish --commit --json
```

`--publish` selects the repository's one declared published dataset location and `--publish-to <path>`
is the caller-named alternative; the two are mutually exclusive, so neither is ever invented.

### 5. Read the result back — the report is the result

**Exit zero is not a publication claim.** Read the report and state each of these as its own fact:

- `run.batchState`, and the entries under `run.committed` / `run.skipped` / `run.refused` — a
  per-entry refusal is a **result**, not a tool failure;
- `publication.state`, and `publishedIdentity` — an independent read of the declared location through
  the read route's own owner: `confirmed`, `mismatch`, or `unavailable`;
- `destinationContents` — what the location holds, with `publishedByThisRun` saying whether any of it
  is this run's work. On a run that published nothing, this block describes a **read of the location**
  and says so;
- `remaining`, `unmeasured`, `carried` and `remainingBasis` — what is still owed, what this run did
  **not** measure, what it inherited from an earlier run, and on what basis;
- `progressRecordWritten`, and the `sourceRevisions` the admission bound.

Verify the stored result through the ordinary read surfaces as well — the published dataset identity,
and the records themselves — so the claim is about the store and not about the run's own prose.

### 6. Report coverage and unresolved work

Report, in the curator's own output:

- the source areas examined, and the areas **not** examined;
- candidate knowledge considered, records stored and revised, and the deliberate no-family outcomes
  with their bases;
- known duplicates, contradictions found, missing sources, and deferred work;
- the exact published dataset identity an independent read confirmed, or the state that says nothing
  was published.

**A partial run stays explicitly partial and keeps a resumable next action.** Progress may be
preserved — the retained record and the owed entries are how — but a run that did not examine a
required area **cannot claim the foundation complete for it**. An empty destination, a batch that
committed nothing, or an exit status of zero is not a populated foundation.

### 7. Clean up through the bounded owner, when there is anything to clean

```text
agents-remember knowledge-bootstrap --repo <repo_id> --discard-staging
```

The staging root is removed **only when the declared location provably holds the very dataset the
staged candidate holds**, read from both files rather than inferred from a finished-looking run, and
the command refuses by name in every other state and leaves the bytes exactly as they are. Staging is
never removed to tidy a run that has not published.

## Failure And Recovery Behavior

| State | Observed from | The move |
| --- | --- | --- |
| A repository the settings do not declare (`repository_not_allowed`) | the run's refusal payload: `state`, `code`, `detail`, `nextAction` | Report it and stop: there is no default repository. |
| No coordination root, no code checkout, a checkout that is not a Git checkout, an unreadable revision (`coordination_root_unavailable`, `code_checkout_unavailable`, `code_checkout_is_not_a_git_checkout`, `code_revision_unavailable`, `memory_revision_unavailable`) | the same refusal payload | Report the real route the refusal names. Do not substitute a path or a revision. |
| No memory layer resolved, or the resolved line is not the declared one (`memory_layer_not_resolved`, `memory_line_moved`) | the same refusal payload | Repair the repository entry or the coordination settings, then re-observe. Never publish onto a line the ordinary reader does not select. |
| The resolved context is a task's enclosure (`enclosure_in_scope`) | the same refusal payload | Resolve the bootstrap with no enclosure selector in scope; a leaf's delta belongs to the leaf route. |
| The destination holds something the writer cannot treat as this repository's dataset (`destination_unusable`) | the same refusal payload | Repair or relocate the object standing there and re-observe. **Nothing was written and the previous dataset is intact.** |
| The staging root belongs to another operation (`staging_belongs_to_another_operation`) | the same refusal payload | Reconcile that staging root before resuming: one staging directory belongs to exactly one bootstrap operation. |
| A run was interrupted, or a later run is narrower than an earlier one | the retained record, read by `--status` | Resume through the same entry. An exact retry replays the batch and does not duplicate records; **changed content under a stored entry identity is refused by design** and is corrected with a successor that names the stored identity. A narrowing run does not silently drop owed work: the record and the run's own `remaining` are what say what is owed. |
| A moved input revision | `sourceRevisions` in a run's report, against the checkouts | A moved source is an explicit new observation, not a silent reuse: re-observe and decide, and keep the previously published dataset as it was until a publication replaces it. |
| A missing optional source | the source inventory | Absent, not failed. It becomes no fact. |

## Preservation Boundaries

1. **No new agent role, harness, scheduler or parallel onboarding implementation.** The curator, the
   bootstrap seat and the setup path are the existing carriers.
2. **No blind onboarding import.** Onboarding is a source to read and reconcile, never a corpus to
   copy into the store.
3. **No hard cutover of operational Markdown.** Onboarding stays where it is and stays supported; a
   foundation does not retire it, and nothing here rewrites a memory file to make a check pass.
4. **No second database, second writer or second destination.** The knowledge batch writer, the
   publication owner and the location the ordinary read route selects are the only ones used.
5. **No invented semantic approval and no invented authority.** The commit word is the developer's,
   the authorization reference is the run's own, and admission is derived from the repository entry
   and the real revisions rather than from a flag.
6. **No fabricated development leaf, worktree, enclosure or task document**, and no manually created
   database, to satisfy an input shape.
7. **No invented project truths, no back-dating, no backfilled task history, no favorable default in
   place of absent evidence.** A fixture, a prototype or another repository's dataset is never
   presented as this repository's foundation.
8. **The delivery gates keep their owners.** This procedure reports its outcome; installation,
   closeout, integration and activation stay with the seats and surfaces that already own them.

## Relationship To Other Skills

| Skill or surface | Relationship |
| --- | --- |
| `c-13-install-and-onboard` | The ordinary setup path. Its memory-repo stages reach the knowledge foundation through this skill; it does not reimplement it, and it does not report a repository's knowledge as ready without this skill's outcome. |
| `c-03-repo-bootstrap` | Builds Markdown onboarding. Its handoff names the knowledge foundation as this skill's step in both directions: onboarding is optional input here, and this procedure does not write onboarding. |
| `c-00-initialize-memory-repo` | Creates or repairs the memory root. `memory_init` reports where this repository's knowledge foundation lives and what a read of that location finds now — the state step 1 reads. It creates no knowledge and never pretends to. |
| `c-10-adopt-memory-baseline` | Commits memory *content* as the first attributed baseline. A baseline is not a knowledge foundation: neither substitutes for the other, and this procedure neither adopts a baseline nor claims one. |
| `c-05-create-or-update-onboarding-files` | Owns onboarding content. The curator writes onboarding there, and authors the foundation here. |
| `c-02-memory-quality-control` | The complete curation operation the curator runs around its work; the foundation's authored records are not a substitute for it, and it is not a substitute for authoring them. |
| `l-01-agent-lifecycles` | Houses the carriers: `skills/l-01-agent-lifecycles/roles/curator.md` and `skills/l-01-agent-lifecycles/operations/curation.md` (the semantic owner and its authoring route), and `skills/l-01-agent-lifecycles/roles/bootstrap.md` and `skills/l-01-agent-lifecycles/operations/bootstrap.md` (the first-hour seat and the step that reaches this procedure). |
| `templates/curator-handoff-list.md` | The shape the hand-off list is authored in. This skill names the shape; that template owns it. |

## Acceptance Criteria

1. A new project reaches this procedure without any onboarding existing — through the taskless
   bootstrap seat, whose own instructions name it, through a **taskless curator seat** (admitted by
   the developer's 2026-09-24 ruling, and instructed by the same capsule route), and through the
   shipped skill catalog, which any instructed session reads — and a curator seat opened on a task
   document holds the same procedure.
2. The state at the knowledge location is read before anything is authored, and `not-recorded`,
   `recorded` and `unusable` are reported as the three different facts they are — with a refused
   admission (`context-not-admitted`) reported as an admission failure rather than as any of the
   three.
3. The taskless entry runs with planning as its default, and a run without the developer's commit
   word writes no batch, no publication and no retained record.
4. Admission is derived from the declared repository entry and the real revisions, and no enclosure,
   leaf or database is fabricated at any point.
5. Every entry's outcome, the publication result, the independent read-back and the remaining work are
   read from the report, and a zero exit status is never quoted as a publication.
6. A partial run reports itself partial, names the areas it did not examine, and leaves a resumable
   next action.
7. The foundation is published at the location the ordinary read route selects, and the identity a
   later task's planner will read is stated exactly.
8. Nothing in this skill writes onboarding, changes another skill's owner, or adds a role, a store or
   a destination.

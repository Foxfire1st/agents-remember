---
name: c-14-knowledge-bootstrap
description: "Build or resume a repository's first knowledge foundation through the existing curator: resolve the repository and its code/memory line, inventory bounded sources, hand the evidence to the curator, author invariants/families through the curator's own file writer as a taskless bootstrap wave, read the result back, and report coverage and unresolved work."
---

# c-14-knowledge-bootstrap Knowledge Bootstrap

A repository's memory has two halves that are created in different ways. Onboarding is **cards a
seat writes**: overviews and file cards, scaffolded by the `c-03-repo-bootstrap` skill and maintained
by `c-05-create-or-update-onboarding-files`. The knowledge foundation is **authored records, stored
as text files in the memory repository**: invariants, the families and guarantees that hold
obligations together, decisions and the other record kinds under `knowledge/`, and the exact source
realizations and proofs in the file sidecars. Nothing scaffolds that half. It is authored, and this
skill is the procedure that authors it — for a repository that has none, and for one resuming a
foundation that is already part-built.

**This is a procedure, not a role and not a second orchestration system.** There is no
knowledge-bootstrap agent, no parallel onboarding track and no knowledge database. The semantic owner
is the **existing curator**, the writer is the **existing curator file writer**, and the records land
in the **memory repository the ordinary read route already selects**. This skill states the order
those existing owners are used in, and the states a run must distinguish.

## When To Use

| Situation | Use this skill? |
| --- | --- |
| A new project has a memory root but no knowledge foundation | yes — the first-foundation entry |
| The memory tree is in the legacy format (no `knowledge/layout.json`) | not yet — the write commands refuse it with `legacy-format`. Converting it (`agents-remember knowledge-convert`) is its own step, and the developer's decision |
| An existing project has onboarding and no knowledge records | yes — this is the ordinary existing-project entry |
| A previous bootstrap was interrupted, or stopped after writing part of its scope | yes — resume under the same wave; the wave's history file and the records in the tree name what was written |
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
  is written into that leaf's memory worktree.
- **No task exists, first hour** — the **bootstrap** seat carries this procedure. It is the seat the
  product admits without a task document *and* instructs about the repository's knowledge foundation:
  it reads the state at the declared location and reports it (see the `l-01-agent-lifecycles`
  bootstrap role and operation). It does **not** author the records — it reaches the step and hands
  it to the curator.
- **No task exists, the foundation is to be built now** — the taskless writer in step 4 is run by an
  instructed session that holds this procedure, from a workspace with **no enclosure in scope**: that
  writer refuses one (`enclosure_in_scope`) so a bootstrap can never write onto a task's memory line.
  **The write plane does not gate on role:** its admission is the repository entry, the resolved memory
  line and the real revisions, and its report records the `--authorization-ref` it ran under.
  That is exactly why the semantic ownership matters here — the reconciliation, the record actions, the
  family guarantees and the memberships are authored under the curator's rules, and a session that is
  not a curator seat follows them rather than inventing its own.
- **A taskless *curator* seat exists, and this is the session that authors the foundation when no task
  does.** The developer's **2026-09-24 ruling** admitted `curator` to the taskless seat roles, taken
  when the missing route was put to them; before it, the only taskless carriers were the bootstrap seat
  and this procedure held by an instructed session. So a repository that must build its foundation
  before any task exists has two carriers: the bootstrap seat, which reads the state and hands the step
  on, and a **curator-labelled session with no task document**, which holds the curator's own rules and
  this procedure and can author. The ruling widened the seats that may open, and did not change what
  a run records.

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
  make every anchor the run resolves meaningless while still looking admitted.
- **The requested scope** — the whole repository, or a named area, route or obligation set.
- **The available sources** — code at exact revisions; declared external specifications and
  documentation; existing onboarding when it is present.
- **The current knowledge state**, read before anything is authored (step 1).
- **The developer's commit word**, when the run is to write. A run without it plans and writes
  nothing.

## The Procedure

### 1. Resolve the entry and name the current state

Resolve the repository and the memory line first, then read what the memory tree holds **now**.
Four states are four different facts, and a run that merges them invents history:

| State | Where it is observed | What it means, and the move |
| --- | --- | --- |
| **Context not admitted** — `context-not-admitted` | `memory_init`'s `knowledge` block, or the refusal a `knowledge-bootstrap` run prints | The bootstrap admission itself refused (see the refusal table below). The route is the refusal's own `nextAction`; it is not a knowledge state at all. |
| **Legacy format** | the memory root holds no `knowledge/layout.json`; `knowledge_read` and the write commands answer `legacy-format` | The tree is not converted, and nothing can be written to it. **Report it; do not create the marker by hand.** The refusal names the conversion command. |
| **Converted, no records** | the memory root holds `knowledge/layout.json` and no record file under `knowledge/<kind>/` | This is the first-foundation entry: continue to step 2. A memory root that `memory_init` created is in this state. |
| **Converted, records present** | record files under `knowledge/<kind>/`; read them with `knowledge_read` (`memoryRoot` and one of `view="family"`, `view="invariant"`, `view="source_context"`; without a seed each lists the records the tree holds) | A foundation exists. Read it before extending it: never author a second record for an obligation the tree already holds. |

For an admitted converted memory tree, `memory_init.knowledge` reports `not-recorded` when its
complete index contains no authored knowledge records, `recorded` when the complete index contains
authored records, and `unusable` when the index is partial. Read its `detail` and `nextAction`: a
partial index names the knowledge files to repair before rebuilding the derived index. `datasetPath`
names that derived index; its mere existence does not prove an authored foundation. An admission
refusal reports `context-not-admitted`, with its actual `code`, `detail` and `nextAction`. An
unconverted root reports `unusable` with `code: legacy-format`; follow the returned crossing-sync or
conversion route, never create a layout marker by hand. Other unreadable states remain unusable:
report the returned reason and recovery rather than infer empty knowledge. Independently read the
text records through `knowledge_read` before extending the foundation; `recorded` establishes their
presence, not semantic coverage of the requested scope.

### 2. Build a bounded source inventory and a coverage plan

Inventory the scope's source areas at exact revisions, and say what the plan will cover and what it
will not. Read code; consult the declared external sources that actually govern the scope; use
existing onboarding when it is present, as one input among several.

- Cover **root and area contracts and their realizations**, not one mandatory record per file. A
  foundation is a small set of load-bearing obligations with real anchors, not a transcription of the
  source tree.
- Note each external source with its document identity and its version or retrieval time, so the
  report can name what was read.
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
  author the invariant's `scope` object with `applicability`, `conditions` and `exclusions` before
  ingest. Empty clause lists mean examined and none; missing scope remains `unfilled_curation_scope`,
  never an inferred workflow sentence;
- an authored **realization rationale for every target**: why that place carries the invariant,
  specific to the construct it names, with an optional `role`. The writer never generates one and
  refuses a list with an unexplained target (`realization_rationale_absent`);
- **families**: where the evidence justifies a joint obligation, a `family` record in the list's
  `records` section with the family's own guarantee text, its members, its routes and its admission.
  An obligation that was not examined for a family is reported as **unexamined** — never as
  family-free;
- **source realizations** that anchor actual code bytes, resolved at the exact revision the admission
  bound;
- **external documents** are never written as a repository path with a Git blob;
- **existing records are reused or revised, not duplicated.** Text that matches is not an identity
  rule; a correction names the stored record's ID and updates it;
- **admission** for every new invariant, family and decision (MIK-R27): the criterion it meets and a
  one-sentence justification, as below.

**Admission (MIK-R27).** A foundation is a small set of meaningful records, not a second test suite
written as prose. Each new invariant claims `spans_locations` (realized in more than one file),
`guarded_by_test` (a proof entry names it), `family_guarantee` or `prevents_costly_mistake` (naming
the costly error); a family claims `joint_guarantee`; a decision claims `real_alternatives` or
`constrains_future_work`. The knowledge validator refuses a new record with no
criterion, with `legacy-unassessed` (the export's mark), with a justification made only of task,
leaf, requirement or ruling (`D14`) references, commit hashes and provenance words ("Per ruling
D14", "Added in commit a4eba7b7"), or with a `spans_locations` or `guarded_by_test` claim its
entries do not support. An exported record (its `legacyId` derives its ID) or an already-stored
record is only reported. A statement that meets no criterion is not a record: it stays prose in the
onboarding Markdown under "Boundaries". A code change alone is never a reason. The reviewer judges
whether each justification is plausible.

- *Admitted:* "Landing pairs code and memory commits", `spans_locations`, "Realized in
  `mcp/src/agents_remember/worktrees/modules/integrate.py`, which refuses an unpaired landing, and
  in `mcp/src/agents_remember/worktrees/ledger_projection.py`, which derives the ledger rows."
- *Admitted:* the decision to give a family several local routes, `real_alternatives`, "One owning
  route per family was weighed and rejected, because a family spanning subtrees collapses to the
  repository root."
- *Refused:* "Every source-content expansion carries admission" justified "introduced by L43": only
  a leaf reference.
- *Refused:* an invariant claiming `guarded_by_test` "because test_direct_landing covers it" while
  no proof entry names it: add the proof, or claim a criterion that holds.

The full rule, with the checked and the judged criteria, is in the hand-off template's "The
admission rule (MIK-R27)" section.

Where code contradicts accepted intent, **preserve the intent and record the contradiction** rather
than quietly rewriting either half.

### 4. Author through the curator's own writer

The writer is the curator file writer, reached by the shipped command line. A bootstrap has no leaf,
so it writes as a **wave**: `--wave` names it, and the wave's judgment rows go to
`knowledge/history/<wave>.json`. Plan first:

```text
agents-remember knowledge-bootstrap --config <active MCP authority settings> --repo <repo_id> --list <hand-off list>
    --wave <wave> --authorization-ref <ref> --json
```

Then, with the developer's commit word, write:

```text
agents-remember knowledge-bootstrap --config <active MCP authority settings> --repo <repo_id> --list <hand-off list>
    --wave <wave> --authorization-ref <ref> --commit --json
```

- **Planning is the default, and planning is the dry run.** Without `--commit` the list is read, the
  tree the run would produce is validated and reported, and **nothing is written**. Run it that way
  first and put its report in front of the developer. The record IDs a planning run prints are
  provisional: the writing run mints its own.
- **Name the active MCP authority settings explicitly with `--config`.** Use the same absolute
  settings path that configured this repository's serving MCP. Default CLI discovery may find
  another harness's settings and therefore another repository registry. Use that explicit path
  for planning and for `--commit`, and verify the repository the report names before proceeding. Do
  not change global discovery or invent an enclosure to repair a wrong selection.
- **`--commit` is the developer's commit word**, and it is the whole of the write act. It writes
  files into the admitted memory root; it makes no Git commit. The curator does not give itself that
  word.
- **`--authorization-ref` is the authorization this run is admitted under.** It must not be blank and
  must not be invented. The report records it.
- **No enclosure is fabricated and no boolean grants admission.** The taskless entry resolves its own
  real admission — the repository entry the settings declare, the memory layer the ordinary read route
  resolves, and the exact code and memory revisions the real checkouts stand at. A repository whose
  only knowledge is its first knowledge therefore needs no development leaf, no worktree and no
  synthetic enclosure, and creating one to satisfy an argument list is exactly what this entry exists
  to make unnecessary.
- The memory root is **derived, never accepted**: it is the one the ordinary read route itself
  selects. No argument on this surface aims a bootstrap at another location.
- **The memory root must be converted.** On a tree in the legacy format the command refuses with
  `legacy-format`, writes nothing, and names the conversion command.

The leaf route — for a repository that already has a leaf, authoring that leaf's own change set — is
the curator's ordinary one:

```text
agents-remember knowledge-ingest --contract <this leaf's enclosure contract> --list <list>
    --authorization-ref <ref> --commit --json
```

It writes into that leaf's memory worktree and takes no destination argument.

### 5. Read the result back — the report is the result

**Exit zero is not a claim that the foundation exists.** Read the report and state each of these as
its own fact:

- `state`: `written`, `planned` or `refused`. A refused run wrote nothing and names every problem;
- the records and the sidecar entries the run wrote, each with its ID, its path and whether it was
  created, updated, unchanged or removed;
- the history rows written to the wave's history file;
- the tests the evidence names and what became of each (`proof_written`, `needs_facet`,
  `unresolvable`).

Verify the stored result through the ordinary read surface as well — `knowledge_read` over the memory
root (`memoryRoot`) for the records and families the run wrote, and `agents-remember
knowledge-validate` for the tree — so the claim is about the files and not about the run's own prose.

### 6. Report coverage and unresolved work

Report, in the curator's own output:

- the source areas examined, and the areas **not** examined;
- candidate knowledge considered, records created and updated, and the obligations examined for a
  family and left without one, with the reason;
- known duplicates, contradictions found, missing sources, and deferred work;
- the files the run wrote, or the state that says nothing was written.

**A partial run stays explicitly partial and keeps a resumable next action.** A rerun of the same
list under the same wave writes the same files with the same IDs, so a resumed run continues the
wave. A run that did not examine a required area **cannot claim the foundation complete for it**. A
tree with no records, a refused run, or an exit status of zero is not a populated foundation.

## Failure And Recovery Behavior

| State | Observed from | The move |
| --- | --- | --- |
| A repository the settings do not declare (`repository_not_allowed`) | the run's refusal payload: `state`, `code`, `detail`, `nextAction` | Report it and stop: there is no default repository. |
| No coordination root, no code checkout, a checkout that is not a Git checkout, an unreadable revision (`coordination_root_unavailable`, `code_checkout_unavailable`, `code_checkout_is_not_a_git_checkout`, `code_revision_unavailable`, `memory_revision_unavailable`) | the same refusal payload | Report the real route the refusal names. Do not substitute a path or a revision. |
| No memory layer resolved, or the resolved line is not the declared one (`memory_layer_not_resolved`, `memory_line_moved`) | the same refusal payload | Repair the repository entry or the coordination settings, then re-observe. Never write onto a line the ordinary reader does not select. |
| The resolved context is a task's enclosure (`enclosure_in_scope`) | the same refusal payload | Resolve the bootstrap with no enclosure selector in scope; a leaf's delta belongs to the leaf route. |
| The memory root is in the legacy format (`legacy-format`) | the refusal the command prints | Report it. Conversion (`agents-remember knowledge-convert`) is its own step and the developer's decision. **Nothing was written.** |
| The repository is under its cutover lock | the refusal the command prints | The repository holds converted memory on another line and this line is not converted yet. Follow the route the refusal names (the crossing sync). |
| The writer refused the list | the report: `state: refused`, with every problem named | Correct the list and run it again. **Nothing was written.** |
| A run was interrupted, or a later run is narrower than an earlier one | the wave's history file and the records in the tree | Resume through the same entry and the same wave. A rerun of the same list writes the same files with the same IDs and does not duplicate records. A rerun removes the sidecar entries this wave wrote earlier from an entry the list no longer names, and reports them `removed`. |
| A moved input revision | the code revision the admission bound, against the checkout | A moved source is an explicit new observation, not a silent reuse: re-observe and decide. Anchors are resolved at the revision the run was admitted on. |
| A missing optional source | the source inventory | Absent, not failed. It becomes no fact. |

## Preservation Boundaries

1. **No new agent role, harness, scheduler or parallel onboarding implementation.** The curator, the
   bootstrap seat and the setup path are the existing carriers.
2. **No blind onboarding import.** Onboarding is a source to read and reconcile, never a corpus to
   copy into the store.
3. **Onboarding stays where it is.** A foundation does not retire or rewrite a card, and nothing
   here rewrites a memory file to make a check pass.
4. **No database, no second writer and no second destination.** The curator file writer and the
   memory root the ordinary read route selects are the only ones used. Records and sidecar entries
   are never written by hand.
5. **No invented semantic approval and no invented authority.** The commit word is the developer's,
   the authorization reference is the run's own, and admission is derived from the repository entry
   and the real revisions rather than from a flag.
6. **No fabricated development leaf, worktree, enclosure or task document**, and no layout marker
   created by hand, to satisfy an input shape.
7. **No invented project truths, no back-dating, no backfilled task history, no favorable default in
   place of absent evidence.** A fixture, a prototype or another repository's records are never
   presented as this repository's foundation.
8. **The delivery gates keep their owners.** This procedure reports its outcome; installation,
   closeout, integration and activation stay with the seats and surfaces that already own them.

## Relationship To Other Skills

| Skill or surface | Relationship |
| --- | --- |
| `c-13-install-and-onboard` | The ordinary setup path. Its memory-repo stages reach the knowledge foundation through this skill; it does not reimplement it, and it does not report a repository's knowledge as ready without this skill's outcome. |
| `c-03-repo-bootstrap` | Builds onboarding. Its handoff names the knowledge foundation as this skill's step in both directions: onboarding is optional input here, and this procedure does not write onboarding. |
| `c-00-initialize-memory-repo` | Creates or repairs the memory root. `memory_init` creates a new memory root in the text format (it writes `knowledge/layout.json`) and reports whether the bootstrap context is admitted — part of the state step 1 reads. It creates no knowledge and never pretends to. |
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
2. The state of the memory tree is read before anything is authored, and a tree in the legacy
   format, a converted tree with no records and a converted tree with records are reported as the
   three different facts they are — with a refused admission (`context-not-admitted`) reported as an
   admission failure rather than as any of the three.
3. The taskless entry runs with planning as its default, and a run without the developer's commit
   word writes no file.
4. Admission is derived from the declared repository entry and the real revisions, and no enclosure,
   leaf, layout marker or database is fabricated at any point.
5. Every record's outcome, the history rows and the remaining work are read from the report, and a
   zero exit status is never quoted as a written foundation.
6. A partial run reports itself partial, names the areas it did not examine, and leaves a resumable
   next action.
7. The foundation is written into the memory root the ordinary read route selects, and the records a
   later task's planner will read are named by ID and path.
8. Nothing in this skill writes onboarding, changes another skill's owner, or adds a role, a store or
   a destination.

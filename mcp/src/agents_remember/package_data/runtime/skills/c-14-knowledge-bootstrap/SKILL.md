---
name: c-14-knowledge-bootstrap
description: "Build or resume a repository's first knowledge foundation through the existing curator, as regular task work the user-started Architect organises: resolve the repository and its code/memory line, inventory bounded sources, hand the evidence to the curator, author invariants/families through the curator's own file writer on the task's leaf, read the result back, and report coverage and unresolved work."
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
| A new project has a memory root but no knowledge foundation | yes — as the procedure the Architect's first task runs for it |
| The memory tree is in the legacy format (no `knowledge/layout.json`) | not yet — the write commands refuse it with `legacy-format`. Converting it (`agents-remember knowledge-convert`) is its own step, and the developer's decision |
| An existing project has onboarding and no knowledge records | yes — the ordinary existing-project case |
| A previous bootstrap was interrupted, or stopped after writing part of its scope | yes — resume on the same task with the same list; the leaf's history file and the records in the tree name what was written |
| The repository's foundation exists and the requested work extends one route | yes, as an extend/refresh run over that bounded scope |
| A leaf's own change set, unrelated to the repository's foundation, needs its requirement-shaped items authored | no — the curator's ordinary curation of that leaf owns it; this skill is the repository's foundation, not a leaf's incidental delta |
| Only onboarding is missing | no — `c-03-repo-bootstrap` owns that, and this skill does not require onboarding to exist |
| No task exists yet, and the repository's foundation is wanted now | not yet — setup reports the state it read, and the developer starts the Architect, whose task opens this procedure (see Who Runs It) |

Onboarding is **optional input, never a precondition**. A repository with no onboarding file at all
is a supported starting state: the source inventory in step 2 is what the run reads, and a thick
corpus of onboarding is one of the sources available to it rather than a gate in front of it.

## Who Runs It

**This procedure is the curator's work, and it runs as regular task work that the user-started
Architect organises.** That order is fixed: every clause below is one link of it.

1. **Setup stops at the memory setup and the baseline.** The bootstrap seat of a first run
   establishes the repository's memory root and first attributed baseline, reports existing
   onboarding and the knowledge state it read, and tells the developer to start the Architect. It
   neither reaches this procedure nor authors a record — see
   `skills/l-01-agent-lifecycles/operations/bootstrap.md` step 8 and its
   `reference/bootstrap-foundation-d301.md`.
2. **The developer starts the Architect**, the one next step setup names.
3. **The Architect opens a task for the foundation** — one leaf with its task document, or a leaf under
   a master, or under a sprint, according to the size of the work. That task owns the foundation's
   making: its scope, its order and its completion are decided there, not in this skill.
4. **The curator authors the foundation inside that task**, started on the task's document through the
   ordinary launch route. Step 4's writer writes into the memory worktree that task's enclosure
   contract names, and the leaf's own closeout publishes it like any other memory change.

**There is no taskless entry to this procedure, and no instructed session stands in for one.** A
curator's authoring seat is opened on a task document, and an instruction to author a repository's
foundation "before any task exists" is reported as the missing task it names rather than improvised
into an authorship. Reading and reporting the knowledge state before such a task exists stays with the
bootstrap seat, where it is a fact about setup rather than a production route. A curator started from
the dashboard has no task and therefore no authoring entry here: it reads and reports, and the writing
happens once the Architect has opened the task the work belongs to.

**Collection and search assistance may be delegated; the authored result may not.** A curator may
spawn read-only sub-agents to read source at exact revisions, search providers, or summarize declared
external documents, and may accept evidence collected by another seat — but the semantic
reconciliation, the record actions, the family guarantees and the memberships are authored by the
curator, and the curator's hand-off list is the only thing handed to the writer.

## Inputs

- **The repository id the MCP authority settings declare**, and the resolved coordination context
  (`context_packet`, `server_info`). There is no default repository.
- **The code and memory revision the foundation is being built against.** The run derives both from
  the real checkouts the task's enclosure contract names; a caller does not type them. Both lines must
  resolve `HEAD`: a checkout that answers no commit is refused by name
  (`code_revision_unavailable`, `memory_revision_unavailable`) with its own next action, because a run
  with no commit would make every anchor it resolves meaningless while still looking admitted.
- **The requested scope** — the whole repository, or a named area, route or obligation set.
- **The available sources** — code at exact revisions; declared external specifications and
  documentation; existing onboarding when it is present.
- **The current knowledge state**, read before anything is authored (step 1).
- **The developer's commit word**, when the run is to write. A run without it plans and writes
  nothing.

## The Procedure

### 1. Name the task the run belongs to, and the current state

Resolve the repository and the memory line first, then read what the memory tree holds **now**.
Four states are four different facts, and a run that merges them invents history:

| State | Where it is observed | What it means, and the move |
| --- | --- | --- |
| **Context not admitted** — `context-not-admitted` | `memory_init`'s `knowledge` block, or the refusal the read route prints | The knowledge admission itself refused (see the refusal table below). The route is the refusal's own `nextAction`; it is not a knowledge state at all. |
| **Legacy format** | the memory root holds no `knowledge/layout.json`; `knowledge_read` and the write commands answer `legacy-format` | The tree is not converted, and nothing can be written to it. **Report it; do not create the marker by hand.** The refusal names the conversion command. |
| **Converted, no records** | the memory root holds `knowledge/layout.json` and no record file under `knowledge/<kind>/` | This is the first foundation's starting state: continue to step 2. A memory root that `memory_init` created is in this state. |
| **Converted, records present** | record files under `knowledge/<kind>/`; name them with `knowledge_read`, then open the named files natively (`memoryRoot` and one of `view="family"`, `view="invariant"`, `view="source_context"`; without a seed each lists the records the tree holds) | A foundation exists. Read it before extending it: never author a second record for an obligation the tree already holds. |

For an admitted converted memory tree, `memory_init.knowledge` reports `not-recorded` when its
complete index contains no authored knowledge records, `recorded` when the complete index contains
authored records, and `unusable` when the index is partial. Read its `detail` and `nextAction`: a
partial index names the knowledge files to repair before rebuilding the derived index. `datasetPath`
names that derived index; its mere existence does not prove an authored foundation. An admission
refusal reports `context-not-admitted`, with its actual `code`, `detail` and `nextAction`. An
unconverted root reports `unusable` with `code: legacy-format`; follow the returned crossing-sync or
conversion route, never create a layout marker by hand. Other unreadable states remain unusable:
report the returned reason and recovery rather than infer empty knowledge. Independently name the
records through `knowledge_read` and open the named files natively before extending the foundation; `recorded` establishes their
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

### 4. Author through the curator's own writer, inside the task that owns the work

The writer is the curator file writer, reached by the shipped command line, and the run happens on the
task the Architect opened for this foundation. `--contract` is that task's leaf enclosure contract: the
run reads the code worktree and writes the memory worktree the contract names, so no argument list can
aim a knowledge write at another line. Plan first:

```text
agents-remember knowledge-ingest --contract <this leaf's enclosure contract> --list <hand-off list>
    --authorization-ref <ref> --json
```

Then, with the developer's commit word, the same call writes:

```text
agents-remember knowledge-ingest --contract <this leaf's enclosure contract> --list <hand-off list>
    --authorization-ref <ref> --commit --json
```

- **Planning is the default, and planning is the dry run.** Without `--commit` the list is read, the
  tree the run would produce is validated and reported, and **nothing is written**. Run it that way
  first and put its report in front of the developer. The record IDs a planning run prints are
  provisional: the writing run mints its own.
- **Run it from the task, with the task's own contract.** Use the enclosure contract of the leaf the
  Architect opened for this work, and the settings that configured this repository's serving MCP.
  Verify the leaf and the repository the report names before proceeding. Do not change global
  discovery, and do not invent a leaf, worktree or enclosure to repair a wrong selection.
- **`--commit` is the developer's commit word**, and it is the whole of the write act. It writes files
  into the task's memory worktree; it makes no Git commit — the leaf's closeout commits that worktree.
  The curator does not give itself that word.
- **`--authorization-ref` is the authorization this run is admitted under.** It must not be blank and
  must not be invented. The report records it.
- **No task, no write.** A foundation with no task behind it is a state to report, not work to
  improvise: this procedure does not create the leaf, the worktree, the enclosure or the task document
  the writer would need, and no flag or boolean takes the place of the task's ownership.
- The memory worktree is **the task's, never chosen here**: it is the one the enclosure contract names
  and the ordinary read route resolves. No argument on this surface aims a knowledge write elsewhere.
- **The memory worktree must be converted.** On a tree in the legacy format the command refuses with
  `legacy-format`, writes nothing, and names the conversion command.

The writer commits nothing to Git. The records land with the rest of that leaf's memory work, and the
leaf's own closeout publishes the worktree — this procedure claims no landing of its own.

### 5. Read the result back — the report is the result

**Exit zero is not a claim that the foundation exists.** Read the report and state each of these as
its own fact:

- `state`: `written`, `planned` or `refused`. A refused run wrote nothing and names every problem;
- the records and the sidecar entries the run wrote, each with its ID, its path and whether it was
  created, updated, unchanged or removed;
- the history rows the run wrote, and the history file they went to;
- the tests the evidence names and what became of each (`proof_written`, `needs_facet`,
  `unresolvable`).

Verify the stored result through the ordinary read surface as well — `knowledge_read` over the memory
worktree (`memoryRoot`) for the records and families the run wrote, opening each named file at its
line to confirm the ID it declares — and validate the tree itself:

```text
agents-remember knowledge-validate <memory worktree> --code <code worktree> --code-commit <revision> --json
```

Both readings are about the files, not about the run's own prose.

### 6. Report coverage and unresolved work

Report, in the curator's own output:

- the source areas examined, and the areas **not** examined;
- candidate knowledge considered, records created and updated, and the obligations examined for a
  family and left without one, with the reason;
- known duplicates, contradictions found, missing sources, and deferred work;
- the files the run wrote, or the state that says nothing was written.

**A partial run stays explicitly partial and keeps a resumable next action.** A rerun of the same
list on the same task writes the same files with the same IDs, so a resumed run continues the same
work. A run that did not examine a required area **cannot claim the foundation complete for it**. A
tree with no records, a refused run, or an exit status of zero is not a populated foundation.

### 7. A weakness met while reading a file is recorded in the same step (MIK-R118 rule 8)

The full migration of MIK-R19 reads every file of the code, and this procedure reads a bounded part
of it. **Whatever the run reads, a weakness it meets in that file is recorded in the same step**, as
a `WKN-` record of kind `context`, `compute`, `storage`, `time`, `fault` or `debt`, on the file and
the symbol where it occurs, with one sentence and any measurement the reader took. The author is the
curator and the route is the writer, exactly as for every other record: nothing here writes a
weakness by hand, and nobody is sent to look for one.

**A run's check neither counts them nor asks for any.** The census, the coverage plan and the
independent check count records and obligations; a weakness is not one of those, it is not a finding,
and its absence from a file is not an omission. A run that meets none records none.

## Failure And Recovery Behavior

| State | Observed from | The move |
| --- | --- | --- |
| A repository the settings do not declare (`repository_not_allowed`) | the refusal payload of the surface that answered: `state`, `code`, `detail`, `nextAction` | Report it and stop: there is no default repository. |
| No coordination root, no code checkout, a checkout that is not a Git checkout, an unreadable revision (`coordination_root_unavailable`, `code_checkout_unavailable`, `code_checkout_is_not_a_git_checkout`, `code_revision_unavailable`, `memory_revision_unavailable`) | the same refusal payload | Report the real route the refusal names. Do not substitute a path or a revision. |
| No memory layer resolved, or the resolved line is not the declared one (`memory_layer_not_resolved`, `memory_line_moved`) | the same refusal payload | Repair the repository entry or the coordination settings, then re-observe. Never write onto a line the ordinary reader does not select. |
| The enclosure contract cannot be loaded, or names no leaf | the refusal the command prints | Report it and stop: `--contract` is the write guard, so a contract that cannot be loaded writes nothing. No other destination and no invented enclosure is substituted. |
| The memory worktree is in the legacy format (`legacy-format`) | the refusal the command prints | Report it. Conversion (`agents-remember knowledge-convert`) is its own step and the developer's decision. **Nothing was written.** |
| The writer refused the list | the report: `state: refused`, with every problem named | Correct the list and run it again. **Nothing was written.** |
| A run was interrupted, or a later run is narrower than an earlier one | the leaf's history file and the records in the tree | Resume on the same task with the same list. A rerun of the same list writes the same files with the same IDs and does not duplicate records. A rerun removes the sidecar entries this run wrote earlier from an entry the list no longer names, and reports them `removed`. |
| A moved input revision | the code revision the admission bound, against the checkout | A moved source is an explicit new observation, not a silent reuse: re-observe and decide. Anchors are resolved at the revision the run was admitted on. |
| A missing optional source | the source inventory | Absent, not failed. It becomes no fact. |

## Preservation Boundaries

1. **No new agent role, harness, scheduler or parallel onboarding implementation.** The curator
   authors the foundation, the Architect organises the task it is authored under, and the bootstrap
   seat reports the state it read: those are the existing owners.
2. **No blind onboarding import.** Onboarding is a source to read and reconcile, never a corpus to
   copy into the store.
3. **Onboarding stays where it is.** A foundation does not retire or rewrite a card, and nothing
   here rewrites a memory file to make a check pass.
4. **No database, no second writer and no second destination.** The curator file writer and the
   memory worktree the task's enclosure contract names are the only ones used. Records and sidecar
   entries are never written by hand.
5. **No invented semantic approval and no invented authority.** The commit word is the developer's,
   the authorization reference is the run's own, and admission is the task's real enclosure contract
   and the real revisions rather than a flag or a word in a document.
6. **No fabricated development leaf, worktree, enclosure or task document**, and no layout marker
   created by hand, to satisfy an input shape: the leaf a foundation is authored on is one a real
   Architect task opened.
7. **No invented project truths, no back-dating, no backfilled task history, no favorable default in
   place of absent evidence.** A fixture, a prototype or another repository's records are never
   presented as this repository's foundation.
8. **The delivery gates keep their owners.** This procedure reports its outcome; installation,
   closeout, integration and activation stay with the seats and surfaces that already own them.

## Relationship To Other Skills

| Skill or surface | Relationship |
| --- | --- |
| `c-13-install-and-onboard` | The setup path. It stops at the baseline, reads the knowledge state and hands the foundation's making to the user-started Architect; it neither reimplements this procedure nor reports a repository's knowledge as ready, because only the Architect's task takes the foundation further. |
| `c-03-repo-bootstrap` | Builds onboarding. Its handoff names the knowledge foundation as this skill's step in both directions: onboarding is optional input here, and this procedure does not write onboarding. |
| `c-00-initialize-memory-repo` | Creates or repairs the memory root. `memory_init` creates a new memory root in the text format (it writes `knowledge/layout.json`) and reports whether the bootstrap context is admitted — part of the state step 1 reads. It creates no knowledge and never pretends to. |
| `c-10-adopt-memory-baseline` | Commits memory *content* as the first attributed baseline. A baseline is not a knowledge foundation: neither substitutes for the other, and this procedure neither adopts a baseline nor claims one. |
| `c-05-create-or-update-onboarding-files` | Owns onboarding content. The curator writes onboarding there, and authors the foundation here. |
| `c-05-create-or-update-onboarding-files` | The complete curation operation the curator runs around its work; the foundation's authored records are not a substitute for it, and it is not a substitute for authoring them. |
| `l-01-agent-lifecycles` | Houses the carriers: `skills/l-01-agent-lifecycles/roles/curator.md` and `skills/l-01-agent-lifecycles/operations/curation.md` (the semantic owner and its authoring route), and `skills/l-01-agent-lifecycles/roles/bootstrap.md` and `skills/l-01-agent-lifecycles/operations/bootstrap.md` (the first-hour seat, which reads the knowledge state, reports it and hands its making to the Architect). |
| `templates/curator-handoff-list.md` | The shape the hand-off list is authored in. This skill names the shape; that template owns it. |

## Acceptance Criteria

1. A new project reaches this procedure without any onboarding existing: the bootstrap seat reads and
   reports the knowledge state and names the Architect, the Architect opens the task, and the curator
   seat opened on that task's document carries this procedure. The shipped skill catalog lets any
   session read it, which is discovery rather than authority to author.
2. The state of the memory tree is read before anything is authored, and a tree in the legacy
   format, a converted tree with no records and a converted tree with records are reported as the
   three different facts they are — with a refused admission (`context-not-admitted`) reported as an
   admission failure rather than as any of the three.
3. The authoring run defaults to planning, and a run without the developer's commit word writes no
   file.
4. Admission is the task's real enclosure contract and the real revisions, and no enclosure, leaf,
   layout marker or database is fabricated at any point.
5. Every record's outcome, the history rows and the remaining work are read from the report, and a
   zero exit status is never quoted as a written foundation.
6. A partial run reports itself partial, names the areas it did not examine, and leaves a resumable
   next action.
7. The foundation is written into the memory worktree the task's enclosure contract names, and the
   records a later task's planner will read are named by ID and path.
8. Nothing in this skill writes onboarding, changes another skill's owner, or adds a role, a store or
   a destination.

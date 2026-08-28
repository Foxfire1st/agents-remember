# Light Task Workflow

## Goal

Run the in-between task lifecycle: plan in a task file, stop for approval, implement against a live checklist, and close only after developer confirmation.

The routing heuristic is simple: if the implementation plan still fits on a single page, light-task-workflow is probably the right tool. That is a rule of thumb, not a hard boundary.

Light-task-workflow still follows the same shared discipline documented in `README.md`:

1. drift check before planning when onboarding is part of the planning context
2. approval before implementation
3. onboarding update through `c-05-create-or-update-onboarding-files` after approved changes, with durable findings routed through that skill during implementation when they become clear enough
4. separate commit approval before `c-09-git-worktree-manager` closeout creates Git commits for worktree-backed tasks

## Phase 0 — Compile And Approve Requirements Before Task Topology

After intent and scope are established, but before creating a sprint, master, standalone task, or
leaf document, the architect creates only the planning wrapper and its canonical requirement
corpus:

```text
<task-root>/<task-slug>/
└── requirements/
    ├── README.md              # stable ID + version index
    └── <stable-id>-<version>-<slug>.md  # one immutable address per revision
```

The architect must:

1. compile every independently falsifiable obligation into `requirements/README.md` with a stable
   ID, explicit version, short name, packet link, status, and developer-approval citation;
2. split clauses whenever they can be violated, reviewed, owned, evidenced, or superseded
   independently;
3. create one self-contained, version-addressed packet per ID + version from
   `requirement-packet-template.md`, covering the problem, required behavior, rationale, scope,
   exclusions, preservation boundaries,
   failure/recovery behavior, examples, forbidden overreach, expected deliverable and verification
   evidence, authority/provenance, dependencies, and open truth gaps, with diagrams wherever they
   materially clarify state, sequence, ownership, or interactions;
4. cold-read every packet through a fresh agent without the planning transcript and record whether
   it can explain what changes, what remains unchanged, the important failure states, and what
   would prove conformance; and
5. present the complete corpus to the developer and stop for approval.

A failed cold read returns the packet for rewriting. A pending approval leaves the topology absent.
Only after the corpus is approved may Phase 1 create task documents and project the approved IDs +
versions into them.

## Phase 1 — Project The Approved Corpus Into Task Documents

### 1. Ensure the local task area exists

All light-task artifacts live under the `c-08-ar-coordination-context-resolver` resolved `<task-root>/`. The durable artifact shape is a wrapper folder plus `task.md`:

```text
<task-root>/<task-slug>/task.md
```

The same `<task-slug>` wrapper and `requirements/` corpus already exist from Phase 0. Create
`task.md` only after the corpus approval. If the task later becomes worktree-backed, the
`c-09-git-worktree-manager` skill places its leaf contract at
`enclosures/<leaf-id>/series-contract.md` under the wrapper folder.

### 2. Reuse an existing active task when appropriate

Before creating a new file:

1. search `<task-root>/` for an active task already covering the same scope
2. update that task instead of creating a duplicate when the scope matches

### 3. Name the task wrapper

Use this naming convention:

| Origin        | Naming convention                     | Example                          |
| ------------- | ------------------------------------- | -------------------------------- |
| Ticket-linked | `YYMMDD_#<number>_<short-slug>/`      | `260319_#42_update-readme/`      |
| Organic       | `YYMMDD_<descriptive-slug>/`          | `260319_readme-rewrite/`         |

The task document inside the wrapper is always `task.md`.

### 4. Run drift detection before planning against onboarding

If the task plan relies on onboarding files:

1. invoke `c-02-memory-quality-control` before planning against those files
2. apply the `c-02-memory-quality-control` skill's clean-source versus dirty-source drift classification before planning against pre-existing onboarding
3. do not plan against clean-source drifted or missing-verification pre-existing onboarding until the update candidates have been handed off to `c-05-create-or-update-onboarding-files` or the developer has explicitly accepted directional-only trust
4. leave dirty-source drift findings alone as active work-in-progress unless the developer explicitly takes ownership of them in this task
5. treat files created or modified during the current task as task-local working state after that initial gate passes; they remain pending verification, but they do not by themselves re-block planning for the same task
6. before any `c-09-git-worktree-manager` worktree starts, commit refreshed external-memory onboarding and the ledger so the worktree starts from a clean, mapped memory baseline

### 5. Gather context before writing the plan

Before planning:

1. check the `c-08-ar-coordination-context-resolver` resolved `docs/` root for local reference material when it exists
2. check glossary or naming references listed in the `c-08-ar-coordination-context-resolver` resolved `system/sources.md` when they exist
3. check `<onboarding-root>/` for any repo whose behavior or terminology the artifact touches
4. use supporting search or docs tools only when the task domain needs them

### 6. Reframe and design before writing the plan

Before writing implementation steps, apply the Task Collaboration Doctrine in
`tasks/AGENTS.md`. Let the nature of the request set the depth: the doctrine
defines when reframing and design thinking are worth it and what to surface.
When they are, do that thinking with the developer in chat, then record the
settled result in the task file's `## Design` section so the implementation
steps derive from it rather than replace it.

### 7. Write `task.md`

Use `template.md` as the canonical scaffold. The task document is **JSON-primary**: author it with the `task_doc` MCP tool, which writes the `ar-task-document/v1` JSON and renders `task.md` / `<slug>.md`. `template.md` is the render spec, so do not hand-edit a tool-managed `task.md` — edit the JSON through `task_doc` and let it re-render. (A series *master* file stays hand-authored markdown for now.)

Write every checkbox on its own line. Under a parent step, indent nested checklist items by two spaces and keep the verification checkbox nested under the step it validates rather than emitting it as a same-level sibling.

The file must include:

1. objective
2. a filtered requirement projection: stable ID, exact approved version, canonical packet link,
   and topology role; never rewritten requirement prose
3. design sized to the request per `tasks/AGENTS.md`, or a note that no design reasoning is needed
4. implementation steps with one checkbox per line and nested checkbox items indented by two spaces under the parent step
5. proposed code examples for each distinct implementation change when code changes are in scope; if examples are deferred to the plan gate, record that intent via `codeExamplesNote` so the render distinguishes deferred from none-needed
6. decision log
7. open questions
8. references
9. for any durable fixture, recording, shared test support, or proof: the stable executable contract
   it graduates to, or the dated expiry/retirement event that removes it

Use `R1`, `R2`, ... or another corpus-local stable ID convention and pair every ID with its approved
version. The task projection links the canonical packet; it does not create master-owned or
leaf-owned rewritten contracts. Every requirement must be uniquely and durably addressable before
task creation, implementation, or dispatch. The packet and dispatch brief name the required
deliverable and verification evidence classes; "requirements addressed" is never a replacement
for the exact ID + version list.

Concrete examples of the required decision shape:

- A versioned Codex frame retained as durable provider evidence names
  `contract:codex-agent-wire-version-matrix`, its source owner, and its executable conformance node.
- A migration comparison retained only through `2026-09-30` names
  `node:mcp/tests/test_migration.py::test_replacement` as the executable replacement and records
  removal at expiry. An undated "temporary" row is invalid.

Use `YYYY-MM-DDTHH:MM` for task-local timestamps such as `Created`, decision log entries, progress notes, and review outcomes.

Decision logs are append-only: never delete or rewrite earlier entries. Add a later entry when a previous decision is superseded, corrected, rejected, or clarified.

Status values should align with the repository rules:

1. `planning`
2. `inProgress`
3. `Completed`

### 8. Present the plan and stop for approval

Present a concise summary in chat:

1. objective in one or two sentences
2. the key implementation steps
3. the distinct implementation examples when code changes are in scope
4. any open questions or decisions needed

Then explicitly ask the developer to review the task file.

Do not implement before approval.

Developer outcomes:

1. approve: set status to `inProgress` and continue to Phase 2
2. request changes: update the task file and re-present
3. reject: record the rejection reason in the decision log and stop

## Phase 2 — Implement Against The Live Checklist

### 1. Start from the first unchecked work item

The task file is the live execution checklist.

Implementation starts at the first unchecked checkbox under the approved implementation steps.

### 2. Work step by step

For each implementation section:

1. read the step objective and its checkbox items
2. read the relevant files or materials
3. perform the approved work
4. route durable current-state findings for that implemented slice through `c-05-create-or-update-onboarding-files` as soon as the finding is stable enough to state accurately
5. use the checks listed in the `c-08-ar-coordination-context-resolver` resolved `tools_path` for that implemented slice when those checks are available
6. finish any remaining onboarding cleanup for that implemented slice through `c-05-create-or-update-onboarding-files` before considering it done
7. mark a substep complete only after its code or artifact change, its onboarding capture or update through `c-05-create-or-update-onboarding-files`, and its relevant listed checks are done
8. mark the parent step checkbox complete only after its nested implementation items and verification checkbox are complete
9. record any meaningful judgment call as a new decision log entry
10. before promoting task-local proof into durable evidence, record the stable-contract-or-expiry
    decision in the task and lifecycle catalog; run the public lifecycle validator through the
    repository quality route, which must reject missing, stale, contradictory, or unowned rows
11. maintain one acceptance block for the task's owned primary stable requirement ID + version in
    the builder handoff artifact (the worker turn report when a worker exists, otherwise a
    task-local implementation handoff). Record dependency/preservation checks separately without
    claiming to close them. The primary block
    block records `satisfied | blocked | approved-change`, delivery rationale/citations,
    verification rationale/citations including the failure caught, and exact command/result or
    durable evidence. Code uses path + symbol anchors; non-code work uses deliverable paths plus
    sections/anchors. `blocked` and `approved-change` additionally cite the durable developer ruling
    for the exception. Keep this envelope separate from the durable-evidence
    stable-contract-or-expiry hold point.
12. advance a worker delivery attempt only when an exact candidate is handed to independent review,
    or after reviewer rejection when a successor candidate is handed off. Internal implementation,
    test, and evidence reruns are experimental protocol events, not attempts; preserve them
    separately with candidate identity, command, result, failure cause, repair, and expected next
    proof. Before review handoff, append one immutable record for the owned primary requirement
    revision and leaf manifestation to the leaf's detailed Requirement Attempt Journal. Bind the
    leaf-local
    attempt ID, predecessor and carried findings, exact candidate tree/commit or non-code
    digest/anchors, requirement-specific status/rationale/citations/findings/failure class, a
    content-addressed reference to immutable expanded evidence, and append time. The frozen artifact
    carries shared definitions and complete command results; do not duplicate the complete master
    envelope or experimental-run body in each attempt. Never edit a prior record. A
    rejected-attempt repair creates a successor attempt at its next review handoff. An unrelated
    later candidate does not reopen an accepted attempt.
    Validate the complete record before append. Append plus exact-candidate review handoff is one
    logical formal-attempt boundary. Preserve a malformed pre-handoff row with an append-only
    `non-attempt-correction`/void reference, consume no attempt ID, and use the same next ID for the
    corrected handoff. A malformed handed-off row requires independent reviewer rejection before
    the worker may append a successor at the next candidate handoff.
13. classify each blocked finding as exactly `implementation defect`, `evidence gap`, `requirement
    contradiction/overconstraint`, `test/tool defect`, or `external blocker`. Requirement problems
    route to architect/developer revision authority and cannot be rewritten by the implementer.

If the `c-08-ar-coordination-context-resolver` resolved `tools_path` is still blank, there may be no repo-specific checks listed yet; the file exists so the developer can fill in that checklist over time.

### 3. Milestone alignment

After each step:

1. re-read the task file
2. confirm the changed work still matches the approved plan
3. if the work drifted materially, stop and update the plan before continuing

### 4. Finish Phase 2

When the approved plan has been fully implemented:

1. confirm the checklist reflects completed code changes, onboarding updates, and listed checks
2. confirm the builder handoff has exactly one complete acceptance block for the owned primary
   requirement ID; an aggregate completion statement, missing citation, or approval-pending
   exception is not completion
3. confirm each delivered leaf manifestation has a newly appended immutable worker attempt bound
   to the exact candidate and that all predecessor findings are accounted for
4. for worktree-backed tasks, run `c-09-git-worktree-manager` closeout in dry-run mode to prepare the commit preview; this does not require commit approval and must not mutate Git
5. present a concise completion summary in chat covering what changed, what onboarding was updated, which listed checks were run, and the proposed code, memory, and ledger commit messages
6. ask explicitly for commit/closeout approval; do not treat implementation approval as commit approval
7. leave worktree-backed task status below `Completed` after closeout; every declared parent/nested step must be explicitly `done` (or intentionally skipped through exact `task_doc.skip_step`) before `lifecycle_finalize_task` can set completion. If a final step includes cleanup, run standalone `worktree_cleanup`, mark that exact step done afterwards, then finalize the already-clean contract

## Phase 3 — Close

Close does not own implementation work. Code changes, onboarding updates, and listed checks all belong to Phase 2 and should already be finished before this phase begins. For worktree-backed tasks, close also must not create commits unless the developer approved the closeout preview.

Close may still consolidate or polish onboarding language through `c-05-create-or-update-onboarding-files` if needed, but it must not depend on rediscovering durable findings that should have been captured during Phase 2.

### 1. Prepare the completion handoff

When all planned work is complete:

1. present what was done, any deviations, and any deferred items
2. verify that the Phase 2 completion summary still reflects the final state accurately
3. confirm that durable findings discovered during implementation were routed through `c-05-create-or-update-onboarding-files` rather than left implicit in chat history
4. for worktree-backed tasks, confirm whether the current state is still awaiting commit approval, closed out, awaiting integration, awaiting PR/pull/carryover, or ready for lifecycle finalization

### 2. Cross-reference check

Before final closure:

1. verify any referenced workflow or skill paths still resolve
2. check whether newly introduced terms belong in the glossary or naming references listed in the `c-08-ar-coordination-context-resolver` resolved `sources_path`
3. update any repo-level descriptions that would now be misleading
4. independently review the owned primary stable-ID + version acceptance block as `accepted |
   rejected`, opening the
   cited artifacts and giving a reviewer rationale; missing rationale, wrong-class evidence,
   invalid citations, or missing developer approval forces rejection, and closure cannot pass
   while any ID is rejected. Bind the adjudication to the exact worker attempt, leaf manifestation,
   and candidate; append a separate immutable reviewer record without changing the worker record.
   Every rejection uses one closed failure class. Accepted attempts remain closed unless an
   independent reviewer proves direct regression and the owning manager (architect in a flat run)
   records bounded invalidation, or an approved requirement revision affects that manifestation.

## Three-touch iteration cycle

When the developer changes scope or requests further changes during implementation, use this cycle.

### Touch 1 — Update the plan before edits

Update the task file first:

| What changed     | Update                                                                           |
| ---------------- | -------------------------------------------------------------------------------- |
| New requirement  | Compile a new stable ID + v1 packet, cold-read it, obtain developer approval, then project it into affected task docs |
| Changed requirement | Increment the existing ID's version, record developer approval, invalidate affected acceptance state, update affected projections, and rebrief affected leaves |
| New work slice   | Add a new `S#` section or new checkbox items under an existing section           |
| Changed approach | Rewrite the affected step text and append the reason to the decision log         |
| Deferred work    | Mark it as deferred in the relevant step or note it in a dedicated deferred line |

If the change is significant, get renewed approval before editing files.

### Touch 2 — Implement and present

Do the work for the current slice, update onboarding for that same slice through `c-05-create-or-update-onboarding-files`, run the listed checks for that same slice when available, then update the same checklist:

1. check off completed substeps
2. check off completed parent steps when they are truly done
3. present the result to the developer for review

### Touch 3 — Record the review outcome

Based on developer feedback:

1. approved: keep the completed checkbox state, append any notable decision entry, and continue
2. changes requested: return to Touch 1 and update the plan before editing again
3. rejected: record the rejection in the decision log and revert or defer as appropriate

When a review outcome or progress note is recorded in the task artifact, use `YYYY-MM-DDTHH:MM` rather than a date-only value.

## Multi-session continuity

If the session ends mid-task:

1. re-read the task file first when resuming
2. continue from the first unchecked checkbox
3. keep step text detailed enough that a fresh agent can recover context quickly

## Master Task Series

When the work outgrows a single-page plan, escalate to a **master + light sub-task series**. Create one
wrapper folder with a master `task.md` (scaffold in `master-template.md`) plus flat, numbered sub-task
files `NN_<name>.md` in execution order.

Masters and leaves project the corpus's stable IDs and versions across that series. Every leaf
brief, builder handoff, and reviewer verdict carries the exact applicable subset; master-exit
review adjudicates the complete set without replacing it with an aggregate master claim. A master
summarizes thematic goals and carries only a filtered ID + version + packet-link projection. Every
leaf owns exactly one primary requirement revision and links its complete packet. One revision may
map to multiple leaves when it has independently executable manifestations. Adjacent requirements
may be listed as dependencies or preservation constraints, but the leaf cannot claim to close
them; a leaf that would close multiple independently falsifiable requirements must be split.

Each leaf's append-only worker attempt records and independent reviewer records are the detailed
Requirement Attempt Journal and remain authoritative. Maintain a rebuildable master summary with
attempt identities, rejection history/count, current state, dominant open failure class, and links
to those leaf records. The summary is a disposable projection: it is never a requirement contract,
lifecycle/closeout gate, queue authority, or task-authoring lock. Missing or stale summary state is
rebuilt from leaf journals and cannot block work. Keep the leaf records requirement-specific and
lightweight by linking content-addressed expanded evidence; keep internal experimental protocol
events in their own log rather than inflating attempt history or the summary.

Run the series as **one master integration branch plus leaf enclosure worktrees**:

1. create or reuse the master root `series-contract.md`; it represents the integration branch and is not itself a worktree
2. start one leaf enclosure per active sub-task at `enclosures/<leaf-id>/series-contract.md`
3. implement each sub-task slice in its own worktree, then close it out behind an explicit commit gate
4. integrate each leaf branch back into the master integration branch and finalize that leaf edge
5. when every sub-task has landed, the master performs the single version bump / release and lands the integration branch through the repo's normal policy

The master owns only the final release step; sub-tasks never bump the version.

## When To Escalate To A Master Series

A single light task is the right tool while its implementation plan fits on one page. When the work
outgrows that — broad cross-repo or high-risk changes, or several distinct slices that each need their
own checklist and commit — escalate to a **master + light sub-task series** (see *Master Task Series*
above and `master-template.md`) rather than forcing it into one light task. The series is still light
sub-tasks; it adds a master `task.md` to sequence them, a master integration branch, leaf enclosure
worktrees for the active slices, and a single release at the end.

```
Developer request
       │
       ▼
  light-task-workflow
       │
      ├─ task wrapper under `<task-root>/<task-slug>/`
      ├─ `task.md` inside the wrapper
      ├─ worktree-backed tasks add `enclosures/<leaf-id>/series-contract.md`
      ├─ approval gate before implementation
      └─ live checkbox checklist during execution
       │
       ▼
  Outgrows a single-page plan? ──yes──▶ master + light sub-task series (`master-template.md`)
```

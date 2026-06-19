# Light Task Workflow

## Goal

Run the in-between task lifecycle: plan in a task file, stop for approval, implement against a live checklist, and close only after developer confirmation.

The routing heuristic is simple: if the implementation plan still fits on a single page, light-task-workflow is probably the right tool. That is a rule of thumb, not a hard boundary.

Light-task-workflow still follows the same shared discipline documented in `README.md`:

1. drift check before planning when onboarding is part of the planning context
2. approval before implementation
3. onboarding update through `c-05-create-or-update-onboarding-files` after approved changes, with durable findings routed through that skill during implementation when they become clear enough
4. separate commit approval before `c-09-git-worktree-manager` closeout creates Git commits for worktree-backed tasks

## Phase 1 — Create Or Update The Task Wrapper

### 1. Ensure the local task area exists

All light-task artifacts live under the `c-08-ar-coordination-context-resolver` resolved `<task-root>/`. The durable artifact shape is a wrapper folder plus `task.md`:

```text
<task-root>/<task-slug>/task.md
```

Create this wrapper folder as soon as the task class, name, and workflow variables are clear. That can and should happen before any `c-09-git-worktree-manager` worktree is created. If the task later becomes worktree-backed, the `c-09-git-worktree-manager` skill places `contract.md` beside `task.md` in the same wrapper folder.

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
2. requirements
3. design sized to the request per `tasks/AGENTS.md`, or a note that no design reasoning is needed
4. implementation steps with one checkbox per line and nested checkbox items indented by two spaces under the parent step
5. proposed code examples for each distinct implementation change when code changes are in scope; if examples are deferred to the plan gate, record that intent via `codeExamplesNote` so the render distinguishes deferred from none-needed
6. decision log
7. open questions
8. references

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

If the `c-08-ar-coordination-context-resolver` resolved `tools_path` is still blank, there may be no repo-specific checks listed yet; the file exists so the developer can fill in that checklist over time.

### 3. Milestone alignment

After each step:

1. re-read the task file
2. confirm the changed work still matches the approved plan
3. if the work drifted materially, stop and update the plan before continuing

### 4. Finish Phase 2

When the approved plan has been fully implemented:

1. confirm the checklist reflects completed code changes, onboarding updates, and listed checks
2. for worktree-backed tasks, run `c-09-git-worktree-manager` closeout in dry-run mode to prepare the commit preview; this does not require commit approval and must not mutate Git
3. present a concise completion summary in chat covering what changed, what onboarding was updated, which listed checks were run, and the proposed code, memory, and ledger commit messages
4. ask explicitly for commit/closeout approval; do not treat implementation approval as commit approval
5. set the task status to `Completed` only after the implementation cycle is finished and any required worktree closeout has received explicit commit approval

## Phase 3 — Close

Close does not own implementation work. Code changes, onboarding updates, and listed checks all belong to Phase 2 and should already be finished before this phase begins. For worktree-backed tasks, close also must not create commits unless the developer approved the closeout preview.

Close may still consolidate or polish onboarding language through `c-05-create-or-update-onboarding-files` if needed, but it must not depend on rediscovering durable findings that should have been captured during Phase 2.

### 1. Prepare the completion handoff

When all planned work is complete:

1. present what was done, any deviations, and any deferred items
2. verify that the Phase 2 completion summary still reflects the final state accurately
3. confirm that durable findings discovered during implementation were routed through `c-05-create-or-update-onboarding-files` rather than left implicit in chat history
4. for worktree-backed tasks, confirm whether the current state is still awaiting commit approval, already closed out, awaiting integration, or awaiting cleanup

### 2. Cross-reference check

Before final closure:

1. verify any referenced workflow or skill paths still resolve
2. check whether newly introduced terms belong in the glossary or naming references listed in the `c-08-ar-coordination-context-resolver` resolved `sources_path`
3. update any repo-level descriptions that would now be misleading

## Three-touch iteration cycle

When the developer changes scope or requests further changes during implementation, use this cycle.

### Touch 1 — Update the plan before edits

Update the task file first:

| What changed     | Update                                                                           |
| ---------------- | -------------------------------------------------------------------------------- |
| New requirement  | Append it to Requirements with a short annotation noting when it was added       |
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

Run the series as **one task, one workflow, one worktree**:

1. open a single `c-09-git-worktree-manager` worktree for the whole series (never one per sub-task)
2. implement each sub-task slice, then commit it via its own `c-09-git-worktree-manager` closeout behind an explicit commit
   gate — multiple commits accumulate on the worktree branch as slices complete
3. keep the worktree open across slices; the test suite + listed checks run green before each commit
4. when every sub-task is committed, **integrate + clean up once** and let the master perform the
   single version bump / release

The master owns only the final release step; sub-tasks never bump the version.

## When To Escalate To A Master Series

A single light task is the right tool while its implementation plan fits on one page. When the work
outgrows that — broad cross-repo or high-risk changes, or several distinct slices that each need their
own checklist and commit — escalate to a **master + light sub-task series** (see *Master Task Series*
above and `master-template.md`) rather than forcing it into one light task. The series is still light
sub-tasks; it adds a master `task.md` to sequence them, one shared worktree, a commit per slice, and a
single release at the end.

```
Developer request
       │
       ▼
  light-task-workflow
       │
      ├─ task wrapper under `<task-root>/<task-slug>/`
      ├─ `task.md` inside the wrapper
      ├─ worktree-backed tasks add `contract.md` beside `task.md`
      ├─ approval gate before implementation
      └─ live checkbox checklist during execution
       │
       ▼
  Outgrows a single-page plan? ──yes──▶ master + light sub-task series (`master-template.md`)
```

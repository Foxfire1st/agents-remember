---
name: w-02-light-task-workflow
description: "In-between task workflow for work that needs a durable task file, distinct implementation examples, and an approval gate but still fits a single-page implementation plan as a rule of thumb."
---

# w-02-light-task-workflow Light Task Workflow

This skill is the thin orchestration contract for the in-between case: work that needs a durable task file, an explicit approval gate, distinct implementation examples, and decision tracking, but still fits a single-page implementation plan as a rule of thumb.

## Companion Files

1. `workflow.md`
2. `template.md`
3. `master-template.md`

## When To Use

Use this workflow when:

1. the work needs a durable task file, an approval gate, and decision tracking
2. the implementation plan can still fit on a single page as a rule of thumb
3. the target may be non-code or a small isolated code change, provided the lighter single-page plan remains a good fit

Treat the single-page-plan test as guidance rather than a hard routing rule. When the work outgrows a single page — richer artifacts, broader coordination, or a sprawling plan — escalate to a master + light sub-task series (`master-template.md`) rather than forcing it into one light task.

## Task Artifact

Light-task-workflow maintains one task wrapper folder under `<task-root>/`, where `<task-root>` is returned by `c-08-ar-coordination-context-resolver` for the target repository. The task document is always named `task.md` inside that wrapper folder.

Naming convention:

1. ticket-linked: `YYMMDD_#<number>_<short-slug>/task.md`
2. organic: `YYMMDD_<descriptive-slug>/task.md`

Create the wrapper folder at the same time the durable task artifact is created, before any `c-09-git-worktree-manager` worktree exists. Use `template.md` as the canonical scaffold. Implementation steps and substeps are tracked with checkboxes, and that checklist is the live execution state during implementation. When code changes are in scope, the task file also carries proposed code examples for each distinct change type so the developer can review the intended implementation shape before approval.

Light-task artifacts use minute-precision timestamps in `YYYY-MM-DDTHH:MM` format wherever they record task-local dates or times.

## Task Document Format (JSON-Primary)

The task document is **JSON-primary**: an `ar-task-document/v1` JSON file is the source of truth, and the `task.md` (or `<slug>.md` for a sub-task) is a deterministic **render** of it. Author and update it with the `task_doc` MCP tool — `create`, `set_status`, `set_step`, `skip_step`, `append_decision`, `set_field` — which writes the JSON and re-renders the markdown on every write. `skip_step` is the only intentional-skip author: target one exact existing parent or parent+nested id and provide a nonblank reason; it never cascades. Do not hand-edit a tool-managed `task.md`; edit through the tool and let it re-render. `template.md` and `master-template.md` document the **render spec** (the shape the renderer produces), not a separate hand-authored format. When a planning slice defers its code examples to the plan gate, record that with `codeExamplesNote` (a `set_field` value, e.g. "Drafted at the plan gate.") so the rendered "Proposed Code Examples" section reads as *deferred* rather than as if none are needed. A leaf doc may also carry a descriptive `statusNote` (a suffix beside the strict status enum), `headerNotes` (extra `**Key:** value` header lines such as Verified/Source), and freeform `sections` appended after References — the escape hatch for bespoke prose (e.g. a Status history) while the standard template sections stay the backbone. Pass `dry_run=true` to any op to **preview** — it builds + validates and returns the rendered markdown, a unified `diff` vs the on-disk `.md`, and a `wouldLose` flag, **without writing**; use it before adopting a hand-authored `.md` into JSON-primary (author the JSON → dry-run → confirm `wouldLose` is false → then write).

Because the JSON encodes step/substep status explicitly, a tool-managed document is legible on the dashboard — the observer projects leaf documents through their enclosure entry in `enclosures[].enclosurePath`, at step granularity. The same schema/tool serves a *lightweight* document for the smallest single-session build (a THIN doc: title plus a few steps — chat is never a build route, so even micro-work gets this thin doc and stays legible on the dashboard) and a *full* one for a durable task; the difference is content completeness, not a separate format.

Scope and transition: the tool manages **`light` and `subTask`** documents, and the format also covers a series **master** (`kind:"master"`: a structured `subTasks` index + ordered `sections` that preserve a master's bespoke prose, so a re-render is lossless). Masters and existing markdown task files stay hand-authored markdown until the runtime ships `task_doc` and they are intentionally migrated — new tasks adopt the JSON-primary format going forward.

## Master-Task Composition (task series)

When a task outgrows a single-page plan, escalate to a **master + light sub-task series**. One wrapper
folder holds a master `task.md` plus flat, numbered sub-task files (`NN_<name>.md`) in execution order;
`master-template.md` is the canonical scaffold.

The series lifecycle follows **one master = one integration branch, one leaf = one enclosure worktree**:
the master owns the root `series-contract.md` and integration branch, while each
sub-task slice gets its own `enclosures/<leaf-id>/series-contract.md`, work branch,
worktree, closeout, integration into the master branch, and lifecycle finalization.
The master owns the final version bump and any release packaging after the leaf
branches have landed into the integration branch. See `master-template.md` for the
convention and scaffolds.

## Agent Responsibilities

The agent should:

1. stay in direct discussion with the developer
2. search `<task-root>/` for an existing active task covering the same scope before creating a new one
3. create or update the task wrapper and `task.md` using `template.md`
4. keep requirements, implementation steps, and decisions aligned with the latest approved intent
5. treat the task file's checkboxes as the live implementation tracker
6. include proposed code examples for each distinct implementation change when code changes are in scope
7. run `c-02-memory-quality-control` before planning against onboarding files
8. stop for approval before implementation
9. after approval, treat code changes, onboarding propagation through `c-05-create-or-update-onboarding-files`, and the checks listed in the `c-08-ar-coordination-context-resolver` resolved `system/tools.md` as one implementation cycle
10. for worktree-backed tasks, present a commit/closeout preview and stop for explicit commit approval before any `c-09-git-worktree-manager` closeout commits are created
11. after the task branch has landed, ensure every declared parent/nested step is `done`; if the final step includes cleanup, run standalone `worktree_cleanup`, then mark that exact step done; finally use `lifecycle_finalize_task` to prove the edge, verify/run cleanup, and set the exact leaf plus immediate parent row to `Completed`

## Context Gathering

Before planning, check:

1. the `c-08-ar-coordination-context-resolver` resolved `docs/` root for relevant local reference material when it exists
2. glossary or naming references listed in the `c-08-ar-coordination-context-resolver` resolved `system/sources.md` when they exist
3. `<onboarding-root>/` for any repo whose behavior the artifact touches

Optional supporting tools such as Confluence search, Brave search, or Context7 may still be used when the task domain needs them, but they are not mandatory here.

## Invariants

1. Every light-task change gets a task wrapper folder and `task.md`.
2. The task file is the living contract for requirements, checklist state, decisions, and proposed code examples.
3. When onboarding files are part of planning context, drift is checked before planning using `c-02-memory-quality-control`.
4. No implementation begins before explicit developer approval.
5. Refreshed external-memory onboarding and ledger changes are committed before the `c-09-git-worktree-manager` skill starts worktrees.
6. Implementation approval is separate from commit approval; worktree-backed closeout commits require a later explicit developer approval after a closeout preview.
7. Implementation steps and substeps use checkbox state rather than freeform progress prose.
8. Code-changing light tasks include code examples for each distinct implementation change; a slice that intentionally defers its examples to the plan gate sets `codeExamplesNote` (e.g. "Drafted at the plan gate.") so the render distinguishes *deferred* from *none needed*.
9. After approval, onboarding is updated through `c-05-create-or-update-onboarding-files` and the listed checks in the `c-08-ar-coordination-context-resolver` resolved `system/tools.md` are run.
10. Durable current-state findings discovered during implementation are routed through `c-05-create-or-update-onboarding-files` during that implementation cycle or, if consolidation is clearer, in the immediate closeout pass right after implementation.
11. Significant mid-implementation changes update the task file before edits continue.
12. When the Task Collaboration Doctrine (`tasks/AGENTS.md`) warrants it, the settled design is recorded in the task file's `## Design` section and the implementation steps derive from it.
13. A task that outgrows a single-page plan escalates to a master + light sub-task series
    (`master-template.md`): one wrapper folder (master `task.md` + flat `NN_<name>.md` sub-tasks),
    a master integration branch, one leaf enclosure/worktree per active slice, leaf integration into
    the master branch, and a final master release at the end.
14. Tool-managed task documents are JSON-primary: author `light` and `subTask` documents through the
    `task_doc` MCP tool (which writes the `ar-task-document/v1` JSON and renders the markdown); do not
    hand-edit a generated `task.md`. `template.md`/`master-template.md` are the render spec; series
    master files can remain hand-authored markdown until intentionally migrated.
15. Worktree-backed task status reaches `Completed` through `lifecycle_finalize_task`, not immediately
    after closeout. The finalizer updates the current task and the immediate parent row after the task
    commit is proven landed on the parent source branch; it does not recursively complete ancestors.

## Relationship To Other Instructions

This skill extends the repository instructions and agent definitions. It does not replace them.

Read `workflow.md` for the phase behavior and `template.md` for the task-file structure.

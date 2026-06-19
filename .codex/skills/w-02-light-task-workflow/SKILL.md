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

The task document is **JSON-primary**: an `ar-task-document/v1` JSON file is the source of truth, and the `task.md` (or `<slug>.md` for a sub-task) is a deterministic **render** of it. Author and update it with the `task_doc` MCP tool — `create`, `set_status`, `set_step`, `append_decision`, `set_field` — which writes the JSON and re-renders the markdown on every write. Do not hand-edit a tool-managed `task.md`; edit through the tool and let it re-render. `template.md` and `master-template.md` document the **render spec** (the shape the renderer produces), not a separate hand-authored format. When a planning slice defers its code examples to the plan gate, record that with `codeExamplesNote` (a `set_field` value, e.g. "Drafted at the plan gate.") so the rendered "Proposed Code Examples" section reads as *deferred* rather than as if none are needed.

Because the JSON encodes step/substep status explicitly, a tool-managed document is legible on the dashboard — the observer projects it, keyed to the lifecycle via the contract's `lifecycle_id`, at step granularity. The same schema/tool serves a *lightweight* document for a chat build (a thin doc: title plus a few steps, so even a no-`task.md` session is legible) and a *full* one for a durable task; the difference is content completeness, not a separate format.

Scope and transition: the tool manages **`light` and `subTask`** documents, and the format also covers a series **master** (`kind:"master"`: a structured `subTasks` index + ordered `sections` that preserve a master's bespoke prose, so a re-render is lossless). Masters and existing markdown task files stay hand-authored markdown until the runtime ships `task_doc` and they are intentionally migrated — new tasks adopt the JSON-primary format going forward.

## Master-Task Composition (task series)

When a task outgrows a single-page plan, escalate to a **master + light sub-task series**. One wrapper
folder holds a master `task.md` plus flat, numbered sub-task files (`NN_<name>.md`) in execution order;
`master-template.md` is the canonical scaffold.

The series lifecycle follows **one task = one workflow = one worktree**: the whole series runs in a
single `c-09-git-worktree-manager` worktree, each sub-task slice is committed via its own closeout (a commit, behind an
explicit commit gate), the worktree stays open across slices, and the series **integrates + cleans up
once, at the end**. The master owns the single version bump and any release packaging, after every
sub-task commit exists. See `master-template.md` for the convention and scaffolds.

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
11. set the task status to `Completed` once the approved implementation cycle and any approved closeout are finished

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
    (`master-template.md`): one wrapper folder (master `task.md` + flat `NN_<name>.md` sub-tasks), one
    shared worktree for the series, a commit per slice, and a single integrate + release at the end.
14. Tool-managed task documents are JSON-primary: author `light` and `subTask` documents through the
    `task_doc` MCP tool (which writes the `ar-task-document/v1` JSON and renders the markdown); do not
    hand-edit a generated `task.md`. `template.md`/`master-template.md` are the render spec; series
    master files stay hand-authored markdown for now, and existing markdown tasks migrate going forward.

## Relationship To Other Instructions

This skill extends the repository instructions and agent definitions. It does not replace them.

Read `workflow.md` for the phase behavior and `template.md` for the task-file structure.

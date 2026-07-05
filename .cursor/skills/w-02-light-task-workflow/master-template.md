# Master Task Template (task series)

Use this when a `w-02-light-task-workflow` task outgrows a single-page plan and is better run as a
**master + light sub-task series**: one master `task.md` that strings several light sub-task files.
This is the composition that replaces the retired heavy workflow — low-ceremony, append-friendly, and
built to grow as the work unfolds.

## When to escalate to a series

The `l-01-agent-lifecycles` orchestrator lifecycle's `decide` step escalates a single task to a series once its size is apparent — the
implementation plan no longer fits on a single page, or the work splits into distinct slices that each
deserve their own checklist and commit. You can also start single and escalate later: drop in the
master `task.md` and move the existing plan into the first `NN_<name>.md`.

## The series convention

- **One wrapper folder** holds the master `task.md` plus flat, numbered, descriptively-named sub-task
  files: `NN_<name>.md` (e.g. `01_job-lifecycle.md`, `02_task-format-reshape.md`). No nested phase
  folders.
- **Append-friendly:** the next sub-task is just the next `NN_<name>.md` dropped into the same folder.
  File numbers are stable creation IDs; the master's **Sub-tasks** list is the authoritative execution
  order (a later-numbered sub-task may run first).
- **One master integration branch, one leaf enclosure per active sub-task.** The master root
  `series-contract.md` represents the integration branch and is not itself worktree material. Each
  sub-task slice gets `enclosures/<leaf-id>/series-contract.md`, its own worktree branch, and its own
  worktree.
- **Each leaf lands into the master branch.** Each sub-task slice is committed via its own
  `c-09-git-worktree-manager` closeout behind an explicit commit gate, integrated into the master
  integration branch, and finalized for that parent-child edge.
- **The master owns the single release:** the version bump and any tag/release packaging happen once,
  at series end, after all sub-task commits exist. Sub-tasks never bump the version.
- **Each slice is test-verified before its commit:** run the repo test suite + the `system/tools.md`
  checks green before each incremental commit; testing is never deferred to the final slice.

## Master `task.md` scaffold

````markdown
# Task: <Master Title>

**Status:** planning | inProgress | Completed
**Repo:** <primary repo>
**Type:** Master (<Skill | Docs | Code | ...>)
**Created:** <YYYY-MM-DDTHH:MM>

---

## Objective

<The one operational outcome the whole series delivers — e.g. a single minor release.>

---

## Sub-tasks (execution order)

> File numbers are stable creation IDs; **this list is the authoritative execution order**.

1. **<Sub-task A>** · `01_<name>.md` — <scope>
2. **<Sub-task B>** · `02_<name>.md` — <scope>

Dependencies: <what must land before what>.

---

## Single Release (the master owns the final bump + tags)

- Sub-tasks commit **incrementally** (one `c-09-git-worktree-manager` closeout per slice, behind a commit gate);
  each leaf integrates into the master integration branch and finalizes its own edge.
- The master owns the **final release step only**: the version bump and any tag/release packaging,
  once every sub-task commit exists.

---

## Shared Decisions

| Date-Time | Decision | Rationale |
| --------- | -------- | --------- |

---

## Open Questions

- <cross-cutting questions; per-slice questions live in the sub-task files>

---

## References

- Sub-task files: `NN_<name>.md`
````

## Sub-task `NN_<name>.md` scaffold

Each sub-task file is a focused light-task plan (a slice of the master). It follows `template.md` but
is scoped to one slice and points back at the master:

````markdown
# Task: <Sub-task Title> (Sub-task <X>)

**Status:** planning | Implemented | Completed
**Repo:** <repo>
**Type:** <Skill | Docs | Code | ...>
**Created:** <YYYY-MM-DDTHH:MM>
**Master:** `task.md`

## Objective
## Requirements
## Implementation Steps        ← checkbox checklist for this slice
## Proposed Code Examples      ← when code changes are in scope
## Decision Log                ← slice-local decisions; cross-cutting ones live in the master
## References
````

## Usage rules

1. Create the master `task.md` and the first sub-task file together in one wrapper folder.
2. Keep sub-task files flat and numbered; do not nest phase folders.
3. Run the series through the master integration branch; create one leaf enclosure/worktree per active slice.
4. Only the master records the version bump and release; sub-tasks never bump.
5. Each slice runs the test suite + listed checks green before its commit.
6. Decision logs are append-only in both the master and the sub-task files.

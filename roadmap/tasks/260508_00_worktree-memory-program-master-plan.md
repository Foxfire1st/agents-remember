# Task: Worktree Memory Program Master Plan

**Status:** planning
**Repo:** agents-remember-md
**Type:** Other
**Created:** 2026-05-08T12:21

---

## Objective

Provide one birds-eye light-task artifact that tracks the ordered rollout from vocabulary cleanup through worktree lifecycle and branch-gated cross-repo resolution, so the full migration stays reviewable and dependency-aware.

---

## Design Philosophy

This program is turning the current loosely defined memory and task setup into an explicit operating model with three core ideas. First, durable memory and local coordination are different layers and need different homes: `ar-memory` or shared memory repos hold durable knowledge, while `ar-management` coordinates tasks, worktrees, notes, and operational state. Second, shared memory must stay aligned with code history through an explicit ledger instead of assuming the latest memory branch head is always valid. Third, worktree and cross-repo flows must be driven by explicit contracts, branch checks, and human approval instead of path guessing or implicit Git state.

The practical goal is to make shared memory usable even when code work diverges across branches and worktrees. The ledger gives the system a way to rewind or align memory to the right code baseline, task contracts make runtime state recoverable, and the C-09 lifecycle separates safe setup from approved closeout. That turns shared memory from a fragile sidecar into something that can support real branching strategies without silently corrupting context.

The safety goal is just as important as the feature goal. Later tasks in this stack are designed so the system can be more capable without becoming more reckless: no silent reuse of incompatible memory state, no implicit cross-repo trust, no guessing of task/worktree relationships, and no auto-mutation of history when human judgment is required. This master file should therefore read as the TL;DR of the whole migration: explicit boundaries, ledger-backed alignment, recoverable workflow state, and human-gated operations where semantic risk is high.

---

## Requirements

- Summarize the ordered task stack without duplicating the full detail of each child task.
- Keep the dependency chain visible so later tasks are not started on top of unresolved foundation gaps.
- Surface the biggest spec-to-current-state mismatches that need explicit decisions during execution.
- Give one place to track overall progress across the ordered light-task files.
- Keep this artifact as an overview only; detailed implementation belongs in the child task files.

---

## Implementation Steps

### S1 — Foundation Layer

- [ ] Complete the terminology and contract foundations first.
  - [ ] `260508_01_ar-memory-baseline-and-vocabulary.md`
  - [ ] `260508_02_resolver-memory-and-coordination-contract.md`
  - [ ] `260508_03_internal-and-shared-scaffold-split.md`
- [ ] Reconfirm the shared baseline before moving deeper.
  - [ ] README, AGENTS, C-00, C-03, and C-08 should no longer disagree on roots and roles.
  - [ ] The resolver should expose the new root model cleanly enough for later tasks to consume.

### S2 — Shared Memory State Layer

- [ ] Build the durable shared-memory primitives.
  - [ ] `260508_04_shared-memory-ledger-and-repo-bootstrap.md`
  - [ ] `260508_05_worktree-task-contract-foundation.md`
- [ ] Validate that the shared state model is coherent before adding the worktree manager.
  - [ ] Canonical `memory.md` format settled.
  - [ ] Task contract and task-folder layout decided.

### S3 — Worktree Lifecycle Layer

- [ ] Implement the pre-review and post-review halves of C-09 in order.
  - [ ] `260508_06_c09-worktree-manager-start-attach-status.md`
  - [ ] `260508_07_c09-worktree-manager-approved-closeout.md`
- [ ] Confirm the wrapper model still holds.
  - [ ] C-09 should wrap workflows rather than replacing them.
  - [ ] Human approval should remain the gate before commit and cleanup.

### S4 — Cross-Repo Branch-Gated Integration

- [ ] Finish the branch-gated cross-repo model only after the memory and worktree foundations exist.
  - [ ] `260508_08_cross-repo-v2-branch-gated-resolution.md`
  - [ ] Migrate live settings examples and validation to the v2 object model.
- [ ] Run a final integration review.
  - [ ] Confirm the resolver, ledger, contracts, C-09, and cross-repo output all agree on branch and path semantics.
  - [ ] Update any remaining live docs that would still teach the pre-migration model.

---

## Proposed Code Examples

### E1 — Not needed for this task

Distinct change covered: This master artifact tracks ordered execution across child tasks rather than introducing a direct implementation slice of its own.

Why this example is included: The light-task template keeps the section even when the artifact is an overview and progress-tracking layer.

```text
No direct code example is needed. This file is the overview and dependency map for the child light-task plans.
```

### E2 — Not needed for this task

Distinct change covered: No second implementation change type exists at the master-plan layer.

Why this example is included: This keeps the artifact aligned with the canonical template while making the overview-only scope explicit.

```text
No additional code example is needed unless the master plan itself later becomes an executable workflow task.
```

---

## Decision Log

| Date-Time        | Decision                                                                                                                     | Rationale                                                                                                                                                                                                          |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2026-05-08T12:21 | Use eight ordered light-task files plus one master overview.                                                                 | The migration spans several distinct implementation areas that are too large to manage safely in one light-task file.                                                                                              |
| 2026-05-08T12:21 | Put cross-repo v2 last in the stack.                                                                                         | Branch-gated cross-repo resolution depends on stable resolver, ledger, contract, and worktree foundations.                                                                                                         |
| 2026-05-08T12:21 | Surface cross-spec mismatches explicitly instead of hiding them.                                                             | The two design sheets currently disagree on some key details, and the plan should keep those decisions visible.                                                                                                    |
| 2026-05-08T12:44 | Keep `system/settings.md` and `system/settings.json` as the default settings structure across the program.                   | The live repo already uses the `system/` layout, and the plan should not invent a new `settings/` tree without a concrete implementation need.                                                                     |
| 2026-05-08T13:03 | Lock `memory.md` to one fenced JSON metadata block plus one newest-first two-column markdown table.                          | This matches the repo's JSON-first machine-readable convention, stays standard-library-friendly for Python, and avoids reintroducing YAML semantics through front matter.                                          |
| 2026-05-08T13:17 | Lock worktree-backed task artifacts into repo-scoped parent folders under `ar-management/tasks/<repo-name>/<task-name>-ar/`. | Mandatory worktree coordination needs each task artifact to live beside `contract.md` in the shared coordinator layer, and grouping by repository plus task/worktree name fits the existing `tasks/` root cleanly. |
| 2026-05-08T14:50 | Add explicit prose for the master-plan design philosophy.                                                                    | The overview should preserve why the stack is ordered the way it is, not just list the child tasks mechanically.                                                                                                   |

---

## Open Questions

- None.

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-cross-repo-mode-design-spec.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_01_ar-memory-baseline-and-vocabulary.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_02_resolver-memory-and-coordination-contract.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_03_internal-and-shared-scaffold-split.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_04_shared-memory-ledger-and-repo-bootstrap.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_05_worktree-task-contract-foundation.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_06_c09-worktree-manager-start-attach-status.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_07_c09-worktree-manager-approved-closeout.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_08_cross-repo-v2-branch-gated-resolution.md`

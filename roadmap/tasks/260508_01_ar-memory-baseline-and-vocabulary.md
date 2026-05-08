# Task: Ar-Memory Baseline And Vocabulary Alignment

**Status:** planning
**Repo:** agents-remember-md
**Type:** Docs
**Created:** 2026-05-08T12:21

---

## Objective

Align the live docs, skill prose, and path examples with the target split between `ar-memory` as durable memory and `ar-management` as local coordination so later implementation tasks do not inherit contradictory terminology.

---

## Design Philosophy

This task is the narrative reset point for the migration. Its purpose is not to redesign behavior directly, but to make sure the live guidance teaches one coherent model before later tasks start encoding that model into resolver logic, scaffolding, ledgers, and worktree automation.

The philosophy here is that vocabulary is architecture. If the live docs, skill prose, and path examples still describe the old world, later implementation work will either reintroduce those assumptions or force every downstream task to keep correcting them piecemeal. That is why this task focuses on aligning the terms `memory_root` and `coordination_root`, on making `ar-memory` versus `ar-management` explicit, and on treating the task stack itself as the current source of truth.

This pass is intentionally conservative about history. It should correct live guidance, not spend its budget adjudicating old design-note archives. The goal is to make the active repo surfaces unambiguous enough that later tasks can build from them without dragging legacy wording back into the implementation.

---

## Requirements

- Replace live guidance that still teaches repo-local `ar-management/` as the durable internal memory root.
- Preserve `ar-management/` as the coordination and worktree root, not the canonical memory layer.
- Standardize the terms `memory_root` and `coordination_root` across live docs and skill descriptions.
- Treat the light-task files as the authoritative planning surface for this migration; legacy design notes are informational only and out of scope for this pass.
- Keep resolver behavior changes, ledger parsing, and worktree operations out of scope for this task.

---

## Implementation Steps

### S1 — Freeze The Baseline Vocabulary Surface

- [ ] Freeze the list of live guidance files that must stop teaching the old internal layout.
  - [ ] Review `README.md`, `AGENTS.md`, `skills/U-01-core-skills/C-00-initialize-management-root/SKILL.md`, `skills/U-01-core-skills/C-03-repo-bootstrap/SKILL.md`, and `skills/U-01-core-skills/C-08-ar-management-resolver/SKILL.md`.
  - [ ] Ignore older design-note directories for this pass and use the light-task files as the authoritative migration plan.
- [ ] Keep this pass focused on live guidance.
  - [ ] Do not reconcile or prune historical design notes yet.
  - [ ] Restrict the actual wording updates to files that actively guide agent behavior or setup.

### S2 — Rewrite Live Terminology And Layout Examples

- [ ] Update internal durable-memory examples from repo-local `ar-management/` to repo-local `ar-memory/`.
  - [ ] Rewrite layout examples so internal repos show `ar-memory/onboarding`, `ar-memory/docs`, and `ar-memory/system/settings*.{md,json}`.
  - [ ] Keep shared examples rooted at `ar-management/` for coordination, `ar-management/system/settings*.{md,json}`, worktrees, tasks, notes, and memory-repo checkouts.
- [ ] Normalize the key vocabulary used by later implementation tasks.
  - [ ] Use `memory_root` for `ar-memory/` or a shared memory-repo root.
  - [ ] Use `coordination_root` for the local `ar-management/` coordinator.

### S3 — Validate The Documentation Baseline

- [ ] Run a focused search for live guidance that still treats repo-local `ar-management/` as internal durable memory.
  - [ ] Review the remaining hits and keep only intentional historical or roadmap references.
  - [ ] Re-read the main entry surfaces together to confirm that README, AGENTS, and core skill docs no longer disagree on the baseline model.
- [ ] Record any follow-up wording that must be deferred to later implementation tasks.
  - [ ] Note unresolved directory-layout decisions that depend on resolver or scaffold work.
  - [ ] Keep those follow-ups out of this baseline terminology pass.

---

## Proposed Code Examples

### E1 — Internal Versus Coordination Layout Example

Distinct change covered: Rewrite live layout examples so durable memory and coordination are shown as separate roots.

Why this example is included: This is the highest-leverage wording change because later skills, docs, and task plans all build on the same path model.

```text
projects/
  my-app/
    src/
    ar-memory/
      onboarding/
      docs/
      system/
        settings.md
        settings.json
        sources.md
        tools.md
  ar-management/
    system/
      settings.md
      settings.json
    memory-repos/
    tasks/
    notes/
    worktrees/
```

### E2 — Vocabulary Example

Distinct change covered: Standardize the terms used by later resolver and worktree tasks.

Why this example is included: The migration will keep slipping if future task files and docs keep using `ar-management` as a catch-all noun.

```text
memory_root = <repo>/ar-memory or ar-management/memory-repos/ar-<repo-name>
coordination_root = ar-management
```

---

## Decision Log

| Date-Time        | Decision                                                                            | Rationale                                                                                                                         |
| ---------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T12:21 | Start the migration with vocabulary and layout alignment.                           | Later code changes will stay error-prone if the live guidance still teaches the old model.                                        |
| 2026-05-08T12:21 | Keep behavioral resolver, scaffold, and worktree changes out.                       | This task should establish a clean narrative baseline, not mix in runtime refactors.                                              |
| 2026-05-08T13:58 | Treat the light-task files as authoritative and ignore legacy design notes for now. | This pass should align the live guidance to the approved task stack instead of reopening historical design-note cleanup.          |
| 2026-05-08T14:50 | Add explicit prose for the vocabulary-alignment design philosophy.                  | This task needs to preserve why wording cleanup is treated as a foundational architectural step rather than documentation polish. |

---

## Open Questions

- None. Legacy design notes are out of scope for this pass; the light-task files are the authoritative planning surface going forward.

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-cross-repo-mode-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/README.md`
- `/home/mohamedreadone/Projects/agents-remember-md/AGENTS.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-00-initialize-management-root/SKILL.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-03-repo-bootstrap/SKILL.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-08-ar-management-resolver/SKILL.md`
- `/home/mohamedreadone/Projects/docs_and_tasks/roadmap/multi-repo-task-worktree-design-spec.md`
- `/home/mohamedreadone/Projects/ar-management/notes/multi-repo-task-worktree-design-spec.md`

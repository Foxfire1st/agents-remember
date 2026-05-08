# Task: Internal And Shared Scaffold Split

**Status:** planning
**Repo:** agents-remember-md
**Type:** Skill
**Created:** 2026-05-08T12:21

---

## Objective

Split the bootstrap and scaffold story so internal durable memory initializes under `ar-memory/`, while the shared `ar-management/` coordinator owns tasks, notes, worktrees, and checked-out memory repos.

---

## Design Philosophy

This task is about making topology visible in the filesystem instead of burying it inside convention drift. Internal durable memory and shared coordination are not the same layer, so the scaffold should stop teaching them as though one root can safely play both roles.

The philosophy here is separation by responsibility. `ar-memory` exists to hold durable repo-local memory artifacts. `ar-management` exists to coordinate shared operational concerns such as worktrees, tasks, notes, and checked-out memory repos. When those responsibilities are scaffolded into distinct homes, later code can reason from the layout instead of compensating for an overloaded root concept.

This task also intentionally preserves the current `system/` structure. The goal is not to rename directories for aesthetic reasons, but to realign ownership boundaries while minimizing avoidable churn in the live repo conventions that later tasks and users already depend on.

---

## Requirements

- Update C-00 so it can create the durable internal `ar-memory/` scaffold and the local `ar-management/` coordinator scaffold as separate responsibilities.
- Stop teaching repo-local `ar-management/` as the default internal durable memory root.
- Keep shared scaffolding explicit and local, with `system/`, `memory-repos/`, `tasks/`, `notes/`, and `worktrees/` under the coordinator.
- Preserve `system/settings.md`, `system/settings.json`, `system/sources.md`, and `system/tools.md` as the default structure unless a later implementation constraint proves a real need to move them.
- Update C-03 and public setup guidance so bootstrap artifacts are written under the resolved `memory_root`, not under whichever root happened to exist first in the old topology.
- Defer shared memory-repo bootstrap details that depend on canonical `memory.md` behavior to the next task.

---

## Implementation Steps

### S1 — Split The Scaffold Responsibilities In C-00

- [ ] Redefine the scaffold targets for internal durable memory and shared coordination.
  - [ ] Internal scaffold should create `<code-repo>/ar-memory/onboarding`, `<code-repo>/ar-memory/docs`, and `<code-repo>/ar-memory/system/` with `settings.md`, `settings.json`, `sources.md`, and `tools.md`.
  - [ ] Shared coordinator scaffold should create `ar-management/system`, `ar-management/memory-repos`, `ar-management/tasks`, `ar-management/notes`, and `ar-management/worktrees`.
- [ ] Preserve the current `system/` layout instead of inventing a new top-level `settings/` tree.
  - [ ] Keep `system/settings.md` and `system/settings.json` as the authoritative settings files.
  - [ ] Keep `system/sources.md` and `system/tools.md` co-located unless a later implementation constraint proves they must move.

### S2 — Update Bootstrap And Setup Guidance

- [ ] Update C-03 and user-facing setup docs to use the resolved `memory_root` and `coordination_root` vocabulary.
  - [ ] Ensure overview/bootstrap state is written under the memory layer, not the coordinator.
  - [ ] Keep task artifacts and scratch coordination under the coordinator root.
- [ ] Narrow the scope of this task to layout and bootstrap flow only.
  - [ ] Do not implement shared memory-repo bootstrap commits here.
  - [ ] Do not implement worktree start or closeout logic here.

### S3 — Validate The New Scaffold Shapes

- [ ] Exercise the updated scaffold paths with focused dry-run or smoke-check scenarios.
  - [ ] Confirm internal setup produces `ar-memory/` rather than repo-local `ar-management/`.
  - [ ] Confirm shared setup produces the coordinator folders without implying one monorepo memory root.
- [ ] Re-read README, C-00, and C-03 together after the change.
  - [ ] Ensure the setup story is consistent from first-run scaffold through bootstrap.
  - [ ] Capture any remaining path ambiguities for the next task.

---

## Proposed Code Examples

### E1 — Split Scaffold Layout

Distinct change covered: Introduce separate directory layouts for durable memory and local coordination.

Why this example is included: The old single-root model is the main source of implementation drift between the live code and the design target.

```text
<code-repo>/
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

### E2 — Bootstrap Target Selection

Distinct change covered: Show the intended branch in scaffold logic between internal memory and shared coordination.

Why this example is included: The bootstrap code should stop assuming one root owns every artifact.

```python
if topology == "internal":
    create_memory_scaffold(target_repo / "ar-memory")
    ensure_system_files(target_repo / "ar-memory" / "system")
else:
    ensure_coordination_root(shared_coordination_root)
    ensure_system_files(shared_coordination_root / "system")
    ensure_memory_repo_parent(shared_coordination_root / "memory-repos")
```

---

## Decision Log

| Date-Time        | Decision                                                                                 | Rationale                                                                                                                                                                  |
| ---------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T12:21 | Treat scaffold splitting as its own task before memory-repo bootstrap.                   | The bootstrap story needs a clean root layout before it can safely create shared memory repos and ledgers.                                                                 |
| 2026-05-08T12:21 | Keep shared memory-repo initialization out of this slice.                                | Canonical `memory.md` handling must be settled first so bootstrap does not guess the wrong format.                                                                         |
| 2026-05-08T12:44 | Keep the existing `system/` settings structure in both memory and coordinator scaffolds. | The live repo already relies on `system/settings*.{md,json}` plus co-located `sources.md` and `tools.md`, so planning a new `settings/` tree here would be needless churn. |
| 2026-05-08T14:50 | Add explicit prose for the scaffold-split design philosophy.                             | The artifact should preserve why internal memory and shared coordination are being separated by responsibility instead of just by path rename.                             |

---

## Open Questions

- None. Keep the current `system/` layout unless a later implementation constraint proves it must change.

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/README.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-00-initialize-management-root/SKILL.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-03-repo-bootstrap/SKILL.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-08-ar-management-resolver/SKILL.md`

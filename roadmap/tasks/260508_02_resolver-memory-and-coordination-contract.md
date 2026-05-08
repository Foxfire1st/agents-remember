# Task: Resolver Memory And Coordination Contract

**Status:** planning
**Repo:** agents-remember-md
**Type:** Skill
**Created:** 2026-05-08T12:21

---

## Objective

Refactor C-08 so it returns a clear split between coordination paths, memory paths, and optional task or worktree context, while keeping C-08 facts-only and leaving Git operations to a future C-09.

---

## Design Philosophy

This task treats the resolver contract as the system boundary between declarative state and imperative workflow behavior. C-08 should answer where things are and what topology is in effect; it should not create, mutate, reconcile, or infer Git state on behalf of later consumers.

The philosophy is to make path facts explicit enough that downstream skills stop guessing. Coordination roots, memory roots, worktree groups, task roots, and ledger paths must become first-class resolved facts because later tasks depend on them operationally. If the resolver leaves those relationships implicit, later code will recreate hidden topology rules from prose, folder names, or ad hoc defaults.

Keeping C-08 facts-only is also a safety boundary. Once resolver output is stable, scaffolding, worktree management, ledger consumers, and drift detection can build on one shared contract without smuggling side effects into what should remain a read-only source of truth.

---

## Requirements

- Extend the resolver contract to distinguish `coordination_root`, `memory_root`, `onboarding_root`, `docs_root`, `system_root`, `settings_path`, `path_settings_path`, `task_root`, `worktree_group`, `code_worktree`, `memory_worktree`, and `ledger_path`.
- Keep C-08 responsible only for path and settings facts, not scaffold creation, branch switching, or worktree mutation.
- Preserve JSON-first settings parsing and keep existing consumer behavior working or explicitly migrated in the same slice.
- Support internal mode, shared mode, and contract-backed worktree context without guessing from prose.
- Add focused validation for the updated JSON output and for the C-02 drift helper that depends on C-08.

---

## Implementation Steps

### S1 — Redefine The Resolver Data Model

- [ ] Add the new context fields and keep the boundary between coordination facts and memory facts explicit.
  - [ ] Extend the dataclasses used by `ar_management_resolver.py` so they can represent internal memory, shared memory repos, and worktree-backed overrides.
  - [ ] Decide which fields are always present versus optional when no task or worktree contract exists.
- [ ] Update serialization helpers and human-readable output.
  - [ ] Keep machine-readable JSON stable and explicit about missing optional fields.
  - [ ] Avoid inventing worktree state when no contract exists.

### S2 — Rework Resolution Logic Around The New Contract

- [ ] Resolve internal and shared roots without collapsing them back into one `management_root` idea.
  - [ ] Internal mode should resolve a durable `memory_root` under `<repo>/ar-memory` and a separate `coordination_root`.
  - [ ] Shared mode should resolve a durable `memory_root` under `ar-management/memory-repos/ar-<repo-name>` and a separate `coordination_root`.
- [ ] Add hooks for task and worktree context.
  - [ ] Read task-contract locations when present instead of relying on path guessing.
  - [ ] Resolve task roots under `ar-management/tasks/<repo-name>/<task-name>-ar/` when a worktree-backed task is active.
  - [ ] Expose worktree-group, code-worktree, memory-worktree, and ledger paths as resolved facts only.

### S3 — Validate The New Resolver Contract

- [ ] Run focused resolver smoke checks against internal and shared examples.
  - [ ] Compare the JSON output to the design-spec examples and record intentional deviations.
  - [ ] Confirm optional worktree fields stay empty rather than guessed when no contract exists.
- [ ] Re-run the C-02 drift helper against `agents-remember-md` using the refactored C-08.
  - [ ] Ensure drift detection still receives the roots it needs.
  - [ ] Confirm that the refactor did not reintroduce resolver duplication into C-02.

---

## Proposed Code Examples

### E1 — Resolver Context Shape

Distinct change covered: Expand the resolver result so coordination and memory are separate first-class concepts.

Why this example is included: The dataclass boundary is the core contract that later scaffold, ledger, contract, and worktree tasks will all depend on.

```python
@dataclass
class ManagementContext:
    topology: Literal["internal", "shared"]
    repo_name: str
    target_repo: Path
    coordination_root: Path
    memory_root: Path
    onboarding_root: Path
    docs_root: Path
    system_root: Path
    settings_path: Path
    path_settings_path: Path | None
    task_root: Path | None
    worktree_group: Path | None
    code_worktree: Path | None
    memory_worktree: Path | None
    ledger_path: Path | None
```

### E2 — Shared-Mode JSON Output

Distinct change covered: Show the target JSON shape for downstream consumers.

Why this example is included: A concrete output sample makes it easier to review whether C-08 is still facts-only and whether later tasks can consume it without path guessing.

```json
{
  "coordination_root": "/workspace/ar-management",
  "memory_root": "/workspace/ar-management/memory-repos/ar-device-management",
  "onboarding_root": "/workspace/ar-management/memory-repos/ar-device-management/onboarding",
  "docs_root": "/workspace/ar-management/memory-repos/ar-device-management/docs",
  "system_root": "/workspace/ar-management/memory-repos/ar-device-management/system",
  "settings_path": "/workspace/ar-management/memory-repos/ar-device-management/system/settings.md",
  "path_settings_path": "/workspace/ar-management/memory-repos/ar-device-management/system/settings.json",
  "ledger_path": "/workspace/ar-management/memory-repos/ar-device-management/memory.md",
  "worktree_group": "",
  "code_worktree": "",
  "memory_worktree": ""
}
```

---

## Decision Log

| Date-Time        | Decision                                                                                                          | Rationale                                                                                                                                                             |
| ---------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T12:21 | Keep C-08 as a facts-only resolver.                                                                               | Git worktree creation, branch checks, and closeout sequencing belong in C-09.                                                                                         |
| 2026-05-08T12:21 | Make the resolver contract explicit before adding consumers.                                                      | Later tasks should build on one stable path contract instead of hidden assumptions.                                                                                   |
| 2026-05-08T12:44 | Preserve the `system/` settings structure in the resolver contract.                                               | The live repo already uses `system/settings.md` and `system/settings.json`, so the plan should resolve those files instead of inventing a new `settings/` directory.  |
| 2026-05-08T13:17 | Treat worktree-backed `task_root` as a repo-scoped shared-coordinator folder, not an optional flat file location. | The task artifact and `contract.md` now need to live together under `ar-management/tasks/<repo-name>/<task-name>-ar/` so resolver consumers can recover both cleanly. |
| 2026-05-08T14:50 | Add explicit prose for the resolver-contract design philosophy.                                                   | The artifact should preserve why C-08 remains facts-only and why explicit path boundaries matter to every later task.                                                 |

---

## Open Questions

- None. When a worktree-backed task exists, `task_root` resolves under the shared coordinator at `ar-management/tasks/<repo-name>/<task-name>-ar/`.

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-cross-repo-mode-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-08-ar-management-resolver/SKILL.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-08-ar-management-resolver/scripts/ar_management_resolver.py`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-02-onboarding-drift-detection/SKILL.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-02-onboarding-drift-detection/scripts/check_onboarding_drift.py`

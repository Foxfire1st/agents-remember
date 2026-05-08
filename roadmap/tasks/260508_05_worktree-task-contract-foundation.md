# Task: Worktree Task Contract Foundation

**Status:** planning
**Repo:** agents-remember-md
**Type:** Skill
**Created:** 2026-05-08T12:21

---

## Objective

Define and implement the task-contract layer that records task identity, workflow, code and memory worktree paths, base commits, and memory mode so resolver and worktree flows can recover state without path guessing.

---

## Design Philosophy

This task treats the worktree contract as operational memory for the workflow itself. Its role is to make runtime state durable enough that later agents and tools can recover what was prepared, where it lives, and how the code and memory sides relate without reconstructing that state from path conventions or chat history.

The philosophy is explicit state over inferred state. Once a worktree-backed task exists, its identity, paths, branches, base commits, memory mode, and ledger relationship should be recorded in one canonical place. That lets C-08 remain facts-only, lets C-09 stay focused on lifecycle logic, and prevents the worktree model from depending on folder-name heuristics that become fragile as the system grows.

The contract is therefore not policy and not a replacement for workflow artifacts. It is the runtime handshake between planning and execution: a durable record of what the workflow prepared, so subsequent steps can continue safely and consistently.

---

## Requirements

- Implement a canonical `contract.md` shape that records task, workflow, repo, worktree, and ledger relationships.
- Put every worktree-backed task inside a repo-scoped parent folder under `ar-management/tasks/<repo-name>/<task-name>-ar/`.
- Keep `contract.md` at the root of that parent folder and place the workflow-owned task artifact beside it.
- Provide reader and writer helpers so C-08 and C-09 can consume the same contract format.
- Model internal, shared, and disabled-memory cases explicitly.
- Keep the contract as operational state only; do not turn it into durable policy or a replacement for task workflow artifacts.

---

## Implementation Steps

### S1 — Freeze The Contract Schema And Storage Layout

- [ ] Define the minimum contract fields and optional fields.
  - [ ] Include task identity, workflow kind, coordination root, worktree group, code repo and worktree, memory repo and worktree, base commits, memory mode, and ledger path.
  - [ ] Decide how to represent memory-disabled work without inventing fake paths.
- [ ] Freeze the storage relationship between task contracts and workflow artifacts.
  - [ ] Make the parent folder mandatory at `ar-management/tasks/<repo-name>/<task-name>-ar/`.
  - [ ] Keep `contract.md` at the folder root.
  - [ ] Store the workflow-owned artifact beside it, using `task.md` for light tasks and a workflow-owned folder such as `task/` when a richer workflow needs multiple artifacts.

### S2 — Implement Contract Helpers

- [ ] Add parser and writer helpers for `contract.md`.
  - [ ] Support create, load, update, and validation flows.
  - [ ] Keep the helpers reusable by C-08 and C-09.
- [ ] Expose contract-backed resolution helpers.
  - [ ] Resolve the active worktree group, code worktree, memory worktree, and ledger path from the contract.
  - [ ] Return explicit missing values rather than guessing from folder names.

### S3 — Validate Contract Consumption

- [ ] Create representative sample contracts for internal, shared, and disabled-memory tasks.
  - [ ] Validate that the helpers read each contract shape correctly.
  - [ ] Confirm that invalid or partial contracts produce useful errors.
- [ ] Reconcile the task-layout implications with the light-task workflow.
  - [ ] Update the workflow docs if task folders become mandatory.
  - [ ] Confirm the new contract layer still lets agents recover the task state quickly.

---

## Proposed Code Examples

### E1 — Contract Metadata Shape

Distinct change covered: Define the operational state the worktree manager and resolver will share.

Why this example is included: The contract schema is the durable boundary between planning artifacts and runtime worktree state.

```markdown
---
schema: ar-worktree-contract/v1
task_id: ARWT-123
task_name: fix-platform-status
repo_name: device-management
workflow_kind: light-task
memory_mode: shared

coordination:
  root: /workspace/ar-management
  task_root: /workspace/ar-management/tasks/device-management/fix-platform-status-ar
  contract_path: /workspace/ar-management/tasks/device-management/fix-platform-status-ar/contract.md
  task_artifact: /workspace/ar-management/tasks/device-management/fix-platform-status-ar/task.md
  worktree_group: /workspace/ar-management/worktrees/device-management/fix-platform-status-ar

code:
  repo_path: /workspace/repos/device-management
  source_branch: dev
  work_branch: feature/fix-platform-status
  base_commit: abc123
  worktree: /workspace/ar-management/worktrees/device-management/fix-platform-status-ar/fix-platform-status

memory:
  repo_path: /workspace/ar-management/memory-repos/ar-device-management
  source_branch: dev
  work_branch: feature/fix-platform-status
  base_commit: def456
  worktree: /workspace/ar-management/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status
  ledger: /workspace/ar-management/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status/memory.md
---
```

### E2 — Contract Helper API

Distinct change covered: Provide one code path for contract consumers.

Why this example is included: C-08 and C-09 should not each parse Markdown contracts in their own way.

```python
@dataclass
class WorktreeContract:
    task_id: str
    repo_name: str
    workflow_kind: str
    memory_mode: str
    worktree_group: Path
    code_worktree: Path
    memory_worktree: Path | None
    ledger_path: Path | None

def load_contract(path: Path) -> WorktreeContract:
    ...
```

---

## Decision Log

| Date-Time        | Decision                                                                      | Rationale                                                                                                                                                                           |
| ---------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T12:21 | Give task contracts their own task before C-09 exists.                        | Worktree lifecycle code will stay fragile if it has to invent state from folder names or chat history.                                                                              |
| 2026-05-08T12:21 | Treat task-layout coexistence as an explicit design question.                 | The new contract folder shape conflicts with the current flat W-02 task storage model.                                                                                              |
| 2026-05-08T13:17 | Make repo-scoped parent folders mandatory for worktree-backed task artifacts. | The task artifact needs to live beside `contract.md`, and `ar-management/tasks/<repo-name>/<task-name>-ar/` gives that pairing a stable home without mixing unrelated repositories. |
| 2026-05-08T14:50 | Add explicit prose for the worktree-contract design philosophy.               | The artifact should preserve why the contract exists as runtime state and why later agents must not recover worktree relationships by guessing.                                     |

---

## Open Questions

- None. Worktree-backed task artifacts live under `ar-management/tasks/<repo-name>/<task-name>-ar/`, with `contract.md` at the root and the workflow-owned task artifact beside it.

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/W-02-light-task-workflow/SKILL.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/W-02-light-task-workflow/workflow.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/W-02-light-task-workflow/template.md`

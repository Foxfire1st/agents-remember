# Task: C-09 Worktree Manager Start Attach Status

**Status:** planning
**Repo:** agents-remember-md
**Type:** Skill
**Created:** 2026-05-08T12:21

---

## Objective

Build the first half of C-09 so it can create or attach to worktree groups, validate memory compatibility, record task contracts, and report current state without committing or closing out work.

---

## Design Philosophy

This task treats a shared memory repo as a durable context store whose current branch head is not automatically valid for every code line a developer may want to work on. The role of the ledger in `memory.md` is to restore semantic alignment between code history and memory history by mapping code baselines to the last known compatible memory state. That is the capability that makes memory rewinding possible in a controlled way and lets shared memory repos support divergent worktree strategies without collapsing into "always use the latest memory head."

The `start` workflow is therefore an alignment workflow before it is a setup workflow. It should prepare the code worktree first, then use the ledger to determine whether a rewindable compatible memory baseline exists for the target code state. If the ledger can identify that baseline, `start` may prepare the shared-memory side from that aligned point. If it cannot, `start` must not guess, silently reuse the latest memory branch, or mutate memory history just to keep moving.

That is why incompatibility handling is intentionally one hundred percent interactive. When no safe compatible baseline exists, the agent must explain the situation in human terms: whether a rewind point exists, how far the current memory lineage has drifted from the target code line, why automatic reuse or rewind is unsafe, and what the available recovery choices are. Reconciliation, clean start, disabled memory, and a custom freeform instruction path are human decisions because the ledger is being used to achieve flexibility that Git does not provide natively for this kind of cross-history alignment.

The safety model is therefore: use the ledger to maximize recoverability and branching flexibility, but require explicit human judgment before any step that could throw away, bypass, or reinterpret valid shared memory history. That balance is the core concept this task needs to preserve.

---

## Requirements

- Create the new C-09 worktree-manager skill and its implementation surface.
- Implement `start`, `attach`, and `status` flows for code worktrees first and memory worktrees second when shared memory is enabled.
- Use C-08, the ledger core, and the task-contract helpers rather than duplicating their logic.
- Support the three no-match outcomes for shared memory: reconciliation, clean start, and disabled memory.
- Treat the shared-memory ledger as the rewind and alignment mechanism that maps a target code baseline to the last known compatible memory state, rather than assuming the memory branch head is always valid.
- Make `start` explicitly interactive when no compatible memory branch exists: explain the incompatibility to the developer, summarize the compatibility gap, present the three built-in choices plus a freeform custom instruction path, and wait for human input before proceeding.
- Keep commits, ledger updates, and cleanup out of scope for this task.

---

## Implementation Steps

### S1 — Scaffold The New C-09 Skill Surface

- [ ] Create the C-09 skill docs and implementation entry points.
  - [ ] Define the public commands or script subcommands for `start`, `attach`, and `status`.
  - [ ] Document the facts C-09 expects from C-08 and the task-contract layer.
- [ ] Keep the task tightly scoped to the pre-review lifecycle.
  - [ ] No closeout behavior.
  - [ ] No auto-commit behavior.

### S2 — Implement Start And Attach Logic

- [ ] Implement code worktree creation or reuse.
  - [ ] Create the worktree group under `ar-management/worktrees/<repo-name>/<worktree-name>-ar/`.
  - [ ] Create the code worktree first and record it in the contract.
- [ ] Implement shared-memory branch and worktree preparation.
  - [ ] Resolve or create the matching memory branch and memory worktree when shared mode is enabled.
  - [ ] Use the ledger to determine whether a rewindable compatible memory baseline exists for the target code state instead of treating the current memory branch head as authoritative by default.
  - [ ] If no compatible branch exists, stop and explain the incompatibility instead of using divergent memory as reference context.
  - [ ] Show the developer the available built-in choices: reconciliation, clean start, and disabled memory.
  - [ ] Offer a fourth freeform path so the developer can supply a custom instruction for unusual recovery cases.
  - [ ] Include the relevant compatibility context in that prompt, such as whether the ledger provides a rewindable baseline, how far apart the current memory lineage is from the target code branch, and why automatic rewind or reuse is not safe.

### S3 — Implement Status Reporting And Validation

- [ ] Add `status` output that reports the current contract and worktree state.
  - [ ] Report code worktree, memory worktree, memory mode, compatibility state, and ledger path when present.
  - [ ] Distinguish between missing state and invalid state clearly.
- [ ] Validate the start and attach flows with focused smoke scenarios.
  - [ ] Shared compatible case.
  - [ ] Shared no-match case.
  - [ ] Internal-memory case.

---

## Proposed Code Examples

### E1 — Start Flow Skeleton

Distinct change covered: Show the control flow for worktree preparation without leaking closeout behavior into this task.

Why this example is included: The start flow is the seam where resolver, contract, and ledger helpers first converge.

```text
start(repo_name, worktree_name, memory_mode)
  -> resolve management context
  -> create or load task contract
  -> ensure code worktree
  -> if shared memory is enabled:
       inspect the ledger for a rewindable memory baseline that matches the target code commit
       if a compatible rewind point exists:
         prepare the memory worktree from that aligned memory state
       else:
         explain the incompatibility and rewindability situation to the developer
         present: reconciliation | clean_start | disabled_memory | custom
         wait for explicit human input
         apply the chosen memory-resolution path
  -> return current worktree and compatibility status
```

### E2 — Status Output Example

Distinct change covered: Make worktree state visible before any implementation begins.

Why this example is included: Clear status output is what lets a human confirm the wrapper prepared the right roots before approving work.

```json
{
  "task_id": "ARWT-123",
  "repo_name": "device-management",
  "memory_mode": "shared",
  "worktree_group": "/workspace/ar-management/worktrees/device-management/fix-platform-status-ar",
  "code_worktree": "/workspace/ar-management/worktrees/device-management/fix-platform-status-ar/fix-platform-status",
  "memory_worktree": "/workspace/ar-management/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status",
  "state": "compatible"
}
```

---

## Decision Log

| Date-Time        | Decision                                                                             | Rationale                                                                                                                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T12:21 | Split C-09 into pre-review and closeout tasks.                                       | Start and closeout have different risks, dependencies, and approval needs.                                                                                                                           |
| 2026-05-08T12:21 | Treat compatibility choices as reporting and setup only here.                        | The wrapper should not commit to reconciliation or bootstrap behavior without later dedicated closeout logic.                                                                                        |
| 2026-05-08T14:23 | Make incompatible shared-memory handling in `start` one hundred percent interactive. | If no compatible memory branch exists, the agent must explain the situation, present the built-in choices plus a custom path, and wait for human direction before proceeding.                        |
| 2026-05-08T14:33 | Make ledger-backed memory rewinding explicit in the `start` contract.                | The shared-memory ledger exists to recover the last known compatible memory baseline for a target code state, so `start` must surface rewindability analysis before asking the human how to proceed. |
| 2026-05-08T14:37 | Add explicit prose for the start-flow design philosophy.                             | The task file should preserve the rationale for ledger-backed rewinding and human-gated incompatibility handling even after chat context is reset.                                                   |

---

## Open Questions

- None. `start` should use the ledger to determine whether a rewindable compatible memory baseline exists, and when compatibility is missing or unsafe it should block for explicit human input with reconciliation, clean start, disabled memory, and a freeform custom-instruction option plus the relevant rewindability context.

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-08-ar-management-resolver/scripts/ar_management_resolver.py`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_04_shared-memory-ledger-and-repo-bootstrap.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_05_worktree-task-contract-foundation.md`

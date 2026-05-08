# Task: C-09 Worktree Manager Approved Closeout

**Status:** planning
**Repo:** agents-remember-md
**Type:** Skill
**Created:** 2026-05-08T12:21

---

## Objective

Implement the human-approved closeout half of C-09 so code changes, memory changes, and ledger updates are wrapped up in the correct sequence after review instead of being auto-committed at task finish.

---

## Design Philosophy

This task treats closeout as a guarded convergence step, not as an automatic epilogue to implementation. The system is deliberately separating start/setup from closeout because once shared memory and ledger history are involved, finishing work is where irreversible mistakes become easiest to make.

The philosophy is to preserve alignment and human judgment through the final write path. Shared memory closeout must respect the ordering between code commits, memory-content commits, and ledger updates so the ledger remains a truthful mapping rather than a best-effort summary. Internal mode stays simpler, but it still inherits the same principle that review approval and current branch state must be revalidated before wrap-up proceeds.

Cleanup is intentionally secondary to correctness. The important thing is that the resulting contract and commit history tell the truth about what happened. Once that is safe and explicit, cleanup can be optional and human-controlled instead of being bundled into a destructive all-in-one closeout step.

---

## Requirements

- Keep human approval as a hard gate before any commit or cleanup action.
- Re-check source-branch movement and surface conflicts before wrapping up.
- For shared memory, commit code changes first, then memory-content changes, then the ledger update commit that records `C2 -> M2`.
- Update the task contract closeout state and optionally support cleanup only after wrap-up succeeds.
- Keep push behavior explicit rather than automatic.

---

## Implementation Steps

### S1 — Define The Approved Closeout Entry Conditions

- [ ] Freeze the inputs C-09 closeout requires.
  - [ ] Approved task contract.
  - [ ] Current code and memory worktree state.
  - [ ] Current source-branch heads for conflict checks.
- [ ] Define the failure and stop conditions.
  - [ ] Source branch moved.
  - [ ] Review requested changes instead of approval.
  - [ ] Memory ledger cannot be updated safely.

### S2 — Implement The Shared And Internal Closeout Sequences

- [ ] Implement internal-memory closeout.
  - [ ] Commit approved code and `ar-memory` changes together or in the approved series.
  - [ ] Update contract status without attempting any ledger work.
- [ ] Implement shared-memory closeout.
  - [ ] Commit code changes and capture `C2`.
  - [ ] Commit memory-content changes and capture `M2`.
  - [ ] Prepend the ledger row, update the metadata summary, and commit the ledger update as `L2`.

### S3 — Finalize Reporting And Optional Cleanup

- [ ] Update the task contract with closeout status and resulting commit identifiers.
  - [ ] Record whether cleanup is pending or completed.
  - [ ] Preserve human review status separately from workflow completion.
- [ ] Validate the closeout flow with focused scenarios.
  - [ ] Internal mode.
  - [ ] Shared mode with successful ledger update.
  - [ ] Shared mode with source movement or conflict surfaced before commit.

---

## Proposed Code Examples

### E1 — Shared Closeout Sequence

Distinct change covered: Preserve the required `C2 -> M2 -> L2` ordering for shared memory.

Why this example is included: The ordering is the core invariant that keeps the code and memory histories aligned without auto-commit shortcuts.

```python
def closeout_shared_task(contract: WorktreeContract) -> CloseoutResult:
    ensure_human_approval(contract)
    recheck_source_heads(contract)
    code_commit = commit_code_changes(contract.code_worktree)
    memory_commit = commit_memory_changes(contract.memory_worktree)
    ledger_commit = commit_ledger_update(contract.ledger_path, code_commit, memory_commit)
    return record_closeout(contract, code_commit, memory_commit, ledger_commit)
```

### E2 — Contract Closeout State Example

Distinct change covered: Show the operational state that should be recorded after wrap-up.

Why this example is included: The contract should let a later agent or human see whether wrap-up completed and what commits were produced.

```yaml
human_review:
  status: approved
closeout:
  status: completed
  code_commit: abc123
  memory_content_commit: def456
  ledger_commit: fedcba
  cleanup: pending
```

---

## Decision Log

| Date-Time        | Decision                                                    | Rationale                                                                                                                                         |
| ---------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T12:21 | Keep approved closeout separate from worktree start/status. | Commit sequencing and approval gates deserve their own reviewable task.                                                                           |
| 2026-05-08T12:21 | Treat push behavior as explicit and optional.               | The design target requires human-controlled integration rather than automatic remote mutation.                                                    |
| 2026-05-08T14:50 | Add explicit prose for the closeout design philosophy.      | The task file should preserve why closeout is treated as a guarded convergence step with strict sequencing instead of an automatic finish action. |

---

## Open Questions

- Should cleanup be part of the same closeout command, or a separate explicit action once commits and contract updates have succeeded?

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_04_shared-memory-ledger-and-repo-bootstrap.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_05_worktree-task-contract-foundation.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_06_c09-worktree-manager-start-attach-status.md`

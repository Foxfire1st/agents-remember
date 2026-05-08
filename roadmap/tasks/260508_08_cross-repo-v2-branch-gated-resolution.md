# Task: Cross-Repo V2 Branch Gated Resolution

**Status:** planning
**Repo:** agents-remember-md
**Type:** Config
**Created:** 2026-05-08T12:21

---

## Objective

Upgrade cross-repo resolution to the branch-gated v2 model so external code and memory clues are included only when they are explicitly allowed, on the expected branch, and backed by valid memory-ledger metadata.

---

## Design Philosophy

This task treats cross-repo inclusion as an explicit trust contract, not as a convenience feature. External code and memory should only influence the current repo when the shared-memory configuration says they may, when the branch expectations line up, and when the external memory lineage can be validated rather than assumed.

The philosophy is that cross-repo context must be safer than ad hoc repository browsing. Because the workflow is already using ledgers, resolver context, and optional worktree state to keep local history aligned, cross-repo inclusion needs the same level of discipline. That is why the old string-based allow list is no longer good enough: it cannot express branch expectations or the distinction between including code alone and including memory as well.

This task also keeps cross-repo resolution read-only. The goal is to let the system reason about whether outside context is trustworthy and relevant, not to let one repo's workflow mutate another repo's memory state as a side effect of discovery.

---

## Requirements

- Upgrade `crossRepo.allow` from string entries to strict object entries with `repo`, `expectedBranch`, `includeCode`, and `includeMemory`.
- Keep cross-repo policy in committed memory settings, not in local coordinator settings.
- Treat legacy string entries as invalid for v2 rather than guessing missing branches.
- Use the resolver contract, task/worktree context, and ledger parser from earlier tasks instead of re-deriving external memory state.
- Keep cross-repo access read-only toward external repos and expose included, included-code-only, and excluded states with reasons.

---

## Implementation Steps

### S1 — Upgrade The Settings Model And Validation

- [ ] Extend the settings dataclasses and JSON validation rules for v2 cross-repo entries.
  - [ ] Add a typed allow-entry model with defaults for `includeCode` and `includeMemory`.
  - [ ] Reject legacy string entries with a migration error instead of silently treating them as safe.
- [ ] Decide how the version transition is enforced.
  - [ ] Current live shared settings are still version 1 with string repo entries.
  - [ ] The migration path must be explicit rather than hidden inside permissive parsing.

### S2 — Implement The Branch-Gated Resolution Algorithm

- [ ] Resolve external code and memory paths from task context or local coordinator hints.
  - [ ] Confirm code branch match before including anything.
  - [ ] Include memory only when `includeMemory` is true and the external memory branch plus ledger metadata match `expectedBranch`.
- [ ] Expose structured result states and reasons.
  - [ ] `included`
  - [ ] `included-code-only`
  - [ ] `excluded`

### S3 — Validate Cross-Repo Resolution And Snapshot Reporting

- [ ] Add focused tests or fixtures for the v2 cases described in the design spec.
  - [ ] Disabled or empty allow list.
  - [ ] Legacy string entry rejected.
  - [ ] Code-only include.
  - [ ] Memory include when branch and ledger checks all pass.
- [ ] Validate worktree-aware snapshot reporting.
  - [ ] Record included and excluded entries without turning the snapshot into policy.
  - [ ] Confirm external repos remain read-only during resolution.

---

## Proposed Code Examples

### E1 — V2 Cross-Repo Settings Shape

Distinct change covered: Replace the old string-array allow list with strict object entries.

Why this example is included: The current live config uses string entries, but branch-gated cross-repo mode cannot be implemented safely until the settings shape changes.

```json
{
  "version": 2,
  "crossRepo": {
    "allow": [
      {
        "repo": "billing-api",
        "expectedBranch": "dev",
        "includeCode": true,
        "includeMemory": true
      }
    ]
  }
}
```

### E2 — Cross-Repo Allow Entry Model

Distinct change covered: Type the new allow-entry contract in code.

Why this example is included: The settings parser and resolver output both depend on the same validation shape.

```python
@dataclass
class CrossRepoAllowEntry:
    repo: str
    expected_branch: str
    include_code: bool = True
    include_memory: bool = False

@dataclass
class CrossRepoSettings:
    allow: list[CrossRepoAllowEntry] = field(default_factory=list)
```

---

## Decision Log

| Date-Time        | Decision                                                              | Rationale                                                                                                                                                |
| ---------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T12:21 | Schedule cross-repo v2 after ledger and worktree foundations.         | Branch-gated memory inclusion depends on stable memory roots, ledger parsing, and optional worktree context.                                             |
| 2026-05-08T12:21 | Treat the version-1 string allow list as a deliberate migration step. | Safe branch-gated resolution requires explicit branch metadata, not inferred defaults.                                                                   |
| 2026-05-08T14:50 | Add explicit prose for the cross-repo design philosophy.              | The artifact should preserve that cross-repo inclusion is an explicit trust contract with read-only branch-gated validation, not a convenience shortcut. |

---

## Open Questions

- Should `storage.mode` stay behavior-oriented as `repo-sidecar`, with shared-versus-internal topology coming entirely from the resolved `memory_root`, or should we still add an explicit `memory-repo` mode even though shared memory repos are already sidecar repos physically?

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-cross-repo-mode-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-08-ar-management-resolver/scripts/ar_management_resolver.py`
- `/home/mohamedreadone/Projects/ar-management/system/settings.json`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_02_resolver-memory-and-coordination-contract.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_04_shared-memory-ledger-and-repo-bootstrap.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_05_worktree-task-contract-foundation.md`
- `/home/mohamedreadone/Projects/ar-management/tasks/260508_06_c09-worktree-manager-start-attach-status.md`

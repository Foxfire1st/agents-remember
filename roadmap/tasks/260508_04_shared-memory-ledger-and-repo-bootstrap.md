# Task: Shared Memory Ledger And Repo Bootstrap

**Status:** planning
**Repo:** agents-remember-md
**Type:** Skill
**Created:** 2026-05-08T12:21

---

## Objective

Define one canonical `memory.md` format, implement the parser and writer for it, and use that core to bootstrap a per-repo shared memory repository with a valid first ledger state.

---

## Design Philosophy

This task treats `memory.md` as the semantic bridge between code history and shared memory history. It is not just a reporting artifact or bootstrap checklist; it is the mechanism that tells later tasks which memory state is actually compatible with which code state.

The philosophy is to make that bridge explicit, machine-readable, and stable enough to support both humans and workflow code. The ledger needs to be easy to parse, easy to diff, and strict enough that later worktree and cross-repo tasks can rely on it for rewindability, compatibility checks, and branch-aware context alignment. That is why this task freezes one canonical format before any broader consumer logic is allowed to depend on it.

Bootstrapping the shared memory repo in the same task is intentional. The first valid ledger state is not an afterthought; it is the first proof that the format, parser, and repository layout actually form a usable shared-memory substrate for the later worktree model.

---

## Requirements

- Adopt one canonical `memory.md` format: a fenced JSON metadata block followed by a newest-first two-column markdown table, and update conflicting spec examples or fixtures to match.
- Implement `memory.md` parsing, validation, writing, newest-first enforcement, and exact code-commit lookup using only the Python standard library.
- Enforce the top-row/header invariants required by the worktree and cross-repo designs.
- Bootstrap a shared memory repo under `ar-management/memory-repos/ar-<repo-name>/` with `onboarding/`, `docs/`, `system/`, and `memory.md`.
- Create the initial shared-memory bootstrap flow so the first code commit, memory-content commit, and ledger entry are internally consistent.

---

## Implementation Steps

### S1 — Settle The Canonical Ledger Format

- [ ] Freeze the canonical `memory.md` envelope before implementation starts.
  - [ ] Use one fenced `json ar-memory-ledger` block as the authoritative machine-readable metadata.
  - [ ] Use the first markdown table after that block as the authoritative newest-first `Code commit | Memory commit` mapping.
  - [ ] Update any conflicting spec examples or fixtures to match.
- [ ] Freeze the invariants the parser must enforce.
  - [ ] `sort_order` or equivalent must be newest-first.
  - [ ] The top ledger row must match the last-verified metadata fields.

### S2 — Implement The Ledger Core

- [ ] Add a parser, validator, and writer for `memory.md`.
  - [ ] Parse metadata and the two-column commit table.
  - [ ] Validate newest-first ordering and top-row consistency.
  - [ ] Expose prepend-row, exact-code-commit lookup, and branch-metadata accessors.
- [ ] Add focused tests or fixtures for valid and invalid ledgers.
  - [ ] Include branch mismatch, malformed metadata, and bad top-row cases.
  - [ ] Keep the parser reusable for both worktree and cross-repo consumers.

### S3 — Bootstrap Shared Memory Repositories

- [ ] Add the shared memory-repo bootstrap flow after the ledger core is stable.
  - [ ] Create `onboarding/`, `docs/`, `system/`, and `memory.md` under `ar-management/memory-repos/ar-<repo-name>/`.
  - [ ] Keep `system/settings.md` and `system/settings.json` as the settings entry points inside the bootstrapped memory repo.
  - [ ] Create the initial memory-content commit and then the ledger commit that maps the selected code commit.
- [ ] Validate the bootstrap output with the new parser and targeted Git checks.
  - [ ] Confirm the first ledger row and metadata agree.
  - [ ] Confirm later consumers can read the bootstrapped repo without special-case logic.

---

## Proposed Code Examples

### E1 — Canonical Ledger Shape

Distinct change covered: Freeze one canonical `memory.md` structure before implementing parser logic.

Why this example is included: The two design sheets currently disagree on the metadata envelope, so this is the first decision the implementation must settle.

````markdown
# Memory Branch Ledger

```json ar-memory-ledger
{
  "schema": "ar-memory-branch-ledger/v1",
  "repoName": "device-management",
  "trackedCodeBranch": "main",
  "memoryBranch": "main",
  "baseCodeCommit": "8d21c91",
  "baseMemoryCommit": "a71f002",
  "lastVerifiedCodeCommit": "f4c8b12",
  "lastMemoryContentCommit": "b9e44aa",
  "sortOrder": "newest-first"
}
```

| Code commit | Memory commit |
| ----------- | ------------- |
| f4c8b12     | b9e44aa       |
| c31a760     | d08219f       |
````

### E2 — Ledger API Shape

Distinct change covered: Expose a reusable API for other tasks to consume.

Why this example is included: The worktree manager and cross-repo resolver should not each reimplement ledger parsing.

```python
@dataclass
class MemoryLedger:
    repo_name: str
    tracked_code_branch: str
    memory_branch: str
    last_verified_code_commit: str
    last_memory_content_commit: str
    rows: list[tuple[str, str]]

def prepend_mapping(ledger: MemoryLedger, code_commit: str, memory_commit: str) -> MemoryLedger:
    ...
```

---

## Decision Log

| Date-Time        | Decision                                                                            | Rationale                                                                                                                                                             |
| ---------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T12:21 | Treat canonical `memory.md` format selection as part of this task.                  | The specs disagree today, and the parser cannot be implemented safely until one format wins.                                                                          |
| 2026-05-08T12:21 | Pair shared memory-repo bootstrap with the ledger core.                             | The bootstrap flow needs a stable writer and validator for the first valid ledger state.                                                                              |
| 2026-05-08T12:44 | Keep bootstrapped shared memory repos on the existing `system/` settings structure. | Shared memory repos are still sidecar repos physically, so there is no need to invent a separate top-level `settings/` tree just for them.                            |
| 2026-05-08T13:03 | Use fenced JSON metadata plus a markdown table for `memory.md`.                     | This keeps machine-readable data in strict JSON, remains easy to parse with Python's standard library, and preserves human-readable Git diffs for the commit mapping. |
| 2026-05-08T14:50 | Add explicit prose for the ledger-and-bootstrap design philosophy.                  | The task file should preserve that `memory.md` is the semantic alignment mechanism for later rewind and compatibility workflows, not just a format choice.            |

---

## Open Questions

- None. The canonical `memory.md` format is one fenced `json ar-memory-ledger` block plus one newest-first two-column markdown table.

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-worktree-memory-final-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/roadmap/agents-remember-cross-repo-mode-design-spec.md`
- `/home/mohamedreadone/Projects/agents-remember-md/skills/U-01-core-skills/C-08-ar-management-resolver/scripts/ar_management_resolver.py`

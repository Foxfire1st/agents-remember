---
name: c-12-closeout
description: "Close out approved Agents Remember edits by preserving explicit commit approval, missing-onboarding checks, external-memory onboarding refresh, memory quality, ledger alignment, and no automatic push for both direct checkout and worktree-backed tasks."
---

# C-12 Closeout

Use this skill when approved Agents Remember edits need to be committed and the
repository uses external memory.

C-12 owns closeout sequencing for both direct current-checkout edits and
worktree-backed tasks. Use `C-09-git-worktree-manager` for worktree start,
attach, status, integration, and cleanup; use this skill for the closeout gate
and code-memory-ledger commit order.

## MCP Tools

Use the matching MCP closeout tools for the current work shape:

```text
direct_closeout_preview(repo_id="<repo-id>", task_name="<task>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
direct_closeout_apply(repo_id="<repo-id>", task_name="<task>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_closeout_preview(contract_path="<contract.md>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_closeout_apply(contract_path="<contract.md>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
```

Use direct closeout for small approved edits made in the current checkout or
memory-only polish that does not need isolated branches, replay bookkeeping,
integration, or cleanup.

Use worktree closeout when C-09 created or attached a task contract. Worktree
closeout records closeout state in the contract; C-09 owns later integration
and cleanup.

## Approval Gate

Closeout is explicitly human-gated. Agents must request the matching preview
tool first, relay the proposed code, memory, and ledger commit messages to the
developer, and ask for explicit commit approval.

Real closeout uses the matching apply tool with an `intent_note`. The note
records the developer's explicit commit approval. Agents must not treat
implementation approval, a previous "looks good", or their own judgment as
commit approval.

## Preconditions

C-12 resolves or consumes the current C-08 context, requires external memory
mode, and requires the code checkout/worktree and memory repo/worktree to be on
the same selected branch.

Ledger compatibility is based on code-to-memory commit mappings, not branch
metadata.

Before committing code, run the package-local missing-onboarding check against
current additions:

```text
python -m agents_remember.memory_quality.integrity.check_missing_onboarding --code-repository-root "<code-root>" --onboarding-root "<resolved-onboarding-root>"
```

The check only evaluates files that are new in the current checkout or
worktree, not the whole historical repository. If it reports missing
onboarding, create those sidecars through C-05 before committing code. After
the code commit exists, refresh the new sidecars' verification metadata to that
commit during the normal post-code-commit memory refresh.

## External-Memory Order

External-memory closeout order is:

1. run `check_missing_onboarding` against current additions
2. create missing onboarding for newly added eligible source files before committing code
3. commit code changes and capture `C2` plus its commit date
4. run C-02 memory quality control's drift check against `C2` to produce the full memory update worklist
5. refresh affected onboarding `lastVerifiedCommitHash` and `lastVerifiedCommitDate` to `C2`
6. refresh affected repo entity catalog `git-blob-set-v1` fingerprints against `C2` when changed source paths are listed as entity evidence
7. refresh affected route overview `lastVerifiedCommitHash` / `lastVerifiedCommitDate` metadata to `C2`
8. refresh generated route indexes so `overview.index.json` matches the updated onboarding tree
9. run MCP `memory_quality_check`; fix reported memory findings before continuing
10. commit memory-content changes and capture `M2`
11. prepend `C2 | M2` to `memory.md`
12. commit the ledger update as `L2`
13. for worktree-backed closeout, update the task contract closeout state

Entity fingerprints must be refreshed after the code commit and before the
memory-content commit because `git-blob-set-v1` uses `HEAD:<path>` Git blobs.
Reviewing the entity prose can happen before closeout, but the final
fingerprint values must be written in the code-commit-to-memory-commit window.

Route overview metadata and generated route indexes are memory-content changes.
They must be refreshed before `memory_quality_check`, and `memory_quality_check`
must be clean before creating the memory content commit.

Push behavior is not automatic.

## Failure Conditions

Closeout fails without mutation when required onboarding is missing,
verification metadata is missing, external memory is not resolved, the code and
memory checkouts are on different selected branches, or no code or memory
changes exist.

Worktree closeout also fails when the recorded code or external-memory source
branch moved since task start.

Missing onboarding is the expected hard failure when the implementation/update
pass did not produce a required onboarding file. The next step is to run C-05
for that source file, then rerun the closeout preview.

## Boundaries

1. C-12 owns closeout approval and code-memory-ledger commit sequencing.
2. C-12 does not create worktrees, integrate worktrees, or clean up worktrees.
3. C-12 does not initialize memory roots; use `C-00-initialize-memory-repo`.
4. C-12 must not commit without explicit commit approval after a closeout preview.
5. C-12 must not create a memory content commit whose affected onboarding metadata still points at pre-closeout code.
6. C-12 must not create a memory content commit before route overview metadata, generated route indexes, and `memory_quality_check` are clean for the new code commit.
7. C-12 must not push automatically.

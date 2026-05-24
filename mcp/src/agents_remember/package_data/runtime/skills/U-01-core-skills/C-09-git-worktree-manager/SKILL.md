---
name: c-09-git-worktree-manager
description: "Create, attach to, report on, and close out Agents Remember worktree-backed tasks while preserving human approval gates and external-memory ledger alignment."
---

# C-09 Git Worktree Manager

Use this skill when a task should run through an explicit code/memory worktree wrapper or when an approved direct checkout edit needs the same code-memory-ledger closeout discipline.

C-09 wraps the existing chat, light-task, heavy-task, or external workflow. It owns Git worktree state, task contracts, direct checkout closeout, external-memory compatibility checks, and approved closeout sequencing. It does not replace the workflow that performs the actual implementation.

## MCP Tools

Use the Agents Remember MCP worktree tools as the normal installed runtime
entry point:

```text
worktree_start(repo_id="<repo-id>", task_name="<task>", worktree_name="<name>", workflow_kind="light-task", dry_run=false)
worktree_attach(repo_id="<repo-id>", task_name="<task>")
worktree_status(repo_id="<repo-id>", task_name="<task>")
worktree_closeout_preview(contract_path="<contract.md>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_closeout_apply(contract_path="<contract.md>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
direct_closeout_preview(repo_id="<repo-id>", task_name="<task>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
direct_closeout_apply(repo_id="<repo-id>", task_name="<task>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_integrate(contract_path="<contract.md>", strategy="ff-only", dry_run=false)
worktree_cleanup(contract_path="<contract.md>", dry_run=false)
```

Callers identify repositories by configured MCP `repo_id`. The MCP server owns
workspace root, coordination root, provider setup settings, and path containment.
The skill tree is instruction-only; installed and development workflows use the
MCP/package route.

## Pre-Worktree Intake

C-09 starts after the normal task intake and onboarding gate, not before them.

The intended order is:

1. run the C-08 resolver for the target repository
2. run C-02 memory quality control's task-start drift check and follow the existing AGENTS Gate 3/4 choice point
3. when onboarding is refreshed, commit the memory content and ledger before starting any worktree
4. decide whether the work is chat-only, W-02 light task, heavy task, or external workflow
5. choose or review the task slug and workflow variables
6. create the durable task wrapper when one is needed
7. request MCP `worktree_start` only after the task identity is stable and external memory is clean

For W-02 light tasks, the durable artifact shape is `<task-root>/<task-slug>/task.md`. C-09 then places `contract.md` beside that `task.md` when worktrees are created.

## Start / Attach / Status

`worktree_start` resolves C-08 context, creates or loads `contract.md`, prepares the code worktree first, and then prepares external-memory state when enabled. External-memory start refuses to continue when the source memory repo has uncommitted changes; refreshed onboarding and the ledger must be committed first so the new worktree starts from an auditable memory baseline.

When external memory is enabled, C-09 validates the memory repo and `memory.md` ledger before allowing memory to be used as trusted context. Missing external memory is not a C-09 bootstrap path; run `C-00-initialize-memory-repo` first. If no compatible memory state exists, C-09 stops and reports the allowed human choices:

1. `reconciliation`
2. `disabled-memory`
3. `custom`

`worktree_attach` and `worktree_status` read the existing contract and report recoverable state without mutating Git. `worktree_status` includes a lifecycle phase, dirty worktree flags, a summary, and typed next hints such as `nextOperation`, `nextTool`, and `nextArgs`.

## Closeout

Closeout is explicitly human-gated. Implementation approval is not commit approval. Agents must first request `worktree_closeout_preview` to prepare a non-mutating commit preview, relay the proposed code, memory, and ledger commit messages to the developer, and ask for explicit commit approval. The preview reports the closeout order plus the affected onboarding metadata and entity fingerprint refresh plan.

Real closeout creates commits and therefore uses `worktree_closeout_apply` with an `intent_note`. The note records the developer's explicit commit approval in the contract. Agents must not self-grant this approval from their own judgment or from earlier implementation approval.

After the code commit is created, use `C-02-memory-quality-control` to run the
post-code-commit drift check and get the concrete onboarding and
entity-fingerprint update worklist. Refresh onboarding and entity fingerprints
from that worklist, then run MCP `memory_quality_check` before the
memory-content commit. The memory commit is allowed only when that full
validation passes.

Before the code commit, run the package-local missing-onboarding check against
the current code worktree additions:

```text
python -m agents_remember.memory_quality.integrity.check_missing_onboarding --code-repository-root "<code-root>" --onboarding-root "<resolved-onboarding-root>"
```

The check only evaluates files that are new in the current worktree, not the
whole historical repository. If it reports missing onboarding, create those
sidecars through C-05 before committing code. After the code commit exists,
refresh the new sidecars' verification metadata to that commit during the normal
post-code-commit memory refresh.

Closeout stops if the recorded code or external-memory source branch moved since task start.

External-memory closeout order is:

1. run `check_missing_onboarding` against current worktree additions
2. create missing onboarding for newly added eligible source files before committing code
3. commit code worktree changes and capture `C2` plus its commit date
4. run C-02 memory quality control's drift check against `C2` to produce the full memory update worklist
5. refresh affected onboarding `lastVerifiedCommitHash` and `lastVerifiedCommitDate` to `C2`
6. refresh affected repo entity catalog `git-blob-set-v1` fingerprints against `C2` when changed source paths are listed as entity evidence
7. run MCP `memory_quality_check`; fix reported memory findings before continuing
8. commit memory-content changes and capture `M2`
9. prepend `C2 | M2` to `memory.md`
10. commit the ledger update as `L2`
11. update the task contract closeout state

Entity fingerprints must be refreshed after the code commit and before the memory-content commit because `git-blob-set-v1` uses `HEAD:<path>` Git blobs. Reviewing the entity prose can happen before closeout, but the final fingerprint values must be written in the code-commit-to-memory-commit window.

Push behavior is not automatic.

## Direct Closeout

Use `direct_closeout_preview` / `direct_closeout_apply` only for small approved edits made in the current source checkout, or for memory-only polish that does not need isolated worktrees or durable task artifacts. If the work is parallel, long-running, conflict-prone, review-heavy, or needs replay/integration bookkeeping, use the normal C-09 worktree flow instead.

Direct closeout is still explicitly human-gated. Agents must request `direct_closeout_preview` first, relay the proposed code, memory, and ledger commit messages to the developer, and ask for explicit commit approval. Real direct closeout uses `direct_closeout_apply` with an `intent_note`.

After the code commit is created, use `C-02-memory-quality-control` to run the
post-code-commit drift check and get the concrete onboarding and
entity-fingerprint update worklist. Refresh onboarding and entity fingerprints
from that worklist, then run MCP `memory_quality_check` before the
memory-content commit. The memory commit is allowed only when that full
validation passes.

Before committing code in direct closeout, run the same package-local
`check_missing_onboarding` pass against current-checkout additions and create
missing sidecars through C-05. This prevents newly added source files from
escaping the drift report's gradual-adoption boundary.

Direct closeout resolves the current C-08 context, requires external memory mode, and requires the code checkout and memory repo to be on the same selected branch. Ledger compatibility is based on code-to-memory commit mappings, not branch metadata.

External-memory direct closeout order is:

1. run `check_missing_onboarding` against current-checkout additions
2. create missing onboarding for newly added eligible source files before committing code
3. commit code checkout changes and capture `C2` plus its commit date
4. run C-02 memory quality control's drift check against `C2` to produce the full memory update worklist
5. refresh affected onboarding `lastVerifiedCommitHash` and `lastVerifiedCommitDate` to `C2`
6. refresh affected repo entity catalog `git-blob-set-v1` fingerprints against `C2` when changed source paths are listed as entity evidence
7. run MCP `memory_quality_check`; fix reported memory findings before continuing
8. commit memory-content changes and capture `M2`
9. prepend `C2 | M2` to `memory.md`
10. commit the ledger update as `L2`

Direct closeout fails without mutation when required onboarding is missing, verification metadata is missing, external memory is not resolved, the code and memory checkouts are on different selected branches, or no code or memory changes exist. Missing onboarding is the expected hard failure when the implementation/update pass somehow did not produce a required onboarding file; the next step is to run C-05 for that source file, then rerun the direct closeout preview.

## Integration

Integration is explicitly human-gated and runs only after closeout completed. It lands the closed task branches back onto the recorded source branches and records the landed commits separately from the closeout commits.

Strategies:

1. `ff-only`: require current code and memory source branches to be ancestors of the closeout commits, then fast-forward both source branches.
2. `replay`: when source branches moved because parallel work landed first, replay the code task commit onto current code source, replay only the memory content commit onto current memory source, regenerate `memory.md` for the final landed code and memory content commits, then fast-forward both source branches.

Conflict rule: if code replay or memory-content replay conflicts, stop before moving source branches. The agent must discuss the resolution with the developer and decide what is true before continuing. Do not replay an old ledger commit over current memory main; always regenerate the ledger row after memory content has been mediated.

After successful integration, ask whether to remove the code and memory worktrees plus merged local task branches. Cleanup is not automatic.

## Cleanup

Cleanup is explicitly human-gated and runs only after integration completed. It removes the recorded code and memory worktrees, deletes local task branches only when Git can prove they are merged, removes empty worktree group folders when safe, and records `cleanup: completed` in the contract.

Cleanup is idempotent. If the worktrees or merged branches are already gone, it reports the already-clean state instead of failing. If Git refuses to delete an unmerged branch, cleanup leaves that branch in place and reports it for developer review.

## Boundaries

1. C-09 may create or reuse worktrees and task contracts.
2. C-09 does not initialize memory roots; use `C-00-initialize-memory-repo` before starting external-memory worktrees.
3. C-09 may directly close out approved current-checkout edits when a worktree wrapper would add ceremony without isolation value.
4. C-09 must not use divergent memory as semi-trusted reference context.
5. C-09 must not commit without explicit commit approval after a closeout preview.
6. C-09 external-memory closeout must not create a memory content commit whose affected onboarding metadata still points at pre-closeout code.
7. C-09 must not move source branches during integration until replay/preflight has produced fast-forwardable code and memory commits and explicit integration approval exists.
8. C-09 must not clean up without explicit cleanup approval.
9. C-08 remains the facts-only resolver; C-09 owns worktree and lifecycle mutation.

---
name: c-10-adopt-memory-baseline
description: "Adopt existing external-memory onboarding as the first ledgered memory baseline after resolving context, checking drift, and requiring explicit acceptance when onboarding is not proven current."
---

# c-10-adopt-memory-baseline Adopt Memory Baseline

Use this skill when an external memory repo already contains onboarding content and the developer wants to create the initial `memory.md` ledger from that content.

This skill does not decide that stale onboarding is true. It makes the trust boundary explicit: the `c-02-memory-quality-control` skill's drift is checked first, and actionable drift blocks adoption unless the developer explicitly accepts the current onboarding as the baseline.

## MCP Tools

Use the Agents Remember MCP memory baseline tools as the normal installed
runtime entry point:

> **Preview first.** `memory_baseline_adopt` now **applies by default**. Call it
> once with `dry_run=true` to preview, confirm, then run the real apply (omit
> `dry_run`).

```text
memory_baseline_status(repo_id="<repo-id>")
memory_baseline_adopt(repo_id="<repo-id>", accept_drift=false, dry_run=true)   # preview
memory_baseline_adopt(repo_id="<repo-id>", accept_drift=true)                  # apply
```

Use `memory_baseline_status` first. Use `memory_baseline_adopt` only after the
developer approves the baseline decision. The skill tree is instruction-only;
installed and development workflows use the MCP/package route.

## Workflow

1. Resolve the code repository with the `c-08-ar-coordination-context-resolver` skill and confirm external topology.
2. Run the `c-02-memory-quality-control` skill's drift classification against the resolved onboarding root; its reusable report is written under the `c-08-ar-coordination-context-resolver` skill's resolved temp root.
3. Inspect the external memory repo for an existing `memory.md`.
4. If a ledger already exists, report it and stop.
5. If drift has actionable findings, stop unless `accept_drift=true` is part of the approved `memory_baseline_adopt` request.
6. Bootstrap the memory repo through the `c-09-git-worktree-manager` skill so the existing onboarding/system/docs content becomes the memory content commit and `memory.md` maps current code HEAD to that memory commit.

## Output States

- `ready`: no ledger exists and drift is clean enough to adopt.
- `blocked-drift`: drift has actionable findings and `accept_drift=true` was not supplied.
- `already-ledgered`: `memory.md` already exists.
- `adopted`: the baseline ledger was created.
- `would-adopt`: dry run would create the baseline.

## Boundaries

1. The `c-10-adopt-memory-baseline` skill may create the initial memory repo Git history and `memory.md` through the `c-09-git-worktree-manager` skill.
2. The `c-10-adopt-memory-baseline` skill must not refresh onboarding content itself; use the `c-05-create-or-update-onboarding-files` skill for that.
3. The `c-10-adopt-memory-baseline` skill must not overwrite an existing `memory.md`.
4. `accept_drift=true` means the developer is asserting the current onboarding content is factual enough to become the baseline despite the `c-02-memory-quality-control` skill's warnings.

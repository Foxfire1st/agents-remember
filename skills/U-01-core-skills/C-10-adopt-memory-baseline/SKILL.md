---
name: c-10-adopt-memory-baseline
description: "Adopt existing shared-memory onboarding as the first ledgered memory baseline after resolving context, checking drift, and requiring explicit acceptance when onboarding is not proven current."
---

# C-10 Adopt Memory Baseline

Use this skill when a shared memory repo already contains onboarding content and the developer wants to create the initial `memory.md` ledger from that content.

This skill does not decide that stale onboarding is true. It makes the trust boundary explicit: C-02 drift is checked first, and actionable drift blocks adoption unless the developer explicitly accepts the current onboarding as the baseline.

## Command

```bash
<this-skill-dir>/scripts/adopt_memory_baseline.py status --code-repository-name <code-repository-name> --workspace-root <workspace>
<this-skill-dir>/scripts/adopt_memory_baseline.py adopt --code-repository-name <code-repository-name> --workspace-root <workspace> --accept-drift
```

Callers that already know the checkout path may pass `--code-repository-root <code-repository-root>` instead of relying on `--workspace-root` lookup.

Use `status` first. Use `adopt` only after the developer approves the baseline decision.

## Workflow

1. Resolve the code repository with C-08 and confirm shared topology.
2. Run C-02 drift classification against the resolved onboarding root; its reusable report is written under C-08's resolved temp root unless `--report` is supplied.
3. Inspect the shared memory repo for an existing `memory.md`.
4. If a ledger already exists, report it and stop.
5. If drift has actionable findings, stop unless `--accept-drift` is present.
6. Bootstrap the memory repo through C-09 so the existing onboarding/system/docs content becomes the memory content commit and `memory.md` maps current code HEAD to that memory commit.

## Output States

- `ready`: no ledger exists and drift is clean enough to adopt.
- `blocked-drift`: drift has actionable findings and `--accept-drift` was not supplied.
- `already-ledgered`: `memory.md` already exists.
- `adopted`: the baseline ledger was created.
- `would-adopt`: dry run would create the baseline.

## Boundaries

1. C-10 may create the initial memory repo Git history and `memory.md` through C-09.
2. C-10 must not refresh onboarding content itself; use C-05 for that.
3. C-10 must not overwrite an existing `memory.md`.
4. `--accept-drift` means the developer is asserting the current onboarding content is factual enough to become the baseline despite C-02 warnings.

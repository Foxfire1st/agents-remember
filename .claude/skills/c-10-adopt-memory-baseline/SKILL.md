---
name: c-10-adopt-memory-baseline
description: "Adopt existing external-memory onboarding as the first Git-attributed memory baseline after resolving context, checking drift, and requiring explicit acceptance when onboarding is not proven current."
---

# c-10-adopt-memory-baseline Adopt Memory Baseline

Use this skill when an external memory repo already contains onboarding content and the developer wants to adopt that content as its first Git-attributed baseline and compute the consumer ledger cache.

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
3. Inspect reachable memory commits for existing `Code-Commit:` attribution.
4. If attributed memory already exists, report `already-adopted` and stop; cache presence does not determine adoption.
5. If drift has actionable findings, stop unless `accept_drift=true` is part of the approved `memory_baseline_adopt` request.
6. Adopt through `memory_baseline_adopt` on the configured memory default branch. Existing onboarding/system/docs become one memory-content commit with a `Code-Commit:` trailer; `memory.md` is computed afterward without another commit.

## Output States

- `ready`: no attributed memory baseline exists and drift is clean enough to adopt.
- `blocked-drift`: drift has actionable findings and `accept_drift=true` was not supplied.
- `unavailable`: a current memory HEAD exists but its history cannot be read; adoption waits for readable Git history.
- `already-adopted`: reachable memory history already carries code attribution.
- `adopted`: the attributed memory-content baseline was committed and the cache refresh was attempted.
- `would-adopt`: dry run would create the baseline.

## Boundaries

1. Baseline adoption commits real memory content and computes the cache; it never commits the cache.
2. The `c-10-adopt-memory-baseline` skill must not refresh onboarding content itself; use the `c-05-create-or-update-onboarding-files` skill for that.
3. A missing, stale, or malformed `memory.md` is a cache state, not evidence that a baseline exists or is absent.
4. `accept_drift=true` means the developer is asserting the current onboarding content is factual enough to become the baseline despite the `c-02-memory-quality-control` skill's warnings.

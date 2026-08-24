---
name: c-02-memory-quality-control
description: "Control Agents Remember memory quality with task-start drift checks, pre-code-commit missing-onboarding checks, and closeout memory quality gates."
---

# c-02-memory-quality-control Memory Quality Control

Use this skill whenever a workflow needs to decide whether the memory layer is
safe to rely on, whether newly added source files have onboarding pairs before a
code commit, or whether memory is clean enough to commit during closeout.

The skill owns the memory quality control procedure. Drift detection remains the
task-start trust baseline, but it is now one integrity check inside the broader
`memory_quality` domain.

## Inputs

This skill operates on one repository at a time and starts from the context
resolved by `c-08-ar-coordination-context-resolver` or MCP `resolve_context`.

## Primary Outputs

1. task-start trust guidance from repo-wide drift classification
2. a concrete onboarding maintenance worklist for `c-05-create-or-update-onboarding-files`
3. a pre-code-commit missing-onboarding report for newly added source files
4. a closeout memory quality report covering integrity and style checks
5. explicit next actions when memory quality is not clean
6. one atomically overwritten curator checklist inside a leaf's worktree enclosure

## Quality Control Phases

| Phase | Check | Purpose |
| --- | --- | --- |
| Task start | `drift_check` | Decide whether existing onboarding is trustworthy enough to plan against. |
| Curator intake and repair loop | contract-scoped `memory_quality_check` | Replace one enclosure-local checklist combining quality, missing-onboarding, drift candidates, and route-index preview. |
| Before code commit | `check_missing_onboarding` | Catch new source files in the current worktree that need sidecars before the code commit lands. |
| Before memory commit | `memory_quality_check` | Validate refreshed memory after code commit and onboarding updates, including drift integrity and memory style. |
| Targeted style repair | `history_order_fix.py` | Fix update-history ordering only after the report identifies that mechanical issue. |

Style checks are closeout quality control. They should not block the beginning
of normal implementation work.

## Boundaries

1. This skill reports and routes memory quality work; it does not rewrite
   onboarding prose itself.
2. It does not replace deep Research.
3. It does not decide requirement or architecture direction.
4. It should qualify stale onboarding rather than silently treating it as
   trustworthy.
5. It must not turn the default repo-wide drift gate into a whole-repository
   adoption scan for files that never had onboarding.
6. It must not treat implementation approval as commit approval; closeout
   commits remain owned by the `c-09-git-worktree-manager` skill approval gates.

## Procedure

### 1. Resolve Context

Use `c-08-ar-coordination-context-resolver` or MCP `resolve_context` to confirm
the target repository's active memory and coordination context.

```text
resolve_context(repo_id="<repo-id>")
```

The MCP server owns topology detection, coordination-root resolution, settings
parsing, storage semantics, and `pathRules` parsing. The `c-02-memory-quality-control` skill consumes the resolved
context and applies memory quality control.

### 2. Run Task-Start Drift Control

At task start, request the MCP drift tool for repo-wide checks instead of
rewriting shell loops:

```text
drift_check(repo_id="<repo-id>", detail_limit=50)
```

Inside a worktree-backed leaf, add the leaf's enclosure contract path so the check reads that
leaf's memory worktree rather than the official memory repo:

```text
drift_check(repo_id="<repo-id>", detail_limit=50, contract_path="<enclosure-contract-path>")
```

By default the MCP drift tool writes the Markdown report to
`<coordination_root>/temp/drift-reports/<repo-name>/<repo-name>_<branch-name>_drift-report.md`.
That keeps temporary drift artifacts out of task contract folders while still
keeping them under the local coordination root.

If actionable drift exists, first classify the affected findings by source
worktree state. Drifted onboarding whose corresponding source file is not dirty
is an onboarding update candidate. Drifted onboarding whose corresponding
source file is dirty is active work-in-progress and should be left alone unless
the developer explicitly takes ownership of that active work.

Do not plan against stale onboarding as trusted current state. Do not silently
drop or ignore onboarding after drift detection. Report update candidates and
dirty-source findings separately, then ask the developer whether to refresh the
update candidates before proceeding. If no actionable drift exists, the existing
memory is clean for task-start planning.

Default repo-wide drift control deliberately does not classify every source file
without onboarding as missing. That gradual-adoption boundary prevents old,
undocumented historical files from flooding the report.

### 3. Understand Drift Classifications

Primary drift detection supports sidecar Markdown onboarding under the resolved
onboarding root, whether that root is repo-local internal memory or external
memory. It classifies file-level onboarding, root repo overviews, route-local
overviews, and repo entity catalogs when those artifacts carry supported
`doc_type` metadata. It may also classify inline onboarding blocks when storage
settings resolve a source path to `inline`.

For file-level sidecars, the `c-02-memory-quality-control` skill compares the source file against the recorded
`lastVerifiedCommitHash`, then checks that same source path for staged or
unstaged local changes.

For repo and route-local overviews, the `c-02-memory-quality-control` skill compares the recorded `sourceRoute`
against the recorded commit, then checks that same route for staged or
unstaged local changes.

For repo entity catalogs, the `c-02-memory-quality-control` skill reconciles `## Entity Inventory` headings against
`## Entity Fingerprints` rows. Missing fingerprint tables, inventory entries
without matching rows, orphaned fingerprint rows, unsupported algorithms,
missing fingerprints, missing evidence paths, or fingerprint mismatches are
actionable maintenance.

Supported classifications are:

1. up to date
2. drifted
3. missing verification
4. missing
5. orphaned
6. disabled
7. unsupported

### 4. Hand Off Drift Maintenance

If actionable files exist, hand only the approved update candidates to
`c-05-create-or-update-onboarding-files`. Dirty-source drift findings remain
active work-in-progress and are not maintenance targets unless the developer
explicitly says to take them over.

The handoff should identify:

1. which onboarding files have clean source files and are update candidates
2. which drifted onboarding files have dirty source files and must be left alone
3. which files are orphaned and may need deletion
4. which overview source routes changed
5. which entity fingerprints changed
6. which inventory entries are missing fingerprint rows
7. which fingerprint rows are orphaned
8. which evidence paths caused the stale signal
9. which stale onboarding can still be used directionally until maintenance
   finishes

Treat the drift report as a maintenance artifact, not as a long-lived research
handoff.

### 5. Run Pre-Code-Commit Missing-Onboarding Control

Before creating a code commit, run the package-local missing-onboarding check
when the task added, copied, renamed, or left untracked source files:

```text
python -m agents_remember.memory_quality.integrity.check_missing_onboarding --code-repository-root "<code-root>" --onboarding-root "<resolved-onboarding-root>"
```

This pass is intentionally different from task-start drift. It checks only the
current worktree additions so the files created by the current developer/agent
cannot slip past the gradual-adoption boundary.

If it reports missing sidecar or inline onboarding, create the reported
onboarding through the `c-05-create-or-update-onboarding-files` skill before the code commit. After the code commit lands,
refresh those onboarding files to the real code commit hash and date during the
normal memory refresh.

### 6. Run The Curator Checklist Loop

The curator begins its pass with one full leaf-scoped call, before it writes onboarding:

```text
memory_quality_check(request={"mode":"sync", "repo_id":"<repo-id>", "contract_path":"<enclosure-contract-path>"})
```

That full contract-scoped call atomically replaces exactly one operational artifact:

```text
<worktree-enclosure>/reports/curator-memory-quality.md
```

The report combines every repairable memory-quality finding, current-worktree missing-onboarding
row, stale route index, source-change reconciliation candidate, closeout-owned real-commit
residual, and report-only noteworthy row. It returns the same path plus component counts. The
curator repairs the zeroable rows, applies `route_index_refresh` only when stale indexes are named,
and reruns the same full call until `curatorActionableCount=0` and
`checklistStatus=ready-for-closeout`. Each run replaces the predecessor; do not copy it to a
timestamped name. Dirty-source drift and truthful real-commit provenance remain visible but do not
create an impossible pre-commit gate; the curator dispositions those source-change candidates in
the durable coherence report.

The `reports/` directory is outside both Git worktrees. Normal cleanup and abandon remove it with
the enclosure, so the checklist cannot enter code, memory, or ledger commits. A subset call with
`checks=[...]` and an unscoped official-repository call do not create this checklist.

### 7. Run Closeout Memory Quality Control

After the code commit exists and the `c-05-create-or-update-onboarding-files` skill has refreshed the affected onboarding and
entity fingerprints to that code commit, run the MCP memory quality tool before
the memory content commit:

```text
memory_quality_check(request={"mode":"sync", "repo_id":"<repo-id>"})
```

Inside a leaf, scope it to that leaf's memory worktree:

```text
memory_quality_check(request={"mode":"sync", "repo_id":"<repo-id>", "contract_path":"<enclosure-contract-path>"})
```

This is the full closeout gate. It combines integrity checks such as drift
summary status with style checks such as update-history ordering. The seat that
WROTE the onboarding can run this form on its own change-set before handing it
back, instead of the closing seat meeting the findings at the commit gate; the
commit gate stays the hard gate either way. Confirm `onboardingRoot` in the
response names the tree you meant to check.

If the report is clean, the memory content can be committed through the selected
workflow's closeout procedure. If the report has findings, fix the reported
memory issues and rerun the check before committing memory.

### 8. Use Targeted Style Fixers Only After Findings

When `memory_quality_check` reports update-history ordering findings, use the
dedicated fixer rather than hand-writing one-off scripts:

```text
python -m agents_remember.memory_quality.style.update_history.history_order_fix --onboarding-root "<resolved-onboarding-root>"
```

Run `memory_quality_check` again after the fixer. If a finding is not
mechanically fixable, update the affected onboarding by hand and rerun the
check.

## Rules

1. Task-start memory quality control begins with `c-08-ar-coordination-context-resolver` skill context and the `drift_check` MCP tool.
2. Closeout memory quality control uses `memory_quality_check`, not just
   repo-wide drift.
3. New files created by the current task are checked before code commit with
   `check_missing_onboarding`.
4. The `c-02-memory-quality-control` skill hands maintenance work to the `c-05-create-or-update-onboarding-files` skill instead of writing onboarding content.
5. Stale onboarding may remain directional evidence until refreshed or
   disproven, but that trust level must be made explicit.
6. Missing verification metadata is itself actionable drift.
7. Orphaned onboarding should be surfaced clearly rather than left to
   accumulate silently.
8. Generated quality reports belong under the resolved coordination/temp root,
   not inside durable memory unless the developer explicitly asks.
9. A curator's full contract-scoped quality run is the one exception to the shared temp-report
   location: it replaces `<worktree-enclosure>/reports/curator-memory-quality.md`, and worktree
   cleanup garbage-collects that reserved directory.

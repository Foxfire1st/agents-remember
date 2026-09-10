---
name: c-02-memory-quality-control
description: "Provide on-demand Agents Remember memory-quality diagnostics and scoped onboarding checks without making closeout or integration a quality gate."
---

# c-02-memory-quality-control Memory Quality Control

Use this skill when the developer explicitly requests memory-quality evidence or
when an approved workflow asks for a scoped diagnostic. Closeout and integration
consume prepared code/memory/ledger Git inputs and do not invoke this skill as a
quality prerequisite.

The skill owns the diagnostic procedure. Drift detection can qualify task-start
context, and curators can run scoped onboarding checks before handoff. Full
memory quality remains an explicit developer-requested operation.

## Inputs

This skill operates on one repository at a time and starts from the context
resolved by `c-08-ar-coordination-context-resolver` or MCP `resolve_context`.

## Primary Outputs

1. task-start trust guidance from repo-wide drift classification
2. a concrete onboarding maintenance worklist for `c-05-create-or-update-onboarding-files`
3. a scoped missing-onboarding report when a curator requests it
4. an on-demand memory-quality report covering the requested scope
5. explicit next actions when the requested diagnostic is not clean

## Quality Control Phases

| Phase | Check | Purpose |
| --- | --- | --- |
| Task start (when requested) | `drift_check` | Qualify existing onboarding before planning against it. |
| Curator handoff | scoped `c-05-create-or-update-onboarding-files` checks | Maintain affected sidecars, overviews, indexes, and entity entries for this change. |
| Explicit developer request | `memory_quality_check` | Run the requested memory-quality scope and report every passed, failed, blocked, and not-run check. |
| Targeted style repair | `history_order_fix.py` | Fix an identified ordering issue only after the requested report names it. |

Diagnostics are evidence only. They should not become an automatic closeout or
integration gate.

## Boundaries

1. This skill reports and routes memory quality work; it does not rewrite
   onboarding prose itself.
2. It does not replace deep Research.
3. It does not decide requirement or architecture direction.
4. It should qualify stale onboarding rather than silently treating it as
   trustworthy.
5. It must not turn the default repo-wide drift diagnostic into a whole-repository
   adoption scan for files that never had onboarding.
6. It must not treat implementation approval as commit approval; closeout
   commits remain owned by the `c-09-git-worktree-manager` skill transaction and
   authority controls.
7. It must not make a memory-quality, curator, or review result a closeout or
   integration prerequisite.

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

When a curator's scoped handoff needs a missing-onboarding report, run the
package-local check for task additions, copies, renames, or untracked source
files:

```text
python -m agents_remember.memory_quality.integrity.check_missing_onboarding --code-repository-root "<code-root>" --onboarding-root "<resolved-onboarding-root>"
```

This pass is intentionally different from task-start drift. It checks only the
current worktree additions in the curator's scope and reports the result to the
owning seat; it is not rerun by closeout or integration.

If it reports missing sidecar or inline onboarding, create the reported
onboarding through the `c-05-create-or-update-onboarding-files` skill before the
curator handoff. The closeout transaction consumes the resulting memory content
without rerunning this report.

### 6. Run Scoped Curator Checks

The curator maintains only the onboarding affected by the fed change set. Use
`c-05-create-or-update-onboarding-files` for sidecars, governing overviews,
route indexes, and entity entries, then run `git diff --check` in the memory
worktree plus any narrowly scoped check named by the curator brief. Record the
exact command, scope, result, and every failed or not-run check. A scoped check
does not establish full memory quality and is not a closeout or integration
gate.

If the owning role explicitly requests an MCP diagnostic, scope it to the leaf:

```text
memory_quality_check(request={"mode":"sync", "repo_id":"<repo-id>", "contract_path":"<enclosure-contract-path>", "checks":["<named-check>"]})
```

Use the returned report only to repair the named affected onboarding or to
report a blocker. Do not require a complete checklist, `finalFullCatalog`,
`curator_coherence`, or a quality certificate for handoff.

### 7. Run Full Memory Quality Only On Explicit Developer Request

When the developer explicitly requests full memory-quality evidence, run the
existing tool against the requested repository or contract path:

```text
memory_quality_check(request={"mode":"sync", "repo_id":"<repo-id>", "contract_path":"<enclosure-contract-path>"})
```

Report every applicable check as passed, failed, blocked, or not-run. Repair
findings through the existing onboarding workflow and rerun only as the
developer-requested operation directs. This evidence remains separate from
closeout/integration authority and never becomes an automatic prerequisite.

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

1. Task-start memory diagnostics begin with `c-08-ar-coordination-context-resolver` skill context and the `drift_check` MCP tool when that diagnostic is requested by the workflow.
2. Closeout and integration do not invoke memory-quality tools; they consume
   the prepared memory-content and ledger Git legs.
3. New files created by the current task may be checked before curator handoff
   with `check_missing_onboarding`.
4. The `c-02-memory-quality-control` skill hands maintenance work to the `c-05-create-or-update-onboarding-files` skill instead of writing onboarding content.
5. Stale onboarding may remain directional evidence until refreshed or
   disproven, but that trust level must be made explicit.
6. Missing verification metadata is itself actionable drift.
7. Orphaned onboarding should be surfaced clearly rather than left to
   accumulate silently.
8. Generated quality reports belong under the resolved coordination/temp root,
   not inside durable memory unless the developer explicitly asks.
9. A requested diagnostic report belongs under the resolved coordination/temp
   root unless the developer explicitly requests another artifact location.
10. Curator scoped checks and any explicitly requested full memory-quality run
    are evidence; neither is a closeout-readiness or integration authority.

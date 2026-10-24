<!-- Kept from the retired retired memory quality control skill (MIK-R196 rule 8): the surviving
owner of the pre-code-commit and pre-curation quality checks. The `memory_quality_check` and
`curator_coherence` tools own the checks themselves; this file is the seat's procedure. Two facts
changed with the product and are stated in the text below: the task-start drift report path is
retired (`docs/reference/drift-c02.md`), so `context_packet` answers drift in its own response and
files no report; and the durable result of a quality check is the one contract-scoped checklist
`memory_quality_check` replaces at the `reportPath` it returns. Authoring new onboarding remains
this same skill's work, in `SKILL.md` and `converted-card-workflow.md`. -->

---
name: c-05-create-or-update-onboarding-files
description: "Run the complete Agents Remember memory-quality operation as part of curation, plus scoped diagnostics, and carry its result into closeout and integration."
---

# Memory Quality Control (kept procedure)

Use this skill when a curator runs the memory-quality operation as part of
curation, or when an approved workflow asks for a scoped diagnostic. A curator
always runs the full operation; closeout and integration consume its result
together with the prepared code/memory/ledger Git inputs rather than invoking
this skill again.

The skill owns the diagnostic procedure. Drift detection can qualify task-start
context. Curation is always complete: the curator's full memory-quality result
travels with the handoff as a closeout and integration prerequisite, while tests
and code-quality checks may still be scoped.

## Inputs

This skill operates on one repository at a time and starts from the context
resolved by `c-08-ar-coordination-context-resolver` or MCP `context_packet`.

## Primary Outputs

1. task-start trust guidance from repo-wide drift classification
2. a concrete onboarding maintenance worklist for `c-05-create-or-update-onboarding-files`
3. a missing-onboarding report scoped to the curator's change set
4. the complete memory-quality operation a curator runs as part of curation
5. the `curator_coherence` authority when the checklist requires it, plus explicit next actions with the exact returned code for every finding that is not clean

## Quality Control Phases

| Phase | Check | Purpose |
| --- | --- | --- |
| Task start (when requested) | drift reporting via `context_packet`/`memory_quality_check` | Qualify existing onboarding before planning against it. |
| Curator handoff | complete `c-05-create-or-update-onboarding-files` checks + `memory_quality_check` | Maintain affected sidecars, overviews, indexes, and entity entries for this change, then run the full memory-quality operation and repair or escalate every curator-actionable finding. |
| Checklist requires it | `curator_coherence` | Publish the coherence authority for the exact checklist result the curator produced. |

Diagnostics are evidence. A code-quality or test diagnostic does not become an
automatic closeout or integration gate, while the curator's complete
memory-quality result is part of the curation handoff that closeout and
integration carry.

## Boundaries

1. This diagnostic procedure reports and routes memory quality work; it does not rewrite
   onboarding prose itself. Authoring and maintenance belong to the sibling procedures of this
   same skill — `../SKILL.md` (`## Converted Memory`) and `converted-card-workflow.md` — which
   the curator seat runs, under its own writer rules.
2. It does not replace deep Research.
3. It does not decide requirement or architecture direction.
4. It should qualify stale onboarding rather than silently treating it as
   trustworthy.
5. It must not turn the default repo-wide drift diagnostic into a whole-repository
   adoption scan for files that never had onboarding.
6. It must not treat implementation approval as commit approval; closeout
   commits remain owned by the `w-02-light-task-workflow` skill transaction and
   authority controls.
7. It must not make a code-quality, test, or independent-review result a
   closeout or integration prerequisite; the curator's complete memory-quality
   result is a curation output and does travel with the handoff.

## Procedure

### 1. Resolve Context

Use `c-08-ar-coordination-context-resolver` or MCP `context_packet` to confirm
the target repository's active memory and coordination context.

```text
context_packet(repo_id="<repo-id>")
```

The MCP server owns topology detection, coordination-root resolution, settings
parsing, storage semantics, and `pathRules` parsing. The `c-05-create-or-update-onboarding-files` skill consumes the resolved
context and applies memory quality control.

### 2. Run Task-Start Drift Control

At task start, request drift reporting (`context_packet` with `include_drift`, or
`memory_quality_check`) for repo-wide checks instead of rewriting shell loops. Inside a
worktree-backed leaf, add the leaf's enclosure contract path so the check reads that
leaf's memory worktree rather than the official memory repo.

No temporary drift report is filed. `context_packet(include_drift=true)` answers the drift
summary in its own response and writes nothing; the task-start report path this procedure used
to name is retired history, not a current output (`docs/reference/drift-c02.md`). The durable
result of a quality check is the single contract-scoped checklist `memory_quality_check`
atomically replaces at the `reportPath` it returns — the enclosure's
`reports/curator-memory-quality.md`, with its `attestationPath` beside it. That pair is
operational state outside both Git worktrees, and cleanup or abandon removes `reports/` with the
enclosure.

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

**On converted memory** (the tree holds the knowledge layout marker), currentness is measured from anchors:
- currentness comes from anchors instead: the `knowledge.converted` check runs the knowledge validator, and stale
  carried sidecar references refuse at every commit and landing route and report only at a sync merge (MIK-R53
  `merge_reports`); repair them with `citation_fix` or curator re-authoring before closeout;
- every changed source file is answered through the onboarding gate (MIK-R30): a current
  `onboarding_trace` row about the file (MIK-R53 rules 1-3; a counted change of its card alone
  never answers);
- cards and their references are authored as the c-05 skill's converted-card workflow describes.

The drift check measures the reference anchors of onboarding cards and the validity of the tree:
how many anchors are current, how many are stale against the code, and which files the knowledge
validator refuses. Whether an invariant is still current is answered by `knowledge_read` and
`knowledge_integrity_check`, not here. A stale reference is reported and never blocks by itself; a reference
whose path is gone refuses the leaf's closeout or landing and is only reported at a sync merge; a leaf
that changes a source file must change that file's card or record why not. Nothing is
repaired by this check: `citation_fix` re-records anchors that only moved, and a curator
re-authors a stale one.

Old-format memory is refused; convert it by the procedure of MIK-R46 rule 6.

### 4. Hand Off Drift Maintenance

If actionable files exist, hand only the approved update candidates to this skill's authoring
procedure (`converted-card-workflow.md`). Dirty-source drift findings remain
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

When a curator's handoff needs a missing-onboarding report, run the
package-local check for task additions, copies, renames, or untracked source
files:

```text
python -m agents_remember.memory_quality.integrity.check_missing_onboarding --code-repository-root "<code-root>" --onboarding-root "<resolved-onboarding-root>"
```

This pass is intentionally different from task-start drift. It checks only the
current worktree additions in the curator's scope and reports the result to the
owning seat; it is not rerun by closeout or integration.

If it reports missing sidecar or inline onboarding, create the reported
onboarding through this skill's authoring procedure (`converted-card-workflow.md`)
before the curator handoff. The closeout transaction consumes the resulting memory
content without rerunning this report.

### 6. Run the Complete Curation Check

The curator maintains the onboarding affected by the fed change set. Use this
skill's authoring procedure (`converted-card-workflow.md`) for sidecars, governing
overviews, route indexes, and entity entries, then run `git diff --check` in the memory
worktree and the full memory-quality operation scoped to the leaf. Curation is
always complete: a named scoped check never stands in for the full operation, and
every curator-actionable finding it returns is either repaired or escalated as
blocked with its exact returned code. Record the exact command, scope, result,
and every finding. Closeout and integration carry this result as a prerequisite.

```text
memory_quality_check(request={"mode":"sync", "repo_id":"<repo-id>", "contract_path":"<enclosure-contract-path>"})
```

A curator-actionable finding count that looks implausible for this change set is a
measurement problem to investigate, never permission to pass incomplete
onboarding. Tests and code-quality checks may be scoped to the change set;
curation may not.

### 6b. Judge Validator Findings by Their MIK-R53 Decision

No validator rule only reports without a stated reason (MIK-R53 rules 10-12).
A rule either refuses, or it reports for a named reason and names the route
that refuses in its place. The ten decided rules:

| Rule | State it names | Decision |
| --- | --- | --- |
| `R13.3-governs` | a decision that links nothing it governs | refuses |
| `R27.2-existing-record` | a record whose checkable admission criterion no longer holds | refuses; `merge_reports` |
| `R04.1-carried-route-absent` | a family route whose directory is gone | refuses; `merge_reports` |
| `R04.4-unrealized-family` | a live family none of whose members has a realization | refuses; `merge_reports` |
| `R22.6-carried-stale` | an anchor at a path that is gone | refuses; `merge_reports` |
| `R09-history-rows-merged` | a row whose entry a merge moved | `merge_reports`, by design: it reports at the merge only, and the next leaf publication refuses the row |
| `R22.3-sidecar-without-markdown` | entries whose card is gone | `migration_debt` |
| `R22.3-unresolved-target` | a citation the conversion could not resolve | `migration_debt` |
| `R04.4-route-unassigned` | an exported family with no route | `migration_debt` |
| `R27.4-legacy-unassessed` | an exported record nobody assessed | `migration_debt`; one finding per record once it refuses |

- `merge_reports`: refuse at every commit and landing route, report only at a
  sync merge (two or more bases, no leaf publication). A merge can create the
  state from two valid sides, and it is repaired only after the merge exists.
  Repair before the leaf's closeout or landing.
- `migration_debt`: a finding a comparison base already holds reports while the
  migration is not complete (not every onboarding route is `migrated` or
  `excluded`) and refuses at every route once it is; a finding no base holds is
  new and refuses at once. The conversion writes these states, not a leaf. A
  reported debt finding names the number of routes not yet migrated.
- The five refusing rules report inside the writer (`writer_reports`) and
  refuse at every commit route: repair them before closeout.
- Every reported finding names its reason and the route that will refuse. No
  finding is reported without a stated reason.

### 7. Publish the Curator Coherence Authority When the Checklist Requires It

`curator_coherence` is the authority a curator produces when the full operation
returns `checklistStatus=coherence-required` — the ordinary outcome for a healthy
memory, which reports a successful `prepare` with `candidateCount 0` and still
requires the record to be published. Carry the leaf's `contract_path` through
`status` → `prepare` → `publish` → `validate`, and supply one
curator/architect-authored disposition, rationale, and evidence reference for
every candidate in the set `prepare` wrote. `prepare` answers with the count by kind,
the set's digest and the locations -- it does not return the candidates. Read them from
the file it names (`candidatesPath`, with a rendering for reading beside it), in parts
when the set is large, and confirm the digest before judging:

```text
curator_coherence(request={"action":"prepare", "contract_path":"<enclosure-contract-path>"})
```

Publish only the exact identities in the set `prepare` wrote, then `validate`. A refusal,
a missing or extra candidate, or a validator failure is a blocked curation finding
with its exact returned code, reported to the owning seat. A hand-written
certification file is never a substitute for this record, and the record never
replaces the closeout transaction that owns the real code and memory commits.

### 8. Use Targeted Style Fixers Only After Findings

Use `citation_fix` for mechanically moved converted anchors after the report names them.

Run `memory_quality_check` again after the fixer. If a finding is not
mechanically fixable, update the affected onboarding by hand and rerun the
check.

## Rules

1. Task-start memory diagnostics begin with `c-08-ar-coordination-context-resolver` skill context and drift reporting (`context_packet`/`memory_quality_check`) when that diagnostic is requested by the workflow.
2. Closeout and integration do not rerun memory-quality tools; they consume
   the curator's complete memory-quality result together with the prepared
   memory-content and ledger Git legs.
3. New files created by the current task may be checked before curator handoff
   with `check_missing_onboarding`.
4. This procedure hands maintenance work to the authoring procedures of the same skill
   (`../SKILL.md` §Converted Memory and `converted-card-workflow.md`) instead of writing
   onboarding content itself.
5. Stale onboarding may remain directional evidence until refreshed or
   disproven, but that trust level must be made explicit.
7. Orphaned onboarding should be surfaced clearly rather than left to
   accumulate silently.
8. A quality check's result is its owning tool's artifact, not a generated file of this
   procedure: `memory_quality_check` replaces the contract-scoped checklist at its returned
   `reportPath` (and `curator_coherence` its record), while `context_packet` writes no report.
   Other generated diagnostics belong under the resolved coordination/temp root, not inside
   durable memory unless the developer explicitly asks.
9. A requested diagnostic report whose owning tool produces no artifact belongs under the
   resolved coordination/temp root unless the developer explicitly requests another artifact
   location; the two quality owners above keep their own locators instead.
10. The curator's complete memory-quality result is the curation evidence that
    closeout and integration carry; tests, code-quality checks, and independent
    review remain separately scoped and are not closeout-readiness or
    integration authority.

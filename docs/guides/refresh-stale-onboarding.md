# Refresh Stale Onboarding

Stale onboarding is expected. The important rule is that agents should detect it before trusting it as current behavior.

## Detect Drift

Follow the [memory-quality diagnostic procedure](../../skills/c-05-create-or-update-onboarding-files/workflows/memory-quality-control.md#2-run-task-start-drift-control)
for the target repository. `context_packet(include_drift=true)` answers the drift
summary in its response and writes no report.

`memory_quality_check` replaces one contract-scoped checklist at the `reportPath`
it returns, with its `attestationPath` beside it. In a worktree-backed leaf, pass
the enclosure contract: the result is the enclosure's `reports/curator-memory-quality.md`,
outside both Git worktrees. Cleanup or abandon removes that operational report
with the enclosure; no temporary drift-report file is a prerequisite.

Classify actionable drift by the corresponding source file's state: a clean source
is an onboarding update candidate; a dirty source is active work and stays with
its owner unless the developer explicitly takes it over. Missing onboarding
remains gradual-adoption work rather than a repository-wide start gate. A scoped
diagnostic does not replace the Curator's complete curation, memory-quality and
required coherence work.

## Interpret Results

Common classifications:

- `up to date`: no action needed
- `drifted`: source changed since onboarding verification
- `missing verification`: onboarding lacks required metadata
- `missing`: expected onboarding is absent
- `orphaned`: onboarding exists for a source file that no longer exists
- `unsupported`: the helper cannot safely validate the storage shape

## Refresh Through `c-05-create-or-update-onboarding-files`

Use `c-05-create-or-update-onboarding-files` for file-level onboarding and repo entity catalogs.

The refresh should:

1. inspect the current source
2. compare stale onboarding against current behavior
3. remove obsolete claims
4. add durable current-state facts that matter to future agents
5. update verification metadata after the content is accurate

## Use Directional Trust Carefully

Drifted onboarding may still be useful as historical context, but the agent should say that explicitly. Do not plan against stale onboarding as if it is verified current behavior.

## After Code Changes

When implementation changes the source, onboarding is task-local pending work until the implementation cycle refreshes it. That does not re-block the same task after the initial drift gate; it does mean the final closeout should include onboarding refresh and verification.

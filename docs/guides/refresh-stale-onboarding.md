# Refresh Stale Onboarding

Stale onboarding is expected. The important rule is that agents should detect it before trusting it as current behavior.

## Detect Drift

Run the `c-02-memory-quality-control` skill for the target repository:

```text
drift_check(repo_id="<repo-id>", detail_limit=50)
```

The helper writes drift reports under the resolved coordination root, usually:

```text
ar-coordination/temp/drift-reports/<repo>/
```

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

---
name: C-02-onboarding-drift-detection
description: "Detect onboarding drift against the resolved internal or shared onboarding root, classify how trustworthy existing onboarding remains, and hand actionable maintenance work to C-05-create-or-update-onboarding-files."
---

# C-02 Onboarding Drift Detection

Use this skill at task start, before relying on older onboarding for unfamiliar surfaces, and again near closure after approved code changes land.

Its job is to decide whether onboarding is still trustworthy enough to ground the current work and to produce a concrete maintenance worklist when it is not.

## Inputs

This skill's standard workflow operates on one repository at a time.

## Primary Outputs

1. a drift summary or drift report
2. a classification of onboarding units as up to date, drifted, missing verification, missing, orphaned, disabled, or unsupported
3. a maintenance worklist for `C-05-create-or-update-onboarding-files`
4. trust guidance for the caller when stale onboarding still contains directional value

## Boundaries

1. This skill detects and classifies drift; it does not rewrite onboarding content itself.
2. It does not replace deep Research.
3. It does not decide requirement or architecture direction.
4. It should qualify stale onboarding rather than silently treating it as trustworthy.

## Procedure

### Preferred helper

Use `C-08-ar-management-resolver` to resolve the target repository's active management context, then use the bundled helper for repo-wide checks instead of rewriting shell loops:

```bash
<this-skill-dir>/scripts/check_onboarding_drift.py \
  --repo <repo-root> \
  --report <repo-root>/ar-management/onboarding/drift-report.md
```

The helper passes compatibility CLI inputs through the C-08 resolver. For explicit shared scaffolding, pass the shared root and keep the repository target explicit:

```bash
<this-skill-dir>/scripts/check_onboarding_drift.py \
  --repo <repo-root> \
  --topology shared \
  --shared-root <shared-ar-management-root> \
  --report <shared-ar-management-root>/onboarding/<repo>/drift-report.md
```

The compatibility `--onboarding-root` override remains available when a caller already resolved the repo onboarding root. Topology detection, management-root resolution, settings parsing, storage semantics, and `pathRules` parsing belong to C-08; this helper consumes that resolved context and classifies drift. The helper requires Python 3 and `git`, uses only the Python standard library, prints a tab-separated summary by default, and can also emit `--format json` or `--format csv`. If the executable bit is unavailable in a local checkout, fall back to invoking the script with the machine's Python 3 interpreter.

### 1. Resolve onboarding units in the repository

Invoke `C-08-ar-management-resolver` for the target repository and use the resolved context. Internal repositories read `<repo-root>/ar-management/system/settings.md`; shared repositories read `<shared-ar-management-root>/system/settings.md`.

C-08 reads `onboarding.storage` and `onboarding.pathRules` separately. Storage decides where eligible onboarding artifacts live. `pathRules` decide whether a source path or file type is eligible for onboarding, and they apply in both internal and shared mode. In shared settings, `pathRules` can be scoped per repository with `path: <repo-name>` or per repository subtree with `path: <repo-name>/<subtree>`.

Primary drift detection supports sidecar markdown onboarding under the resolved onboarding root, whether that root is repo-local internal storage or shared storage. It may also classify inline onboarding blocks when storage settings resolve a source path to `inline`.

If repo-level entity catalogs or overview files are in scope, treat them as follow-up maintenance surfaces rather than trying to diff them directly against one source file.

### 2. Extract verification metadata

For each onboarding unit in scope, read the verification metadata appropriate to its storage mode.

For external mirrored onboarding files, read:

1. `repository`
2. `path`
3. `lastVerifiedCommitHash`
4. `lastVerifiedCommitDate`

For inline onboarding blocks, read the marker-delimited block and use its metadata such as `sourceDigest` and `verifiedAt`.

If the onboarding unit is missing the metadata needed for verification, classify it as missing verification and flag it for maintenance.

### 3. Compare the source file against the recorded verification point

Use the recorded metadata plus the resolved storage mode to classify the current state:

1. If the source file no longer exists, classify the onboarding file as orphaned.
2. If the resolved storage mode is `disabled`, classify the source path as disabled.
3. If sidecar onboarding is expected but the mirrored markdown file is missing, classify it as missing.
4. If inline onboarding is expected but the marker-delimited block is missing, classify it as missing.
5. If the external or inline metadata needed for verification is empty, classify it as missing verification.
6. For sidecar onboarding, compare the source file against the recorded commit using git diff.
7. For inline onboarding, recompute the source digest from the source body with the onboarding block removed.
8. If verification matches, classify the onboarding unit as up to date.
9. If verification does not match, classify it as drifted.
10. If the storage mode or source encoding cannot be handled safely, classify it as unsupported.

### 4. Qualify how trustworthy the onboarding still is

For drifted onboarding, record how much directional value remains:

1. high when the drift is small or the source path is intentionally disabled
2. medium when the onboarding is useful for adjacent context but no longer safe as a direct statement of current behavior
3. low when the source changed so much that the onboarding should not be trusted without refresh or when the storage mode is unsupported

Also note which sections are likely affected:

1. logic
2. invariants and boundaries
3. conventions
4. cross-repo references
5. repo-level entity catalog follow-up when entity shape or naming drift likely changed

### 5. Produce the maintenance artifact

Write a drift report when the scope is large enough that the caller needs a reusable worklist.

Preferred report locations:

1. `<onboarding-root>/<repo>/drift-report.md` for the repository run

The report should include:

1. scope checked
2. generated timestamp
3. counts for up to date, drifted, missing verification, missing, orphaned, disabled, and unsupported files
4. an actionable table listing onboarding file, source file, classification, current trust level, and likely affected sections
5. enough summary detail for `C-05-create-or-update-onboarding-files` to refresh the right surfaces without rerunning the scan from scratch

Treat the drift report as a maintenance artifact, not as a long-lived research handoff.

### 6. Hand off to onboarding maintenance

If actionable files exist, hand the worklist to `C-05-create-or-update-onboarding-files`.

The handoff should identify:

1. which onboarding files need refresh
2. which files are orphaned and may need deletion
3. whether related repo-level catalogs or overview files likely need follow-up
4. which stale onboarding can still be used directionally until maintenance finishes

If no actionable files exist, return a clean summary and stop.

## Rules

1. Drift detection remains evidence qualification and maintenance routing, not deep Research.
2. It must use the canonical onboarding root returned by `C-08-ar-management-resolver` for the target repository.
3. It hands maintenance work to `C-05-create-or-update-onboarding-files` instead of performing that maintenance itself.
4. Stale onboarding may remain directional evidence until refreshed or disproven, but that trust level must be made explicit.
5. Missing verification metadata is itself actionable drift.
6. Orphaned onboarding should be surfaced clearly rather than left to accumulate silently.

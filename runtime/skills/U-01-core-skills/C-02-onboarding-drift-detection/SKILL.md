---
name: c-02-onboarding-drift-detection
description: "Detect onboarding drift against the resolved internal-memory or external-memory onboarding root, classify how trustworthy existing onboarding remains, and hand actionable maintenance work to C-05-create-or-update-onboarding-files."
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

Use `C-08-ar-coordination-context-resolver` to resolve the target repository's active memory and coordination context, then use the bundled helper for repo-wide checks instead of rewriting shell loops:

```bash
<this-skill-dir>/scripts/check_onboarding_drift.py \
  --code-repository-root <code-repository-root>
```

By default the helper writes the Markdown report to `<coordination_root>/temp/drift-reports/<repo-name>/<repo-name>_<branch-name>_drift-report.md`. That keeps temporary drift artifacts out of task contract folders while still keeping them under the local coordination root, and each repository/branch run gets a collision-resistant filename.

The helper passes the explicit code repository root through the C-08 resolver. For explicit external-memory scaffolding, pass the coordination root and keep the code repository root explicit:

```bash
<this-skill-dir>/scripts/check_onboarding_drift.py \
  --code-repository-root <code-repository-root> \
  --topology external \
  --coordination-root <ar-coordination-root>
```

If `--report` is supplied, C-08's resolved `coordination_root` still owns report placement. Relative paths are resolved from the resolved `temp_root`. Absolute paths are only used as-is when they are already inside the resolved coordination root and outside the resolved `memory_root`; paths inside the durable memory repo are redirected to `<coordination_root>/temp/drift-reports/<repo-name>/` so temporary reports do not dirty versioned memory. Absolute paths outside the coordination root are redirected the same way. The repo/branch-prefixed default filename applies whenever `--report` is omitted.

The `--onboarding-root` override remains available when a caller already resolved the code repository onboarding root. Topology detection, coordination-root resolution, settings parsing, storage semantics, and `pathRules` parsing belong to C-08; this helper consumes that resolved context and classifies drift. The helper requires Python 3 and `git`, uses only the Python standard library, prints a tab-separated summary by default, and can also emit `--format json` or `--format csv`. If the executable bit is unavailable in a local checkout, fall back to invoking the script with the machine's Python 3 interpreter.

### 1. Resolve onboarding units in the repository

Invoke `C-08-ar-coordination-context-resolver` for the target repository and use the resolved context. Internal-memory repositories use `<repo-root>/ar-memory/system/settings.md` for prose instructions and prefer a sibling `system/settings.json` for machine-readable settings when present; external-memory repositories use the same pair under `ar-coordination/memory-repos/ar-<repo-name>/system/` when an external memory repo exists.

C-08 resolves `onboarding.storage` and `onboarding.pathRules` separately. Storage decides where eligible onboarding artifacts live. `pathRules` decide whether a source path or file type is eligible for onboarding, and they apply in both internal-memory and external-memory mode. In external-memory JSON settings, `pathRules` can be scoped per repository with `path: <repo-name>` or per repository subtree with `path: <repo-name>/<subtree>`.

Primary drift detection supports sidecar markdown onboarding under the resolved onboarding root, whether that root is repo-local internal memory or external memory. It classifies file-level onboarding, root repo overviews, route-local overviews, and repo entity catalogs when those artifacts carry supported `doc_type` metadata. It may also classify inline onboarding blocks when storage settings resolve a source path to `inline`.

Root and route-local overviews do not map one-to-one to a source file. They are verified against their recorded `sourceRoute`: C-02 compares that route from `lastVerifiedCommitHash` through `HEAD` and checks the same route for staged or unstaged local changes.

Repo entity catalogs are verified through deterministic entity fingerprints. Each `## Entity Inventory` entry must have a matching `## Entity Fingerprints` row. Each entity fingerprint row records `git-blob-set-v1`, an aggregate hash over a curated set of repo-relative evidence paths. The script sorts the paths, resolves each `HEAD:<path>` Git blob hash, hashes the `path + blob_hash` list, and compares the stored aggregate. Agent judgment belongs in choosing or refreshing the evidence path set; C-02 only checks the stored fingerprint deterministically.

### 2. Extract verification metadata

For each onboarding unit in scope, read the verification metadata appropriate to its storage mode.

For external mirrored onboarding files, read:

1. `repository`
2. `path`
3. `lastVerifiedCommitHash`
4. `lastVerifiedCommitDate`

For repo and route-local overviews, read:

1. `repository`
2. `doc_type`
3. `sourceRoute`
4. `lastVerifiedCommitHash`
5. `lastVerifiedCommitDate`

For repo entity catalogs, read:

1. `repository`
2. `doc_type`
3. `lastUpdated`
4. the `Entity Inventory` section headings
5. the `Entity Fingerprints` table with `Entity`, `Algorithm`, `Fingerprint`, and `Evidence Paths` columns

For inline onboarding blocks, read the marker-delimited block and use its metadata such as `sourceDigest` and `verifiedAt`.

If the onboarding unit is missing the metadata needed for verification, classify it as missing verification and flag it for maintenance.

### 3. Compare the source evidence against the recorded verification point

Use the recorded metadata plus the resolved storage mode to classify the current state:

1. If the source file no longer exists, classify the onboarding file as orphaned.
2. If the resolved storage mode is `disabled`, classify the source path as disabled.
3. If sidecar onboarding is expected but the mirrored markdown file is missing, classify it as missing.
4. If inline onboarding is expected but the marker-delimited block is missing, classify it as missing.
5. If the external or inline metadata needed for verification is empty, classify it as missing verification.
6. For file-level sidecar onboarding, compare the source file against the recorded commit through `HEAD`, then check that same source path for staged or unstaged local changes.
7. For repo and route-local overviews, compare the recorded `sourceRoute` against the recorded commit through `HEAD`, then check that same route for staged or unstaged local changes.
8. For repo entity catalogs, reconcile inventory headings against fingerprint rows before trusting any entry. Missing fingerprint tables, inventory entries without matching fingerprint rows, unsupported algorithms, missing fingerprints, missing evidence paths, or fingerprint mismatches are actionable drift. Fingerprint rows without matching inventory entries are orphaned and must be reviewed as possible removed, renamed, or moved entities.
9. For inline onboarding, recompute the source digest from the source body with the onboarding block removed.
10. If verification matches, classify the onboarding unit as up to date.
11. If verification does not match, classify it as drifted.
12. If the storage mode, algorithm, or source encoding cannot be handled safely, classify it as unsupported.

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
5. overview route summaries when a governed route changed
6. repo-level entity catalog follow-up when an entity fingerprint drifted

### 5. Produce the maintenance artifact

Write a drift report when the scope is large enough that the caller needs a reusable worklist.

Preferred report locations:

1. `<resolved-coordination-root>/temp/drift-reports/<repo-name>/<repo-name>_<branch-name>_drift-report.md` for the repository run

The report should include:

1. scope checked
2. generated timestamp
3. counts for up to date, drifted, missing verification, missing, orphaned, disabled, and unsupported files
4. an actionable table listing onboarding file, source file, classification, current trust level, and likely affected sections
5. enough summary detail for `C-05-create-or-update-onboarding-files` to refresh the right surfaces without rerunning the scan from scratch

The markdown report should not include a full inventory of up-to-date files; stdout, JSON, or CSV output can be used when a complete row list is needed.

Treat the drift report as a maintenance artifact, not as a long-lived research handoff.

### 6. Hand off to onboarding maintenance

If actionable files exist, hand the worklist to `C-05-create-or-update-onboarding-files`.

The handoff should identify:

1. which onboarding files need refresh
2. which files are orphaned and may need deletion
3. which overview source routes changed
4. which entity fingerprints changed, which inventory entries are missing fingerprint rows, which fingerprint rows are orphaned, and which evidence paths caused the stale signal
5. whether related repo-level catalogs or overview files likely need follow-up
6. which stale onboarding can still be used directionally until maintenance finishes

If no actionable files exist, return a clean summary and stop.
If actionable files exist, consult this repo's [AGENTS.md](../../../AGENTS.md) "Onboarding Rules" section.

## Rules

1. Drift detection remains evidence qualification and maintenance routing, not deep Research.
2. It must use the canonical onboarding root returned by `C-08-ar-coordination-context-resolver` for the target repository.
3. It hands maintenance work to `C-05-create-or-update-onboarding-files` instead of performing that maintenance itself.
4. Stale onboarding may remain directional evidence until refreshed or disproven, but that trust level must be made explicit.
5. Missing verification metadata is itself actionable drift.
6. Orphaned onboarding should be surfaced clearly rather than left to accumulate silently.
7. Drift detection must not scan unrelated untracked files or classify source files without existing onboarding as missing during the default repo-wide gate.

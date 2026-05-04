# Inline Onboarding Workflow

Use this workflow when file-level onboarding is stored inline inside the source file rather than as a mirrored markdown file.

The semantic content still comes from `file-level-onboarding-template.md`. This workflow only adds storage-specific rules for syntax, placement, digesting, and fallback behavior.

## Goal

Write or maintain one inline onboarding block inside a source file without breaking the file's runtime, parsing, build, or tooling behavior.

## Workflow

1. Start from the canonical file-level onboarding content model.
2. Choose comment delimiters that are valid and idiomatic for the host language.
3. Place the block at the highest safe position in the file, preserving required shebangs, directives, encoding markers, generated-file notices, or other leading syntax.
4. Keep the `@ar-onboarding` and `@ar-onboarding-end` markers stable.
5. Recompute `sourceDigest` from the source body with the onboarding block removed.
6. If no safe inline position exists, fall back to external onboarding instead of forcing inline storage.

## Verification Rules

1. `verifiedAt` uses `YYYY-MM-DDTHH:MM`.
2. `sourceDigest` uses SHA-256 over the source body with the inline onboarding block removed.
3. Missing markers or missing `sourceDigest` count as missing or missing verification during drift detection.
4. If the source file cannot be parsed safely as text for inline onboarding, treat it as unsupported and fall back to external onboarding.

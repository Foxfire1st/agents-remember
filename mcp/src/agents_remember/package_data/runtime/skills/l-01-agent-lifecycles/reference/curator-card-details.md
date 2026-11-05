# Curator — card format and citation-check step detail

Read this file when routing cards and checking their citations. The no-handwritten-sidecar
boundary and the authored trace obligation remain in the capsule.

## Curation 2: before ingest; reuse during Curation 4

Read the subject’s earlier rows before ingest. For invariants:

- `changed` changes the record’s meaning and increments revision;
- `moved` re-anchors without changing meaning;
- `deleted` removes entries or retires the invariant;
- `extended` adds an entry without changing meaning;
- `no_impact` preserves meaning and the entry set, including admission/origin/slug-only changes.

Family and onboarding rows keep their own schemas.

Check each changed claim, count and prose line reference against code; a prose line reference lies inside its citation anchor. Green structural checks and authentic anchors do not prove prose true.

Retain one independent statement-by-statement fact-check. citation_fix makes deterministic fixes only; semantic or ambiguous items are Curator decisions with the full decision-item report in the workspace. State that principle without claiming an unbuilt L68 feature already exists.

## Overview format

An overview reads from its purpose (MIK-R80): title, the governing overview, what the area is for
   under one of the fixed headings `## What This Repo Is`, `## What This Area Is` or `## Purpose`,
   how it is built, its invariants and boundaries, and last all citation lines together. A change
   goes into the section its change belongs to.

## Current card content

When routing or refreshing cards, apply the current-truth and all-carrier repair rule from
**Before curation step 1** in the already-delivered reconciliation detail.

## Converted-card format and citation command

**On converted memory** (the tree holds the knowledge layout marker), follow the c-05 skill's converted-card
   workflow:
   - cards have no metadata table, no Update History and no citation tables;
   - a new card's evidence, or a refreshed reference, is a citation table that `citation_fix` on `agents-remember-task` turns into
     `- <finding> [n]` lines and sidecar references with resolved anchors.

## Trace-row format

(`onboarding:<path>` or `onboarding:<route>/overview`, `updated` with a counted card change,
     or `no_impact` with a reason)

## Batch citation check

Check the cards of your change in one `citation_fix` call and repair them in one call: pass
   every card's onboarding-relative path as `documents` (one call, dry run first to preview),
   never one call per card.

# Curator — card format and citation-check step detail

Read this file when routing cards and checking their citations. Source passages are retained
verbatim from `roles/curator.md`; the no-handwritten-sidecar boundary and the authored trace
obligation remain in the capsule.

## Overview format

An overview reads from its purpose (MIK-R80): title, the governing overview, what the area is for
   under one of the fixed headings `## What This Repo Is`, `## What This Area Is` or `## Purpose`,
   how it is built, its invariants and boundaries, and last all citation lines together. A change
   goes into the section its change belongs to.

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

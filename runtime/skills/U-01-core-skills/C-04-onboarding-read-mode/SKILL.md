---
name: c-04-onboarding-read-mode
description: "Use onboarding as a cheap routing layer for source work: discover likely routes, then confirm only the candidate packet."
---

# C-04 Onboarding Read Mode

Use this skill when repository source work should use existing Agents Remember
onboarding. Its purpose is to reduce reads, searches, tool calls, and tokens.

## Modes

Choose one mode and state it.

- `fast-memory-discovery`: use when the relevant route or files are unknown.
- `bounded-source-confirmation`: use when the route/files are known or discovery
  produced a packet; it consumes the packet only.

Discovery is routing only. Confirmation is source-backed evidence.

## Candidate Packet

Fast discovery exits with this packet, and bounded confirmation starts from it:

- selected route: one highest-confidence route, or `unknown`
- candidate sources: no more than three files, or one route-scoped glob
- hot-path summary and anchors used, if present
- route index status: read, missing, or unavailable
- sidecar status per candidate: `read`, `present-by-index`,
  `absent-by-index`, `absent-by-probe`, or `out-of-scope`
- governing overview for indexed absence
- one named unresolved source question, if search is still needed
- confidence and gaps

If the packet lacks route/file candidates, stay in discovery. Missing sidecars
or sparse memory are not packet failures; they are inputs for targeted source
reads/searches in confirmation.

## Fast Memory Discovery

Read only what is needed to produce the packet:

1. Read `<onboarding_root>/overview.index.json` when it exists; use root
   `hotPath`, `childRoutes`, and routing terms to pick likely routes.
2. Read `<onboarding_root>/overview.md` only when root index is insufficient.
3. If a semantic memory index exists, query it once for likely routes.
4. Pick no more than three likely routes from index/overview evidence.
5. Read selected route `overview.index.json` first; use `hotPath` summary,
   candidate hints, and anchor hints as the cheap route packet.
6. Read selected route `overview.md` only when `hotPath`/index is insufficient.
7. If no candidate source emerges, run one capped route-scoped search:
   `rg -n -m 20 <literal-or-identifier> <selected-route-or-glob>`.
8. Stop when the candidate packet is small enough for confirmation.

Discovery must not read every possible sidecar, probe sidecars with `Test-Path`,
search the repository root, search multiple subsystems, or use search hits as
final evidence.

## Bounded Source Confirmation

Start from the candidate packet. Do not reread root overview, root index, route
index, or route overview that discovery already read. Return to discovery only
when there is no route, source file, or route-scoped glob to constrain source.

Before source `rg`, start from `hotPath.anchorHints` and use anchors more
specific than the route: exact identifiers, filenames, config keys, flags,
commands, errors, APIs, schema fields, test names, or distinctive comments. Do
not search route labels or broad domain words after they selected the route.

If no source anchor exists, narrow by filenames first with `rg -l`,
`coveredFiles`, route-scoped globs, or one small filename search. Only print
matching lines after a small file set is selected. If hits are noisy, stop and
choose a narrower anchor in the same route or candidate file set.

Confirm only the packet contents:

1. For `present-by-index`, read the source with its deterministic sidecar.
2. For `absent-by-index`, do not probe the sidecar; read the governing overview
   only if needed, then read the source.
3. Without a route index, probe only the deterministic sidecar for the candidate
   source being confirmed.
4. If one named source question remains, run one capped source-anchor search
   over candidate files or the selected route: `rg -n -m 20 <source-anchor> ...`.
5. After search, read the resulting source/sidecar or source/governing-overview
   pair before any further search.

Confirmation must not expand into a second investigation. No repository-root,
multi-route, or multi-subsystem search; no `rg --files`, broad `find`, or
`rg -C 8` while selecting files. If targeted source evidence proves the
route/files irrelevant, stop and return to discovery.

## Route Index Semantics

`overview.index.json` is generated metadata: `sourceScope` governs the route;
`childRoutes` narrows; `coveredFiles` lists sidecars; `hotPath` gives cheap
summary/anchors; `fallback.governingOverview` names absent-sidecar fallback.

For a source path inside `sourceScope` but absent from `coveredFiles`, infer
that no generated file-level sidecar exists for that route. This only skips the
sidecar probe; it never forbids reading source.

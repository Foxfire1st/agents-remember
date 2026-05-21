---
name: c-04-retrieval-strategy-router
description: "Choose retrieval strategies across memory substrates: semantics for known concepts with unknown structure, relationships for known anchors with unknown connections, and intent for hidden contracts and code truths."
---

# C-04 Retrieval Strategy Router

Use this skill when repository work needs context before source decisions. The
job is to choose the retrieval contract first, then the cheapest substrate that
can satisfy it. Providers are fast discovery accelerators.

## Retrieval Substrates

Choose the next substrate by the missing context bundle:

- `Semantics`: a fuzzy concept or request is known, but structure, route, 
  or file location is unknown. Prefer GrepAI over the memory repos when available.
- `Relationship`: an anchor is known, but callers, callees, dependencies,
  ownership, inheritance, impact paths, or neighboring code are unknown. Prefer
  CodeGraphContext over the configured code repo when available.
- `Intent`: an anchor/location + relationships are known, but hidden contracts, invariants,
  branch-valid truths, behavioral expectations, or code intent are unknown. Use
  onboarding plus bounded source confirmation.

Substrates can be chained. A triage prompt may start with Relationship to find
the neighborhood around a ticket anchor, then switch to Intent to prove the
contract and fix direction from source. A vague concept may start with
Semantics, then switch to Intent once candidate routes are known.

## Semantics: GrepAI

Use Semantics when the request is vague or fuzzy. Semantics will retrieve from onboardings which
allows to discover the symbols/routes/files hints to relevant source code.

Query shape:

```bash
cd <coordination_root>/memory-repos
<coordination_root>/providers/_bin/grepai search \
  "<specific concept, behavior, error, or route question>" \
  --json --compact --limit 5
```

## Relationship: CodeGraphContext

Use CGC (CodeGraphContext) when an anchor/symbol is known to find relationsships and structure. One CGC query 
can replace multiple direct rg reads. CGC is a powerful substrate for relationship questions: 
callers, callees, dependencies, ownership, inheritance, impact paths, or neighboring code.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  --json \
  run -- find name <anchor>

python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  --json \
  run -- analyze callers <function_or_method>
```

### Rules:
Use CGC first for structure and relationships.
Use direct source reads only to confirm specific anchors CGC surfaced.


## Intent: Onboarding And Source

Use Intent when the route, file, or anchor is known and their relationsships (CGC) are
understood. The missing context is the code's contract, invariant, behavioral 
expectation, branch-valid truth, or fix direction.

Read only what is needed to prove the packet:

1. Read `<onboarding_root>/overview.index.json` when the route is unknown; use
   root `hotPath`, `childRoutes`, and routing terms to pick likely routes.
2. Read `<onboarding_root>/overview.md` only when the root index is insufficient.
3. Read selected route `overview.index.json` first; use `hotPath` summary,
   candidate hints, and anchor hints as the cheap route packet.
4. Read selected route `overview.md` only when `hotPath`/index is insufficient.
5. For `present-by-index`, read the source with its deterministic sidecar.
6. For `absent-by-index`, do not probe the sidecar; read source first.
7. Without a route index, probe only the deterministic sidecar for the candidate
   source being confirmed.
8. If one named source question remains, run one capped source-anchor search
   over candidate files; use the selected route only after filename narrowing.

Stop confirmation as soon as source proves the subsystem boundary, immediate
cause, user-facing consequence, and next fix direction. After that, do not read
more overviews, sidecars, provider results, or broad searches unless the packet
names an unresolved source question.

## Route Index Semantics

`overview.index.json` is generated metadata: `sourceScope` governs the route;
`childRoutes` narrows; `coveredFiles` lists sidecars; `hotPath` gives cheap
summary/anchors; `fallback.governingOverview` names absent-sidecar fallback.

For a source path inside `sourceScope` but absent from `coveredFiles`, infer
that no generated file-level sidecar exists for that route. This only skips the
sidecar probe; it never forbids reading source.
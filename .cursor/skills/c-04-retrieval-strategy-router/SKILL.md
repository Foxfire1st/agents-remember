---
name: c-04-retrieval-strategy-router
description: "Choose retrieval strategies across memory substrates: semantics for known concepts with unknown structure, relationships for known anchors with unknown connections, and intent for hidden contracts and code truths."
---

# c-04-retrieval-strategy-router Retrieval Strategy Router

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

First request an MCP `context_packet(repo_id="<repoId>", include_providers=true)`
when the MCP server is configured. Use provider results only when the packet
reports healthy provider state and the MCP exposes an appropriate provider
query tool. Coordinator-local provider lifecycle scripts are not installed
runtime tools.

The two highest-value GrepAI patterns are broad semantic routing and scoped
memory-project search. Examples here are synthetic response shapes only; do not
copy private repository names, symbols, paths, snippets, or results into
reusable skill examples.

Synthetic answer shape:

```text
results[3]{project,path,lines,score}:
  1 | <memoryProject> | onboarding/src/jobs/retry-policy.ts.md | 18-34 | 0.84
  2 | <memoryProject> | onboarding/src/http/client.ts.md | 41-59 | 0.78
  3 | <memoryProject> | onboarding/overview.md | 72-81 | 0.73
```

Use this when the route is unknown and the next step is choosing which
overview, sidecar, or source area to confirm.

Synthetic answer shape:

```json
{
  "query": "validation rules for imported records",
  "results": [
    {
      "project": "<memoryProject>",
      "path": "onboarding/src/import/record-validator.ts.md",
      "startLine": 22,
      "endLine": 46,
      "score": 0.86
    }
  ]
}
```

Use `--project` after the relevant memory root is known. Use `--path` after
route discovery narrows the search further. For the full GrepAI usage catalog
with synthetic example outputs, read
[grepai-high-leverage-usage.md](./grepai-high-leverage-usage.md) beside this skill.

## Relationship: CodeGraphContext

Use CGC (CodeGraphContext) when an anchor/symbol is known to find relationships and structure. One CGC query
can replace multiple direct `rg` reads. CGC is a powerful substrate for relationship questions:
callers, callees, dependencies, ownership, inheritance, impact paths, or neighboring code.

First request an MCP `context_packet(repo_id="<repoId>", include_providers=true)`
when the MCP server is configured. Use CGC only when the packet reports healthy
CGC state and the MCP exposes an appropriate relationship query tool.

The two highest-value CGC patterns are impact tracing and complexity triage.
Examples here are synthetic response shapes only; do not copy private
repository names, symbols, or paths into reusable skill examples.

Synthetic answer shape:

```text
Function 'handleRequest' calls:
validateRequest      <repo>/src/http/validation.ts:42
loadSession          <repo>/src/auth/session.ts:18
dispatchCommand      <repo>/src/app/command-router.ts:77

Total: 3 function(s)
```

Use `analyze callers` for the reverse direction and `analyze chain` when two
symbols are known and the missing question is whether one can reach the other.

Synthetic answer shape:

```text
Most Complex Functions (threshold: 10):
Function             Complexity  Location
renderDashboard              42  <repo>/src/ui/dashboard.tsx:88
buildReport                  37  <repo>/src/reports/report-builder.ts:114
syncExternalState            31  <repo>/src/sync/state-sync.ts:57
```

Use `analyze complexity` as an early risk map before touching large or tangled
functions. Use `deps` with the module import string recorded in code, not
necessarily the source file path. For the full CGC method catalog with
synthetic example outputs, read
[codegraphcontext-high-level-methods.md](./codegraphcontext-high-level-methods.md) beside this skill.

### Rules:
Use CGC first for structure and relationships.
Use direct source reads only to confirm specific anchors CGC surfaced.


## Intent: Onboarding And Source

Use Intent when the route, file, or anchor is known and their relationships (CGC) are
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

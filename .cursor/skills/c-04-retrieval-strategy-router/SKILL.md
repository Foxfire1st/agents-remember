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
  onboarding plus bounded source confirmation. Prefer the `read_ar_files` MCP tool for the
  paired onboarding + source read (mirrors how Semantics prefers GrepAI and Relationship
  prefers CodeGraphContext). The same call also carries the repository's **published intent**:
  the invariants a previous task already recorded about the paths you asked for, at their exact
  snapshot and without needing a task (see _Published Intent Before Planning_ below).

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

The `read_ar_files` MCP tool performs this entire paired-read recipe in one batch call: each
source path is returned with its deterministic sidecar, plus the repository overview and the
governing route-overview chain auto-attached.

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

### Rules:

- During the research phase (the lifecycle up to the build decision), read managed-repo
  source with `read_ar_files`, not the native read tool — one call returns each file paired
  with its onboarding plus the repository and governing route overviews (the recipe above,
  batched and observable).
- Native read is reserved as the edit precondition once building begins.
- Keep a running count of your `read_ar_files` calls and list them as research evidence,
  alongside Semantics (GrepAI) and Relationship (CGC) queries.

## Published Intent Before Planning

The first question about a route is often "what did this repository already intend here?". The
paired read answers it in the same call: `read_ar_files` resolves the repository's memory tree from
that repository's own coordination context -- no leaf, no enclosure and no task is needed -- and
reads the recorded intent about each requested path from that tree's knowledge files. The result
carries it as `published_intent`.

**Where the route reads.** Knowledge is text in the memory repository: cards and sidecars under
`onboarding/`, records under `knowledge/`. The read goes through the tree's **derived index**, a
cache the tools build from those files and rebuild when the tree changes; nothing writes it
directly, and there is no knowledge database to publish to. The curator's writer
(`agents-remember knowledge-ingest`, or `knowledge-bootstrap` for a taskless wave) writes the files,
and a read of the tree sees them.

Which memory root that is depends on scope, and there is no fallback between the two. With no
enclosure in scope -- the taskless planner this route exists for -- it is the canonical external
memory root. Inside a leaf enclosure the coordination context's memory root is the contract's memory
**worktree**, that task's own memory line, so the read sees the knowledge on the line it is standing
on. Neither root stands in for the other.

`state: "recorded"` means the tree was read, and the block names it exactly: `memoryTree`
(`memoryRoot`, the tree's identity and the state of its index), `datasetPath` (the derived index
file the read used, a cache and never an input you name), `schemaVersion`, `snapshot` (the logical
digest every page was verified against) and `sourceResolution` (`repositoryRoot` plus `codeTreeId`,
the tree recorded anchors were observed against). `seeds` holds one bounded entry per requested
path; each entry carries its `rows` (every row names its `kind` and the record's `id`, with the
authored statement and its essential conditions on a member row), its `counts`, `hasMore`, and
`continuation` with `continuationOperation` and `continuationView` whenever the page was bounded.
A bounded page is not a smaller scope: `counts.rowsTotal` is the whole selection,
`counts.rowsReturned` is what this page carries and `counts.rowsRemaining` is what is still ahead.

`state: "legacy-format"` means the memory tree is not converted (it holds no
`knowledge/layout.json`). Its onboarding is returned as it is, marked `legacy-format`, and no
knowledge section is read from it. That is an answer, not a failure: source and onboarding research
continue unchanged, and nothing is claimed to have been measured. The block's `detail` names how the
tree converts (the crossing sync for a line that descends from a converted line, or
`agents-remember knowledge-convert`).

Inside a `recorded` block, a seed that selects nothing is named rather than returned empty:
`refusalCode: "registration_absent"` means the tree records nothing about that path, and
`refusalCode: "selector_absent"` means the identity you named is not in this tree. Neither is filled
from another source, and a path no recorded anchor could carry is refused as a seed rather than
answered with an absence the read never observed.

**A path is read family-complete.** Each path's entry is the family-complete leaf read (`page.selectionPolicy:
"family-complete-leaf"`): its `rows` are, in order, the path's own invariants (a `member` row --
statement, applicability, conditions, exclusions, status, admission, `state` and every containing
family in `families` -- followed by its `realization` and `proof` entry rows, each with path,
locator, role or facet and `state`); then each containing family's `family_header` (title,
guarantee, `routes`, `memberCount`, `members`, `staleMembers`) followed by its remaining members
and their entries (a member already returned under an earlier family is a `member_reference` row);
then one `advertised_family` row per further family a member belongs to, listing in `via` the
members that reach it, which this read names but does not expand -- read it with `view: "family"`
if you need it. `counts` gives families, members, entries, distinct paths, invariants by state and
the rows returned and remaining, and `memoryTreeId` names the tree it was read from.
`knowledge_read` with `view: "source_context"` and `sourcePath` returns the same selection under
the same `manifestDigest`; that is the view to use when you need a file's whole invariant
neighbourhood outside `read_ar_files`. The `invariant` view of a tree names its invariant's
families in `families`.

**Route-chain families come last.** After that content, each path lists one compact
`chain_family` row per family routed at its directory or an ancestor (ID, title, guarantee,
`routes`, `memberCount`, `via`, `memberAtSeed`), so a new or unattributed file still sees the
families whose territory it is in; `routeChain` states the mechanical chain, or
`no_governing_family`. A path with no entries but a governing family returns these rows and
states `registration_absent`. To read a chain family whole, follow the row's `expand`:
`knowledge_read` with `view: "source_context"` and `familyRevisionId` set to the family ID and no
`sourcePath`. On a repeated `read_ar_files` in the same session an unchanged chain row may come
back as a short `served_earlier` row; `knowledge_read` always returns it in full.

**Follow a bounded page through `knowledge_read`.** The whole knowledge block is cut to one
shared threshold, stated as `threshold` (8,000 `tiktoken:o200k_base` tokens); each page's `page`
block states the walk's `total`, `returned` and `remaining` rows. A page with `continuation` set
continues through the mounted read (`continuationOperation: "knowledge_read"`): pass the **value
of** `memoryTree.memoryRoot` (not `datasetPath`, which names the derived index) as `memoryRoot`,
the `continuationView` as `view`, and the token as `continuation` -- nothing else. The tool takes
no repository identifier: the server supplies it. The token carries its seed, its ordering and the code tree page 1 resolved
anchors at, so naming a different `orderingInput` or `codeTreeId` is refused; name
`repositoryRoot` only when the code repository is not the mount's workspace. Repeat with each
response's `continuation` (and its `payload.continuationView`) until a response carries none.

The threshold bounds the whole block, not each path. Once the block is full, each remaining path
arrives as `state: "deferred"` with only its counts and a first `continuation`; when even those
would not fit, they arrive as one deferred entry listing its `seeds`, whose single continuation
walks them in turn; a tail too long for one continuation's queue is refused as
`seed_queue_exceeded` -- read those paths in smaller requests. Follow either the same way. Every
row arrives exactly once per path; a leaf page that continues a family starts with a
`family_header_reference` row (a view page names it in `page.headerReference`), and a single row
too large for the threshold on its own arrives alone, whole, flagged `oversized_row`. A
`knowledge_read` view page of a memory tree pages the same way. A refusal
`continuation_binding_mismatch` means the memory tree, the selection, the ordering or the code tree
differs from the walk's: restart from the seed, without a continuation. `continuation_unreadable`
means the token is not one of these, or belongs to another view.

**`knowledge_read` reads a memory tree, never a database.** Its `memoryRoot` names a converted
memory tree (the canonical root, or a leaf's memory worktree). An unconverted tree or a database file
is refused as `legacy-format`, and the refusal names how that memory converts. To read deeper by
identity, pass `memoryRoot` with `view: "invariant"` and `invariantRevisionId`, `view: "family"` and
`familyRevisionId`, or `view: "source_context"` and `sourcePath` for the neighbourhood of one file.
No route here substitutes another repository, the current working tree or a scratch dataset for the
memory tree the repository actually records.

## Route Index Semantics

`overview.index.json` is generated metadata: `sourceScope` governs the route;
`childRoutes` narrows; `coveredFiles` lists sidecars; `hotPath` gives cheap
summary/anchors; `fallback.governingOverview` names absent-sidecar fallback.

For a source path inside `sourceScope` but absent from `coveredFiles`, infer
that no generated file-level sidecar exists for that route. This only skips the
sidecar probe; it never forbids reading source.

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
- `Relationship` (retired — no relationship-query tool): an anchor is known, but callers, callees, dependencies,
  ownership, inheritance, impact paths, or neighboring code are unknown. Use native search over the configured code repo.
- `Intent`: an anchor/location + relationships are known, but hidden contracts, invariants,
  branch-valid truths, behavioral expectations, or code intent are unknown. Use
  onboarding plus bounded source confirmation. Prefer the `read_ar_files` MCP tool to name
  which cards govern the paths, then read the named files with your own tools.

A triage prompt starts with Intent: the route index plus `read_ar_files`
naming prove the contract and fix direction from source. Relationship and
Semantics are retired (no tools); their method catalogs beside this skill
remain syntax references for native search, not callable substrates.

## Semantics: GrepAI (retired)

GrepAI is not in the registered roster: there is no semantic-query tool to
call. For vague or fuzzy requests use Intent below (route index plus
`read_ar_files` naming, then native reads).

First request an MCP `context_packet(repo_id="<repoId>")`
when the MCP server is configured.

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

## Relationship: CodeGraphContext (retired)

CodeGraphContext is not in the registered roster: there is no relationship-query
tool to call. For anchor/symbol structure questions use Intent (route index plus
native reads) instead.

First request an MCP `context_packet(repo_id="<repoId>")`
when the MCP server is configured.

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
Use native search (`rg`) first for structure and relationships.
Use direct source reads to confirm specific anchors search surfaced.


## Intent: Onboarding And Source

The `read_ar_files` MCP tool names, in one batch call for up to twenty paths
(as strings), which knowledge governs each path: whether the file exists,
the `card` that governs it as a location in your memory worktree (or that it
has none), the card's `recordCounts` by state on the code you see
(`codeState` says what the states were measured on, `codeTree` is the tree
observed), and — only for records whose state asks you to act — an
`actionNeeded` entry with the text ID, kind, state and card heading of each.
`overviews` lists the governing overviews once for the call, as locations,
nearest first. `memoryState` says whether cards could be named at all. No
source text, card text or overview text is returned: open the named files
with your own tools. A call that passes the removed `refresh` parameter, or
a file as an object, is refused with `argument-removed`.

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

- During the research phase (the lifecycle up to the build decision), ask `read_ar_files`
  which cards govern the paths, then read the named files with your own tools — one call
  names the cards, states and overviews for up to twenty paths (the recipe above, batched
  and observable).
- Native read stays the research-phase norm all the way through: read the named files with your own
  tools whenever a claim is about file content, before and after the build decision — it is never
  reserved as a mere edit precondition.
- Keep a running count of your `read_ar_files` calls and list them as research evidence,
  alongside Semantics (GrepAI) and Relationship (CGC) queries.

## Recorded Intent Before Planning

The first question about a route is often "what did this repository already intend here?".
`read_ar_files` answers which cards govern the paths and in which state their records are;
it does not carry the records. To read the recorded intent itself, use `knowledge_read`,
which names records by ID, kind, state and location: pass `memoryRoot` with
`view: "invariant"` and `invariantRevisionId`, `view: "family"` and `familyRevisionId`, or
`view: "source_context"` and `sourcePath` for the neighbourhood of one file. Then open the
named record files with your own tools.

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

A `knowledge_read` answer names records; it does not carry them. A row holds the
record's text ID, its kind, its computed state, and its location: the file in
your memory worktree and the heading or line. What no file holds stays in the
answer in short form: the currentness of a record, the state of a link, a
derived status. The text of a record is not in the answer: open the named
file with your own tools. Paging stays: a page is within the bound, and a
view whose result is a list to work through writes the list as a report and
answers with the counts, the first entries and the report's location.

`state: "legacy-format"` means the memory tree is not converted (it holds no
`knowledge/layout.json`). The refusal names how the tree converts (the
crossing sync for a line that descends from a converted line, or
`agents-remember knowledge-convert`).

A seed that selects nothing is named rather than returned empty:
`refusalCode: "registration_absent"` means the tree records nothing about that path, and
`refusalCode: "selector_absent"` means the identity you named is not in this tree. Neither is filled
from another source.

**A path is read family-complete.** `knowledge_read` with `view: "source_context"` and
`sourcePath` returns the family-complete leaf read: the path's own invariants named by ID,
kind, state and location, then each containing family's header and its remaining members
named the same way, then one compact `chain_family` row per family routed at the path's
directory or an ancestor. `counts` gives families, members, invariants by state and the rows
returned and remaining. The `invariant` view of a tree names its invariant's
families in `families`. To read a chain family whole, follow the row's location:
`knowledge_read` with `view: "source_context"` and `familyRevisionId` set to the family ID and no
`sourcePath`.

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
too large for the threshold on its own arrives alone, whole (unsplit, still named -- never with
record text), flagged `oversized_row`. A
`knowledge_read` view page of a memory tree pages the same way. Deferred entries and pages name
rows (text ID, kind, state, location) and counts only -- never whole rows or record text, which
stay behind the answer's log and open through the named files. A refusal
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

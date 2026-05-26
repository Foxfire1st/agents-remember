# GrepAI High-Leverage Usage

This reference explains how to use GrepAI after C-04 selects the `Semantics`
substrate. Examples are synthetic and show response shapes only. Do not copy
private repository names, symbols, paths, snippets, or search results into
reusable skill examples.

GrepAI is the fuzzy discovery tool for memory and onboarding. Use it when the
request names a concept, behavior, route, invariant, error, or domain phrase
but the relevant memory project, overview, sidecar, or source path is not known
yet. Use CGC for structural code relationships once a symbol or file anchor is
known.

## Managed Invocation

Request GrepAI through the Agents Remember MCP provider tools. The MCP uses the
configured Docker runner and provider-owned environment so workspace config,
logs, state, and cache stay under `providers/runners/grepai/` instead of using a
global user install.

```text
grepai_search(query="<query>", all_repos=true, limit=5, output_format="json", dry_run=false)
grepai_search(query="<query>", repo_ids=["<repoId>", "<repoId>"], limit=5, output_format="json", dry_run=false)
grepai_trace(trace_action="callers", symbol="<symbol>", output_format="json", dry_run=false)
```

The managed workspace name comes from the MCP-generated GrepAI lifecycle
settings. `repo_ids` accepts only repositories configured through MCP.

## Choosing A Command

| Question | Command Pattern |
| --- | --- |
| Which memory project or route talks about this vague concept? | `grepai_search(query="<query>", all_repos=true, limit=<n>, output_format="json", dry_run=false)` |
| I need machine-readable anchors. | `grepai_search(query="<query>", all_repos=true, limit=<n>, output_format="json", dry_run=false)` |
| I know the target memory project. | Add `repo_ids=["<repoId>"]`; repeat repo ids for a small configured set. |
| I know the likely onboarding route or folder. | Search the configured repo, then open the selected paths with source/onboarding reads. |
| I need a symbol neighborhood inside a GrepAI-indexed source project. | `grepai_trace(trace_action="callers"|"callees"|"graph", symbol="<symbol>", output_format="json", dry_run=false)`; prefer CGC when available for code relationships. |
| I need provider coverage/health, not retrieval. | `provider_status()` |

## Cross-Memory Semantic Routing

Use broad workspace search when the task is vague and the missing packet is
"where should I look first?"

```text
grepai_search(
  query="where is the retry backoff behavior documented",
  all_repos=true,
  limit=5,
  output_format="json",
  dry_run=false,
)
```

Synthetic JSON shape:

```json
{
  "results": [
    {"project": "<memoryProject>", "path": "onboarding/src/jobs/retry-policy.ts.md", "startLine": 18, "endLine": 34, "score": 0.84},
    {"project": "<memoryProject>", "path": "onboarding/src/http/client.ts.md", "startLine": 41, "endLine": 59, "score": 0.78},
    {"project": "<memoryProject>", "path": "onboarding/overview.md", "startLine": 72, "endLine": 81, "score": 0.73}
  ]
}
```

Use the result as a route hint. Open the selected overview, sidecar, or source
file next; do not treat the semantic result as proof.

## Scoped Project Search

Use project scoping after C-08 or earlier discovery tells you which memory
project is relevant. This avoids cross-repo noise and keeps the answer small.

```text
grepai_search(
  query="validation rules for imported records",
  repo_ids=["<repoId>", "<repoId>"],
  limit=5,
  output_format="json",
  dry_run=false,
)
```

Synthetic JSON shape:

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

Use JSON when an agent needs stable anchors. If the matching reason is unclear,
keep the limit small and inspect the selected onboarding/source.

## Route-Focused Snippet Search

Use scoped search when you already know the likely repo and need the most
relevant sidecar or overview inside it. If a returned path looks promising,
open that onboarding/source file directly.

```text
grepai_search(
  query="how rejected records are surfaced to operators",
  repo_ids=["<repoId>"],
  limit=3,
  output_format="json",
  dry_run=false,
)
```

Synthetic full JSON shape:

```json
{
  "query": "how rejected records are surfaced to operators",
  "results": [
    {
      "project": "<memoryProject>",
      "path": "onboarding/src/import/error-summary.ts.md",
      "startLine": 14,
      "endLine": 33,
      "score": 0.82,
      "content": "The synthetic sidecar explains where validation failures are summarized..."
    }
  ]
}
```

Use this after route discovery, not as the first query against the whole memory
workspace.

## Trace Commands

GrepAI exposes trace commands for callers, callees, and local call graphs. In
Agents Remember, CGC is the preferred relationship substrate for code when it
is configured. Use GrepAI trace only when CGC is unavailable or when the
GrepAI-indexed project is the only available source of symbol relationships.

```text
grepai_trace(
  trace_action="graph",
  symbol="processImportedRecord",
  repo_ids=["<repoId>"],
  depth=2,
  output_format="json",
  dry_run=false,
)
```

Synthetic output shape:

```json
{
  "query": "processImportedRecord",
  "mode": "fast",
  "nodes": [
    {"name": "processImportedRecord", "path": "<repo>/src/import/processor.ts", "line": 30},
    {"name": "validateImportedRecord", "path": "<repo>/src/import/validator.ts", "line": 12}
  ],
  "edges": [
    {"from": "processImportedRecord", "to": "validateImportedRecord", "type": "calls"}
  ]
}
```

Treat trace results as discovery. Confirm contracts, dynamic entry points, and
edit direction with source.

## Coverage And Health

Use status commands when search results look stale or missing.

```text
provider_status()
```

Synthetic output shape:

```text
Workspace: <workspace>
Projects indexed: 4
Watcher: running
Last update: recent
```

## Practical Rules

- Start broad with `all_repos=true` when the route is unknown.
- Use `output_format="json"` when an API caller needs stable anchors.
- Keep the first MCP search small. If snippets are not enough to choose, inspect
  the selected onboarding/source instead of widening the search reflexively.
- Add `repo_ids` as soon as the relevant configured memory root is known.
- Use source/onboarding reads after route discovery has narrowed the search;
  the MCP GrepAI tools do not currently expose path scoping.
- Keep `limit` small, usually 3 to 8.
- Use GrepAI output as semantic discovery, not proof. Confirm with onboarding
  and bounded source reads before answering or editing.
- Do not use a global GrepAI binary/config path in reusable instructions; use
  MCP provider tools so the Docker runner container and provider-owned
  environment are selected by server settings.

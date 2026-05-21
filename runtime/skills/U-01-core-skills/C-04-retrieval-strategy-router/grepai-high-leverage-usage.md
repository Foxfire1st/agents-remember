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

Use the runtime-owned GrepAI binary and provider-owned environment. This keeps
workspace config, logs, state, and cache under `providers/grepai/` instead of
using a global user install. On Windows or in API callers, set the same
environment variables programmatically rather than relying on POSIX `env`
syntax.

```bash
cd <coordination_root>/providers/grepai
env \
  HOME=<coordination_root>/providers/grepai/home \
  XDG_STATE_HOME=<coordination_root>/providers/grepai/state/xdg \
  XDG_CACHE_HOME=<coordination_root>/providers/grepai/cache/xdg \
  <coordination_root>/providers/_bin/grepai <command>
```

The managed workspace name normally comes from
`contextProviders.providers.grepai-memory.workspace`; examples use
`<workspace>`.

## Choosing A Command

| Question | Command Pattern |
| --- | --- |
| Which memory project or route talks about this vague concept? | `search "<query>" --workspace <workspace> --toon --compact --limit <n>` |
| I need compact machine-readable anchors only. | `search "<query>" --workspace <workspace> --json --compact --limit <n>` |
| I need a snippet to understand why the result matched. | `search "<query>" --workspace <workspace> --json --limit <n>` |
| I know the target memory project. | Add `--project <projectId>`; repeat it for a small project set. |
| I know the likely onboarding route or folder. | Add `--path <path-prefix>`. |
| I need a symbol neighborhood inside a GrepAI-indexed source project. | `trace callers`, `trace callees`, or `trace graph`; prefer CGC when available for code relationships. |
| I need provider coverage/health, not retrieval. | `workspace status <workspace>`, `status --no-ui`, or lifecycle `grepai status`. |
| I need to estimate token savings. | `stats --json` or `stats --history --json`. |

## Cross-Memory Semantic Routing

Use broad workspace search when the task is vague and the missing packet is
"where should I look first?"

```bash
cd <coordination_root>/providers/grepai
env \
  HOME=<coordination_root>/providers/grepai/home \
  XDG_STATE_HOME=<coordination_root>/providers/grepai/state/xdg \
  XDG_CACHE_HOME=<coordination_root>/providers/grepai/cache/xdg \
  <coordination_root>/providers/_bin/grepai search \
  "where is the retry backoff behavior documented" \
  --workspace <workspace> \
  --toon --compact --limit 5
```

Synthetic output shape:

```text
results[3]{project,path,lines,score}:
  1 | <memoryProject> | onboarding/src/jobs/retry-policy.ts.md | 18-34 | 0.84
  2 | <memoryProject> | onboarding/src/http/client.ts.md | 41-59 | 0.78
  3 | <memoryProject> | onboarding/overview.md | 72-81 | 0.73
```

Use the result as a route hint. Open the selected overview, sidecar, or source
file next; do not treat the semantic result as proof.

## Scoped Project Search

Use project scoping after C-08 or earlier discovery tells you which memory
project is relevant. This avoids cross-repo noise and keeps the answer small.

```bash
cd <coordination_root>/providers/grepai
env \
  HOME=<coordination_root>/providers/grepai/home \
  XDG_STATE_HOME=<coordination_root>/providers/grepai/state/xdg \
  XDG_CACHE_HOME=<coordination_root>/providers/grepai/cache/xdg \
  <coordination_root>/providers/_bin/grepai search \
  "validation rules for imported records" \
  --workspace <workspace> \
  --project <memoryProject> \
  --json --compact --limit 5
```

Synthetic compact JSON shape:

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

Use compact JSON when an agent only needs anchors. If the matching reason is
unclear, rerun without `--compact` for a small limit and inspect the snippet.

## Route-Scoped Snippet Search

Use path scoping when you already know the likely route and need the most
relevant sidecar or overview inside it.

```bash
cd <coordination_root>/providers/grepai
env \
  HOME=<coordination_root>/providers/grepai/home \
  XDG_STATE_HOME=<coordination_root>/providers/grepai/state/xdg \
  XDG_CACHE_HOME=<coordination_root>/providers/grepai/cache/xdg \
  <coordination_root>/providers/_bin/grepai search \
  "how rejected records are surfaced to operators" \
  --workspace <workspace> \
  --project <memoryProject> \
  --path onboarding/src/import \
  --json --limit 3
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

```bash
cd <coordination_root>/providers/grepai
env \
  HOME=<coordination_root>/providers/grepai/home \
  XDG_STATE_HOME=<coordination_root>/providers/grepai/state/xdg \
  XDG_CACHE_HOME=<coordination_root>/providers/grepai/cache/xdg \
  <coordination_root>/providers/_bin/grepai trace graph \
  "processImportedRecord" \
  --workspace <workspace> \
  --project <projectId> \
  --depth 2 --json
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

```bash
cd <coordination_root>/providers/grepai
env \
  HOME=<coordination_root>/providers/grepai/home \
  XDG_STATE_HOME=<coordination_root>/providers/grepai/state/xdg \
  XDG_CACHE_HOME=<coordination_root>/providers/grepai/cache/xdg \
  <coordination_root>/providers/_bin/grepai workspace status <workspace>
```

Synthetic output shape:

```text
Workspace: <workspace>
Projects indexed: 4
Watcher: running
Last update: recent
```

For lifecycle-managed health, prefer:

```bash
python <coordination_root>/scripts/provider-lifecycle.py grepai \
  --coordination-root <coordination_root> \
  status --json
```

## Practical Rules

- Start broad with `--toon --compact` when the route is unknown.
- Switch to `--json --compact` when an API caller needs stable anchors.
- Rerun without `--compact` only when snippets are needed to choose between
  close candidates.
- Add `--project` as soon as the relevant memory root is known.
- Add `--path` only after route discovery has already narrowed the search.
- Keep `--limit` small, usually 3 to 8.
- Use GrepAI output as semantic discovery, not proof. Confirm with onboarding
  and bounded source reads before answering or editing.
- Do not use a global GrepAI binary/config path in reusable instructions; use
  the runtime-owned binary and provider-owned environment.

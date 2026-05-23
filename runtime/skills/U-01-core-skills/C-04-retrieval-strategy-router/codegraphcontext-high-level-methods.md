# CodeGraphContext High-Level Methods

This reference explains the typed CodeGraphContext tools an agent can request
after the `Relationship` substrate is selected. Examples are synthetic and show
response shapes only. Do not copy private repository names, symbols, or paths
into training-style examples.

Request CGC through the Agents Remember MCP provider tools:

```text
cgc_symbol_search(repo_id="<repoId>", name="<symbol>", dry_run=false)
cgc_callers(repo_id="<repoId>", function="<function>", file="<optional path>", dry_run=false)
cgc_callees(repo_id="<repoId>", function="<function>", dry_run=false)
cgc_dependencies(repo_id="<repoId>", module="<module>", dry_run=false)
cgc_complexity(repo_id="<repoId>", function="<optional function>", dry_run=false)
cgc_visualize(repo_id="<repoId>", port=8000, dry_run=false)
```

Provider authority comes from MCP settings. It does not come from coordinator
`system/settings.json`. The MCP intentionally exposes typed CGC operations
instead of a generic native CLI pass-through.

CGC is not just a locator. `cgc_symbol_search` is a useful smoke test, but the
relationship tools expose call edges, reverse call edges, import neighborhoods,
and complexity signals.

## Choosing A Method

| Question | MCP Tool | Native CGC Operation |
| --- | --- | --- |
| Where is this symbol? | `cgc_symbol_search` | `find name <name>` |
| What does this function/method call? | `cgc_callees` | `analyze calls <function>` |
| Who calls this function/method? | `cgc_callers` | `analyze callers <function>` |
| Which files import this module string? | `cgc_dependencies` | `analyze dependencies <module>` |
| Which functions are most complex? | `cgc_complexity` | `analyze complexity [function]` |
| Do I need the interactive graph view? | `cgc_visualize` | `visualize` |

CGC has additional native operations, but they are not public MCP tools right
now. Do not ask for removed generic `cgc_query` behavior or arbitrary native
argument lists. If an uncovered CGC operation is needed for real work, ask the
developer to add a typed MCP tool for that operation.

## Symbol Search

Use `cgc_symbol_search` when the anchor name is known and the missing packet is
where the symbol exists.

```text
cgc_symbol_search(
  repo_id="<repoId>",
  name="dispatchCommand",
  dry_run=false,
)
```

Synthetic output shape:

```text
Found 2 result(s) for name 'dispatchCommand':
Function dispatchCommand <repo>/src/app/command-router.ts:77
Function dispatchCommand <repo>/src/tests/command-router.test.ts:14
```

Use this to locate candidate anchors, then use a relationship tool or source
read before editing.

## Callees

Use `cgc_callees` when the anchor function is known and the missing packet is
what it invokes next.

```text
cgc_callees(
  repo_id="<repoId>",
  function="handleRequest",
  dry_run=false,
)
```

Synthetic output shape:

```text
Function 'handleRequest' calls:
Called Function      Location                                  Type
validateRequest      <repo>/src/http/validation.ts:42          Project
loadSession          <repo>/src/auth/session.ts:18             Project
dispatchCommand      <repo>/src/app/command-router.ts:77       Project
serializeResponse    <repo>/src/http/response.ts:31            Project

Total: 4 function(s)
```

Use this to jump from an entry point into immediate downstream behavior.
Confirm any selected target with source before editing.

## Callers

Use `cgc_callers` when the anchor function is known and the missing packet is
who can reach it. Pass `file` when the function name is common, overloaded, or
implemented in many places.

```text
cgc_callers(
  repo_id="<repoId>",
  function="dispatchCommand",
  file="<repo>/src/app/command-router.ts",
  dry_run=false,
)
```

Synthetic output shape:

```text
Functions that call 'dispatchCommand':
Caller Function       Location                                  Call Type
handleRequest         <repo>/src/http/request-handler.ts:24      Project
runScheduledJob       <repo>/src/jobs/scheduler.ts:63            Project
processMessage        <repo>/src/queue/consumer.ts:91            Project

Total: 3 caller(s)
```

Use this for blast-radius checks, entry-point discovery, and regression-risk
triage.

## Dependencies

Use `cgc_dependencies` to ask which files import a module string. It expects
the import name recorded by CGC, not necessarily a file path. If a file-path
query returns no data, inspect a few source imports and retry with the module
string.

```text
cgc_dependencies(
  repo_id="<repoId>",
  module="../shared/validation",
  dry_run=false,
)
```

Synthetic output shape:

```text
Files that import '../shared/validation':
<repo>/src/http/request-handler.ts:3
<repo>/src/jobs/job-runner.ts:8
<repo>/src/queue/consumer.ts:5
<repo>/src/tests/request-handler.test.ts:11
```

Use this for module impact checks and import-neighborhood discovery.

## Complexity

Use `cgc_complexity` to identify large or risky functions before changing a
route. Pass `function` for a specific function or omit it for the broader
complexity report.

```text
cgc_complexity(
  repo_id="<repoId>",
  function="renderDashboard",
  dry_run=false,
)
```

Synthetic output shape:

```text
Function             Complexity  Location
renderDashboard              42  <repo>/src/ui/dashboard.tsx:88
```

Use this to decide where source confirmation needs extra care.

## Practical Rules

- Use `cgc_symbol_search` only to locate candidate symbols. Use typed
  relationship tools to understand connections.
- Pass `file` to `cgc_callers` when a symbol name is common, overloaded, or
  implemented in many places.
- For impact and regression checks, prefer `cgc_callees`, `cgc_callers`, and
  `cgc_dependencies`.
- For risk triage, prefer `cgc_complexity`.
- Treat CGC output as discovery, not proof. Use bounded source reads to confirm
  any contract or edit direction before changing code.

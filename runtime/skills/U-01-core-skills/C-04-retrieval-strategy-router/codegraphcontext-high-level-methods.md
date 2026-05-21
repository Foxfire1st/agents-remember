# CodeGraphContext High-Level Methods

This reference explains the high-level `cgc analyze ...` commands an agent can
use after the `Relationship` substrate is selected. Examples are synthetic and
show response shapes only. Do not copy private repository names, symbols, or
paths into training-style examples.

Run CGC through the managed provider wrapper:

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- <cgc command>
```

Add `--from-settings <coordination_root>/system/settings.json` when the
coordinator settings are not discoverable from the runtime root.

CGC is not just a locator. `find name <anchor>` is a useful smoke test, but the
high-level methods below expose call edges, reverse call edges, import
neighborhoods, inheritance, complexity, unused-code candidates, method
implementations, and variable occurrences.

## Choosing A Method

| Question | Method |
| --- | --- |
| What does this function/method call? | `analyze calls <function> --file <path>` |
| Who calls this function/method? | `analyze callers <function> --file <path>` |
| Is there a path from one function to another? | `analyze chain <from> <to> --from-file <path> --to-file <path> --depth <n>` |
| Which files import this module string? | `analyze deps <module-name> --no-external` |
| What does this class inherit from, and what methods are attached to it? | `analyze tree <class> --file <path>` |
| Which functions are most complex? | `analyze complexity --limit <n>` |
| Which functions look unused? | `analyze dead-code` |
| Which classes implement this method name? | `analyze overrides <method>` |
| Where does this variable name appear? | `analyze variable <name> --file <path>` |
| Are Kotlin call edges ambiguous? | `analyze kotlin-call-audit --limit <n>` |

## Calls

Use `calls` when the anchor function is known and the missing packet is what it
invokes next.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze calls handleRequest \
  --file <repo>/src/http/request-handler.ts
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

Use this to jump from an entry point into the immediate downstream behavior.
Confirm any selected target with source before editing.

## Callers

Use `callers` when the anchor function is known and the missing packet is who
can reach it.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze callers dispatchCommand \
  --file <repo>/src/app/command-router.ts
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

## Chain

Use `chain` to prove whether one known function can reach another through call
edges.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze chain handleRequest saveAuditEvent \
  --from-file <repo>/src/http/request-handler.ts \
  --to-file <repo>/src/audit/audit-store.ts \
  --depth 3
```

Synthetic output shape:

```text
Call Chain #1 (length: 2):
handleRequest (<repo>/src/http/request-handler.ts:20)
  calls at line 27
  dispatchCommand (<repo>/src/app/command-router.ts:77)
    calls at line 83
    saveAuditEvent (<repo>/src/audit/audit-store.ts:14)
```

Use this when a fix might affect an indirect path or when a bug report names
two separate symbols.

## Dependencies

Use `deps` to ask which files import a module string. It expects the import name
recorded by CGC, not necessarily a file path. If a file-path query returns no
data, inspect a few `IMPORTS` edges or source imports and retry with the module
string.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze deps ../shared/validation --no-external
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

## Tree

Use `tree` for class inheritance and attached methods.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze tree CachedRepository \
  --file <repo>/src/storage/cached-repository.ts
```

Synthetic output shape:

```text
Class Hierarchy for 'CachedRepository':

Parents (inherits from):
  BaseRepository (<repo>/src/storage/base-repository.ts:12)

Children (classes that inherit from this):
  UserRepository (<repo>/src/users/user-repository.ts:18)

Methods (4):
  get(None)
  set(None)
  invalidate(None)
  refresh(None)
```

Use this before modifying class contracts, inherited behavior, or polymorphic
call sites.

## Complexity

Use `complexity` to identify large or risky functions before changing a route.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze complexity --limit 5
```

Synthetic output shape:

```text
Most Complex Functions (threshold: 10):
Function             Complexity  Location
renderDashboard              42  <repo>/src/ui/dashboard.tsx:88
buildReport                  37  <repo>/src/reports/report-builder.ts:114
syncExternalState            31  <repo>/src/sync/state-sync.ts:57
applyPolicyRules             24  <repo>/src/policy/rule-engine.ts:33
normalizePayload             18  <repo>/src/http/payload.ts:21

5 function(s) exceed threshold
```

Use this to decide where source confirmation needs extra care.

## Dead Code

Use `dead-code` for candidates that have no incoming CGC call edges. Treat the
result as a prompt for source confirmation, because dynamic callbacks,
framework entry points, event handlers, and reflection can look unused.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze dead-code
```

Synthetic output shape:

```text
Potentially Unused Functions:
legacyTransform       <repo>/src/legacy/transform.ts:19
debugRender           <repo>/src/ui/debug-panel.ts:44
handleResize          <repo>/src/ui/layout.ts:72
cleanupTempFiles      <repo>/src/jobs/cleanup.ts:28

Total: 4 function(s)
Note: These functions might be unused, but could be entry points, callbacks, or called dynamically
```

Use this for cleanup investigation, not as deletion proof.

## Overrides

Use `overrides` to find all class implementations of a method name.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze overrides serialize
```

Synthetic output shape:

```text
Found 4 implementation(s) of 'serialize':
Class                 Function    Location
JsonSerializer        serialize   <repo>/src/serialization/json.ts:15
XmlSerializer         serialize   <repo>/src/serialization/xml.ts:18
EventSerializer       serialize   <repo>/src/events/event-serializer.ts:22
ReportSerializer      serialize   <repo>/src/reports/report-serializer.ts:41
```

Use this before changing method contracts or shared serialization behavior.

## Variable

Use `variable` to find occurrences of a variable name, optionally limited to a
file.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze variable requestId \
  --file <repo>/src/http/request-handler.ts
```

Synthetic output shape:

```text
Variable 'requestId' Usage Analysis:

MODULE Scope (3 instance(s)):
Location
<repo>/src/http/request-handler.ts:12
<repo>/src/http/request-handler.ts:26
<repo>/src/http/request-handler.ts:41

Total: 3 instance(s) across 1 scope type(s)
```

Use this for local data-flow orientation, then read the source around each
selected occurrence.

## Kotlin Call Audit

Use `kotlin-call-audit` only for repositories with Kotlin code. It reports
multi-target callsite ambiguity in Kotlin call edges.

```bash
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  run -- analyze kotlin-call-audit --limit 10
```

Synthetic output shape for a non-Kotlin repo:

```text
Kotlin CALLS ambiguity audit
Metric                    Value
Kotlin fn->fn CALLS edges 0
Ambiguous groups          0
Ambiguous edges           0

No ambiguous Kotlin call groups found.
```

If the repository has no Kotlin nodes, this method is a coverage check rather
than a relationship-retrieval tool.

## Practical Rules

- Use `find name <anchor>` only to locate a candidate symbol. Use `analyze ...`
  to understand relationships.
- Pass `--file` when a symbol name is common, overloaded, or implemented in many
  places.
- For impact and regression checks, prefer `calls`, `callers`, `chain`, and
  `deps`.
- For risk triage, prefer `complexity`.
- For object contracts, prefer `tree` and `overrides`.
- Treat CGC output as discovery, not proof. Use bounded source reads to confirm
  any contract or edit direction before changing code.

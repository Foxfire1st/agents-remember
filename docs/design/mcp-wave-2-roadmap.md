# MCP Transport, Caching, and Agentic UI Roadmap

This design note describes the planned second-wave architectural evolution of the Agents Remember
Model Context Protocol (MCP) server, its local caching and reload model, the handling of
long-running tool calls under fixed-timeout harnesses, and the adoption criteria for emerging
agentic user-interface standards. None of the designs below are implemented yet; each section
states what current plumbing it would replace and the concrete conditions that justify starting
the work.

## 1. Shared Warm Local Streamable HTTP Server

### What it replaces

Today every launched role seat starts its own dedicated stdio MCP server process (plus any
per-seat harness forwarder plumbing). Each seat pays Python interpreter startup, package import,
and settings load independently, and serializes concurrent seat launches behind a process-level
role-start lock while each server writes its own per-process inventory record under
`runtime/servers/` and `runtime/server-bindings/`.

### Target design

Move the local serving layer to a single warm local Streamable HTTP server implementing the MCP
2026-07-28 stateless transport profile, bound to loopback (`127.0.0.1`) alongside the existing
local dashboard daemon:

- Role identity and tool-scope narrowing move from per-process environment variables to per-request
  seat tokens or signed launch headers validated against the coordination root's seat registry.
- A lightweight stdio shim remains available for harnesses that only speak stdio, forwarding
  JSON-RPC frames to the warm loopback daemon instead of importing the full Python runtime per
  seat.
- Role-start lock contention drops because launching a new seat registers a token and returns
  without waiting for cold Python process initialization.

### Concrete triggers

Start this work when any of the following hold:

- Every primary harness in the supported fleet ships native Streamable HTTP client support
  without requiring a third-party stdio bridge for basic tool calls.
- Live telemetry shows cold-start or role-start lock wait latency exceeding five seconds at the
  ninetieth percentile, or concurrent multi-seat runs regularly exceeding twenty live stdio
  server processes on one workstation.
- First-wave compact answers, schema trimming, and progress notifications are already landed and
  measured.

## 2. Warm In-Process Caches for Knowledge, Code Index, and Explainer Pages

### What it replaces

Each short-lived stdio tool server currently rebuilds or re-opens the SQLite knowledge index,
re-reads repository configuration and tree metadata, and serves explainer assets without sharing
parsed state across sibling role seats.

### Target design

Once a shared warm local server holds long-lived process state, introduce explicitly bounded,
evictable in-process caches keyed by immutable Git tree digests and content hashes:

- **Knowledge and code index cache:** keep parsed SQLite index handles and tree-keyed anchor
  observations warm across reads from sibling seats, invalidating immediately whenever a tree
  digest or working-tree capture key changes.
- **Explainer page cache:** retain rendered page manifests and static presentation assets in a
  bounded LRU cache shared with the local canvas host rather than re-reading disk artifacts on
  every seat request.
- Every cache must declare an explicit maximum entry/byte cap and deterministic eviction policy so
  memory footprint stays bounded under long-running multi-repo sessions.

### Concrete triggers

Start this work when:

- The shared warm local Streamable HTTP server is in place (so caches actually live across seat
  calls rather than dying with each stdio process).
- Measured `knowledge_read`, `knowledge_integrity_check`, or explainer load latency exceeds two
  seconds at the ninetieth percentile on real repositories despite the SQLite index being current.

## 3. No-Downtime Reloads and Fleet Observability

### What it replaces

Currently, upgrading the installed package leaves already-running stdio MCP servers on the old
Python build. Stale servers detect digest drift, annotate answers with `buildCurrentness`, and
refuse publication calls with `server-build-stale` until the developer or harness manually
restarts or reconnects each seat.

### Target design

- **Graceful reload for the warm server:** supervise the local HTTP server behind a thin socket
  owner (or graceful drain handoff) so a new package install spawns a fresh worker on the new
  Python build, drains in-flight tool calls cleanly, and switches new requests without killing
  active role sessions or stranding half-finished commits.
- **Fleet observability:** expose structured OpenTelemetry traces, per-tool latency histograms,
  overflow-guard rates, and active-seat build currentness directly in the local dashboard so
  regressions in wire size, lock wait time, or stale builds are visible without scanning raw daily
  JSONL logs by hand.

### Concrete triggers

Start this work when:

- Package upgrades during active multi-seat sprints routinely trigger `server-build-stale`
  refusals that interrupt in-progress seats and require manual reconnection.
- Telemetry analysis requires recurring manual log scraping across multiple days of JSONL files
  to diagnose latency spikes, lock contention, or error rates.

## 4. Honest Disposition of Fixed-Timeout Harness Long Calls

### The constraint

Some agent harnesses enforce a hard, non-negotiable client-side tool-call timeout (for example
three hundred seconds) regardless of whether the server emits MCP progress notifications. At the
same time, mutating Git and publication operations (`worktree_sync`, `worktree_closeout_apply`,
`worktree_integrate`, `worktree_checkpoint_landing`) must execute sequentially under the
process-global write lock and run repository pre-commit checks that can legitimately take several
minutes on large repositories.

Automatic background promotion of mutating calls onto detached threads is explicitly rejected:
moving a mutating call off the serialized write lock breaks first-in-first-out write ordering,
complicates cancellation draining, and risks leaving ambiguous partial state.

### Target design

- Keep mutating operations strictly serialized under the write lock and preserve accurate timeout
  and cancellation logging.
- Where first-wave MCP progress notifications reset client timers (harnesses that honor
  `progressToken`), rely on native progress streaming.
- Where a harness ignores progress notifications and enforces a fixed ceiling below three hundred
  seconds, treat long-running repository gates as an explicit job-oriented workflow only when
  measured need justifies it: a dedicated, explicit start-and-poll operation family (following the
  existing `memory_quality_check` start/poll pattern) rather than silent auto-promotion of
  ordinary mutating tools.

### Concrete triggers

Start explicit start-and-poll job wrappers for additional mutating tools only when:

- Live telemetry after MCP progress streaming shows a supported harness still timing out healthy
  `worktree_sync` or closeout calls because that harness ignores progress notifications and cannot
  raise its client timeout above repository gate duration.

## 5. Agentic UI Roadmap: MCP Apps, A2UI, and WebMCP

Agents Remember already ships a local dashboard and a standalone canvas host for review and
explanation pages. Emerging protocol standards offer standardized ways to embed interactive views
directly inside agent hosts rather than relying solely on a separate browser tab. Each standard
serves a distinct surface and has its own adoption trigger.

### MCP Apps (`io.modelcontextprotocol/ui`)

- **Role:** Standardized mechanism for an MCP tool to return or reference an interactive HTML/UI
  resource rendered inside a sandboxed host iframe, communicating back through the MCP session.
- **Potential fit:** Embedding interactive review verdicts, knowledge diff inspectors, and
  explanation pages directly inside chat harnesses that support `io.modelcontextprotocol/ui`,
  reducing context switching to the standalone dashboard.
- **Concrete adoption triggers:**
  - At least two primary supported harnesses (for example Claude Code, Codex, or Cursor) ship
    stable, non-experimental rendering support for `io.modelcontextprotocol/ui`.
  - The host sandbox supports our offline presentation kit without breaking strict Content
    Security Policy or requiring external network fetches.

### A2UI (Agent-to-User Interface)

- **Role:** Declarative JSON component-tree protocol allowing agents to stream structured,
  host-styled UI widgets (forms, approval cards, status tables) that the host renders natively
  without arbitrary script execution.
- **Potential fit:** Native approval prompts (commit approval, developer decision gates, worktree
  conflict triage) and compact status cards inside multi-agent hosts where an iframe is too heavy.
- **Concrete adoption triggers:**
  - Supported agent hosts adopt a stable A2UI schema version for interactive approval and form
    responses.
  - Approval round-trips currently handled through plain-text chat questions become a measurable
    bottleneck in operator workflow speed or error rate.

### WebMCP

- **Role:** Browser-side protocol exposing structured web-page actions and state as MCP tools to
  in-browser or connected agents.
- **Potential fit:** Exposing the Agents Remember local dashboard's own navigation, review triage,
  and cockpit inspection controls as typed WebMCP tools so browser-connected agents can inspect or
  drive the dashboard deterministically instead of relying on brittle DOM scraping.
- **Concrete adoption triggers:**
  - WebMCP reaches stable specification and browser/runtime support in Chromium-based environments
    used by our workstation and test infrastructure.
  - Agent workflows routinely need to read or drive live dashboard views in ways that existing
    server-side MCP tools cannot satisfy directly.

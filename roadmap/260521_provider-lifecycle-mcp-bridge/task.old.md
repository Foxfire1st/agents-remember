# Task: Provider Lifecycle MCP Bridge

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Config | Skill | Other
**Created:** 2026-05-21T18:03

---

## Objective

Build a minimal MCP bridge for the Agents Remember provider lifecycle so agents can query CGC and GrepAI reliably across harnesses without per-harness allowlist setup, while preserving provider-native terminal output instead of forcing JSON normalization.

---

## Requirements

- The MCP must wrap the existing provider lifecycle entry point instead of reimplementing CGC or GrepAI behavior.
- The implementation target is `agents-remember-md`; the runtime command being wrapped is `ar-coordination/scripts/provider-lifecycle.py`.
- The MCP must preserve native CGC/GrepAI stdout, including CGC tables, because JSON-normalized CGC output has proven too lossy for agent investigation.
- The MCP must run host-side so provider commands can reach local graph/search backends, process state, watcher status, provider runtime directories, and host-local network endpoints.
- The MCP tool surface should be as small as possible: one primary provider lifecycle command tool unless implementation evidence proves a second tool is necessary.
- The MCP must not become arbitrary shell execution. It may accept provider-lifecycle arguments or stdin text, but the executable path and coordination root should be server-owned.
- Raw stdout, stderr, command text, exit code, and duration should be persisted under `ar-coordination/temp/provider-mcp/runs/<run-id>/`.
- Tool responses should inline normal-sized transcripts and return file paths plus head/tail excerpts when output is too large for a reliable MCP response.
- Agent guidance must say to use this MCP for CGC/GrepAI and not fall back to source-only tracing until the MCP attempt fails and the failure is reported.
- Existing direct `provider-lifecycle.py` usage remains valid for harnesses where host-side execution already works; the MCP is a portability layer, not a second lifecycle contract.

---

## Implementation Steps

### S1 - Confirm Existing Provider Lifecycle Contract

- [ ] Document the exact supported `provider-lifecycle.py` invocations the MCP must cover first.
  - [ ] Confirm CGC relationship query patterns, including `find name`, `analyze callers`, `analyze calls`, and `analyze chain`.
  - [ ] Confirm watcher/status patterns for `watchers status`, CGC, and GrepAI.
  - [ ] Confirm whether GrepAI search is already fully routed through `provider-lifecycle.py` or needs a small lifecycle-script addition before the MCP wraps it.
  - [ ] Record any provider command that still requires raw provider CLI access as deferred rather than broadening the MCP shell surface.

### S2 - Implement Minimal Host-Side MCP Server

- [ ] Add an MCP server that executes only the canonical provider lifecycle script with server-owned configuration.
  - [ ] Use `subprocess.run(..., shell=False)` or the project equivalent; do not allow caller-provided executable paths.
  - [ ] Inject `--coordination-root /home/mohamedreadone/Projects/ar-coordination` server-side, or derive it from server config.
  - [ ] Accept command input in a form that preserves provider-native argument text without forcing provider output into JSON.
  - [ ] Return a transcript containing command, exit code, duration, stdout, and stderr.
  - [ ] Persist each run under `ar-coordination/temp/provider-mcp/runs/<run-id>/`.

### S3 - Preserve Native Output And Bound Response Size

- [ ] Keep provider stdout/stderr faithful and only apply transport-safe truncation at the MCP response boundary.
  - [ ] Write full `stdout.txt`, `stderr.txt`, `command.txt`, and `result.txt` for every run.
  - [ ] Inline full transcript for normal responses.
  - [ ] For large responses, inline a head/tail excerpt and return absolute paths to the full output files.
  - [ ] Verify that a CGC table survives the MCP round trip without being normalized or collapsed.

### S4 - Wire Agent Guidance

- [ ] Update the relevant Agents Remember skills/instructions so agents route CGC/GrepAI through the MCP when available.
  - [ ] Update C-04 relationship/semantic provider guidance.
  - [ ] Update provider lifecycle guidance to state that MCP failure must be reported before source-only fallback.
  - [ ] Document direct `provider-lifecycle.py` as a valid fallback for harnesses that can execute host-side commands safely.
  - [ ] Include at least one CGC and one GrepAI usage example.

### S5 - Smoke Test Across Representative Paths

- [ ] Smoke test the MCP bridge against real provider commands.
  - [ ] Run a CGC caller/callee query against `device-management` or `agents-remember-md`.
  - [ ] Run provider watcher/status through the MCP.
  - [ ] Run a GrepAI semantic search if the GrepAI runtime is available.
  - [ ] Verify temp run artifacts are written and include complete stdout/stderr.
  - [ ] Record any harness-specific limitations as documentation, not as separate adapters.

---

## Proposed Code Examples

### E1 - Single Provider Lifecycle MCP Tool

Distinct change covered: minimal MCP tool that accepts provider-lifecycle-style input and returns a raw transcript.

Why this example is included: this is the core portability surface; it should stay boring and avoid provider-specific result normalization.

```python
@server.tool()
def provider_lifecycle(command: str, timeout_seconds: int = 120) -> str:
    args = shlex.split(command)
    result = run_provider_lifecycle(args, timeout_seconds)
    return format_transcript(result)
```

### E2 - Server-Owned Command Execution Boundary

Distinct change covered: execute the canonical lifecycle script without exposing arbitrary shell access.

Why this example is included: the MCP must run host-side, so this is the minimum safety boundary that is actually justified.

```python
completed = subprocess.run(
    [
        sys.executable,
        str(provider_lifecycle_script),
        *server_owned_context_args,
        *user_provider_args,
    ],
    shell=False,
    cwd=coordination_root,
    capture_output=True,
    text=True,
    timeout=timeout_seconds,
)
```

### E3 - Transcript Persistence

Distinct change covered: preserve full native output while keeping MCP responses bounded.

Why this example is included: CGC tables and provider diagnostics must remain inspectable even when the MCP transport cannot safely inline everything.

```text
ar-coordination/temp/provider-mcp/runs/<run-id>/
  command.txt
  stdout.txt
  stderr.txt
  result.txt
```

---

## Decision Log

| Date-Time        | Decision | Rationale |
| ---------------- | -------- | --------- |
| 2026-05-21T18:03 | Build one MCP portability bridge around `provider-lifecycle.py`. | Per-harness allowlists are not portable, while direct wrappers add little when the lifecycle script already exists. MCP becomes the compatibility boundary, but the lifecycle script remains the real provider contract. |
| 2026-05-21T18:03 | Preserve provider-native terminal output instead of JSON-normalizing CGC/GrepAI results. | CGC's useful relationship output is table/transcript shaped. JSON-in/JSON-out would repeat the earlier failure mode where useful CGC output becomes crippled. |
| 2026-05-21T18:03 | Keep direct `provider-lifecycle.py` execution as a supported fallback. | Harnesses that can execute host-side commands should not be forced through MCP, and the system should avoid building parallel lifecycle semantics. |

---

## Open Questions

- Should the MCP command input be a raw `provider-lifecycle.py` argument string, a stdin transcript block, or both?
- Where should the MCP server live in `agents-remember-md`: under `runtime/providers/mcp/`, `runtime/mcp/`, or another existing convention?
- Should the first implementation configure the MCP only for the local `/home/mohamedreadone/Projects/ar-coordination` path, or make the coordination root configurable from launch arguments immediately?
- Should timeout defaults differ between query commands and watcher/status commands?

---

## References

- Conversation on 2026-05-21 about CGC/GrepAI sandbox friction, harness-specific allowlist limitations, and MCP as the least-bad compatibility boundary.
- Existing provider lifecycle entry point: `/home/mohamedreadone/Projects/ar-coordination/scripts/provider-lifecycle.py`
- Related broader provider task: `/home/mohamedreadone/Projects/ar-coordination/tasks/agents-remember-md/260519_trusted-context-router/task.md`
- Related provider evaluation artifact: `/home/mohamedreadone/Projects/ar-coordination/tasks/agents-remember-md/260519_trusted-context-router/provider-evaluation.md`

# Task: Phase 3 - MCP Read Surface

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Config | Skill | Other
**Created:** 2026-05-22T00:07

---

## Objective

Expose read-first MCP tools for context packets, provider status, provider queries, drift checks, and run-artifact reads.

---

## Request And Deeper Request

### Surface Request

Build the first useful MCP tool surface after the server boundary and context packet are designed.

### Deeper Request

Make host-side context operations reliable across sandboxes and harnesses without granting arbitrary execution authority.

### Highest-Leverage Framing

Read tools should become boring first because they prove response shape, transcript handling, and provider output behavior. Mutation-capable tools do not need to wait on principle when they are typed, facade-backed, and tested in the isolated workbench. Provider output should remain native or clean-equivalent, with full transcripts stored as artifacts.

### Assumptions

- Phase 1 chooses the MCP server setup.
- Phase 2 supplies or informs `context.packet`.
- Provider query tools use typed allowlisted operations.
- Phase 1's minimal `ping`/`server_info` server is already installable from the runtime before real read tools are added.

### Boundaries

- No caller-provided executable path, cwd, or raw shell command.
- No provider/native output is written to MCP server stdout except through proper MCP tool results.
- No mutation should be hidden inside a tool whose contract is read-only.

---

## Requirements

- Add read-first MCP tools only.
- Start with a small v1 surface: `context.packet`, `provider.status`, typed provider query operations, and `run_artifact.read`.
- Persist provider run artifacts under the approved temp root.
- Preserve useful provider-native stdout/stderr.
- Bound inline responses and return artifact paths for large outputs.
- Include tests for safety validation and transcript persistence.

---

## Implementation Steps

### S1 - Define Read Tool Contracts

- [ ] Specify v1 read tools and schemas.
  - [ ] Include `context.packet`.
  - [ ] Include provider status/query operations.
  - [ ] Include run artifact read.
  - [ ] Decide which outputs are MCP tool results and which large outputs become resources/artifacts.
  - [ ] Stop for developer approval.

### S2 - Implement Read Surface

- [ ] Implement the approved read tools.
  - [ ] Add transcript persistence.
  - [ ] Add output truncation/excerpt behavior.
  - [ ] Smoke test CGC and GrepAI through MCP.

---

## Proposed Code Examples

### E1 - Provider Transcript Result

Distinct change covered: native output plus durable artifacts.

Why this example is included: provider output corruption was one of the original failure modes.

```json
{
  "ok": true,
  "provider": "cgc",
  "operation": "find_name",
  "stdout": "...native table...",
  "artifacts": {"runId": "...", "stdoutPath": ".../stdout.txt"}
}
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | MCP read surface establishes the first response/transcript model. | Read tools are the simplest way to prove useful output handling before more stateful operations reuse the same boundary. |
| 2026-05-22T11:50 | Keep read tools typed and artifact-backed. | The model gets useful native provider output without receiving arbitrary execution authority or unbounded stdout. |
| 2026-05-22T12:05 | Read-first is sequencing guidance, not a mutation ban. | The workbench makes mutation-capable operations testable early; the contract must make mutation explicit instead of forbidding it categorically. |

---

## Open Questions

- Should provider operations be one `provider.query` tool or provider-specific tools?
- Should every response include both structured content and text transcript?
- Should run artifacts be exposed only through `run_artifact.read` tools first, or also as MCP resources with stable URIs?

---

## References

- `phase-01-mcp-design-and-scaffolding.md`
- `phase-02-context-packet.md`
- `agentic-context-kernel-mcp-design-note.md`

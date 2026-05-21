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

Read tools should become boring before mutation tools exist. Provider output should remain native or clean-equivalent, with full transcripts stored as artifacts.

### Assumptions

- Phase 1 chooses the MCP server setup.
- Phase 2 supplies or informs `context.packet`.
- Provider query tools use typed allowlisted operations.

### Boundaries

- No start/stop/refresh/purge in this phase unless explicitly re-approved.
- No caller-provided executable path, cwd, or raw shell command.

---

## Requirements

- Add read-first MCP tools only.
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
| 2026-05-22T00:07 | MCP read surface precedes mutation tools. | Safety and compatibility should be proven before lifecycle mutations move behind MCP. |

---

## Open Questions

- Should provider operations be one `provider.query` tool or provider-specific tools?
- Should every response include both structured content and text transcript?

---

## References

- `phase-01-mcp-design-and-scaffolding.md`
- `phase-02-context-packet.md`
- `agentic-context-kernel-mcp-design-note.md`

# Task: Phase 2 - Context Packet

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Skill | Other
**Created:** 2026-05-22T00:07

---

## Objective

Extract the repeated session-start context checks into a fast context packet service and CLI/MCP operation.

---

## Request And Deeper Request

### Surface Request

Create a single operation that reports repo, branch, memory, provider, watcher, drift, and safe-use state.

### Deeper Request

Reduce repeated model reasoning between small shell calls by turning common startup choreography into a deterministic kernel primitive.

### Highest-Leverage Framing

The context packet is a trust snapshot, not closeout validation. It should be fast, stable, and cheap enough to call at session start.

### Assumptions

- Phase 1 decides whether the first exposure is CLI-only, MCP-only, or both.
- Provider health should use existing lifecycle/status services rather than new provider logic.

### Boundaries

- Do not include expensive full drift/closeout validation.
- Do not claim providers are fresh unless freshness is actually checked or explicitly reported as unknown.

---

## Requirements

- Define a versioned context packet shape.
- Include resolver, branch/worktree, memory, provider health, watcher state, and drift summary.
- Keep the packet fast and safe for normal session startup.
- Document when full validation is still required.

---

## Implementation Steps

### S1 - Define Packet Contract

- [ ] Draft the packet fields and semantics.
  - [ ] Include only fields with present-day consumers.
  - [ ] Separate healthy, stale, unknown, and unavailable states.
  - [ ] Stop for developer review before implementation.

### S2 - Implement Service And Entry Point

- [ ] Implement the approved packet service.
  - [ ] Reuse resolver/provider/drift code where available.
  - [ ] Add tests for configured, unconfigured, healthy, and degraded provider states.
  - [ ] Expose through the approved CLI/MCP surface.

---

## Proposed Code Examples

### E1 - Context Packet Shape

Distinct change covered: stable startup snapshot.

Why this example is included: later tool and skill rewiring depends on this contract.

```json
{
  "contextPacketVersion": 1,
  "repo": {"id": "agents-remember-md", "branch": "main"},
  "providers": {"cgc": {"mayUse": true}, "grepai": {"mayUse": true}},
  "warnings": []
}
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Context packet is Phase 2. | MCP/server placement and quality findings should inform the implementation boundary first. |

---

## Open Questions

- Should packet output include `contextPacketVersion: 1` immediately?
- Should the dashboard/TUI consume this same packet or a separate projection?

---

## References

- `phase-00-quality-baseline.md`
- `phase-01-mcp-design-and-scaffolding.md`
- `agentic-context-kernel-mcp-design-note.md`

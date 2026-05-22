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
- The first context packet should compose existing facade contracts rather than invent new behavior.

### Boundaries

- Do not include expensive full drift/closeout validation.
- Do not claim providers are fresh unless freshness is actually checked or explicitly reported as unknown.
- Do not start or refresh providers from the read-only packet builder.

---

## Requirements

- Define a versioned context packet shape.
- Include resolver, branch/worktree, memory, provider health, watcher state, and drift summary.
- Use the current resolver, provider status, and drift contracts as the initial composition sources.
- Preserve existing contract surfaces while implementation moves behind importable runtime modules.
- Keep the packet fast and safe for normal session startup.
- Document when full validation is still required.

---

## Composition Notes

The first implementation should treat the packet as a bundled response over current contracts:

```json
{
  "coordination_context": "ar_coordination_context_resolver.py --format json",
  "provider_status": "provider-lifecycle.py watchers status --json",
  "drift_status": "check_onboarding_drift.py --format json or an approved summary"
}
```

The controller can later call importable services directly, but the externally observable behavior should match those contracts until a later task explicitly approves contract changes.

`context.packet` should be read-only. An actionful `session.start` controller can be designed separately when startup policy includes actions such as checking or starting watchers.

## Implementation Steps

### S1 - Define Packet Contract

- [ ] Draft the packet fields and semantics.
  - [ ] Include only fields with present-day consumers.
  - [ ] Separate healthy, stale, unknown, and unavailable states.
  - [ ] Map every first-version field back to an existing facade contract or an explicitly approved new field.
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
  "drift": {"status": "notChecked"}
}
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Context packet is Phase 2. | MCP/server placement and quality findings should inform the implementation boundary first. |
| 2026-05-22T11:50 | Build the first packet by composing existing facade contracts. | This reduces model/tool-call latency without mixing behavior changes into the refactor. |

---

## Open Questions

- Should packet output include `contextPacketVersion: 1` immediately?
- Should the dashboard/TUI consume this same packet or a separate projection?

---

## References

- `phase-00-quality-baseline.md`
- `phase-01-mcp-design-and-scaffolding.md`
- `agentic-context-kernel-mcp-design-note.md`

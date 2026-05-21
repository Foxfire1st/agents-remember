# Task: Phase 5 - Provider Lifecycle Expansion

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Config | Other
**Created:** 2026-05-22T00:07

---

## Objective

Move provider lifecycle mutations behind focused service/controller boundaries after read-only MCP behavior is stable.

---

## Request And Deeper Request

### Surface Request

Eventually support provider watcher start/stop/refresh/hard-refresh/purge through modular services and possibly MCP.

### Deeper Request

Keep lifecycle mutation explicit, safe, and testable while reducing script monolith pressure.

### Highest-Leverage Framing

Lifecycle mutations are higher trust than status/query operations. They should come after the read surface proves the safety and transcript model.

### Assumptions

- Phase 0 identifies current lifecycle-script complexity pressure.
- Phase 3 proves transcript and read-tool patterns.

### Boundaries

- No destructive purge operations without explicit developer-approved safety design.
- No hidden watcher mutations from context packet or provider query tools.

---

## Requirements

- Identify provider lifecycle service boundaries.
- Separate provider health/status from mutation operations.
- Preserve direct CLI behavior where justified.
- Add explicit approval/safety rules for destructive operations.

---

## Implementation Steps

### S1 - Design Lifecycle Services

- [ ] Define service boundaries for provider lifecycle mutation.
  - [ ] Separate watcher control, backend control, refresh, and purge.
  - [ ] Map existing script functions to target modules.
  - [ ] Stop for developer approval.

### S2 - Implement Approved Slice

- [ ] Extract one lifecycle slice at a time.
  - [ ] Keep tests green after each slice.
  - [ ] Update MCP/CLI wrappers only after service tests pass.
  - [ ] Update onboarding for changed source files.

---

## Proposed Code Examples

### E1 - Lifecycle Service Boundary

Distinct change covered: controller delegates to service.

Why this example is included: mutation logic should not accumulate inside the MCP server.

```python
def start_provider_watchers(repo_id: str) -> WatcherStartResult:
    context = context_service.resolve(repo_id)
    return provider_lifecycle_service.start_watchers(context)
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Provider lifecycle mutation is deferred after read surfaces. | Mutations require stronger safety and should not be the first MCP milestone. |

---

## Open Questions

- Which lifecycle mutation is safe enough to expose first?

---

## References

- `phase-00-quality-baseline.md`
- `phase-03-mcp-read-surface.md`
- `agentic-context-kernel-mcp-design-note.md`

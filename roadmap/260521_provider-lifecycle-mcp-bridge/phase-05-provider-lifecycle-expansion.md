# Task: Phase 5 - Provider Lifecycle Expansion

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Config | Other
**Created:** 2026-05-22T00:07

---

## Objective

Move provider lifecycle mutations behind focused service/controller boundaries after the MCP skeleton and facade contracts are stable enough to wire operations safely in the workbench.

---

## Request And Deeper Request

### Surface Request

Eventually support provider watcher start/stop/refresh/hard-refresh/purge through modular services and possibly MCP.

### Deeper Request

Keep lifecycle mutation explicit, safe, and testable while reducing script monolith pressure.

### Highest-Leverage Framing

Lifecycle mutations are explicit operations, not forbidden operations. The workbench makes start/stop/refresh/purge behavior safe to wire and test before touching the real coordinator. The important rule is that the MCP exposes typed lifecycle operations over pinned facades rather than hiding mutations inside read tools or accepting shell commands.

### Assumptions

- Phase 0 identifies current lifecycle-script complexity pressure.
- Phase 3 proves transcript and read-tool patterns.
- Workbench-based coordinator installs can absorb destructive provider lifecycle tests without risking the real `ar-coordination`.

### Boundaries

- No hidden watcher mutations from context packet or provider query tools.
- No lifecycle operation may accept caller-provided shell commands, executable paths, or arbitrary runtime roots.

---

## Requirements

- Identify provider lifecycle service boundaries.
- Separate provider health/status from mutation operations.
- Preserve direct CLI behavior where justified.
- Preserve existing provider lifecycle command contracts as facades while extracting service internals.
- Use the workbench as the first target for destructive lifecycle tests.
- Keep destructive operations explicit in tool names, arguments, and test cases.

---

## Implementation Steps

### S1 - Design Lifecycle Services

- [ ] Define service boundaries for provider lifecycle mutation.
  - [ ] Separate watcher control, backend control, refresh, and purge.
  - [ ] Map existing script functions to target modules.
  - [ ] Pin current provider lifecycle command inputs, outputs, exit behavior, and status semantics before extraction.
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
| 2026-05-22T00:07 | Provider lifecycle mutation initially sat after read surfaces. | This was the original conservative sequencing before workbench isolation made earlier mutation wiring practical. |
| 2026-05-22T11:30 | Provider lifecycle commands become facades during extraction. | The refactor should change code organization behind the current contracts first, avoiding behavior changes while lifecycle logic is decomposed. |
| 2026-05-22T12:05 | Workbench isolation makes lifecycle mutation wiring acceptable earlier. | Destructive provider state is disposable in the workbench; sequencing should focus on contract pins and typed facades instead of a blanket mutation delay. |

---

## Open Questions

- Which lifecycle mutation should be wired first after `ping`/`server_info` and the context/session path prove the MCP skeleton?

---

## References

- `phase-00-quality-baseline.md`
- `phase-03-mcp-read-surface.md`
- `agentic-context-kernel-mcp-design-note.md`

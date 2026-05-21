# Task: Phase 7 - Worktree Services

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Other
**Created:** 2026-05-22T00:07

---

## Objective

Extract worktree status, planning, pairing, and later creation/cleanup behavior into focused services after context/provider read surfaces stabilize.

---

## Request And Deeper Request

### Surface Request

Move worktree state and creation planning toward modular service/controller APIs.

### Deeper Request

Protect code/memory branch pairing and closeout truth through deterministic services instead of repeated model-driven shell choreography.

### Highest-Leverage Framing

Worktree operations are workflow safety boundaries, not convenience helpers. Start with status and planning before creation or cleanup mutations.

### Assumptions

- C-09 remains the governing workflow contract until replacement services are proven.
- MCP worktree mutation is higher trust and should come after read-only status/plan tools.

### Boundaries

- No cleanup/delete operations without explicit destructive approval design.
- Do not hide branch-pairing risks behind implicit behavior.

---

## Requirements

- Extract worktree status and planning services before creation mutation.
- Preserve C-09 approval gates.
- Keep code/memory pairing visible in outputs.
- Add tests for branch mismatch, dirty state, and external-memory compatibility.

---

## Implementation Steps

### S1 - Extract Status And Planning

- [ ] Define worktree service boundaries.
  - [ ] Identify status facts.
  - [ ] Identify create-plan facts.
  - [ ] Preserve approval gates.
  - [ ] Stop for developer approval.

### S2 - Implement Approved Slice

- [ ] Implement the first worktree service slice.
  - [ ] Add tests for safe and blocked states.
  - [ ] Keep C-09 behavior valid until replaced.
  - [ ] Update skill guidance only after behavior is stable.

---

## Proposed Code Examples

### E1 - Worktree Status Packet

Distinct change covered: explicit branch/memory pairing facts.

Why this example is included: this shows the kind of truth the service must make visible.

```json
{
  "repoId": "agents-remember-md",
  "branch": "main",
  "memoryBranch": "main",
  "dirty": false,
  "pairingValid": true
}
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Worktree services are a later phase. | Provider/context read surfaces should stabilize before higher-trust workflow mutation services. |

---

## Open Questions

- Should worktree creation ever be exposed through MCP v1.x, or stay CLI/workflow-only until dashboard/TUI needs it?

---

## References

- `phase-00-quality-baseline.md`
- `agentic-context-kernel-mcp-design-note.md`

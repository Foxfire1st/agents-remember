# Task: Phase 6 - Provider Query Expansion

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Skill | Other
**Created:** 2026-05-22T00:07

---

## Objective

Expand provider query operations into typed, high-value CGC and GrepAI affordances after the base MCP read surface is stable.

---

## Request And Deeper Request

### Surface Request

Add richer query operations such as CGC callers/calls/chain/deps/complexity and GrepAI compact/scoped searches.

### Deeper Request

Let agents ask the right substrate-shaped question without reconstructing provider command syntax or unsafe command strings.

### Highest-Leverage Framing

Provider query expansion should follow actual usage pressure from C-04 and benchmarks, not expose every provider command.

### Assumptions

- C-04 provider cards identify the high-leverage operation set.
- Provider output remains native or clean-equivalent.
- The base MCP read surface already proves transcript/artifact handling before richer provider-specific operations are added.

### Boundaries

- Do not normalize provider output so aggressively that CGC tables or GrepAI result shape lose signal.
- Do not expose broad/open-ended commands without output budgets.
- Do not expose provider-native command pass-through as an MCP operation.

---

## Requirements

- Define typed operation names and argument schemas.
- Keep operation arguments close to the questions C-04 teaches, such as `grepai.search`, `cgc.find_name`, `cgc.analyze_callers`, and `cgc.analyze_complexity`.
- Preserve native useful provider output.
- Store run artifacts for every provider query.
- Add examples to provider capability cards.
- Keep query budgets aligned with C-04.

---

## Implementation Steps

### S1 - Select Operation Set

- [ ] Choose the first typed CGC and GrepAI operations.
  - [ ] Use real C-04 use cases.
  - [ ] Exclude low-value broad operations.
  - [ ] Stop for developer approval.

### S2 - Implement Operations

- [ ] Add approved query operations.
  - [ ] Add argument validation.
  - [ ] Add transcript persistence.
  - [ ] Add provider response smoke tests.
  - [ ] Update skill/provider-card examples.

---

## Proposed Code Examples

### E1 - Typed CGC Caller Query

Distinct change covered: operation-specific provider query.

Why this example is included: this is a representative relationship-discovery operation.

```json
{
  "provider": "cgc",
  "operation": "analyze_callers",
  "repoId": "agents-remember-md",
  "args": {"symbol": "dispatchCommand"}
}
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Provider query expansion follows base read surface. | The operation model should be proven before adding many provider-specific affordances. |
| 2026-05-22T11:50 | Provider query MCP tools are typed affordances, not command pass-through. | Agents should ask substrate-shaped questions without receiving arbitrary provider CLI authority. |

---

## Open Questions

- Should CGC and GrepAI use one shared `provider.query` tool or provider-specific MCP tools?

---

## References

- `phase-03-mcp-read-surface.md`
- `phase-04-skill-rewiring.md`
- `agentic-context-kernel-mcp-design-note.md`

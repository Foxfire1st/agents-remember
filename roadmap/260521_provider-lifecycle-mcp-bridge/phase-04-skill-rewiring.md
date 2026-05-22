# Task: Phase 4 - Skill Rewiring

**Status:** planning
**Repo:** agents-remember-md
**Type:** Skill | Docs
**Created:** 2026-05-22T00:07

---

## Objective

Update model-facing skills so they teach MCP-first context operations while keeping onboarding/source proof rules intact.

---

## Request And Deeper Request

### Surface Request

Migrate C-04 and related instructions from direct script/provider usage toward MCP tool usage.

### Deeper Request

Keep skills as pedagogy and routing doctrine instead of operational code containers. Skills should teach what operation to use, why, how to interpret output, and when fallback/proof is required.

### Highest-Leverage Framing

The skill layer should become thinner and clearer after MCP exists. Provider cards remain close to C-04 because that is where substrate selection happens.

### Assumptions

- MCP read tools are already working before skills make them the primary route.
- Direct CLI fallback remains documented only where justified.
- Phase 1/3 have established the MCP operation names and response shapes before skill examples are rewritten.

### Boundaries

- Do not remove onboarding/source proof requirements.
- Do not make providers the proof layer.
- Do not hide MCP/provider failures before falling back to source-only investigation.

---

## Requirements

- Update C-04 Semantics/Relationship guidance to prefer MCP when configured.
- Keep provider capability cards beside C-04.
- Teach tools and operation names, not low-level Python command choreography.
- Require MCP/provider failures to be reported before fallback.
- Keep direct lifecycle scripts as a justified fallback, not the main user-mode path.
- Update related tools/settings guidance where needed.

---

## Implementation Steps

### S1 - Update Retrieval Guidance

- [ ] Rewire C-04 and provider cards.
  - [ ] Replace direct command examples with MCP examples where appropriate.
  - [ ] Preserve direct CLI fallback with explicit conditions.
  - [ ] Verify examples use synthetic names where reusable.

### S2 - Update Operational Guidance

- [ ] Update related provider lifecycle/tools docs.
  - [ ] Clarify MCP-first when providers are configured.
  - [ ] Clarify onboarding/source proof after provider discovery.
  - [ ] Add tests/checks for skill references where available.

---

## Proposed Code Examples

### E1 - Skill Guidance Shape

Distinct change covered: pedagogy points to MCP operations, not ad hoc scripts.

Why this example is included: this preserves the skills/controller separation.

```text
Use MCP `provider.query` for CGC callers. If MCP is unavailable, report the failure,
then use direct lifecycle CLI only when host-side execution is available.
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Skill rewiring waits until MCP read tools exist. | Skills should teach stable operations, not speculative APIs. |
| 2026-05-22T11:50 | Skills should explain MCP operation use, not MCP implementation mechanics. | MCP becomes the host-side control surface while skills stay the model-facing pedagogy layer. |

---

## Open Questions

- Which skills beyond C-04 need MCP-first routing?

---

## References

- `phase-03-mcp-read-surface.md`
- `agentic-context-kernel-mcp-design-note.md`

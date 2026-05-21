# Task: Phase 1 - MCP Design And Scaffolding

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Config | Skill | Other
**Created:** 2026-05-22T00:07

---

## Objective

Design the first MCP server shape, installation path, configuration model, harness compatibility plan, and safety boundary after Phase 0 quality findings are reviewed.

---

## Request And Deeper Request

### Surface Request

Plan where the MCP lives, how it is installed, how harnesses configure it, and what the first server exposes.

### Deeper Request

Use MCP as the stable host-side controller boundary for context-kernel operations, while keeping Python services importable/testable and skills as the instruction layer.

### Highest-Leverage Framing

The first MCP should prove the architecture, not expose every operation. It should be small, typed, server-owned, and read-first.

### Assumptions

- Phase 0 findings will identify the service boundaries most worth extracting first.
- MCP setup differs across harnesses and needs current documentation research before implementation.
- Direct CLI usage can remain valid when justified, but must not become a duplicated contract.

### Boundaries

- No arbitrary shell bridge.
- No destructive provider/worktree operations in the initial MCP surface.
- No compatibility fallback unless justified in this task file before implementation.

---

## Requirements

- Research current MCP setup expectations for target harnesses before implementation.
- Decide source and installed runtime locations for the MCP server.
- Decide configuration inputs: coordination root, allowed repo ids, provider ids, timeout caps, transcript root.
- Define the first typed tool surface and safety rules.
- Decide how direct CLI usage coexists with MCP without duplicating lifecycle semantics.
- Produce a reviewable implementation plan before code changes.

---

## Implementation Steps

### S1 - Research Harness Setup

- [ ] Gather current MCP installation/configuration patterns for target harnesses.
  - [ ] Check Codex-relevant setup.
  - [ ] Check Claude Code setup.
  - [ ] Record compatibility implications for local stdio servers.

### S2 - Define Server Boundary

- [ ] Specify source/runtime placement and configuration.
  - [ ] Decide source module layout.
  - [ ] Decide installed runtime layout.
  - [ ] Define server-owned roots and allowlists.
  - [ ] Define transcript persistence behavior.

### S3 - Present First Implementation Slice

- [ ] Produce a reviewable Phase 1 implementation plan.
  - [ ] Identify exact modules/files to create or extract.
  - [ ] Include safety model and fallback justification.
  - [ ] Stop for developer approval.

---

## Proposed Code Examples

### E1 - Typed MCP Tool Boundary

Distinct change covered: server-owned typed operation instead of raw shell execution.

Why this example is included: this is the core safety shape Phase 1 must preserve.

```python
def provider_query(repo_id: str, provider: str, operation: str, args: dict) -> ProviderTranscript:
    request = validate_provider_query(repo_id, provider, operation, args)
    return provider_service.run_query(request)
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Prior "Phase 0 MCP Design Discussion & Scaffolding" is now Phase 1. | Quality baseline must come first. |

---

## Open Questions

- Which harnesses are mandatory for v1 compatibility?
- Should v1 expose `context.packet` first, or a provider status/query surface first?

---

## References

- `phase-00-quality-baseline.md`
- `agentic-context-kernel-mcp-design-note.md`

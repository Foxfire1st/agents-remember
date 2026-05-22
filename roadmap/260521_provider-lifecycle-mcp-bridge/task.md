# Task: Agentic Context Kernel MCP Roadmap

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Config | Skill | Other
**Created:** 2026-05-22T00:07

---

## Objective

Turn the original provider-lifecycle MCP bridge idea into a phased roadmap for an Agents Remember context kernel: a cleaner host-side controller/API boundary over deterministic Python services, provider drivers, workflow state, and model-facing skills.

---

## Request And Deeper Request

### Surface Request

Create a new master task file in this folder, keep the old provider-MCP task as historical input, and split the broader kernel/MCP work into phase-level light-task files.

### Deeper Request

Preserve the architectural intent behind the design note: the MCP is no longer only a workaround for sandbox friction. It is the controller boundary that lets Agents Remember become a cleaner, more reliable, provider-aware context control plane.

### Highest-Leverage Framing

Use the task structure to prevent the work from collapsing into another monolithic script. The first phase must install and run code quality tooling, collect findings, and use those findings to guide the refactor/MCP strategy before implementation begins.

### Assumptions

- `task.old.md` is retained as historical input, not the active plan.
- `agentic-context-kernel-mcp-design-note.md` is a mature brainstorm, not a binding spec.
- The root `pyproject.toml` and `requirements.txt` define the initial quality-tool baseline.
- Each phase is its own light-task file and still needs developer approval before implementation.

### Boundaries

- Do not implement the MCP before Phase 0 quality findings are collected and reviewed.
- Do not add compatibility/fallback code unless it is justified in the relevant phase task.
- Do not treat "existing users" as a sufficient reason for fallback complexity; the project is pre-1.0.
- Do not turn MCP into arbitrary shell execution or a new monolith.

---

## Requirements

- Keep this file as the master roadmap for the kernel/MCP direction.
- Keep `task.old.md` as the archived original provider-lifecycle MCP framing.
- Use one phase task file per phase, with light-task sections plus this task's additional request/deeper-request section.
- Track add-on tasks that support this roadmap but do not belong to a single implementation phase.
- Make Phase 0 dedicated to installing code quality tools, running tests/static analysis, and writing a findings report inside this task folder.
- Keep `phase-00-refactor-strategy.md` as the standing refactor backdrop for module boundaries, controller/service separation, and context-packet direction.
- Shift the previous "Phase 0 MCP Design Discussion & Scaffolding" to Phase 1.
- Treat later kernel/MCP phases as dependent on Phase 0 findings and Phase 1 design decisions.
- Preserve the separation of concerns: skills teach, MCP controls, Python core owns deterministic services, providers act as drivers, harnesses consume views.

---

## Standing Strategy Artifacts

| File | Purpose | Status |
| --- | --- | --- |
| `phase-00-refactor-strategy.md` | Living refactor strategy, intended `src/agents_remember/...` structure, controller/service boundaries, and context-packet contract. | planning |
| `phase-00-quality-findings.md` | Phase 0 executable quality baseline and refactor risk evidence. | Completed |

---

## Phase Task Index

| Phase | File | Purpose | Status |
| --- | --- | --- | --- |
| 0 | `phase-00-quality-baseline.md` | Install quality tools, run tests/static analysis, and write findings report. | Completed |
| 1 | `phase-01-mcp-design-and-scaffolding.md` | Decide MCP location, setup, configuration, compatibility, and server shape. | planning |
| 2 | `phase-02-context-packet.md` | Extract and expose the fast startup context packet. | planning |
| 3 | `phase-03-mcp-read-surface.md` | Expose read-first MCP tools for context and provider queries. | planning |
| 4 | `phase-04-skill-rewiring.md` | Rewire skills to teach MCP-first operations and explicit fallbacks. | planning |
| 5 | `phase-05-provider-lifecycle-expansion.md` | Move provider lifecycle mutations behind modular services/controllers. | planning |
| 6 | `phase-06-provider-query-expansion.md` | Add typed provider query operations for CGC and GrepAI. | planning |
| 7 | `phase-07-worktree-services.md` | Extract worktree/status/planning services after read surfaces stabilize. | planning |

## Add-On Task Index

| Add-On | File | Purpose | Status |
| --- | --- | --- | --- |
| A1 | `../260522_harness-mcp-compatibility-docs/task.md` | Research current MCP configuration across documented harnesses and plan the install-doc/skill-installation changes that support the MCP-first runtime model. | planning |

---

## Implementation Steps

### S1 - Establish The Phase Roadmap

- [ ] Create the master task file and phase task files.
  - [ ] Preserve the old task and design note as references.
  - [ ] Add the request/deeper-request section to each active task file.
  - [ ] Verify every phase task follows the light-task structure.

### S2 - Execute Phase 0 First

- [x] Complete `phase-00-quality-baseline.md` before MCP design implementation.
  - [x] Install the root quality tools in a dedicated virtual environment.
  - [x] Run the agreed tests and static-analysis commands.
  - [x] Write the findings report in this task folder.
  - [x] Review-ready findings are available before choosing refactor/MCP implementation order.

### S3 - Gate Later Phases On Findings

- [ ] Revise later phase task files after Phase 0 findings are reviewed.
  - [ ] Use quality findings to prioritize modularization boundaries.
  - [ ] Require explicit fallback/compatibility justification in phase task files.
  - [ ] Ask for phase-specific approval before implementation begins.

---

## Proposed Code Examples

### E1 - Not Needed For This Master Task

Distinct change covered: roadmap/task structure only.

Why this example is included: this master file does not implement code; phase files will carry code examples when their implementation shape is ready for review.

```text
No code change is proposed by the master roadmap itself.
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Rename the work from provider-lifecycle MCP bridge toward Agentic Context Kernel MCP. | The MCP is now understood as the controller boundary for broader context-kernel operations, not just sandbox friction. |
| 2026-05-22T00:07 | Add Phase 0 for quality tooling and findings before MCP design implementation. | Refactor and MCP planning should be guided by concrete code-quality data from the current source state. |
| 2026-05-22T00:07 | Require explicit justification for fallback/compatibility code. | The project is pre-1.0 and should reduce accumulated slop rather than preserving accidental complexity. |
| 2026-05-22T00:11 | Phase 0 quality tooling venv belongs in the source repo at `.venv`. | The tools are repo development dependencies and should be installed where the code quality commands run. |
| 2026-05-22T00:36 | Add `phase-00-refactor-strategy.md` as a standing strategy artifact. | The refactor/MCP phases need a stable backdrop for package structure, controller/service boundaries, and the context-packet slice. |
| 2026-05-22T11:50 | Start MCP implementation with a minimal stdio server and isolated workbench install tests. | This proves host-side transport and runtime placement before moving context/provider behavior behind the MCP, and keeps the real coordinator untouched during early install experiments. |
| 2026-05-22T12:05 | Treat mutation-capable MCP wiring as acceptable when workbench-gated. | The isolated workbench removes the need for a blanket initial ban on destructive/provider/worktree operations; contracts and typed facades remain the guardrails. |
| 2026-05-22T17:57 | Add harness MCP compatibility documentation as a roadmap add-on task. | Harness compatibility cuts across Phase 1 setup and Phase 4 skill rewiring, so it should support the roadmap without being forced into a single phase file. |

---

## Open Questions

- Should Phase 0 include formatter decisions, or only diagnostics from `ruff`, `radon`, and tests?
- Should later phase tasks be revised only after the quality report, or should Phase 1 design research proceed in parallel?

---

## References

- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/task.old.md`
- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/agentic-context-kernel-mcp-design-note.md`
- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/phase-00-refactor-strategy.md`
- `C:/ew/agents-remember-md/pyproject.toml`
- `C:/ew/agents-remember-md/requirements.txt`

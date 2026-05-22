# Task: Phase 0 - Quality Baseline And Findings Report

**Status:** Completed
**Repo:** agents-remember-md
**Type:** Config | Script | Other
**Created:** 2026-05-22T00:07

---

## Objective

Install the repository-defined quality tools, run tests/static analysis against the current codebase, and write a findings report that will guide the kernel/MCP refactor strategy.

---

## Request And Deeper Request

### Surface Request

Add a phase before MCP design that installs code quality tools, runs tests and analysis, and stores findings in this task folder.

### Deeper Request

Make the future MCP/refactor plan evidence-based. Before moving logic into services or exposing MCP tools, identify the current weak points, complexity pressure, dead/stale code, duplicated patterns, and test gaps.

### Highest-Leverage Framing

Phase 0 is not cleanup for its own sake. It is a discovery phase that decides where modularization will actually reduce risk and where current scripts should remain stable until a better service boundary exists.

### Assumptions

- Quality tools are defined by `C:/ew/agents-remember-md/requirements.txt`.
- Static-analysis configuration starts from `C:/ew/agents-remember-md/pyproject.toml`.
- Tool installation should happen in the repository virtual environment at `C:/ew/agents-remember-md/.venv`, not directly into the base Python environment.
- The findings report is an input to later phase planning, not an implementation mandate by itself.

### Boundaries

- Do not refactor code in this phase unless the developer explicitly approves a follow-up implementation slice.
- Do not auto-fix lint findings before the report is reviewed.
- Do not treat tool output as truth without interpreting whether it matters architecturally.

---

## Requirements

- Create or reuse the repository virtual environment at `C:/ew/agents-remember-md/.venv`.
- Install the repository quality dependencies from `requirements.txt`.
- Run the existing unit tests.
- Run `ruff` diagnostics using the repository `pyproject.toml`.
- Run `radon` diagnostics using the repository `pyproject.toml`.
- Write a findings report in this task folder, proposed path: `phase-00-quality-findings.md`.
- Separate mechanical issues from architectural design signals.
- Review findings with the developer before Phase 1 implementation planning.

---

## Implementation Steps

### S1 - Prepare Quality Tool Environment

- [ ] Install quality tooling in an isolated virtual environment.
  - [x] Create or reuse `C:/ew/agents-remember-md/.venv`.
  - [x] Install `requirements.txt`.
  - [x] Record exact tool versions in the findings report.

### S2 - Run Baseline Validation

- [ ] Run tests and static analysis without mutating source.
  - [x] Run the current unit test suite.
  - [x] Run `ruff check`.
  - [x] Run `radon cc` and `radon mi` using the configured thresholds.
  - [x] Capture raw outputs or summarized excerpts in the report.

### S3 - Write Findings Report

- [ ] Produce `phase-00-quality-findings.md`.
  - [x] Group findings by severity and architectural relevance.
  - [x] Identify likely service/module extraction candidates.
  - [x] Identify false positives or low-value mechanical noise.
  - [x] List recommended next-phase decisions for developer review.

---

## Proposed Code Examples

### E1 - Quality Command Shape

Distinct change covered: diagnostic workflow, not implementation.

Why this example is included: Phase 0 needs reproducible commands before the findings are interpreted.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s runtime\skills\U-01-core-skills\tests
.\.venv\Scripts\ruff check .
.\.venv\Scripts\radon cc runtime installer -s -a
.\.venv\Scripts\radon mi runtime installer -s
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Phase 0 must precede MCP design implementation. | Current code quality and modularity pressure should determine the safe refactor path. |
| 2026-05-22T00:07 | Findings go into `phase-00-quality-findings.md`. | The report must be durable task context instead of transient chat output. |
| 2026-05-22T00:11 | Use the source repo `.venv` for Phase 0 tools. | The quality tools are repo development dependencies and should live beside the repo they validate. |
| 2026-05-22T00:11 | Phase 0 completed with diagnostic-only findings. | Tests pass, `ruff`/`radon` findings are recorded, and no source auto-fixes were applied. |

---

## Open Questions

- Should Phase 0 run only diagnostics, or also produce a no-op formatter/lint auto-fix preview?

---

## References

- `C:/ew/agents-remember-md/pyproject.toml`
- `C:/ew/agents-remember-md/requirements.txt`
- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/agentic-context-kernel-mcp-design-note.md`

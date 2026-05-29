---
name: p-01-implementation-planning
description: "W-01 heavy-task-workflow only — Phase P-04 Planning (step P-01). Do not use in chat (W-03) or light (W-02) workflows. Write or revise a scheduling-only implementation plan from approved requirements, approved architecture, output documentation, and code, pulling any concrete code examples directly from D-04."
---

# P-01 Implementation Planning

## Scope

Phase-local step of the **W-01 heavy task workflow**, Phase **P-04 Planning**. Invoked only by the `w-01-heavy-task-workflow` orchestrator at its checkpoint — never standalone, and not in the `w-02` light or `w-03` chat workflows.

Companion files:

1. `workflow.md`
2. `implementation-plan-template.md`
3. `quality-checks.md`

Write one file:

`<task-folder>/P-04-planning/P-01-implementation-planning/implementation_plan.md`

## Inputs

1. `requirements.md`
2. `architecture.md`
3. `P-03-design/D-04-output-documentation/overview.md`
4. relevant per-file output documentation under `P-03-design/D-04-output-documentation/`
5. relevant code needed to confirm real dependency order

## Rules

1. Planning stays scheduling-only.
2. Keep planning-discovered issues in the plan's issues section.
3. Pull any concrete code examples from `P-03-design/D-04-output-documentation/`; do not invent new examples in Planning.
4. Do not reintroduce segment-style scheduling, batch orchestration, or planner-owned design artifacts.

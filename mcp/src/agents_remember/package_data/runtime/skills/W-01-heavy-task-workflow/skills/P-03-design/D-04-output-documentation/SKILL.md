---
name: d-04-output-documentation
description: "W-01 heavy-task-workflow only — Phase P-03 Design (step D-04). Do not use in chat (W-03) or light (W-02) workflows. Project the approved Design direction into per-file target-state output docs with representative projected code examples and one root overview for CP3 and Planning."
---

# D-04 Output Documentation

## Scope

Phase-local step of the **W-01 heavy task workflow**, Phase **P-03 Design**. Invoked only by the `w-01-heavy-task-workflow` orchestrator at its checkpoint — never standalone, and not in the `w-02` light or `w-03` chat workflows.

Companion files:

1. `output-documentation-workflow.md`
2. `output-documentation-template.md`
3. `output-documentation-overview-template.md`

Inputs:

1. `D-03` dry-run plan
2. approved Design artifacts
3. relevant input documentation
4. corresponding onboarding references

## Rules

1. Keep this entrypoint thin.
2. Keep target-state projection task-local.
3. Put representative projected code examples in D-04 so downstream Planning can pull them without inventing new examples.
4. Do not approve architecture from this package.
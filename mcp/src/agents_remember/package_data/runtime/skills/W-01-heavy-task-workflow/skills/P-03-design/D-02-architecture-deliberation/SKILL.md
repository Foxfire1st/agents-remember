---
name: d-02-architecture-deliberation
description: "W-01 heavy-task-workflow only — Phase P-03 Design (step D-02). Do not use in chat (W-03) or light (W-02) workflows. Ask architecture-facing Design questions one by one, record the developer's answers, and prepare architecture decisions for projection."
---

# D-02 Architecture Deliberation

## Scope

Phase-local step of the **W-01 heavy task workflow**, Phase **P-03 Design**. Invoked only by the `w-01-heavy-task-workflow` orchestrator at its checkpoint — never standalone, and not in the `w-02` light or `w-03` chat workflows.

Use:

1. approved `requirements.md`
2. `S-02` framing output
3. `architecture_open_questions.md`
4. Research input documentation
5. `D-01` output

Write:

`P-03-design/D-02-architecture-deliberation/architecture-deliberation.md`

## Rules

1. Do not auto-answer architecture questions.
2. Assign architecture IDs here, but do not approve architecture here.
3. Preserve already-made requirement decisions from `D-01`.
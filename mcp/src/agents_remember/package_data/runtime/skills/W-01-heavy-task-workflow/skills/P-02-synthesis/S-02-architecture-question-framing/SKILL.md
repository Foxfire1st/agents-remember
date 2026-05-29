---
name: s-02-architecture-question-framing
description: "W-01 heavy-task-workflow only — Phase P-02 Synthesis (step S-02). Do not use in chat (W-03) or light (W-02) workflows. Convert Research and requirement-framing outputs into the architecture-facing question set for D-02."
---

# S-02 Architecture Question Framing

## Scope

Phase-local step of the **W-01 heavy task workflow**, Phase **P-02 Synthesis**. Invoked only by the `w-01-heavy-task-workflow` orchestrator at its checkpoint — never standalone, and not in the `w-02` light or `w-03` chat workflows.

Use:

1. `S-01` output
2. `architecture_open_questions.md`
3. approved requirements
4. Research input documentation

Write:

`P-02-synthesis/S-02-architecture-question-framing/architecture-question-framing.md`

## Rules

1. Do not assign architecture IDs here.
2. Frame technical uncertainty instead of approving architecture.
3. Keep evidence gaps explicit instead of collapsing them into decisions.
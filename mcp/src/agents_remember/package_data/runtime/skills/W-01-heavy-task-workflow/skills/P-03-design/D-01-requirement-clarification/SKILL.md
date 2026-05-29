---
name: d-01-requirement-clarification
description: "W-01 heavy-task-workflow only — Phase P-03 Design (step D-01). Do not use in chat (W-03) or light (W-02) workflows. Ask requirement-facing Design questions one by one, record the developer's answers, and prepare requirement promotion."
---

# D-01 Requirement Clarification

## Scope

Phase-local step of the **W-01 heavy task workflow**, Phase **P-03 Design**. Invoked only by the `w-01-heavy-task-workflow` orchestrator at its checkpoint — never standalone, and not in the `w-02` light or `w-03` chat workflows.

This entrypoint should route:

1. `S-01` framing intake
2. staged requirement candidates
3. existing approved requirements
4. Research input documentation

It writes:

`P-03-design/D-01-requirement-clarification/requirement-clarification.md`

## Rules

1. Do not auto-answer requirement questions.
2. Leave approved promotion into `requirements.md` to the orchestrator.
3. Keep evidence-pending items staged rather than erasing them.
---
name: d-03-output-dry-run-planning
description: "W-01 heavy-task-workflow only — Phase P-03 Design (step D-03). Do not use in chat (W-03) or light (W-02) workflows. Plan the target-state output documentation pass from approved Design direction and mapped input coverage."
---

# D-03 Output Dry Run Planning

## Scope

Phase-local step of the **W-01 heavy task workflow**, Phase **P-03 Design**. Invoked only by the `w-01-heavy-task-workflow` orchestrator at its checkpoint — never standalone, and not in the `w-02` light or `w-03` chat workflows.

This entrypoint should route:

1. `D-02` deliberation output
2. `D-01` clarification output
3. approved `requirements.md`
4. relevant Research input docs
5. corresponding onboarding references for every in-scope input file

It writes:

`P-03-design/D-03-output-dry-run-planning/output-dry-run-planning.md`

## Rules

1. Do not write per-file output docs here.
2. Prepare the projection pass and review surface only.
3. Use the rollout order from `ARCH-8` when defining packets.
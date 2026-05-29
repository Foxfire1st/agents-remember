---
name: r-01-requirements-normalization
description: "W-01 heavy-task-workflow only — Phase P-01 Research (step R-01). Do not use in chat (W-03) or light (W-02) workflows. Read raw requirement candidates together with onboarding and code, then normalize them into the normalized list in requirement_change_candidates.md."
---

# R-01 Requirements Normalization

## Scope

Phase-local step of the **W-01 heavy task workflow**, Phase **P-01 Research**. Invoked only by the `w-01-heavy-task-workflow` orchestrator at its checkpoint — never standalone, and not in the `w-02` light or `w-03` chat workflows.

This skill is the first operational step in Research.

It converts raw requirement intake into a clearer normalized requirement candidate set so `R-02-input-documentation` can investigate the current system against stable normalized candidates instead of raw phrasing noise.

Because `requirement_change_candidates.md` is orchestrator-owned, this skill updates that artifact directly instead of creating a separate task-local output file.

Companion files:

1. `workflow.md`
2. `normalized-candidate-template.md`

Update one file:

`<task-folder>/requirement_change_candidates.md`

## Inputs

1. raw requirement intake in `requirement_change_candidates.md`
2. current onboarding
3. relevant current code
4. already approved requirements when overlap must be checked

## Rules

1. Translate raw intake into stable normalized requirement records.
2. Remove ambiguity from raw wording without changing intent casually.
3. Separate distinct requirement candidates cleanly.
4. This step is contract-shaping, not deep area research.
5. It does not approve requirements.
6. It does not create target-state design.
7. It should not silently expand scope while cleaning up wording.

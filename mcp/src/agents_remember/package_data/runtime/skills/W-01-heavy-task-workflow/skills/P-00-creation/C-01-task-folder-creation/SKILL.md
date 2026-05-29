---
name: c-01-task-folder-creation
description: "W-01 heavy-task-workflow only — Phase P-00 Creation (step C-01). Do not use in chat (W-03) or light (W-02) workflows. Create the task folder after explicit developer naming choice and initialize the orchestrator-owned root artifacts without normalizing or approving intake."
---

# C-01 Task Folder Creation

## Scope

Phase-local step of the **W-01 heavy task workflow**, Phase **P-00 Creation**. Invoked only by the `w-01-heavy-task-workflow` orchestrator at its checkpoint — never standalone, and not in the `w-02` light or `w-03` chat workflows.

This entrypoint stays lean.

It should route task start work around:

1. task folder naming choice
2. task folder creation
3. `task.md` initialization
4. `requirement_change_candidates.md` initialization
5. `architecture_open_questions.md` initialization
6. `P-00-creation/progress.md` initialization

## Rules

1. Preserve raw requirement language in staging.
2. Capture raw architecture intake without approving it.
3. Before scaffolding, present task-name options in direct discussion and wait for explicit developer selection.
4. The naming options must include a current-branch-based suggestion when available, the normal task-intent-based suggestions, and a custom option.
5. Hand onboarding maintenance off to skill `C-05-create-or-update-onboarding-files` instead of hiding it here.

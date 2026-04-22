---
name: W-02-light-task-workflow
description: "Task lifecycle for non-code artifacts: docs, skills, configs, READMEs, presentations. Provides plan-in-file discipline, a hard approval gate, milestone checks, and decision logging — without onboarding infrastructure. Every non-code change gets a task file, regardless of size."
---

# Light Task

This skill provides the planning discipline and human-in-the-loop approval gate for:

1. **Non-code artifacts** — documentation, skills, configs, READMEs, presentations, and similar files that live outside the onboarding system. Always, regardless of size.
2. **Small code changes below heavy-task-workflow's threshold** — code changes that don't warrant the full heavy-task-workflow lifecycle (drift detection, onboarding companions, Task blocks) but still need a plan, approval, and decision tracking.

**Why it exists:** The `heavy-task-workflow` skill's approval gates (Phases 1, 2, and 3) are embedded within phases that assume onboarding companion files: drift detection, onboarding reads, Task block stamps. When working on artifacts that have no companion files, those phases collapse to zero and the approval gate gets swept past. This skill provides the gate as the central feature, without the onboarding machinery.

**When to use it:**

- The target artifacts are **non-code** — they live outside the onboarding system and have no companion MDs. Always, regardless of size.
- The target is a **small code change below heavy-task-workflow's threshold** — it needs a plan but doesn't warrant the full lifecycle.
- **Every change that goes through light-task-workflow gets a task file.** No size threshold for non-code; judgement call for small code (if it needs a plan, it gets one).

**When NOT to use it:**

- The target is **code above heavy-task-workflow's threshold** (multiple files, cross-component, cross-repo, risky invariants, multi-session). Code above threshold always goes to `heavy-task-workflow`, which creates onboarding if none exists (via `repo-bootstrap`).

---

## Core Rules

The non-negotiable behavioral rules (R1–R7) are defined in [Orchestrator.agent.md](../../../.github/agents/Orchestrator.agent.md). They apply throughout all phases of this skill. Read them before starting.

This skill adds one workflow-specific rule on top:

### Three-touch iteration cycle

When the developer requests changes to work in progress during Phase 2, follow this cycle: update the task file first (Touch 1), implement and present (Touch 2), then update after approval (Touch 3). Never go straight from a chat request to a file edit without updating the task file's plan sections first. See Phase 2.2 for details.

---

## Phase 1 — Create task file and plan

### 1.1 Create the task file

Use the same naming convention as `heavy-task-workflow`:

| Origin        | Naming convention                  | Example                       |
| ------------- | ---------------------------------- | ----------------------------- |
| Ticket-linked | `YYMMDD_#<number>_<short-slug>.md` | `260319_#42_update-readme.md` |
| Organic       | `YYMMDD_<descriptive-slug>.md`     | `260319_readme-rewrite.md`    |

All task files live in `docs_and_tasks/tasks/`.

### 1.2 Task file template

```markdown
# Task: <Title>

**Status:** Planning
**Repo:** <primary repo or "docs_and_tasks">
**Type:** <Docs | Skill | Config | Other>
**Created:** <YYYY-MM-DD>

---

## Objective

<What and why — 2-3 sentences.>

---

## Requirements

<What the artifact must accomplish. Audience, constraints, acceptance criteria.>

---

## Implementation Steps

### S1 — <title>

<What to do, in enough detail that a compressed agent can resume.>

---

## Decision Log

| Date   | Decision           | Rationale |
| ------ | ------------------ | --------- |
| <date> | <what was decided> | <why>     |

---

## Open Questions

<Unresolved items. Mark resolved inline when answered.>

---

## References

<Links to related files, tickets, Confluence pages, conversations.>
```

Not every section needs content for small tasks. But the structure must be present — it's cheap to have an empty "Open Questions" section, and expensive to not have one when a question arises mid-implementation.

### 1.3 Gather context

Before writing the plan:

- **Check existing docs** in `docs_and_tasks/docs/` for relevant reference material.
- **Check the glossary** for canonical terminology.
- **If the artifact touches cross-repo concerns** (e.g., a skill that references multiple repos, a config that affects infrastructure), check onboarding for those repos.
- **Optional sub-skills:** Confluence search, Brave search, Context7 — invoke if the developer requests or if the task domain requires it. These are not mandatory for light-task-workflow (unlike heavy-task-workflow).

### 1.4 Write the plan and present for approval

Fill in the task file sections. Then present a concise summary in chat:

- Objective (1-2 sentences)
- Key implementation steps (bullet list)
- Any open questions or decisions needed

**STOP. Wait for developer approval before proceeding to Phase 2.**

The developer may:

- **Approve** → proceed to Phase 2.
- **Request changes** → update the task file per R5, re-present.
- **Reject** → record the rejection reason in the decision log, close the task file.

---

## Phase 2 — Implement

### 2.1 Work through implementation steps

For each step in the plan:

1. **Do the work** — create or edit the target artifact(s).
2. **Milestone check (R3)** — after completing the step, re-read the task file. Confirm the work matches the plan. If it diverged, stop: update the plan (with developer approval if the change is significant), then continue.
3. **Decision escalation (R4)** — if an unforeseen choice arises, ask the developer. Record in the decision log.

### 2.2 Handle mid-implementation iterations

When the developer requests changes, extends scope, or feedback reveals new requirements during implementation, follow the **three-touch iteration cycle**. This prevents the task file from drifting out of sync with reality.

#### Touch 1 — Plan the iteration (before any edits)

Read the task file. Then update it:

| What changed                      | Update                                                                    |
| --------------------------------- | ------------------------------------------------------------------------- |
| New requirement emerged           | Append to Requirements (e.g., R7, R8) with "(added during SN)" annotation |
| New implementation step needed    | Append to Implementation Steps (e.g., S5, S6) with date                   |
| Approach changed on existing step | Update the step description. Add decision log entry explaining why.       |
| Scope narrowed or deferred        | Note in the step or add to a "Deferred" section. Decision log entry.      |

Present the updated plan to the developer. **If the change is significant (new requirements, new steps, changed approach), get approval before implementing.** Minor wording/polish within an already-approved step can proceed without a gate.

#### Touch 2 — Implement and present for review

Do the work. Then:

1. **Update Implementation Progress** — mark the step's current state (what was done, key details).
2. **Present the result** to the developer for review. Show what changed and how it relates to the plan.

#### Touch 3 — After review feedback

Based on developer response:

- **Approved** → Update Implementation Progress to mark the step as Done with a brief summary. Update Decision Log if the approval involved a notable judgment call. Proceed to next step.
- **Changes requested** → Return to Touch 1 for the next iteration. Update the task file before implementing corrections.
- **Rejected** → Record rejection reason in Decision Log. Revert or defer as appropriate.

#### Why three touches

The problem this solves: during the README rewrite, multiple iterations (citation swap, intro rewrite, section expansion) were implemented directly from chat feedback. The task file's Implementation Progress tracked completion but the Requirements, Implementation Steps, and Decision Log fell out of sync. Fixing this retroactively required a dedicated alignment pass. Three touches prevent that — the task file stays current because it's updated at each transition, not only at the end.

### 2.3 Multi-session continuity

If a session ends mid-task:

- The task file is the recovery document. On resumption, re-read it to recover state.
- Implementation steps should be written with enough detail that a fresh agent (post-compression or new session) can continue from where work stopped.
- Mark completed steps in the task file (strikethrough or status annotation) so the next session knows what's done.

---

## Phase 3 — Close

### 3.1 Completion summary

Present the developer with:

- What was done (per implementation step).
- Any deviations from the original plan.
- Any deferred or follow-up items.

**STOP. Request developer review. The agent never marks a task as completed on its own — only the developer can close a task.**

The developer may:

- **Approve completion** → proceed to 3.2.
- **Request changes** → return to Phase 2 (follow the three-touch iteration cycle).
- **Request additional steps** → update the task file per R5, return to Phase 2.

### 3.2 Update task file (after developer confirms completion)

1. Set status to `Completed` with the current date.
2. Add a final decision log entry summarising the outcome.
3. Note any follow-up work.

### 3.3 Cross-reference check

Light-touch verification:

- If the artifact references other files (e.g., a skill referencing heavy-task-workflow), confirm those references are still accurate.
- If new terms were introduced, check whether they belong in the glossary.
- If the artifact is a skill, confirm the skills list in relevant instruction files or READMEs is up to date.

---

## What light-task-workflow does NOT cover

These are delegated to `heavy-task-workflow` for code work:

- Onboarding companion file reads/writes
- Task block lifecycle (planned → in-progress → readyForReview → completed)
- Drift detection (`lastVerifiedCommitHash`)
- Mandatory sub-skill invocations (Confluence, Context7, Brave are optional here)
- Substep status transitions on onboarding MDs
- Post-commit hash stamping

---

## Relationship to heavy-task-workflow

```
Developer request
       │
       ▼
  Code above heavy-task-workflow threshold? ──yes──▶ heavy-task-workflow (full lifecycle)
       │                                         │
       no                                        ├─ creates onboarding if missing (repo-bootstrap)
       │                                         └─ drift detection, Task blocks, commit hashing
       ▼
  Non-code artifact OR small code change?
       │
       yes
       │
       ▼
  light-task-workflow (plan → approval → implement → close)
       │
       ├─ task file: always
       ├─ approval gate: always
       └─ no onboarding infrastructure
```

`heavy-task-workflow` should reference `light-task-workflow` in its Phase 1: when the agent detects that the target is a non-code artifact or a small code change below the heavy-task-workflow threshold, it directs to this skill instead. Code above threshold — regardless of whether onboarding exists yet — always stays in `heavy-task-workflow`.

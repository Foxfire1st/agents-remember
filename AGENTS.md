## Memory System Awareness

This workspace uses a layered memory system. Understand the layers before acting.

Resolve `AR_MANAGEMENT_ROOT` from `.env` in this repository first. If `.env` is absent, use the default from `.env.example`: `../ar-management`, resolved relative to that `.env.example` file. The active management files then live under this scaffold:

| Layer      | Location                                  | Purpose                                                         |
| ---------- | ----------------------------------------- | --------------------------------------------------------------- |
| settings   | `<AR_MANAGEMENT_ROOT>/system/settings.md` | Derived path contract and management-root scaffold              |
| onboarding | `<AR_MANAGEMENT_ROOT>/onboarding/`        | Code commentary — logic, invariants, conventions, task tracking |
| tasks      | `<AR_MANAGEMENT_ROOT>/tasks/`             | Current change intent, plans, decision logs                     |
| docs       | `<AR_MANAGEMENT_ROOT>/docs/`              | Local domain documentation and mirrors                          |
| sources    | `<AR_MANAGEMENT_ROOT>/system/sources.md`  | References to external technical documentation, mcps, etc.      |
| tools      | `<AR_MANAGEMENT_ROOT>/system/tools.md`    | Repo-specific commands, checks, tools, and MCP notes            |

## Task Format Routing

This workspace has exactly three task/work formats. Choose deliberately before
creating or updating task artifacts.

### 1. Chat Mode

Use chat mode by default when the work is small enough to finish in the current
session and does not need a durable task file.

### 2. Light Task Workflow

Use `W-02-light-task-workflow` whenever a task file is needed. This is the
standard durable-task format for planning and implementation work in this
workspace.

### 3. Heavy Task Workflow

Use `W-01-heavy-task-workflow` only when the developer explicitly asks for the
heavy task workflow, a heavy task, or the full phased workflow.

---

## Chat Based Coding Workflow

1. At the start of a coding workflow, invoke `C-02-onboarding-drift-detection` once for the relevant repository. Do not plan against drifted, missing-verification, or orphaned pre-existing onboarding until it has been refreshed through `C-05-create-or-update-onboarding-files` under `Autonomous Onboarding Maintenance`. This establishes the task-start trust baseline. Do not skip this step, and do not re-trigger it solely because the current task later creates or modifies files in that repository.

2. During investigation, read each relevant source file with its verified onboarding as a pair. If the current task has already modified or created that pair after the gate passed, read the current working versions together and treat them as pending verification rather than re-verified onboarding. Do not bulk-read onboarding as detached background context, and do not defer the onboarding read until after source interpretation. After enough paired reads, show the developer the plan in chat, including code examples for every distinct change you intend to make. Wait for explicit developer approval before you start changing any code.

3. After approval, apply code changes and update the corresponding onboarding in the same editing pass whenever the change affects durable current-state knowledge. Do not postpone required onboarding changes to the end of the task. Use the appropriate code quality checks from `<AR_MANAGEMENT_ROOT>/system/tools.md`.

---

## No Code Changes Before Explicit Developer Approval

When asked to find a sollution to a problem, do not change any code before you have explained your solution in chat with code examples for all distinct changes you intend to make.
**Then wait for developer approval before touching any code!**

---

## Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is
to be read alongside the code they describe, at the moment that code is
inspected. They are not a bulk pre-read and they are not a replacement for
source.

### Onboarding Rules

- When onboarding verification for pre-existing files is blocked because source
  files have no committed state or commit hash, ask the developer whether you may
  commit and push the current state.
- When opening a relevant source file, open its verified onboarding with it.
- When changing code, update the corresponding onboarding in the same editing
  pass whenever the change affects durable knowledge
- Once the hard onboarding gate has passed for the task's repository context,
  files created or modified during the current task may still be opened, read,
  and reasoned about within that same task even though they are now pending
  verification.

### Hard Start-of-Task Onboarding Gate

This gate applies ALWAYS for every Task. Even for code explanations!
No matter if that touches, explains, reviews, plans around,
debugs, or changes a repository code area. Read-only analysis is not an
exception. Code explanation is not an exception. Review is not an exception.
Planning is not an exception.

Before opening, reading, summarizing, or reasoning from source file contents in
the relevant repository:

1. Resolve `AR_MANAGEMENT_ROOT`.
2. Run `C-02-onboarding-drift-detection` for the relevant repository at the
   start of the task.
3. If drifted, missing-verification, or orphaned onboarding is found,
   use the skill `C-05-create-or-update-onboarding-files`. Do not ask the
   developer whether to create or update drifted files!!!! Just do it!
4. Re-run `C-02-onboarding-drift-detection` for the refreshed repository.
5. Proceed only after every pre-existing onboarding file needed for the task is
   classified as up to date. This establishes the start-of-task trust baseline;
   it does not re-trigger solely because the current task creates or dirties
   source/onboarding files.
6. Do not repeatedly drift-check during the same task unless the work expands
   materially to another repository or to a separately unmanaged area.

Do not open or inspect source file contents before this gate passes.

There is no source-only or directional bypass for establishing the initial
baseline in this workspace. If drift detection or onboarding update for
pre-existing files cannot be completed, stop and report the blocker instead of
continuing from source.

DO NOT SKIP OR BYPASS THIS GATE UNDER ANY CIRCUMSTANCE. Quick explanation is not an excuse or exception!

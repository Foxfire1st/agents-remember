## Memory System Awareness

This workspace uses a layered memory system. Make sure to read "Onboarding Rules" chapter before performing actions.

Infer which repository is supposed to be worked on for a given task from the developer prompt. Ask the developer in case its unclear. That inferred repository is considered the "target" repository.

Resolve the active `ar-management/` context for the target repository before relying on onboarding, task files, docs, or tools. Use `C-08-ar-management-resolver` as the normal resolver entry point: pass the repository name and consume the returned local or shared context.

Default to internal topology: the target repository owns `<target-repo>/ar-management/` with `system/settings.md` for prose guidance and `system/settings.json` for machine-readable settings when present. This local root is sufficient for normal operation and does not require a shared `AR_MANAGEMENT_ROOT`.

Use shared topology only when the developer explicitly asks for shared scaffolding or the current repository has already been selected for shared management. In shared topology, resolve `AR_MANAGEMENT_ROOT` from `.env` or `.env.example` and use the shared root for the selected repository. Mixed workspaces are allowed: resolve topology per target repository, so a locally managed repo keeps using its own root while a neighboring shared-managed repo uses the shared root. Keep this paragraph as fallback guidance if the C-08 helper or script cannot run.

The active management files then live under the resolved root:

| Layer         | Location                               | Purpose                                                         |
| ------------- | -------------------------------------- | --------------------------------------------------------------- |
| instructions  | `<resolved-root>/system/settings.md`   | Human and agent guidance, path contract, and scaffold notes     |
| path settings | `<resolved-root>/system/settings.json` | Machine-readable storage, pathRules, and cross-repo data        |
| onboarding    | `<resolved-onboarding-root>/`          | Code commentary — logic, invariants, conventions, task tracking |
| tasks         | `<resolved-root>/tasks/`               | Current change intent, plans, decision logs                     |
| docs          | `<resolved-root>/docs/`                | Local domain documentation and mirrors                          |
| sources       | `<resolved-root>/system/sources.md`    | References to external technical documentation, mcps, etc.      |
| tools         | `<resolved-root>/system/tools.md`      | Repo-specific commands, checks, tools, and MCP notes            |

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

1. At the start of a coding workflow, invoke `C-08-ar-management-resolver` for the relevant repository, then invoke `C-02-onboarding-drift-detection` with the resolved context once for that repository. Do not plan against drifted, missing-verification, or orphaned pre-existing onboarding until it has been refreshed through `C-05-create-or-update-onboarding-files` under `Autonomous Onboarding Maintenance`. This establishes the task-start trust baseline. Do not skip this step, and do not re-trigger it solely because the current task later creates or modifies files in that repository.

2. During investigation, read each relevant source file with its verified onboarding as a pair. If the current task has already modified or created that pair after the gate passed, read the current working versions together and treat them as pending verification rather than re-verified onboarding. Do not bulk-read onboarding as detached background context, and do not defer the onboarding read until after source interpretation. After enough paired reads, show the developer the plan in chat, including code examples for every distinct change you intend to make. Wait for explicit developer approval before you start changing any code.

3. After approval, apply code changes and update the corresponding onboarding in the same editing pass whenever the change affects durable current-state knowledge. Do not postpone required onboarding changes to the end of the task. Use the appropriate code quality checks from `<resolved-root>/system/tools.md`.

---

## No Code Changes Before Explicit Developer Approval (Onboarding Maintenance is an exception!)

When asked to find a sollution to a problem, do not change any code before you have explained your solution in chat with code examples for all distinct changes you intend to make. Onboarding maintenance does not count as code changes!
**Then wait for developer approval before touching any code!**

---

# Onboarding Rules

## Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is
to be read alongside the code they describe, at the moment that code is
inspected. They are not a bulk pre-read and they are not a replacement for
source.

## Code-Onboarding Paired Reads

- Onboarding paths mirror their source code counterparts.
  For example, `src/components/Button.js` has onboarding at `onboarding/src/components/Button.js.md`.
- Read onboardings alongside source files.
- Reading onboarding before planning changes avoids regressions.
- If onboardings are current, they have to be read alongside the code.
- When opening a relevant source file, open its verified onboarding with it.

## Onboarding Maintenance during Implementation

- When you make code changes, do also update or create onboardings using
  `C-05-create-or-update-onboarding-files`.
- Once the hard onboarding gate has passed for the task's repository context,
  files created or modified during the current task may still be opened, read,
  and reasoned about within that same task even though they are now pending
  verification.

## Hard Start-of-Task Onboarding Gate

This gate applies ALWAYS at the start for every Task. Even for code explanations!
No matter if that touches, explains, reviews, plans around,
debugs, or changes a repository code area. Read-only analysis is not an
exception. Code explanation is not an exception. Review is not an exception.
Planning is not an exception.

Before opening, reading, summarizing, or reasoning from source file contents in
the relevant repository you must perform these five gates in order:

Gate 1: Invoke `C-08-ar-management-resolver` for the target repository and use its resolved context for the authoritative `ar-management/` root,
onboarding root, settings path, task root, docs root, system files, storage semantics, `pathRules`, and cross-repo allowances.

Gate 2: Run `C-02-onboarding-drift-detection` for the relevant repository and then read its drift report.
Do not for any reason skip execution of the drift detection skill.

Gate 3: If the drift report indicates any drifted, missing-verification, or orphaned onboarding, tell the developer what
the report says briefly and then ask if they want to update the onboarding before proceeding.
If they say yes, then orchestrate the update process and split the work to up to 5 sub agents who each handle at max 15 files.
All sub agents shall use this skill: `C-05-create-or-update-onboarding-files` and you pass it the instructions it needs to perform the job.
If the developer says says no, tell them that reasoning over drifted onboardings may introduce risk of regressions.

Gate 4: Run `C-02-onboarding-drift-detection` again to confirm that all onboarding is now verified and up to date.
Do not for any reason skip execution of the drift detection skill.

Gate 5: Only after steps 1 - 4 are completed, report to the developer. Then delete the drift report file.

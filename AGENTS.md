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


## Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is
to be read alongside the code they describe, at the moment that code is
inspected. They are not a bulk pre-read and they are not a replacement for
source.

### Hard Start-Of-Task Onboarding Gate

This gate applies to every task that touches, explains, reviews, plans around,
debugs, or changes a repository code area. Read-only analysis is not an
exception. Code explanation is not an exception. Review is not an exception.
Planning is not an exception.

Before opening, reading, summarizing, or reasoning from source file contents in
the scoped code area:

1. Resolve `AR_MANAGEMENT_ROOT`.
2. Scope the relevant repo area using discovery only: file names, directory
   listings, `rg --files`, and path searches are allowed; reading source file
   contents is not allowed yet.
3. Run `C-02-onboarding-drift-detection` once for that relevant scope.
4. If drifted, missing-verification, missing, or orphaned onboarding is found
   for the relevant scope, stop normal task work and route those files through
   `C-05-create-or-update-onboarding-files`.
5. Re-run `C-02-onboarding-drift-detection` for the refreshed scope.
6. Proceed only after every in-scope onboarding file needed for the task is
   classified as up to date.
7. Do not repeatedly drift-check each file during the same task unless the
   investigation expands materially beyond the original scope.

Do not open or inspect source file contents before this gate passes.

There is no source-only or directional bypass in this workspace. If drift
detection or onboarding update cannot be completed, stop and report the blocker
instead of continuing from source.

### Paired Reading Rule

After the hard onboarding gate passes, read source and onboarding as pairs:

- When opening a relevant source file, open its verified onboarding with it.
- Do not bulk-read all onboarding up front unless the source files are also
  being read then.
- Do not read source first and defer its onboarding for later.
- Do not use onboarding as a substitute for source; use both together.
- If a newly relevant file enters scope mid-task, return to the hard onboarding
  gate for that expanded scope before reading the new source file.

### Same-Breath Onboarding Updates

When changing code, update the corresponding onboarding in the same editing
pass whenever the change affects durable knowledge, including:

- purpose or responsibilities
- invariants and boundaries
- conventions
- control flow or data flow
- integration points
- known risks or TODOs
- test/check expectations

Do not postpone required onboarding updates to the end of the task. The update
should happen while the code change and its rationale are still fresh.

### Read Ledger

When working with code, include a short read ledger in chat before planning or
answering from code context. The ledger must distinguish source/onboarding
pairs actually read from files only searched, listed, drift-checked, or
inferred.

Use this shape:

```text
Read for this task:
- Source + onboarding: <source path> + <onboarding path>
- Repo/component onboarding: <path>

Checked but not read:
- <path or scope> - <reason>

Not trusted / refreshed:
- <path> - <missing, drifted, missing verification, orphaned, refreshed, or user-approved bypass>
```

Only list onboarding as read if it was actually opened alongside the
corresponding source file after the relevant drift check passed. Do not claim
onboarding informed the answer unless its content was actually read after the
relevant drift check passed, or unless you explicitly state that it is being
used only as directional context.

## No Code Changes Before Explicit Developer Approval

When asked to find a sollution to a problem, do not change any code before you have explained your solution in chat with code examples for all distinct changes you intend to make.
**Then wait for developer approval before touching any code!**

## Chat Based Coding Workflow

1. At the start of a coding workflow, scope the relevant files/component/repo area and invoke `C-02-onboarding-drift-detection` once for that scope. Do not plan against drifted, missing-verification, missing, or orphaned onboarding until it has been refreshed through `C-05-create-or-update-onboarding-files` or the caller has explicitly accepted directional-only/source-only trust. Do not skip this step; it is critical to maintain onboarding integrity and prevent untracked drift.

2. During investigation, read each relevant source file with its verified onboarding as a pair. Do not bulk-read onboarding as detached background context, and do not defer the onboarding read until after source interpretation. After enough paired reads, show the developer the plan in chat, including code examples for every distinct change you intend to make. Wait for explicit developer approval before you start changing any code.

3. After approval, apply code changes and update the corresponding onboarding in the same editing pass whenever the change affects durable current-state knowledge. Do not postpone required onboarding changes to the end of the task. Use the appropriate code quality checks from `<AR_MANAGEMENT_ROOT>/system/tools.md`.

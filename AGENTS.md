# AGENTS.md

## Task Format Routing

This workspace has exactly three task/work formats. Choose deliberately before
creating or updating task artifacts.

### 1. Chat Mode

Use chat mode `w-03-chat-task-workflow` by default when the work is small enough to finish in the current
session and does not need a durable task file.

### 2. Light Task Workflow

Use `W-02-light-task-workflow` whenever a task file is needed. This is the
standard durable-task format for planning and implementation work in this
workspace.

### 3. Heavy Task Workflow

Use `W-01-heavy-task-workflow` only when the developer explicitly asks for the
heavy task workflow, a heavy task, or the full phased workflow.

---

## Memory System

This workspace uses a layered memory system. Make sure to read the below rules before performing actions.

### Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is to be read alongside the code they describe, at the moment that code is
inspected. They can be found using the ar-coordination resolver.

---

## Ar-coordination & Memory Layer Resolver

Infer which code repository is supposed to be worked on for a given task from the developer prompt. Ask the developer in case its unclear. That inferred repository is the code repository for resolver inputs.

Resolve the active memory and coordination context for the code repository before relying on onboarding, task files, docs, or tools. Use `C-08-ar-coordination-context-resolver` as the normal resolver entry point: pass `code_repository_name` or `code_repository_root` and consume the returned local or shared context.

Default to internal topology: the code repository owns durable memory under `<code-repository-root>/ar-memory/` with `system/settings.md` for prose guidance and `system/settings.json` for machine-readable settings when present. Local coordination artifacts such as tasks, notes, worktrees, and shared memory repo checkouts live under `ar-coordination/`.

Use shared topology only when the current repository has a shared memory repo under the active coordinator. Resolve the shared coordinator from explicit input, `agents-remember-md/.env`, or the built-in default `../ar-coordination`; `.env.example` is scaffolding documentation, not runtime configuration. For a given code repository, check `<code-repository-root>/ar-memory/` first and `<coordination-root>/memory-repos/ar-<code-repository-name>/` second. If neither memory location exists, tell the developer that Agents Remember memory is missing, show both checked paths, ask whether to bootstrap it, and explain that C-00 creates the memory/coordination scaffold while C-03 can then generate onboarding content. Mixed workspaces are allowed: resolve topology per code repository, so a locally managed repository keeps using its own memory root while a neighboring shared-memory repository uses a shared memory repo. Keep this paragraph as fallback guidance if the C-08 helper or script cannot run.

---

The active files then live under the resolved memory and coordination roots:

| Layer         | Location                      | Purpose                                                         |
| ------------- | ----------------------------- | --------------------------------------------------------------- |
| memory root   | `<memory_root>/`              | Durable repo memory, either `ar-memory/` or `memory-repos/ar-*` |
| coordination  | `<coordination_root>/`        | Local tasks, notes, worktrees, and memory-repo checkouts        |
| instructions  | `<system_root>/settings.md`   | Human and agent guidance, path contract, and scaffold notes     |
| path settings | `<system_root>/settings.json` | Machine-readable storage, pathRules, and cross-repo data        |
| onboarding    | `<resolved-onboarding-root>/` | Code commentary — logic, invariants, conventions, task tracking |
| tasks         | `<task_root>/`                | Current change intent, plans, decision logs, contracts          |
| docs          | `<docs_root>/`                | Local domain documentation and mirrors                          |
| sources       | `<sources_path>`              | References to external technical documentation, mcps, etc.      |
| tools         | `<tools_path>`                | Repo-specific commands, checks, tools, and MCP notes            |

# AGENTS.md

## Task Format Routing

This workspace has exactly three task/work formats. Choose deliberately before
creating or updating task artifacts.

### 1. Chat Mode

Use chat mode `w-03-chat-task-workflow` by default when the work is small enough to finish in the current session and does not need a durable task file.

### 2. Light Task Workflow

Use `W-02-light-task-workflow` whenever a task file is needed. This is the
standard durable-task format for planning and implementation work in this
workspace.

### 3. Heavy Task Workflow

Use `W-01-heavy-task-workflow` only when the developer explicitly asks for the
heavy task workflow, a heavy task, or the full phased workflow.

---

Do not change code without following one of the above workflows!

## Memory System

This workspace uses a layered memory system. Make sure to read the below rules before performing actions.

### Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is to be read alongside the code they describe, at the moment that code is
inspected. They can be found using the ar-coordination resolver.

Before trusting the onboarding documentation, check the [Memory Layer Instructions](system/AGENTS.md)

---

## Ar-coordination & Memory Layer Resolver

Infer which code repository is supposed to be worked on for a given task from the developer prompt. Ask the developer in case its unclear. That inferred repository is the code repository for resolver inputs.

Resolve the active memory and coordination context for the code repository before relying on onboarding, task files, docs, or tools. Use `C-08-ar-coordination-context-resolver` as the normal resolver entry point: pass `code_repository_name` or `code_repository_root` and consume the returned local or shared context.

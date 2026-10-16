# Core — The Ambient Launcher (a chat without a role, not a role)

The developer-facing **free chat** is a **launcher, not a role seat** (ruled 2026-07-09). It is
deliberately **not** a role: the role registry
holds only roles, and no file under `roles/` describes this mode. It has no binding
or worktree of its own.

## What it is

A developer opened this session and the product did not start it: it has no binding. The
launcher is the workspace entrypoint's developer-facing face.

- **Research-only questions are answered inline, with no role taken.** Reading, explaining,
  assessing, and answering change no code and need no task file: chat is the right medium, and
  there is no worktree and no task artifact.
- **The launcher is never the architect, and never becomes one.** It does not assume the architect
  role, does not own the developer conversation for a sprint, and does not accumulate portfolio
  state. The architect is a *separate, spawned* seat.

## Ordinary role-shaped work — the one call

For ordinary role-shaped work (a sprint, a task, any durable change that is not an explicit
task-seat takeover), the launcher:

1. resolves the canonical target sprint and its **canonical sprint document**;
2. writes the request of the developer into that document;
3. starts an Architect with `role_start` **once** (the call carries no brief, and for
   an Architect it names no document);
4. tells the developer that the agent is in the dashboard and which sprint document holds
   the request, and stops role work here.

The call carries no parent and no brief: what the new agent must know stands in the sprint
document before the start, and the developer names that document to the Architect in the
dashboard. The role settings choose harness, model, effort and permission mode.
The agent is shown in the dashboard and the developer speaks with it there; `role_message` from
this chat is refused. A refusal never falls back to another tool.

For a **first sprint**, free chat uses the ordinary durable task workflow to create the master and
first leaf *before* this launch; that bounded bootstrap creates scope data, not a global role seat.

## Task-seat takeover — the bounded exception

An explicit developer-declared task-seat takeover starts the **named role** on that role's
canonical task document instead of first creating an architect — and only when the
developer names the role and the task. The full contract is in
`core/authority.md`; the launcher's part is that it makes the same single `role_start`
call with the named role and that role's canonical document.

If the named role does not fit the named document, `role_start` refuses, and the launcher says
what is missing rather than guessing.

## What the launcher must not do

- Do not take a role, wear a hat, or "temporarily be" the architect to get started.
- Do not start an agent any other way than `role_start`, and do not message it: the developer
  speaks with it in the dashboard.
- Do not keep working locally after the agent is started.

## Why it is authored here rather than in `roles/`

The architecture requires the launcher to remain a **distinct routing condition** rather than an
invented role. Authoring its obligations in `core/` keeps that boundary structural — a reader
enumerating `roles/` sees only seats — while still giving the launcher a complete, single home for
its own duties.

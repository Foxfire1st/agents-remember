# Core — The Ambient Launcher (routing condition 3, not a role)

The developer-facing **free chat** is a **launcher, not a role seat** (ruled 2026-07-09). It is
routing condition 3 of `../SKILL.md`, and it is deliberately **not** a role: the role registry
holds only roles, and no file under `roles/` describes this mode. It has no plane
identity, no worktree, and no lifecycle of its own.

## What it is

A developer opened this session, and neither `AR_SPAWN_ROLE` nor a role brief claimed it. The
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
2. compiles **one complete brief** from `../templates/architect-brief.md`;
3. calls `dispatch_agent(task_document_ref=<canonical sprint document>, role="architect",
   brief=<compiled brief>)` **once**;
4. on `dispatched` **or** `dispatch-queued`, switches the developer conversation to the canonical
   `(sprint document, architect)` chat and stops role work here.

Both results mean the brief is durable, so **never send a second brief**. No plane-injected hosted
identity selects ambient-launcher mode; the canonical target document and architect altitude supply
its authority. The launcher never submits caller identity and never handles a session id. The
control plane chooses the settings-owned harness/model/effort, creates the seat, and durably pins
the exact brief. A plane refusal never falls back to ambient.

For a **first sprint**, free chat uses the ordinary durable task workflow to create the master and
first leaf *before* this launch; that bounded bootstrap creates scope data, not a global role seat.

## Task-seat takeover — the bounded exception

An explicit developer-declared task-seat takeover dispatches the **named role** on that role's
canonical task document instead of first creating an architect. The full contract is in
`core/authority.md`; the launcher's part is that it makes the same single ambient `dispatch_agent`
call with the named role and that role's canonical document.

If the named role's altitude cannot be matched to the named document, ambient mode has no basis to
choose, parent operations fail closed, and the launcher records the structural blocker rather than
guessing.

## What the launcher must not do

- Do not take a role, wear a hat, or "temporarily be" the architect to get started.
- Do not call a terminal attach/session primitive, or read, request, paste, or retain a
  session/lifecycle/agent id.
- Do not fabricate caller identity, and do not use ambient mode as an in-hierarchy escape.
- Do not compile spend knobs (harness/model/effort) into the brief.
- Do not keep working locally after a durable dispatch result.

## Why it is authored here rather than in `roles/`

The architecture requires the launcher to remain a **distinct routing condition** rather than an
invented role. Authoring its obligations in `core/` keeps that boundary structural — a reader
enumerating `roles/` sees only seats — while still giving the launcher a complete, single home for
its own duties.

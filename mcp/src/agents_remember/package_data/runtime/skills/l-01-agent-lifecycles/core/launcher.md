# Core — The launcher for a chat without a role

This authoring reference defines the duties of developer-facing free chat when it launches role work. The launcher is not a role seat: it has no binding or worktree, and the role registry and `roles/` contain only actual roles.

## Answer research questions in chat

A developer opens this session; the product did not start it and supplied no role binding. It is the developer-facing entry to the workspace.

Answer research-only questions inline without taking a role. Reading, explaining, assessing and answering change no code. They need no task file, worktree or task artifact.

Before handling a request for role work, read [Keep the launcher's boundaries](#keep-the-launchers-boundaries) for the role the launcher never takes and its single `role_start` route.

## Launch ordinary role work once

For a sprint, task or other durable change that is not an explicit task-seat takeover, first read [Keep the launcher's boundaries](#keep-the-launchers-boundaries) for the role the launcher never takes and its single `role_start` route. Then:

1. Resolve the canonical target sprint and its canonical sprint document.
2. Write the developer's request in that document.
3. Call `role_start` once to start an Architect. The call carries no brief and, for an Architect, names no document.
4. Tell the developer that the agent is in the dashboard and name the sprint document holding the request.

The call carries no parent or brief. Everything the new agent must know is in the sprint document before the start; the developer names that document to the Architect in the dashboard. Role settings choose the harness, model, effort and permission mode.

For a first sprint only, create the sprint document, a master and its first leaf before you start the Architect. Write into them the developer's request and scope only: no requirement packets and no approved order.

## Handle an explicit task-seat takeover

When the developer explicitly names both a role and its task, start that named role on its canonical task document instead of first creating an Architect. Follow [Handle a developer-declared takeover](authority.md#handle-a-developer-declared-takeover) for the complete takeover contract.

The takeover contract supplies the single start call, canonical selection and refusal response.

## Keep the launcher's boundaries

Take no role, wear no role's hat and do not temporarily act as the Architect to get started. The launcher never becomes the Architect. It does not own a sprint's developer conversation or accumulate portfolio state. The Architect is a separate, started seat.

Use `role_start` to start the agent; no other route is allowed. Do not message it. The developer speaks to the agent in the dashboard. A `role_message` from launcher chat is refused. Report a refusal without switching to another tool. After the start, stop working locally on that role's work.

## Keep this routing condition outside the role registry

The launcher is a distinct routing condition, not an invented role. Its instructions live in `core/` so that readers enumerating `roles/` see only seats, while the launcher has one complete home for its own duties.

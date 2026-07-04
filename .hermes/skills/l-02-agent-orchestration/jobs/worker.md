# Job — Worker

> **Role + lens in one file.** This is the portable job file the `l-02-agent-orchestration` frame
> houses at the **worker** seat. It carries both axes: the **role** (implement exactly one leaf,
> short-lived) and the **lens** (the `l-01-session-job-lifecycle` build spine, worker lens). A harness
> overlay ships: `jobs/worker.claude-code.md`.
>
> Drawn as the **WORKER** model on the FlowTab canvas (`dashboard/src/panels/flowModels.ts`).

## What This Seat Is

**One per task leaf, short-lived, fresh session.** Spawned by the manager on a single leaf, onboarded
from the **context packet + the `task_doc`** — **never from a transcript**. It runs the normal
`l-01-session-job-lifecycle` build spine inside the leaf worktree (`worktree_attach`, its own harness +
MCP process — the AmbientLifecycle singleton is preserved, D11). Its continuity lives in the `task_doc`
+ its turn report, not in the session, which is why it can be short-lived and respawned safely.

## Lens

- **Opening move:** attach the leaf worktree and read the leaf plan + `task_doc`; the leaf is already
  scoped, so there is no reframe here — the design was done upstream (designer + orchestrator).
- **Retrieval lean:** intent-confirmation on the leaf's own files (paired `read_ar_files`); implement to
  the plan.
- **Decide default:** build — the leaf is a build unit by construction.

## The Default-Behavior Rule (read this before anything else)

The **default agent behavior stands**: **fulfill the task, fill small blanks.** A worker gets **no
creative-liberty prompting in either direction.** Implement the leaf plan; fill small, unambiguous
blanks a competent implementer would fill; run the checks; refresh onboarding in the same pass.

> **The spirit test does NOT apply to this seat.** It is orchestrator-only. A worker's changes can
> collide with what it cannot see, so a **plan delta beyond blank-filling escalates to the manager**
> (up the ladder — never straight to the developer, never a reshape of its own). This is the ordinary
> "do the leaf well, ask when the leaf itself is in question" default.

## Duties (the l-01 build spine, worker lens)

1. **Attach** — `worktree_attach` resumes the leaf's persistent lifecycle; own harness + MCP process.
2. **Implement per the leaf plan** — and **refresh the matching onboarding in the same pass** via the
   `c-05-create-or-update-onboarding-files` skill (a changed source file's sidecar body is updated now;
   a new file's missing sidecar is created before commit).
3. **Checks green before every incremental commit** — the `system/tools.md` suite (lint · typecheck ·
   complexity · tests). Testing is never deferred.
4. **Closeout** — `worktree_closeout_preview` (dry-run, self-fix first), then `worktree_closeout_apply`
   (commit code · memory · ledger). **The closeout gate is decided by the MANAGER** (delegated,
   attributed) — not the developer; human review waits at the master/super seams. (Enforcement is L4;
   until then the hand-off follows the `l-01-session-job-lifecycle` skill.)
5. **Integrate** — `worktree_integrate` lands the leaf into the master integration branch (the recorded
   source branch).
6. **Turn report** — write the **mandatory** turn-report artifact (see below), then the session ends.

## Artifact Obligations

- **A MANDATORY turn report** (`templates/turn-report.md`) at **every** hand-off, written to
  `notes/reports/<leaf-id>-worker-report.md`: what was done · issues hit · what was solved · what is
  left · the respawn-onboarding state for a successor. **A missing report gets nudged by the manager**
  through the `orchestration_nudge_manager` helper. This is the worker's single most important
  obligation — it is how the work survives the session's death and how a respawned successor onboards
  from state.

## Comms Protocol

- **Inbox** (`operator_inbox_post` / `_poll` / `_consume`) — receive the dispatch/context packet; post
  the turn report; raise escalations. Agent-to-agent rows carry sender/recipient role metadata and a
  `messageKind` (`turn-report`, `nudge`, `escalation`, …), while the same durable row stays visible on
  the dashboard.
- **Stdin push** — the manager delivers nudges/messages into this hosted session; the worker's replies
  are inbox rows or the turn-report artifact — **never an untracked side channel**.
- **Escalation** — **up to the manager, never straight to the developer.** A stumped worker, and any
  plan delta beyond blank-filling, raises to the manager via the inbox (push-delivered).

## Knobs

| Knob    | Default        | Notes                                                          |
| ------- | -------------- | -------------------------------------------------------------- |
| harness | claude-code    | portable default; `jobs/worker.claude-code.md` carries specifics|
| model   | mid-reasoning  | competent implementer on a scoped leaf                         |
| effort  | medium         | one leaf; effort scales with leaf difficulty via settings      |
| tools   | build surface  | `worktree_attach` · native edit · `read_ar_files` · `c-05` · `system/tools.md` checks · closeout/integrate · inbox |

Settings.json `orchestration.roles.worker` overrides these (job base < variant < settings).

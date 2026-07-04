# Job Variant — Worker on Claude Code

> **Overlay, not a replacement.** This file overlays the portable `roles/worker.md` when the worker seat
> runs on the **claude-code** harness. It carries only what is harness-specific: the concrete knobs and
> the AR-mutations-stay-in-the-main-loop rule as it applies to a worker's own fan-out. It **does not
> restate** the worker loop, the default-behavior rule, or the escalation rung — read `roles/worker.md`
> for those.
>
> Resolution: `roles/worker.md` (base) → **this overlay** → settings.json orchestration block.

## Harness Knobs (override the base knob block)

| Knob    | Value on Claude Code                              |
| ------- | ------------------------------------------------- |
| harness | claude-code                                       |
| model   | a mid-reasoning model (settings.json `orchestration.roles.worker.model` names the concrete id) |
| effort  | medium (scales with leaf difficulty via settings) |
| tools   | build surface + the `Agent`/`Task` sub-agent tool for read-only fan-out |

## AR-Mutations-Stay-In-The-Main-Loop (the Claude Code idiom)

A worker mostly implements directly, but when it fans out sub-agents on Claude Code (e.g. a broad search
to locate call sites, an onboarding sweep across the leaf's files), the same invariant as the
orchestrator applies, scoped to the leaf:

- **Sub-agents fan out for READ/SEARCH and write durable notes**; they return compact summaries.
- **The worker's own main loop owns every durable act** — native edits,
  `c-05-create-or-update-onboarding-files` sidecar writes, and the **mandatory turn report** are all
  main-loop acts. The worker owns **no** AR lifecycle machinery at all — closeout, integration, and
  finalization belong to the owning seat. A sub-agent never edits the worktree, never writes a
  sidecar, and never posts the turn report.
- **The turn report is written by the worker, in the main loop**, from the sub-agents' summaries + its
  own work — never delegated to a sub-agent, because it is the leaf's single artifact of record and must
  reflect the main loop's actual state.

## Fresh Session, State-Not-Transcript

On respawn, a Claude Code worker onboards from the leaf `task_doc` + the previous worker's turn report
(`../templates/turn-report.md`) — **not** from any prior transcript. The short-lived-by-design rule is a
harness-level guarantee here: continuity is the durable state, so a killed or compacted worker session
loses nothing a successor cannot reconstruct from the report.

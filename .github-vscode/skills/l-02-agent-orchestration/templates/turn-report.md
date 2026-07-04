# Turn-Report Template

The **mandatory** artifact a worker writes at **every** hand-off (`jobs/worker.md`). It is how the
leaf's work survives the session's death and how a respawned successor onboards from **state, not the
transcript**. A missing turn report is nudged by the manager.

## Rules

1. Write it in the **main loop**, from your own work plus any sub-agent summaries — never delegate it to
   a sub-agent (it is the leaf's single artifact of record).
2. State facts, not a narrative: what changed, what broke, what is proven green, what remains.
3. The **Respawn State** section must let a fresh successor continue **without reading any transcript**.
4. Keep it durable and in the series notes; reference it from the leaf `task_doc`.
5. Default path: `notes/reports/<leaf-id>-worker-report.md`. The control-plane helper exposes this
   convention so manager nudges and respawn onboarding can point at the same artifact.

## Shape

```md
# Turn Report — <leaf id> · <short leaf title>

| Field        | Value                                   |
| ------------ | --------------------------------------- |
| leaf         | <leaf id / task_doc path>               |
| master       | <master id>                             |
| worker       | <this session's agent/lifecycle id>     |
| worktree     | <branch / worktree>                     |
| status       | in-progress | leaf-complete | blocked    |
| checks       | green | failing:<which> | not-yet-run    |
| written      | <YYYY-MM-DDTHH:MM>                       |
| inbox        | <operator_inbox entry id or none>        |

## What Was Done
- <concrete change> (files: `<path>`, …)

## Issues Hit
- <issue> → resolved: <how> | still open: <what it blocks>

## Solved On The Spot
- <blank filled / small decision made> (a plan delta beyond blank-filling is NOT here — it was
  escalated to the manager; see Escalations)

## What Is Left
- [ ] <remaining step from the leaf plan>

## Onboarding Refreshed
- <sidecar path> — <changed | created> in this pass (c-05)

## Escalations
- <plan delta beyond blank-filling raised to the manager> | none

## Respawn State (onboard a successor from this — no transcript needed)
- Current position in the leaf plan:
- Files touched so far:
- The one thing a successor must know before editing:
- Uncommitted vs committed state:
```

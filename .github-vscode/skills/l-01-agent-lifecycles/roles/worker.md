# Lifecycle — Worker

> One leaf, one session, one report. The worker lifecycle is **self-contained**: everything this
> seat does is on this page, and your **brief is your session start** — a workspace session-start
> notice is not addressed to you.

## What This Seat Is

**One per task leaf, short-lived, fresh session.** Spawned by the leaf's owning seat (manager, or
the orchestrator in a flat series) with a brief compiled from `templates/worker-brief.md`. It
onboards from **the brief + the leaf `task_doc` + the previous worker's turn report** — never from a
transcript. Its continuity lives in the `task_doc` + its own turn report, which is why it can be
killed, compacted, or respawned without losing anything a successor cannot reconstruct.

The worker builds; it does not manage lifecycle machinery. **Closeout, integration, finalization,
gates, and task-doc bookkeeping belong to the owning seat, not to this one.** The worker's terminal
state is *checks green + turn report written* — nothing after that is its concern.

## The Worker Loop

```
brief -> orient -> build (edit + onboarding same-pass) -> checks green -> turn report -> end
                        |
                        +-- blocked or plan delta beyond blank-filling -> escalate to the owning seat
```

### 1 — Intake

Read the brief fully, then the leaf spec / `task_doc` it names. The leaf is already scoped and
approved upstream — there is no reframe here and no plan gate. The brief names your two writable
areas: the leaf's **code worktree** and **memory worktree** (plus your report path). You edit
nothing outside them.

### 2 — Orient (paired reads before edits)

- Read the files you will touch **paired with their onboarding** — via the `read_ar_files` MCP tool
  (note: it serves the official baseline, not your worktree) and native reads inside the worktree
  for current state. Native read is your edit precondition.
- Retrieval when the leaf needs it: `grepai_search` (semantics), `cgc_*` (relationships) — both
  read-only, with whatever stack key the brief names. Keep the evidence tally your brief asks for
  (calls made, files inspected, gaps remaining).

### 3 — Build

- Implement exactly the leaf plan; fill small, unambiguous blanks a competent implementer would
  fill (see "Default Behavior" below).
- **Refresh the matching onboarding in the same editing pass** per
  `c-05-create-or-update-onboarding-files`: a changed source file's sidecar **body** is updated now;
  a new file's sidecar is created; route overviews that need a genuine body update get one, and a
  no-impact route gets the literal history form `- <ISO timestamp> — No route impact: <reason>`.
  Regenerate generated route indexes with a **local `build_route_indexes(...)`** invocation from the
  memory worktree.
- **Never `git commit`.** Leave all changes uncommitted in both worktrees — the owning seat commits
  at closeout after reviewing your report.

### 4 — Checks (green before you report)

Run what the brief prescribes — typically the focused suite over your changed paths plus the full
`system/tools.md` wrapper from the code worktree root — and record the exact commands + outcomes for
the report. A red check you cannot fix inside the leaf's scope is an escalation, not a workaround.

### 5 — The Turn Report (mandatory, your last act)

Write `templates/turn-report.md` to the path the brief names (convention:
`notes/reports/<leaf-id>-worker-report.md`): what was done · issues hit · solved on the spot · what
is left · onboarding refreshed · checks with commands · retrieval evidence · escalations · respawn
state. **A missing report gets nudged.** The report is the leaf's artifact of record and how a
respawned successor onboards — write it even when blocked (with the Escalations section filled),
then end your turn.

## Tool Surface (positive statement — this is all of it)

- **Native file tools** inside the two worktrees (read / edit / create).
- **Read-only AR retrieval:** `read_ar_files`, `grepai_search`, `cgc_*`, `context_packet`.
- **Shell** for the prescribed checks (use the interpreter paths the brief names — do not assume a
  `python` shim exists).
- **Inbox** (`operator_inbox_post` / `_poll` / `_consume`) for receiving context and raising
  escalations, when the brief wires it.

Everything else — `worktree_*`, `lifecycle_*`, `task_doc`, `gate_*`, `memory_*`,
`route_index_refresh` — is the owning seat's machinery, not yours. A worker that never touches
lifecycle machinery never instantiates a lifecycle; that is the designed shape, not a gap.

## Fan-Out (capability doctrine — any harness that has it)

When the harness offers sub-agents, use them for **read/search only**, scoped to the leaf (locate
call sites, sweep onboarding): each writes durable notes and returns a compact summary. The
worker's own main loop owns **every durable act** — native edits, `c-05` sidecar writes, and the
mandatory turn report, which is never delegated because it must reflect the main loop's actual
state. No sub-agent touches AR tools; a harness without fan-out simply does these reads
sequentially (workers do not spawn AR sessions — that is the spawning seats' channel).

## Loop Position (when the leaf runs as a three-party loop)

The owning seat scores each leaf into a tier at dispatch (loop doctrine: `../SKILL.md`, The
Three-Party Loop). On a **builder-verified** or **full-loop** leaf, this seat is the **BUILDER**:
your turn report is the round's input, and the owner verifies it report-vs-artifact before
anything lands. Two consequences for you:

- **Fix rounds resume THIS session** — the same builder, with its context intact. Your round-2+
  report **appends** to your report file rather than rewriting it, so the loop history stays
  legible.
- **Rounds are capped and must converge**, but the cap, the convergence call, and any escalation
  are the OWNER's controls, not yours. You build and report honestly; if you disagree with a
  reviewer finding you were handed, say so **with evidence in your report** — the owner rules,
  you never argue a verdict into the code.

## Default Behavior

**Fulfill the task, fill small blanks.** No creative-liberty prompting in either direction. The
spirit test lives with the orchestrator, not here: your changes can collide with what you cannot
see, so a **plan delta beyond blank-filling escalates to the owning seat** — never straight to the
developer, never a reshape of your own. This is the ordinary "do the leaf well, ask when the leaf
itself is in question" default.

## Comms

- **Inbox** — receive dispatch/context; post escalations; agent-to-agent rows carry role metadata
  and a `messageKind` (`turn-report`, `nudge`, `escalation`, …), durable + dashboard-visible.
- **Stdin push** — the owning seat delivers nudges/messages into this hosted session; your replies
  are inbox rows or the turn report — never an untracked side channel.
- **Escalation** — one rung up, always: **worker → owning seat (manager/orchestrator).**

## Knobs

| Knob    | Default        | Notes |
| ------- | -------------- | ----- |
| harness | codex          | default preference only — settings picks the actual harness |
| model   | mid-reasoning  | competent implementer on a scoped leaf |
| effort  | medium         | scales with leaf difficulty via settings |
| launchArgs | — | free-form escape: verbatim harness argv (settings-only; never validated, recorded in spawn provenance) |
| sessionCommands | — | free-form escape: lines pasted + submitted into the fresh session before the brief (settings-only; never validated) |
| promptKeywords | — | free-form escape: prepended as the first line of the dispatch brief paste (settings-only; never validated) |
| tools   | build surface  | native edit · read-only AR retrieval · prescribed checks · inbox |

Settings.json `orchestration.roles.worker` overrides these, and `orchestration.rolesPerLevel.<level>.worker` overrides per dispatch level (role-file defaults < settings < level override; spawn knobs manual: `docs/reference/harnesses.md`).

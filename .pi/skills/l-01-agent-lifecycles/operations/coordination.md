# Operation — Coordination

**What it covers:** driving a portfolio or a master-series from durable state — routing backend
events, recomputing the ready frontier, releasing work in order, and keeping the developer relay to
one decision at a time. The owning seats' **own** series loops live in their role files; this block
carries only what a coordinating seat needs that its role file does not already own.

**When it is selected:** a managing seat is driving its level's sequence — the orchestrator its
portfolio, the manager its one master, and the architect when it wears either hat in a solo/flat run.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| orchestrator | routes backend events into portfolio/orchestration work; recomputes after every material event; releases only the first-ready generation |
| manager | drives exactly one master's leaf sequence; publishes door facts; never ranks the portfolio |
| architect (solo/flat only) | the same duties under one owner chair when no backend seat is spawned |

## Required inputs

- The canonical sprint/master documents and their leaf docs, read from durable state — masters,
  leaves, statuses, decision logs, `openQuestions`, contracts, inbox rows — never the transcript.
- The mechanical facts the control plane supplies: graph validity, derived waves, completed
  predecessors, current closeout-door generations, closeout-projection validity, lineage, gates, and
  changed routes/seams.
- The accepted priority grades and any recorded urgency change.
- For a manager: the master's explicit `executionNature` and its sprint graph reference.

## Normal workflow

1. **Route the event by what exists and what is asked** — no task doc and a backend request →
   a decision/design item to the architect; designed masters with coherence/order in question →
   portfolio streamlining and planning; an approved series ready for implementation → orchestrate;
   an ask that changes no code → a research-only exit with no worktree and no task artifact.
2. **Build the legal frontier first.** Never rank a blocked node. Dispatch independent ready work in
   parallel up to the applicable `orchestration.concurrency` cap.
3. **Select from current truth, not from a queue row.** A missing, malformed, source-mismatched, or
   `invalid-empty` closeout projection is non-admitting: execute its exact addressed rebuild action
   and read status again. A valid projection contains only current `waiting` door generations.
4. **Record a priority judgment before it changes selection** — rationale, evidence, author,
   confidence, and supersession in the sprint decision log and the orchestration task's Judgment
   Register — then publish the changed grade through the door owner and rebuild. **The record makes
   judgment visible; it does not turn the projection into the judge.**
5. **Recompute after every material event**: a task mutation's `projectionEffects`, a door
   declaration/disposition/provenance change, a landing, an atomic-blocker change, or an accepted
   reprioritization.
6. **Keep the developer relay to one item at a time.** Every developer-worthy item goes to the
   architect as one `decision-item`; the coordinating seat stops acting on it until a ruling or
   clarification returns. Do not open a second item while the first is unresolved.
7. **Apply the spirit test** (orchestrator only — see `../roles/orchestrator.md`) to escalated
   deltas and route the result.

## Authority gates

- **Every intrinsically valid `task_doc` mutation is allowed in every phase.** Read the returned
  `projectionEffects`; the task write is already authoritative, and a closeout generation is never a
  reason to refuse, roll back, or delay it. A projection rebuild is follow-up work.
- **Gate delegation**: leaf gates are the manager's to decide (attributed:
  `decidedBy: <manager lifecycle>`, `decidedVia: orchestration`). Human-pinned kinds —
  `integration-approval`, `push-approval`, `cleanup-approval` — are never delegable.
- **Owner-never-self-approves** holds: the agent that raised a gate never decides it; a configured
  distinct role may.
- **A manager never orders another master**, never assigns or changes cross-master priority, never
  releases another manager, and never closes out before the orchestrator grants the current
  first-ready generation from a valid projection.
- **The orchestrator never becomes the developer-facing architect** and never converses with the
  developer directly.
- **No native sub-agents on orchestration seats.** Every agent the work needs is either the seat's
  own main loop or a role seat spawned through AR itself. **AR state mutations stay in the owning
  seat's main loop** — no other agent calls `task_doc`, gates, `dispatch_agent`, or closeout on its
  behalf.
- **Conflict resolution:** integration branches are not workbenches. An overlap found during planning
  becomes a cited predecessor edge or an atomic foundation master implemented first. Late in baseline
  execution, return the repair to the leaf that owns the change, or create a scoped fix leaf when it
  is genuinely new work, then re-enter the door/projection path with new proven provenance. Direct
  feature/fix commits on main, super, or an atomic integration branch are forbidden.
- **Failed-deliverable rule:** during baseline execution a leaf whose deliverable came out wrong is
  **reopened under its own id** and its doc reshaped; new leaves are only for genuinely **new**
  changes. Spawning a sibling per failed attempt hides what went down and splits the change-set.

## Failure handling

- **A missing primitive is never approximated** with direct Git, task freezes, queue lifecycle rows,
  scanned journals, or compatibility readers. If an owner is unreadable, fail closed only at its own
  boundary and report the task-addressed repair.
- **Mechanization boundary:** the typed graph and execution-nature schema own task ordering; door
  generations own closeout intent; the closeout queue is a disposable waiting-only projection; the
  enclosure-root journal owns accepted-operation lifecycle; the landing lane owns protected-ref
  movement and atomic exclusion only.
- **A manager escalation may carry a loop's full round history** (cap hit, or a round that failed to
  shrink the finding set). Preserve that history and escalate it; **do not** silently re-run a
  governed review, reset its baseline, or create a new finding list.
- **Work outside the accepted plan** stops for the developer: the plan's meaning changed, checks
  remain red outside scope, or a quo-vadis truth is in play.

## Handoff / exit

A coordination seat's exit is its level's own packet: the manager's master-handover packet, the
orchestrator's super-exit packet plus demo notes. Write the packet before ending the turn; the
mechanical turn-ended signal then wakes the structural owner, who validates it (see
`../core/acceptance.md`).

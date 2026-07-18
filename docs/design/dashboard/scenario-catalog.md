# Cockpit Dashboard — Workflow Scenario Catalog

The intended user workflows the agents-remember "Mission Control" cockpit dashboard must support, and
the views that serve each. This is the foraging-target set the `dashboard-experience-review` dev-skill
grades the live UI against: every scenario step with no serving view, and every reachable system state
with no rendering, is a **missing view**.

**Living document.** Stage 1 of the review refreshes this from the running system
(`provider_status` / `worktree_status` / `server_info` + the dashboard's `observer/` + `data/` +
`types/projection` code) on each run. Seeded 2026-06-23 from a paired source+onboarding grounding of
the `260610` dashboard + Task-6 worktrees; refreshed 2026-07-18 for the FEUI Chats-cockpit cutover.

**Design thesis** (the two fixed points the cockpit is judged against): *"it should never be just a
pretty toy"* and *"a cockpit is useless if all you can do is watch."* Value = (a) visibility into the
inner workings, and (b) a surface to **act on** the running system without switching back to the
harness. The unit of work is **the lifecycle**, not the repo.

---

## View inventory

Persistent shell (never hides the alarms): top status bar (master-caution `⚠ N waiting`), left rail
(AttentionQueue + LifecycleList), switchable centre viewport, right rail (EventRiver), bottom ModeBar.

| View | Purpose | Primary persona | Status |
|---|---|---|---|
| **Operations** (`DetailPanel`) | inspect the selected lifecycle; phase stepper; Gate Review drawer; task-document reader | reviewer/approver | built (gate write = 6c) |
| **Engine Room** | full-bleed bird's-eye podracer map of a worktree's git-lifecycle; engines, conduits, coupler, boot timeline, diagnostics, failure overlays | diagnoser | built (most-iterated) |
| **Memory** (`MemoryMirror`) | onboarding-vs-code health; coverage/drift/ledger/stalest-sidecar | drift auditor | built |
| **Topology** | full-bleed radial constellation; at-a-glance workspace overview; click node → Operations | overview | built — ⚠ rendered an **empty canvas** in the 2026-06-23 dogfood (0 painted pixels, no nodes, no empty-state) — verify |
| **Hangar** | persistent (never-reaped) worktree debt; review/closeout/integration/cleanup badges | cleanup | built |
| **Chats** | sole product-facing full-bleed fleet cockpit: native hosted-chat and raw-terminal launch, focus/PTY, reliable text submit/pop-back, interactions, lifecycle/leaf routing, requested/effective controls, and Evidence/Capabilities/Bus inspection | operator | built — keep-alive cockpit; Operations remains the application default |

Persistent side panels: **AttentionQueue** (server-ranked "what needs the human"; `Open` jumps to the
lifecycle), **LifecycleList** (all lifecycles; BY REPO / BY PHASE pivot), **EventRiver** (raw event
feed; trust-keyed left border). The cockpit also retains **HighlightComposer** (selection → reliable
text submit) and the shared **SessionComposer** used by session surfaces. Attachments/images remain an
upstream contract gap; there is no image-by-path composer claim.

**Not user views** (developer-only, dropped from prod bundle): `/dev/bench`, `/dev/reference` — the
review must NOT treat these as user views.

**Planned, not built** (real intended views the review can flag as "missing today"): an **Onboarding
Inspector** (tree | code | paired-onboarding, worktree-aware — task 260621) and a **live-data /
REMOTE·ORIGIN** extension of the engine room.

---

## W1 — Triage what needs me (the home workflow)

**Persona:** operator
**Job story:** "When I sit down across many repos and parallel sessions, I want to see and clear
everything waiting on me, so I don't miss a gate or a question."
**Frequency:** every session, first thing.

**Steps → serving view → stuck risk**

1. Land on the cockpit                  → top-bar master-caution + AttentionQueue   → ok
2. Read what's waiting                  → AttentionQueue (severity-ranked)          → ok
3. Open an item                         → `Open` → Operations, lifecycle selected   → ok
4. **Act on it**                        → Gate Review drawer (commit gate)          → ⚠ only the commit gate has a write path

**Forced states:** content · first-run-empty · zero-result-empty (nothing waiting) · loading · stale-disconnected
**Known gap:** non-gate attention items (answer a question, make a decision, ack an alarm,
retry-failed-setup, restart-degraded-provider) are listed but render as display-only `aria-disabled`
affordances — the queue says "decision needed" with no control to give it.

## W2 — Watch a lifecycle run and land

**Persona:** operator
**Job story:** "When a worktree is running, I want to watch it go start → build → closeout → integrate
→ cleanup, so I know it's progressing and notice when it needs me."

**Steps → serving view → stuck risk**

1. Select the lifecycle                 → LifecycleList                             → ok
2. Watch the choreography               → Engine Room (boot/land/teardown)          → ok
3. Notice a human-gated landing beat    → AttentionQueue + engine-room phase-chip pulse → ok

**Forced states:** content · loading · partial · stale-disconnected
**Known defect (carried in onboarding):** at D5 (`cleanup-pending`) the landing tier fails to retract
(feat ~0.98, conduits ~0.6, flows ~0.9 at rest) because `cleanup-pending` is still in `LANDING_PHASES`.

## W3 — Diagnose a degraded provider / broken worktree

**Persona:** incident-responder
**Job story:** "When something broke, I want to see the state and the safe next action, so I can fix it
fast."

**Steps → serving view → stuck risk**

1. Notice the alarm                     → top-bar master-caution / AttentionQueue   → ok
2. Open the enclosure                   → Engine Room (select enclosure)            → ok
3. Read root cause                      → DiagnosticsPanel (refs, fact-state chips, missing-observability notice, failed phases) + engine gauge + conduits + boot timeline → ok
4. **Take the safe action**             → recovery chip                             → ⚠ **GAP**: recovery chips are display-only

**Forced states:** nominal · booting · saturated · failed (**flicker ≠ blocked-steady**) · dormant · stale-disconnected
**Known gap:** the user can see the next safe action but cannot execute it from the dashboard yet.

## W4 — Review a task's contract / task document

**Persona:** reviewer
**Job story:** "When I'm approving a lifecycle, I want to read what it's supposed to do, so I can judge
the gate."

**Steps → serving view → stuck risk**

1. Select the lifecycle                 → LifecycleList                             → ok
2. Read the task document               → Operations → DetailPanel (`ar-task-document/v1`: master overview, drill into slices, decisions, proposed code) → ok

**Forced states:** content · empty ("No task document bound") · loading
**Known gap:** content appears only for lifecycles with an authored task-document keyed by
`lifecycleId`; an un-bound lifecycle shows "No task document bound" (the historical blank-everywhere cause).

## W5 — Inspect onboarding vs code

**Persona:** drift auditor
**Job story:** "When I suspect onboarding drifted, I want to see the sidecar next to its code, so I know
what's stale."

**Steps → serving view → stuck risk**

1. Get aggregate drift health           → MemoryMirror (coverage/drift/stalest-sidecar)   → ok
2. **Inspect a specific sidecar vs its code**  → **GAP** (no tree | code | paired-onboarding view) → ⚠ **MISSING VIEW**

**Forced states:** content · empty · loading · stale
**Known gap:** the clearest missing view — only aggregate numbers exist; the side-by-side inspection is
the planned Onboarding Inspector (task 260621).

## W6 — Operate an agent session via the control plane

**Persona:** operator
**Job story:** "When I want to steer an agent, I want to drive it from inside the cockpit, so I don't
have to leave for the harness."

**Steps → serving view → stuck risk**

1. Launch a native hosted chat          → Chats → dynamic harness/model/effort flow → ok; options come only from authoritative capability envelopes
2. Launch a raw terminal                → Chats `＋ Terminal`                        → ok; the selected lifecycle is inherited on the open request
3. Focus and operate the fleet          → Chats rail + keep-alive PTY stage          → ok; controlled and legacy-raw panes keep distinct truth
4. Send reliable text                   → Chats `SessionComposer`                    → ok; receipt/reconcile evidence and the operator's bounded queue are visible
5. Withdraw the last queued submit      → Chats `Alt+Up` authoritative pop-back     → ok; restore is revision-safe and server-authorized
6. Answer a structured interaction      → Chats `InteractionBar`                    → ok; pending/answered/error states remain visible
7. Inspect or change model/effort        → Chats exact-session control + Inspector   → ok; requested and effective values remain separate through queued/readback promotion
8. Attach or move a projected leaf      → Chats routing bar                          → ok; the server accepts or refuses the leaf-role pair before local state changes

**Forced states:** empty fleet · starting · ready · working · turn-ended · awaiting-input · failed ·
landed read-only · restored exited · restored retired · cleanup-result unavailable/partial/success ·
stale catalog · dropped PTY · long output · queued/unknown/reconciled submit.

**Known gaps/boundaries:** controlled chats still lack an entry-granular structured transcript,
attachments, whole-queue authority, usage/cost telemetry and interrupt; the xterm runner line-log is
not a replacement conversation UI. Existing-row lifecycle routing remains explicitly local and may
be replaced by the next catalog hydrate because no attach-lifecycle endpoint exists. New launches
inherit lifecycle context on the server, and leaf attach/move is server-authoritative. The contextual
Operations `RailChat` remains separate. Detailed candidate evidence and serving-contract boundaries live in
[`session-cockpit-closeout-evidence.md`](session-cockpit-closeout-evidence.md) and
[`session-cockpit-upstream-register.md`](session-cockpit-upstream-register.md), respectively.

---

## Refresh notes

When Stage 1 discovers a reachable entity state or a new view not represented above, add a scenario or
step rather than dropping it. When a "GAP"/missing-view is later built, update its step from **GAP** to
the serving view and note the landing.

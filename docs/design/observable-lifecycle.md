# Observable Lifecycle, Events, and Gates — the Agents Remember 3.0 Design

**Status:** Design — approved, pre-implementation.

**Scope:** the architecture that makes the Agents Remember lifecycle *observable* and
*controllable* — a first-class lifecycle entity, an append-only event substrate with
trust provenance, a single projection layer, durable enforced gates with a four-layer
return channel, and the local dashboard that renders and acts on all of it.

This document is the contract the 3.0 implementation follows. It fixes the **entities and
their state machines first**; the wire protocol and the visual design are downstream and
deliberately not pinned here. Detailed implementation questions are collected in the final
section and resolved by the subsystem that owns them.

---

## 1. The Lifecycle Entity

A lifecycle is a durable work envelope with identity, state, and an episodic record. It
represents **work, not a repo** — one lifecycle may enclose work spanning multiple
repositories; repositories are where work lands, never the identity key. A session holds
zero or one active lifecycle.

### 1.1 Identity and minting

- Ids are minted **server-side, adjacent to `context_packet`** (governance confirmed ⇒
  start); minted locally (no network), unique across parallel sessions (ULID).
- **The model never handles ids.** Guarded start prevents duplicates; the worktree
  contract owns resume; only `switch_lifecycle` carries a target *reference* (a worktree,
  which the contract resolves to a lifecycle id server-side).
- **The contract enclosure is the identity anchor** for persistent lifecycles: one
  enclosure = one lifecycle, even when it wraps several repos. A multi-task series runs
  in one enclosure and is therefore one lifecycle.
- **The contract document lives in the task folder** (`tasks/<repo>/<task>/contract.md`)
  — durable coordination space, *not* the disposable worktree wrapper. The identity
  anchor therefore outlives worktree cleanup, and a task folder (task files + contract)
  plus the branches on the forges is a complete reconstruction unit.
- Harness session = provenance metadata only. A lifecycle survives chat sessions.

### 1.2 States and the state machine

States: `running | paused | blocked | completed | abandoned`. One state at a time.
`completed`/`abandoned` are terminal. **`paused` is system-owned — there is no pause
signal:** a model cannot reliably signal its own chat's death, and once every task is a
lifecycle there is no "off-task digression" left for a model-driven pause to mean.

| From | To | Trigger | Actor / trust | Event (`data.cause`) |
| --- | --- | --- | --- | --- |
| — | running | `lifecycle_start` (guarded) | model / declared | `lifecycle.started` |
| — | running | `switch_lifecycle` / `worktree_attach` into target | system / observed | `lifecycle.resumed` (`adopted`) |
| running | blocked | `lifecycle_block` (bare or with ask) | model / declared | `lifecycle.blocked` |
| running | paused | switch-away (persistent lifecycle) | system / observed | `lifecycle.paused` (`switched-away`) |
| running | paused | dormancy inference: heartbeats stale | system / inferred | `lifecycle.paused` (`quiet`) |
| running | completed \| abandoned | `lifecycle_end` | model / declared | `lifecycle.ended` (`outcome`) |
| blocked | running | `lifecycle_resume` after gate resolution | model / declared | `lifecycle.resumed` |
| blocked | running | tool activity follows resolved gate, signal missed | system / inferred | `lifecycle.resumed` (`inferred`) |
| blocked | paused | gate resolved ∧ no live session | system / inferred | `lifecycle.paused` (`released-quiet`) |
| blocked | blocked | switch-away or chat death | — | none (gate record keeps the queue item) |
| paused | running | re-adoption via switch/attach | system / observed | `lifecycle.resumed` (`adopted`) |
| paused | running | activity inference (heartbeats return) | system / inferred | `lifecycle.resumed` (`inferred`) |
| paused (fleeting) | abandoned | TTL: dormant ~1h (fleeting only) | system / inferred | *(projected; log pruned — §1.5)* |

Dormancy inference applies to `running` only; `blocked` survives a dead chat because the
gate record is the queue's truth. The TTL transition is the one terminal state with **no
written event**: by definition no live owner remains to write it, so readers *project*
`abandoned` and the sweep prunes the log (§1.5) — keeping the single-writer invariant
(§2.3) intact. Every other transition above is written by the live owning session. Switching away from a *fleeting* lifecycle routes
through the save gate first: save ⇒ promote (then the switch-away pauses it); decline ⇒
deliberate discard (`lifecycle.ended`, outcome `abandoned`).

### 1.3 Signals (the complete model-facing surface — six)

| Signal | Effect | Notes |
| --- | --- | --- |
| `lifecycle_start` | mint + `running` | **Guarded**: rejected with a reminder naming the active lifecycle while one is active in the session. Takes no identifier. |
| `lifecycle_block` | `blocked` | Optional structured **ask** (`kind: question \| decision \| conflict`, prompt, options). With ask ⇒ server materializes a gate/request record; bare ⇒ auto-associates with the most recently opened gate in the lifecycle. |
| `lifecycle_resume` | `running` | Clears `blocked` once the gate resolved. Re-adoption of `paused` lifecycles is system-driven via switch/attach, never this signal. |
| `lifecycle_end` | `completed` \| `abandoned` | `completed` = the human declared done; `abandoned` otherwise. |
| `switch_lifecycle` | transition | The **only** id-carrying signal (a worktree reference, contract-resolved). Creates new, or resumes existing. Leaving fleeting ⇒ save gate; leaving persistent running ⇒ auto-pause. |
| `lifecycle_phase` | phase change | Orthogonal axis (§1.4). |

`worktree_attach` **always behaves as `switch_lifecycle`** with the contract-resolved
target (active fleeting → save gate; active persistent → auto-pause; same lifecycle →
no-op; none → resume). `worktree_start` mid-lifecycle is **not** a switch: it is the
fleeting→persistent **promotion** of the current lifecycle (`lifecycle.promoted`).

Naming rule: the family is `lifecycle_*` — never `workflow_*`, never "runtime".

### 1.4 Phases (orthogonal to states)

Canonical enum: `request | trust-checkpoint | reframe-research | decide | build | close`
— the session-lifecycle skill's heading vocabulary, hyphenated. (The skill's internal
spine wording is aligned to this enum when the skills are updated.) Dashboard display
labels map freely (e.g. "Trust checkpoint"). Phases are orthogonal to states: you can be
in phase `reframe-research` and state `paused` at once; you cannot be `paused` and
`running` at once.

### 1.5 Fleeting vs persistent; save gate; TTL

- **Commitment boundary = the worktree.** Fleeting (no worktree): server-side record
  only, a bare-bones dashboard entry. Persistent (worktree locked): lingers because the
  fixture lingers; disk fixture ↔ dashboard entity is a consistency rule.
- **Save gate** on switch-from-fleeting: offer promotion (worktree + notes + task file)
  vs deliberate discard. Landing zones for saved work without a single tangible repo:
  **`0_unscoped`** (no managed-repo binding) and **`1_cross-repo`** (multi-repo
  enclosure work). Number prefixes sort both above the repo folders.
- **TTL is fleeting-only, and project-and-prune (not a written end).** A fleeting
  lifecycle dormant for ~1h is *abandoned* — but since no live owner remains to write a
  terminal event, readers **derive** `abandoned` from its log (started, never promoted,
  dormant > TTL) and the opportunistic sweep **prunes** (deletes) the log directory. No
  process ever appends to a lifecycle file it does not own, so the single-writer
  invariant (§2.3) holds without coordination. The sweep runs on the next live trigger —
  `lifecycle_start`/`switch_lifecycle`, and the dashboard's projection tick once it
  exists — never a daemon (a dead stdio process cannot reap itself). TTL exists because
  disconnects may be involuntary (a dropped network is not intent), and pruning a
  worktree-less, artifact-less stub loses nothing audit-critical. Persistent lifecycles
  are **never** auto-reaped — the dashboard's hangar panel surfaces rot for the developer
  instead.

### 1.6 Ambient attribution and the heartbeat

- The stdio MCP server process ≈ one harness session. `lifecycle_start` /
  `switch_lifecycle` set the **ambient current lifecycle**; every subsequent tool
  response is auto-tagged server-side. An explicit `lifecycle_id` parameter exists only
  as an override. Subagents sharing the connection inherit the ambient id. (Per-harness
  session scoping is confirmed during implementation and recorded in a harness matrix.)
- While a lifecycle is ambient, the server emits **`lifecycle.heartbeat`** on a timer
  (interval and stale-multiplier are configuration; the idiom is the existing
  `setup-progress.json` heartbeat/stale rule). Heartbeats stopping ⇒ the projection
  reads `paused (quiet)`. Heartbeats live in the rolling retention tier.

---

## 2. The Event Substrate

### 2.1 Envelope (`ar-observer-event/v1`)

| Field | Type / values | Notes |
| --- | --- | --- |
| `schema` | `ar-observer-event/v1` | versioned from day one |
| `id` | ULID | globally unique, time-sortable |
| `ts` | ISO 8601 with offset | event time, never render time |
| `kind` | dot-namespaced string | `lifecycle.promoted`, `gate.approved`, `tool.completed`, `span.heartbeat` |
| `trust` | `declared \| observed \| inferred \| approved` | rendering rule: never pretend declared is observed |
| `actor` | `model \| system \| developer` | who caused it; for developer actions `data.via: chat \| dashboard \| cli` carries "through what" — the two axes never share a field |
| `lifecycleId?` | ULID | absent on workspace-scoped events |
| `enclosure?` | contract reference | the multi-repo anchor — never keyed by repo alone |
| `repoId?` | string | only when genuinely repo-specific |
| `sessionId?` | string | provenance only |
| `spanId?` | ULID | shared across one span's started/progress/finished |
| `data` | object | kind-specific payload |

Corrections are append-only events that reference the corrected event id — history is
never mutated.

### 2.2 The v1 kind families (four, plus heartbeat)

- `lifecycle.*` — started, phase-changed, blocked, resumed, paused, promoted, ended,
  heartbeat. Signals emit with trust `declared` (the *call* is observed; the *claim* is
  declared); system transitions follow the §1.2 table.
- `gate.*` — opened, approved, rejected, answered (question/decision kinds),
  acknowledged (alarms). Flips carry trust `approved` and full attribution.
- `tool.*` — completed: tool name, duration, tokens, ok — emitted at the `_tool_payload`
  choke point (every public tool response routes through it). Trust `observed`.
- `span.*` — started, progress, heartbeat, finished — generalizes the
  `setup-progress.json` idiom (provider setup, indexing scans, build phases).

Memory, worktree-contract, and read event families arrive with their respective
subsystems; until then they are derivable from `tool.*`. The fleeting→persistent
**promotion event is in the v1 set**.

### 2.3 Store layout

```
<coordination-root>/logs/observer/
  lifecycles/<lifecycle-id>/events.jsonl    # per-lifecycle truth — single writer
  workspace/events.jsonl                    # lifecycle-less events (providers, watchers)
  archive/<lifecycle-id>/...                # closed lifecycles, compressed, replayable
```

- **Exclusive adoption ⇒ exactly one writer per lifecycle file.** No cross-process
  locking, ever, even with parallel harness sessions. The invariant is total: every event
  in a lifecycle file is written by that lifecycle's live owner. The only "cleanup" of a
  dead lifecycle is the TTL *prune* of a dormant fleeting log (§1.5) — a directory
  deletion, never a non-owner append.
- **Replayability is a schema requirement:** stable ordering (append order; ULIDs
  tie-break), and each lifecycle file is **self-contained** — `lifecycle.started`
  carries full initial context so one file alone replays one lifecycle's projection. A
  recorded log is therefore simultaneously dev fixture, test fixture, demo, and replay
  substrate.
- The merged workspace feed is a **projection**, never a second truth. All reads go
  through one path-resolution layer so a synced coordination repo can later take over the
  durable tiers without touching call sites.

### 2.4 Retention (three tiers)

| Tier | Contents | Policy |
| --- | --- | --- |
| Forever | lifecycle skeleton (started/promoted/ended), **all gate records** | never reaped — the approval audit is an invariant |
| Rolling raw | dense `tool.*`/`span.*`/heartbeats | never pruned while the lifecycle is open; after closure: archived compressed, pruned past a configurable grace window |
| Derived aggregates | rollups (tokens/day, events/hour, health series) | tiny, kept forever — trend charts survive raw pruning |

A dormant fleeting lifecycle reaped by TTL is the exception to the Forever tier: it has no
gates and no persistent skeleton, so its whole log is **pruned** (§1.5) rather than
retained — nothing audit-critical is lost.

### 2.5 The observer and its projections

`_tool_payload` (observed) · signal tools (declared) · gate actions (approved) ·
derivations and corrections (inferred). The reducer — `agents_remember.observer`,
producing **projections** — is the single owner of interpretation: state tree, metrics,
staleness, and **precomputed action availability** (`disabledReason` / `nextSafeAction`).
No frontend reimplements lifecycle assembly; the projection API is client-agnostic
(dashboard, a future TUI, or an agent are equal clients).

---

## 3. Gates and the Return Channel

### 3.1 The gate record

| Field | Notes |
| --- | --- |
| `id` | ULID |
| `kind` | `commit \| question \| decision \| conflict \| alarm` (open enum) |
| `state` | `pending → approved \| rejected \| answered \| acknowledged \| superseded` |
| `lifecycleId`, `enclosure?` | attribution anchors |
| `openedBy` | tool-opened (e.g. `worktree_closeout_preview`) or block-ask |
| `ask?` | prompt, options (question/decision kinds) |
| `resolution` | value/answer + who/when/from where |
| `history[]` | append-only; every flip attributed |

`superseded`: re-running the opening tool supersedes its predecessor's pending gate — the
commit-message-only iteration path (re-running closeout preview so the dashboard shows the
current blocker text) without dropping back to the build phase. (Code changes, by
contrast, *do* return the lifecycle to the build phase.)

Two opening doors only: **tools** open gates server-side (the commit gate at closeout
preview — no model involvement), and **`lifecycle_block` with an ask** materializes
question/decision/conflict records. Alarms are system-opened.

### 3.2 Enforcement

A **data-driven registry** (tool → required gate kind). v1 contains exactly one row:
`worktree_closeout_apply` ← approved commit gate. UI affordances are never the
enforcement; mutating tools check server-side. Extending enforcement is a registry row,
not a refactor. Questions, decisions, and alarms enforce nothing in v1 — visibility and
the answer channel are their value; over-enforcing breeds workarounds, which is how gates
get weaker.

### 3.3 The return channel (four layers on one durable truth)

A model is not a process: between turns nothing exists to push to, so "push" can only mean
arranging the harness's own re-invocation.

- **L0 — the gate record.** Durable truth; no layer above can lose an approval.
- **L1 — passive pull.** Envelope enrichment: open / recently-resolved gate state stamped
  into every tool response in the ambient lifecycle. Universal across harnesses.
- **L2 — active pull.** Poll `worktree_status`; an optional **bounded `waitSeconds`**
  lets the server hold the response until the gate flips or a short timeout passes — never
  a true long-poll (a held stdio call serves nothing else on that connection).
- **L3 — push-equivalent.** **`agents-remember gate-wait`**, a CLI file-watcher whose
  *exit* is the notification. The blocked model backgrounds it and ends its turn;
  harnesses that re-invoke the model on background-task completion get real push the moment
  the dashboard click lands. Where a harness cannot wake on background completion, the turn
  ends honestly and L1/L2 resume on the next poke.

Taught behavior at a gate: signal `block` → start `gate-wait` in the background where
supported → end the turn. Unblock is an explicit `lifecycle_resume` (with the inferred
fallback of §1.2).

---

## 4. Read Packet (envelope only)

A repo-scoped batch request `{path, range?, onboarding-deviations?}` per file → per-file
`{path, status: found|missing|disabled|unsupported|not_requested, source?, onboarding?}`,
token-stamped, sharing the §2.1 envelope conventions. Attribution is ambient (no lifecycle
parameter). It emits a **facts-only** `read.packet` event: paths, ranges, statuses, sizes
— **never content**. The final tool name, the overview front-door, and batch/budget caps
are settled when this tool is implemented.

---

## 5. Placement and Packaging

- Reducer module: `agents_remember.observer` (plumbing vocabulary; dashboard copy never
  says "observer").
- **Dashboard canonical source: repo root `dashboard/`** — contributor-visible; a
  build/sync step wraps the built bundle into MCP package data (the same canonical-source
  + sync-script pattern already used for `skills/`). Node is a release-build-time
  dependency only, never an install-time one.
- Serving: an in-package **`agents-remember dashboard`** CLI command; the observer runs
  in-process (it tails files directly — no IPC hop). The SSE transport is specified when
  the serving layer is built.
- The event store lives under the coordination root (`logs/observer/`), behind the
  path-resolution layer of §2.3.

---

## 6. The 3.0 Scope Line

**Breaking (the version-flip justification):**

1. Gate enforcement inside mutating tools (`worktree_closeout_apply` refuses without an
   approved gate record).
2. Worktree contract front-matter **v2**: lifecycle anchor fields, plus reserved concept
   space for per-child restart strategies and rebuild-seed sufficiency.
3. Tool response envelope **v2**: ambient attribution (`lifecycleId`) and a conditional
   `gates` block; the response schema version flips.
4. The six-signal vocabulary becomes required doctrine (the skills teach it; a session
   that never signals is outside the documented workflow).
5. The new subsystems that define 3.0: the observer, the event store, the serving layer,
   the dashboard, and the CLI verbs `dashboard` and `gate-wait`.

**Explicitly compatible:** no tool removals, renames, or parameter breaks; memory, ledger,
and onboarding formats untouched; provider tooling untouched; storage path-rules untouched.

**Governance:** any change discovered during implementation to be breaking and not on this
list returns to a design review before it lands. The single 3.0.0 version flip happens once,
after every part exists.

---

## 7. Design Principles Preserved

These long-horizon invariants were checked against every mechanic above; each names where
it is honored.

1. **Settled primitives are not bent.** `paused` was removed by its own obsolescence, not
   contorted, when "every task is a lifecycle" dissolved the digression case.
2. **Projections are client-agnostic** (§2.5) — a dashboard, a future TUI, and an
   orchestrating agent are equal clients.
3. **Contention stays derivable** — blocked spans, gate wait times (opened→resolved), and
   span durations are first-class (§2.2, §3.1), so later coordination analysis needs no
   re-instrumentation.
4. **Multi-repo is never designed out** — `enclosure` keys events and gates (§2.1, §3.1),
   never a single repo.
5. **One read abstraction** (§2.3) keeps a future synced coordination store a swap, not a
   rewrite.
6. **Persistence tiers stay open-ended** — fleeting → persistent → (future) portable;
   promotion is a first-class event (§1.5, §2.2).
7. **The attention taxonomy is extensible** — gate `kind` is an open enum (§3.1).
8. **The contract trends toward a rebuild seed** — v2 reserves the concept space without
   implementing it (§6).
9. **Per-lifecycle token accounting is kept** as the fuel-gauge projection (§2.4) and the
   seed of a future agent-working-set metric.
10. **Work records, never knowledge** — the event store holds episodic records; durable
    knowledge stays in path-mirrored onboarding beside its code (§2.3).

---

## 8. Deferred to Implementation Phases

| Question | Owner phase |
| --- | --- |
| Heartbeat interval / stale multiplier / TTL defaults (configuration) | lifecycle tools |
| Retention grace windows + archive format | lifecycle tools |
| Per-harness matrix: ambient scoping, background-wake, sleep/loop | lifecycle tools |
| `waitSeconds` cap + held-call concurrency behavior | gate control plane |
| `superseded` mechanics (preview re-run wiring; semantics fixed here) | gate control plane |
| Read-packet tool name, overview front-door, batch/budget caps | read packet |
| Phase display labels | dashboard cockpit |
| SSE event-id / offset semantics over the derived feed | serving layer |

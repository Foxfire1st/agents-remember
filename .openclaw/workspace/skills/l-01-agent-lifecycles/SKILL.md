---
name: l-01-agent-lifecycles
description: "The agent lifecycles: one lifecycle per agent type, under one roof. Routes every session by exactly three conditions (spawn-role env -> role brief -> otherwise orchestrator), carries the minimal lifecycle frame (the six lifecycle signals every session shares), and houses the self-contained per-role lifecycles (orchestrator, designer, strategist, manager, worker, adversarial reviewer) plus the report-template library and the reviewer criteria catalogs. A developer-facing session IS the orchestrator; solo work is the degenerate portfolio. Supersedes and replaces both l-01-session-job-lifecycle and l-02-agent-orchestration."
---

# l-01-agent-lifecycles — The Agent Lifecycles

Lifecycle and job are **one entity**: each agent type runs its own, self-contained lifecycle. This
skill is the single roof over all of them — a thin router, the minimal frame every session shares,
and the role lifecycles as the payload files. No role is defined by reference to another role's
lifecycle, and no role reads another role's file.

## Which Lifecycle Am I? (the router — exactly three conditions, in order)

1. **`AR_SPAWN_ROLE` is set** (spawn env, injected by `spawn_agent_session`) → run
   `roles/<value>.md`. Nothing else in this file's "developer session" material applies to you.
   (`designer` here means the same design hat in a separate chair — see `roles/designer.md`.)
2. **Else: the first user message is a role brief** — a `templates/*-brief.md`-shaped dispatch or
   a first line of the form `ROLE BRIEF — <role>` from an orchestrating agent → run that role's
   lifecycle. The brief is your session start; a workspace session-start notice is not addressed
   to you.
3. **Else** (a developer opened this session) → you are the **orchestrator**: run
   `roles/orchestrator.md`. Solo work is the degenerate portfolio — the same three jobs with hats
   collapsed (the orchestrator wears the manager hat in flat runs and builds hands-on at session
   scale); the task doc still comes first.

There is no fourth entry, and the edge cases are decided: an **unresolvable `AR_SPAWN_ROLE`
value** (no matching `roles/<value>.md`) falls through to condition 2 (the brief); a role-env
session **whose brief never arrives** announces itself on the inbox and waits — it never
improvises a task; `AR_SPAWN_ROLE=orchestrator` is valid only as a takeover chair (the Profile
check (takeover) in `roles/orchestrator.md`, The Event Loop) — the developer still talks to **one** orchestrator. Orchestrated
fan-out (spawning managers/workers at scale) begins only on an explicit developer request (e.g.
*"orchestrate these masters"*) — no agent promotes itself into a spawning seat.

One exception to the no-cross-reading rule above: **a seat that WEARS a hat runs that hat's file
as its own** — the orchestrator always for `roles/designer.md`, and in flat runs for
`roles/manager.md` (the hat-collapse rule).

## The Role Registry

| Role | Seat | Lifecycle file |
| --- | --- | --- |
| **orchestrator** | the developer-facing session; first coordination leaf of an orchestrated series | `roles/orchestrator.md` |
| **designer** | a HAT the orchestrator pulls inline (front of the pipeline or mid-flight; separate chair optional) | `roles/designer.md` |
| **strategist** | the sprint planner, SPAWN-FIRST; a strategist run is a **mandatory precondition for any orchestrated run** — its deliverable is the orchestration task (sprint plan + scope); spawn value `strategist` | `roles/strategist.md` |
| **manager** | one coordination leaf per master; drives that master's leaf loop | `roles/manager.md` |
| **worker** | one leaf worktree, short-lived, fresh session | `roles/worker.md` |
| **adversarial reviewer** | short-lived, spawned at the two seams (master-exit, super-exit) and as any three-party loop's reviewer seat (criteria catalogs bound per review type); spawn value `reviewer` | `roles/reviewer.md` |

The **lenses** (bug · feature · triage · research — `lenses.md`) are how the scoping seats
(orchestrator, designer) read a piece of work; a dispatched role never picks a lens — its brief
already carries the flavor.

## The Minimal Frame (the only machinery every session shares)

Every session in a managed repo may be a **lifecycle**: six signals — `lifecycle_start` ·
`lifecycle_phase` · `lifecycle_turn_end_notification` · `worktree_attach` · `switch_lifecycle` ·
`lifecycle_end` (plus the automatic `worktree_start` promotion) — record where it
is and what it waits on, so work is observable and resumable across chat deaths. The model **never
handles a lifecycle id** — identity is server-side, anchored in the worktree contract.

| When | Signal | Effect |
| --- | --- | --- |
| Trust checkpoint passes (managed repo) | `lifecycle_start` | begin a **fleeting** lifecycle (guarded: one per session; no id) |
| Entering a phase | `lifecycle_phase` | move the phase axis (`request` / `trust-checkpoint` / `reframe-research` / `decide` / `build` / `close`) |
| A developer hand-off | `lifecycle_turn_end_notification(summary=…)` | set `awaiting-developer`, surface the attention item, return immediately; the **next turn's first AR call auto-resumes** — never resume by hand |
| `worktree_start` | *(automatic promotion)* | the fleeting lifecycle becomes **persistent**, anchored in the contract |
| Resuming an existing task | `worktree_attach` | re-adopts the contract's lifecycle (contract-resolved) |
| Leaving unsaved fleeting work | `switch_lifecycle` (`on_unsaved=save`\|`discard`) | the save gate — never dropped silently |
| Close | `lifecycle_end` (`completed`\|`abandoned`) | the terminal record |

Rules: a tool call outside any lifecycle is **dropped, never misattributed**; `paused` is
system-owned. **A spawned role that never touches mutating AR tools simply never instantiates a
lifecycle — that is correct, not a violation.** A spawned role runs its **own** lifecycle when it
runs one; it never adopts its spawner's. The session↔leaf association is the catalog binding made
at spawn (the **qualified** leaf key `<repository>/<master>/<docId>`), not lifecycle adoption.

## Shared Invariants (every role can count on these)

- **Continuity lives in the `task_doc` + durable artifacts, never in transcripts** — which is why
  short-lived workers and reviewers are safe, and why every seat writes its artifact of record.
- **Escalation ladder: worker → manager → orchestrator → developer.** No rung is skipped, ever.
  Each role file states only its own rung.
- **Observability:** coordination seats are `task_doc` leaves with attached chats; the developer
  can walk into any seat at any level.
- **Decision-needing questions land in the task doc's `openQuestions`** — the rendered decision
  surface; `notes/` carries the analysis behind them.

## The Three-Party Loop (one home — this section owns the loop doctrine)

**OWNER → BUILDER → REVIEWER → owner, at every level that owns work.** The owner never
self-approves; the builder never lands; the reviewer never decides — verdicts are evidence. The
owner checks the verdict and either redispatches a builder or escalates. Role files reference this
section; they do not restate it.

| Level | Owner (holds the deliverable, rules, lands) | Builder | Reviewer |
| --- | --- | --- | --- |
| Leaf | the leaf's owning seat (manager; orchestrator in tight/flat mode) | spawned worker (no-commit contract) | spawned reviewer, criteria catalog + liberty |
| Master | the manager | the leaf workers | the master-exit seam reviewer (verdict rides `master-handover-approval`) |
| Portfolio | the orchestrator | the STRATEGIST (spawn-first) | reviewer with the plan-review catalog |

**Complexity-scored tiers (per leaf, at dispatch).** The owning seat scores three axes — blast
radius (doctrine/enforcement/public surface vs leaf-local) · novelty (new subsystem vs
pattern-following) · size (files × steps) — into three tiers: **direct** (no loop
machinery — the level's ordinary build channel implements: hands-on at session scale, the leaf's
worker under a manager; self-check + checks ladder only), **builder-verified** (builder
implements; owner verifies report-vs-artifact; no reviewer), **full loop** (builder + independent
reviewer rounds). The
strategist's blast-radius register is the scoring input when an orchestration task exists. A
leaf's loop mark (tier + scope: manager | orchestrator — the owning level runs the loop with ITS
agent set) is recorded on the leaf doc with a decision-log entry. **A master whose leaves all
score `direct` is a workflow-free manager** — no loop machinery; the knobs make every loop
optional per level, never mandatory ceremony.

**Rounds and the HARD cap.** A round = implement → review. **Hard cap: 3 rounds per loop — and
ONLY full end-to-end rounds count against it.** Residuals of a passing round are landed and
**delta-verified by the SAME reviewer via a follow-up message** (it retains everything it already
verified, at a fraction of a fresh round's cost); **fix rounds resume the SAME builder**. A fresh
reviewer is spawned only for a full round or when new scope opens. Delta-verifies close rounds;
they do not open them.

**The convergence rule (the real control; the cap is the backstop).** Every round must SHRINK the
open finding set. A round that does not shrink it escalates immediately, regardless of the count;
a monotonically converging loop may never hit the cap at all. At the cap, or on non-convergence,
the owner does not spin another round — it **escalates one seat up the ladder (worker → manager →
orchestrator → developer) with the full round history attached**; the escalation packet IS the
upper seat's visibility.

**Quo-vadis (the written developer-escalation criterion).** A question is developer-worthy when it
is a **high-blast-radius truth** — answered wrong it means big rewrites later (architecture
direction, security posture, doctrine contradictions, irreversible data/branch operations, where
agent settings live). Quo-vadis questions escalate IMMEDIATELY, regardless of round count.
Presentation-grade choices (2px vs 3px) never do — the owner rules and logs.

**Criteria catalogs (the reviewer as test bench).** Criteria are never made up on the spot: every
review runs its type's standing catalog from `criteria/` (code-seam · doctrine ·
onboarding-memory · report-verification · plan-review) plus an exploratory mandate, under the
promotion ratchet (each catalog carries it). `roles/reviewer.md` binds them.

**Per-level agent sets.** Each level runs its loop with its own harness/model/effort set — the
orchestrator-level set (the strongest models) and the manager-level set (cheaper, possibly
workflow-free) are configured per level in the `orchestration.loops` settings block (schema in
`docs/reference/settings-json.md`; stored in the global agentic settings file with repo-local
override, parsed by the kernel agentic-settings loader — L13, landed). The strategist's mandatory
pre-run is doctrine, not a knob — it is unconditional.

## Knob Block & Capability Doctrine (no per-harness files)

Role files are **model-interpreted markdown, never an executor**. Each carries a portable **knob
block** (harness / model / effort / tools) — the defaults the terminal host injects at spawn.
Resolution: **role-file defaults < settings.json orchestration block.** There are deliberately
**no per-harness role files** (developer decision 2026-07-05): harness-specific ABILITIES —
sub-agent fan-out and the like — are covered inside the portable files as capability-conditional
doctrine any coding agent can apply, and harness PREFERENCE is deployment configuration
(settings), not doctrine. Hard-coding a vendor would fork the doctrine per harness. For spawning
seats, `spawn_agent_session` is itself the harness-independent fan-out: a harness with no
sub-agent facility still dispatches seats through the framework (a chat, no leaf attachment
required) — the DBMS principle: one behavior, any engine.

## settings.json Orchestration Block

Machine/user overrides layer over the role-file defaults, in the **global agentic settings file**
(`<coordination-root>/system/settings.json`), with `<code-repo>/system/settings.json` as the
repo-local override layer (leaf-key deep merge, arrays replace, unknown `orchestration.*` keys
fail loud — schema in `docs/reference/settings-json.md`, Agentic Settings). Precedence: role-file
defaults < global settings < repo-local settings.

```jsonc
{
  "orchestration": {
    "roles": {  // role → knob override (harness / model / effort); harness = registry id
      "orchestrator": { "harness": "claude", "effort": "high" },
      "worker":       { "harness": "codex",  "effort": "medium" }
    },
    "concurrency": { "maxParallelMasters": 2, "maxParallelLeaves": 3, "maxSubAgents": 4 },
    "spawn": { "harness": "claude" },  // spawn_agent_session default when the seat passes none
    "gateDelegation": {
      "policy": "manager-decides-leaf-gates",
      "requireReviewerVerdictAtSeams": true
    }
  }
}
```

**As-built (L13):** the whole block is parsed by the kernel agentic-settings loader
(`kernel/agentic_settings.py`) — typed models for `roles` / `concurrency` / `spawn` / `loops`,
read PER-USE (an edit applies on the next use, no restart). `orchestration.gateDelegation` is
parsed and **enforced** (`controlplane/gate_policy.py` — all-human default, opt-in delegation,
human-pinned kinds `integration-approval` / `push-approval` / `cleanup-approval`, owner never
self-approves); it is the ONE boot-snapshot key — read from the global file at MCP boot, a change
needs a restart (an authority-file value is a one-cycle legacy fallback with a boot warning).
`requireReviewerVerdictAtSeams` **binds delegated seam decisions** (`master-handover-approval`) to
attached reviewer-verdict evidence; the named policy `manager-decides-leaf-gates` routes leaf gates
to the manager and the master-exit handover to the **orchestrator** (human review concentrates at
the super gate). `spawn_agent_session` resolves its harness as explicit argument > repo-local
settings > global settings > detection-gated default; `orchestration.roles` knobs are the spawning
seat's source when compiling a dispatch (model/effort ride as `AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT`
env). `orchestration.loops` (the three-party-loop knobs: per-level loop sets, round cap, reviewer
reuse, complexity thresholds) lives in the same block — meaning in
`docs/reference/settings-json.md`; no knob touches the master-exit seam gate.

## Companion Files

- `lenses.md` — the four job lenses for the scoping seats.
- `roles/…` — the six self-contained role lifecycles (the registry above).
- `templates/…` — turn-report · worker-brief · manager-brief (`ROLE BRIEF — manager`; the
  orchestrator compiles a manager's session start from it) · master-handover-packet ·
  conversation-handover-packet · verdict · impact-analysis · onboarding-coherency ·
  deep-research-report · orchestration-task (the strategist's sprint plan). Spawning seats compile
  briefs FROM these; sub-agents fan out and fill them, so analysis survives compaction.
- `criteria/…` — the reviewer criteria catalogs (code-seam · doctrine · onboarding-memory ·
  report-verification · plan-review), the review test bench the three-party loop binds; maintained
  through the promotion ratchet, never made up on the spot.

## The Super Integration Branch (orientation only — the doctrine lives with its owner)

```
main
  └── super-integration (orchestrator-owned, off main)
        ├── master-A branch (off super)  ── leaves land via C-11
        ├── integrate A → super (orchestrator worktree, C-11)
        ├── master-B branch (off the moved super — sees A)
        └── … final: super → main PR + memory carry-over + push
```

The full topology — dependency-ordered dispatch, the integration-duty procedure, the two
conflict-resolution modes, leaf moves — lives in **`roles/orchestrator.md`** and only there.

## Credits

This skill absorbs and supersedes `l-01-session-job-lifecycle` and `l-02-agent-orchestration`
(converged 2026-07-05: lifecycle and job are one entity — one lifecycle per agent type). The
orchestration vocabulary adopts the parked `260619_agentic-control-plane` spec — jobs as
model-interpreted markdown (D6), the knob block (D7), role + lens in one file (D10), the
ambient-singleton rule (D11), per-harness variants (D12), the judge rung, short-lived workers with
structured handoff, dev-talks-to-one-orchestrator (D15) — which in turn credits **Archon** and the
**agent-control-plane** project (D14); that credit carries forward.

## Relationship To Other Instructions

This skill extends — never replaces — the coordinator `AGENTS.md`, the `w-02-light-task-workflow`
task format, and the memory layer (`c-…` skills). Each `roles/<role>.md` is self-contained for its
seat; read exactly the one the router selects.

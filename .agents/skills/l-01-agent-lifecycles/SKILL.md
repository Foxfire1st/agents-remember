---
name: l-01-agent-lifecycles
description: "The agent lifecycles: one lifecycle per agent type, under one roof. Routes every session by exactly three conditions (spawn-role env -> fresh role brief -> otherwise free-chat launcher), carries the minimal lifecycle frame (the six lifecycle signals every session shares), and houses the self-contained per-role lifecycles (architect, orchestrator, designer, strategist, manager, worker, curator, system-specialist, adversarial reviewer, bootstrap) plus the report-template library and the reviewer criteria catalogs. Solo work is the degenerate portfolio. Supersedes and replaces both l-01-session-job-lifecycle and l-02-agent-orchestration."
---

# l-01-agent-lifecycles — The Agent Lifecycles

Lifecycle and job are **one entity**: each agent type runs its own, self-contained lifecycle. This
skill is the single roof over all of them — a thin router, a shared core every seat reads once, and
the role lifecycles as the payload files. No role is defined by reference to another role's
lifecycle, and no role reads another role's file.

**This file routes. It does not carry doctrine.** Every rule lives in exactly one place:

| Layer | File | Holds |
| --- | --- | --- |
| Router | `SKILL.md` (this file) | the three routing conditions, the role registry, the composition map |
| Core | `core/…` | rules that genuinely apply across roles, authored once |
| Roles | `roles/<role>.md` | one self-contained lifecycle per seat |
| Operations | `operations/…` | the operation-scoped procedure, extracted from interwoven role prose |
| Routing metadata | `composition-manifest.json` | role → core + role + operation blocks; no prose |
| Reference-only | `reference/…`, `lenses.md`, `criteria/…`, `templates/…` | rationale, history, superseded rulings, the criteria catalogs, the brief/report field schemas |

## Which Lifecycle Am I? (the router — exactly three conditions, in order)

1. **`AR_SPAWN_ROLE` is set** (injected by the hosted-seat control plane) → run `roles/<value>.md`.
   Nothing else in this file applies to you. (`designer` here means the same design hat in a
   separate chair — see `roles/designer.md`.)
2. **Else: the first user message is a role brief in a fresh session** — a `templates/*-brief.md`-shaped
   dispatch or a first line of the form `ROLE BRIEF — <role>` from an orchestrating agent → run that
   role's lifecycle. The brief is your session start; a workspace session-start notice is not
   addressed to you.
3. **Else** (a developer opened this session) → you are the developer-facing **free chat**: a
   **launcher, not a role seat** (ruled 2026-07-09), and not a role. Research-only questions
   are answered inline with no role taken. For ordinary role-shaped work, resolve the target sprint,
   compile one complete brief from `templates/architect-brief.md`, and call
   `dispatch_agent(task_document_ref=<canonical sprint document>, role="architect", brief=<compiled
   brief>)` once; on `dispatched` or `dispatch-queued`, switch the developer conversation to the
   canonical `(sprint document, architect)` chat and stop role work here — both results mean the
   brief is durable, so never send a second brief. The full launcher contract is `core/launcher.md`.

There is **no fourth entry**, and the edge cases are decided:

- An **unresolvable `AR_SPAWN_ROLE`** (no matching `roles/<value>.md`), a role env without its
  matching plane-injected hosted identity, or hosted identity without its matching role, is
  malformed plane identity and **fails closed** — it never falls through to a pasted brief or
  free-chat routing. A valid role-env session **whose brief never arrives** announces itself on the
  inbox and waits; it never improvises a task.
- `AR_SPAWN_ROLE=orchestrator` is valid only as a spawned backend seat or a backend takeover chair:
  the developer still talks to the **architect**, not the orchestrator.
- An explicit **developer-declared task-seat takeover** is the bounded exception on condition 3: it
  dispatches the named role on that role's canonical task document instead of first creating an
  architect (`core/authority.md`).
- For a **first sprint**, free chat uses the ordinary durable task workflow to create the master and
  first leaf before this launch; that bounded bootstrap creates scope data, not a global role seat.
- The **spool-up chain is fixed and self-driving** (ruled 2026-07-09): free chat spawns the architect
  for the resolved sprint; the architect spawns the orchestrator; the orchestrator spawns managers
  per the approved plan and the concurrency settings; managers spawn their workers. No seat waits to
  be told "spawn this, spawn that". Only two spool-up decisions ever go back to the developer, both
  raised as questions by the agent itself: whether to run a **strategist** pass (proposed, never
  auto-run) and whether to take the **short root** (solo, no orchestration) when the work looks tiny
  — see `roles/architect.md`.
- One exception to the no-cross-reading rule: **a seat that WEARS a hat runs that hat's file as its
  own** — the architect may wear `roles/designer.md`, and in solo/flat runs may wear backend or
  build hats. A spawned role seat never wears another role's hat. See `core/authority.md`.

## The Role Registry

| Role | Seat | Lifecycle file |
| --- | --- | --- |
| **architect** | sprint-local developer-facing owner seat; design conversation, decision-item relay, and drawing board | `roles/architect.md` |
| **orchestrator** | sprint-local spawned backend portfolio/orchestration seat; never developer-facing | `roles/orchestrator.md` |
| **designer** | a HAT the architect pulls inline (front of the pipeline or mid-flight; separate chair optional) | `roles/designer.md` |
| **strategist** | the sprint planner, SPAWN-FIRST when the developer approves the architect's propose-first question; its deliverable is the orchestration task draft (sprint plan + scope); spawn value `strategist` | `roles/strategist.md` |
| **manager** | sprint-local coordination seat per master; drives that master's leaf loop | `roles/manager.md` |
| **worker** | one leaf worktree, short-lived, fresh session | `roles/worker.md` |
| **curator** | fresh per leaf after builder/reviewer; writes onboarding only from task docs, notes, and code diff | `roles/curator.md` |
| **system-specialist** | backend provider-degradation investigator; report first, fixes only after explicit orchestrator order; spawn value `system-specialist` | `roles/system-specialist.md` |
| **adversarial reviewer** | short-lived, spawned at the two seams (master-exit, super-exit) and as any three-party loop's reviewer seat (criteria catalogs bound per review type); spawn value `reviewer` | `roles/reviewer.md` |
| **bootstrap** | the new user's first-hour seat for one repository: memory root, spear branch, first onboarding, first attributed baseline, indexing; reachable before any task document exists, and it says so when it is not reachable | `roles/bootstrap.md` |

Exactly ten roles. The **ambient launcher** in condition 3 is a routing condition, not a role,
and its own obligations live in `core/launcher.md` rather than `roles/`.

The **lenses** (bug · feature · triage · research — `lenses.md`) are how the scoping seats
(architect, designer, orchestrator) read a piece of work; a dispatched role never picks a lens — its
brief already carries the flavor.

## Composition Map (how a capsule is assembled)

A delivered capsule is assembled deterministically from explicit source selections — no model call
decides what to include. Order is **core → role → operation → explicitly admitted repository
specialization**; task facts travel as a separate context channel. `composition-manifest.json` holds
the machine-readable routing metadata (role → core + role + operation blocks, plus the operation
vocabulary and applicability); it contains **no prose and no copies** of any source section.

- **Core** (`core/`): `authority.md` · `invariants.md` · `lifecycle-frame.md` · `loop.md` ·
  `acceptance.md` · `launcher.md`. Read once, composed once.
- **Operation vocabulary** (`operations/`): `orientation` · `planning` · `implementation` · `review`
  · `curation` · `coordination` · `authorized-closeout` · `recovery` · `session-bootstrap`. The set
  is small and frozen; an unknown operation is an explicit error, never a silent fallback.
- **Role** (`roles/<role>.md`): self-contained for its own seat in one readable order — purpose and
  authority → required inputs → normal workflow → role-specific permitted writes/actions →
  stop/escalation cases → completion/handoff.
- **Reference-only** (`reference/`, `lenses.md`, `criteria/`, `templates/`): rationale, history,
  superseded rulings, the criteria catalogs, and the brief/report **field schemas**. Reference
  material is not injected into the normative path; a role file points at the template it compiles.
- **Composition invariants**: duplicate identities collapse once; explicit supersession is recorded;
  unresolved equal-authority contradictions stop affected compilation; required obligations are
  never truncated to meet a size target.

## Developer-Declared Task-Seat Takeover

The operational checklist, the exact `dispatch_agent` contract, the idempotent-retry behaviour, and
the `source-lineage-*` recovery all live in `core/authority.md` § Developer-declared task-seat
takeover. The one-line shape: resolve the named task document, converge on that role's canonical seat
at its canonical altitude with one `dispatch_agent` call, verify the `(taskDocumentRef, role)` row,
and never manually replace a live incumbent.

## Developer Clarification Triage

The current-implementation-vs-future-queue test lives in `core/authority.md` § Developer
clarification triage. Read the active queue first; the question is not whether a note is useful, but
whether the developer is effectively steering the work already in hand.

## Companion Files

- `lenses.md` — the four job lenses for the scoping seats.
- `roles/…` — the ten self-contained role lifecycles (the registry above).
- `core/…` — the shared core: authority, invariants, lifecycle frame, loop doctrine, acceptance,
  and the ambient launcher.
- `operations/…` — the nine operation-scoped blocks.
- `templates/…` — the field schemas the spawning seats compile briefs from: turn-report ·
  worker-brief · manager-brief (`ROLE BRIEF — manager`) · architect-brief · curator-brief ·
  master-handover-packet · conversation-handover-packet · verdict · impact-analysis ·
  onboarding-coherency · deep-research-report · orchestration-task (the strategist's sprint plan).
  Spawning seats compile briefs FROM these; sub-agents fan out and fill them, so analysis survives
  compaction.
- `criteria/…` — the reviewer criteria catalogs (code-seam · doctrine · onboarding-memory ·
  report-verification · plan-review), the review test bench the three-party loop binds; maintained
  through the promotion ratchet, never made up on the spot.
- `reference/…` — rationale, provenance, and the durable rulings this corpus implements, kept out of
  the normative path.

## The Super Integration Branch (orientation only — the doctrine lives with its owner)

```
main
  └── super-integration (orchestrator-owned, off main)
        ├── organizational master A (logical owner; leaves land directly on super)
        ├── atomic master B (isolated branch; all leaves land there, then B lands once)
        ├── later leaves refresh from moved super before closeout; closeout performs that
        │     refresh itself when it is a plain fast-forward, and refresh, closeout and
        │     landing stay adjacent per leaf so the line cannot move between them
        └── … final: super → main PR + memory carry-over + push
```

The full topology — canonical graph, execution-nature classification, ready-frontier recomputation,
landing procedures, conflict routing, and leaf moves — lives in **`roles/orchestrator.md`** and only
there.

## settings.json Orchestration Block

Machine/user overrides layer over the role-file defaults, in the **global agentic settings file**
(`<coordination-root>/system/settings.json`), with `<code-repo>/system/settings.json` as the
repo-local override layer (leaf-key deep merge, arrays replace, unknown `orchestration.*` keys fail
loud — schema in `docs/reference/settings-json.md`, Agentic Settings). Precedence: role-file
defaults < global settings < repo-local settings. The typed shape, the as-built loader behavior, the
resolution order, the native launch selection, and the `orchestration.loops` knobs are documented
there and in `docs/reference/harnesses.md`; **this router does not restate an example block.**

The parts a seat must obey are in `core/invariants.md` § Knob resolution and capability doctrine:
only the launch-setting rows are settings keys, `dispatch` and `tools` are structural descriptions,
and there are deliberately no per-harness role files.

## Relationship To Other Instructions

This skill extends — never replaces — the coordinator `AGENTS.md`, the `w-02-light-task-workflow`
task format, and the memory layer (`c-…` skills). Each `roles/<role>.md` is self-contained for its
seat; read exactly the one the router selects, plus the `core/` files it names.

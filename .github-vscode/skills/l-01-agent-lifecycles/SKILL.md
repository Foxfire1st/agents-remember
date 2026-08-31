---
name: l-01-agent-lifecycles
description: "The agent lifecycles: one lifecycle per agent type, under one roof. Routes every session by exactly three conditions (spawn-role env -> fresh role brief -> otherwise free-chat launcher), carries the minimal lifecycle frame (the six lifecycle signals every session shares), and houses the self-contained per-role lifecycles (architect, orchestrator, designer, strategist, manager, worker, curator, system-specialist, adversarial reviewer) plus the report-template library and the reviewer criteria catalogs. Solo work is the degenerate portfolio. Supersedes and replaces both l-01-session-job-lifecycle and l-02-agent-orchestration."
---

# l-01-agent-lifecycles — The Agent Lifecycles

Lifecycle and job are **one entity**: each agent type runs its own, self-contained lifecycle. This
skill is the single roof over all of them — a thin router, the minimal frame every session shares,
and the role lifecycles as the payload files. No role is defined by reference to another role's
lifecycle, and no role reads another role's file.

## Which Lifecycle Am I? (the router — exactly three conditions, in order)

1. **`AR_SPAWN_ROLE` is set** (injected by the hosted-seat control plane) → run
   `roles/<value>.md`. Nothing else in this file's "developer session" material applies to you.
   (`designer` here means the same design hat in a separate chair — see `roles/designer.md`.)
2. **Else: the first user message is a role brief in a fresh session** — a `templates/*-brief.md`-shaped dispatch or
   a first line of the form `ROLE BRIEF — <role>` from an orchestrating agent → run that role's
   lifecycle. The brief is your session start; a workspace session-start notice is not addressed
   to you.
3. **Else** (a developer opened this session) → you are the developer-facing **free chat** — a
   launcher, not a role seat (ruled 2026-07-09). Research-only questions are answered inline with
   no role taken. For ordinary role-shaped work (a sprint, a task, any durable change that is not
   an explicit task-seat takeover), do NOT assume the architect role in this chat: resolve the
   target sprint, compile one complete brief
   from `templates/architect-brief.md`, and call
   `dispatch_agent(task_document_ref=<canonical sprint document>, role="architect",
   brief=<compiled brief>)` once. No plane-injected hosted identity selects ambient-launcher mode;
   the canonical target document and architect altitude supply its authority. The launcher never
   submits caller identity or handles a session id. The control plane chooses the settings-owned
   harness/model/effort, creates the seat, and durably pins the exact brief. On `dispatched` or
   `dispatch-queued`, switch the developer conversation to the canonical `(sprint document,
   architect)` chat and stop role work here; both results mean the brief is durable, so never send
   a second brief. The resulting architect runs `roles/architect.md`. An explicit
   developer-declared task-seat takeover is the bounded exception below: it dispatches the named
   role on that role's canonical task document instead of first creating an architect.
   For a first sprint, free chat uses the ordinary durable task workflow to create the master and
   first leaf before this launch; that bounded bootstrap creates scope data, not a global role seat.

There is no fourth entry, and the edge cases are decided: an **unresolvable `AR_SPAWN_ROLE`
value** (no matching `roles/<value>.md`) is malformed hosted identity and fails closed — it never
falls through to a pasted brief or free-chat routing. A role env without the matching
plane-injected hosted-session identity fails closed for the same reason. A valid role-env session
**whose brief never arrives** announces itself on the inbox and waits — it never improvises a
task; `AR_SPAWN_ROLE=orchestrator` is valid only as a spawned backend seat or a
backend takeover chair — the developer still talks to the **architect**, not the orchestrator.
The spool-up chain is fixed and self-driving (ruled 2026-07-09): free chat spawns the
**architect for the resolved sprint**; the architect spawns the **orchestrator** for that sprint's
portfolio execution; the
orchestrator spawns **managers** per the approved plan and the concurrency settings; managers
spawn their **workers**. No seat waits to be told "spawn this, spawn that" — each level spawns
its next level from the plan. Only two spool-up decisions ever go back to the developer, both as
questions the agent raises itself: whether to run a **strategist** pass (proposed, never
auto-run), and whether to take the **short root** (solo, no orchestration) when the work looks
tiny — see `roles/architect.md`.

One exception to the no-cross-reading rule above: **a seat that WEARS a hat runs that hat's file
as its own** — the architect may wear `roles/designer.md`, and in solo/flat runs may wear backend
or build hats (the hat-collapse rule). A spawned role seat never wears another role's hat.

## Developer-Declared Task-Seat Takeover

When the developer says *"you are the orchestrator/manager/worker for task X"* (or equivalent),
that is a **task-seat takeover**, not a loose role hint. Before analysis, profile checks,
dispatch, or implementation, resolve the named task document and converge on that role's canonical
seat at its canonical altitude: sprint for architect/orchestrator/optional sprint roles, master for
manager, leaf for worker/curator, and leaf/master/sprint for a reviewer according to the exact
review seam. `dispatch_agent` reuses a viable occupant or its durable
queued brief for the same `(task document, role)`; takeover never means manually replacing a live
incumbent. Only the lifecycle-owned transaction may retire one generation that it has positively
proved failed.

Operational checklist:

1. Resolve the canonical JSON-primary task document and the role being claimed.
2. Compile the role's complete canonical brief and call `dispatch_agent` once with that document,
   role, and brief. An identity-free developer chat uses ambient-launcher mode; it does not submit
   caller identity, call a terminal attach/session primitive, or read, request, paste, or retain a
   session/lifecycle/agent id. Repeating the exact call after an advertised recovery is idempotent:
   it reconciles the canonical seat and pinned brief instead of creating a duplicate.
3. If desired, call `rename_self(label=...)` after the hosted role chat is active.
4. Verify Operations and Chats show the expected `(taskDocumentRef, role)` row before continuing.

If `dispatch_agent` cannot establish that document+role binding, record the structural blocker.
For `source-lineage-stale` or `source-lineage-unavailable`, follow the refusal's ordered,
contract-addressed `worktree_sync` recovery and retry the same document+role. A retained merge
conflict is a resumable reconciliation phase: resolve mechanically derivable conflicts, run the
advertised continuation, and then retry dispatch. That retry converges on any viable existing
occupant or durable queued brief; never clear either one merely to make the retry look fresh.
Escalate only when the conflicting changes encode a semantic truth that current requirements and
evidence cannot resolve. For other structural
refusals, ask for the missing document or role authority. Never improvise an exact-id attachment or
ask for branch, occupant, session, lifecycle, or agent ids.

## Developer Clarification Triage

When the developer clarifies a rule, boundary, or desired behavior during an active task, decide
whether it is **current implementation** or **future queue** before writing only a note. Read the
active queue first: the current leaf, parent/master, neighboring leaves, decision log, open
questions, and in-flight branch state. The question is not whether a note is useful; it is whether
the developer is effectively steering the work already in hand.

Treat it as current implementation when queue context and closeness point at the active change: it
names the same task/leaf/master, resolves a defect exposed by the current work, or improves the
same doctrine or code path. A small change that plainly fits the current diff is a strong signal
for immediate implementation even if the developer phrases it as "maybe" or "we can wrap this in."
In that case, extend the current task surface/decision log enough to make the added scope visible
and implement it now.

Treat it as future queue when it names a later release, a separate subsystem, a large scope jump,
work whose correctness depends on another unfinished master, or a change that would reorder
already-running leaves. Record the item in the right durable queue or ask the owning seat to plan it
later. If the intent is genuinely ambiguous after reading the queue, ask the developer directly
whether they want immediate implementation or a queued note. Do not silently downgrade a
close/current/small change into future speak, and do not silently expand the active leaf when the
fit is unclear.

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

The **lenses** (bug · feature · triage · research — `lenses.md`) are how the scoping seats
(architect, designer, backend orchestrator) read a piece of work; a dispatched role never picks a lens — its brief
already carries the flavor.

## Role-Seat Immutability (dashboard-owned sessions)

When the dashboard owns a session, its role is fixed for the session lifetime. Roles expand
**horizontally** by spawning new, individually addressable chats; sub-agents drill **vertically**
inside one seat's context for deeper analysis. A dashboard-owned session that already has a role
refuses a pasted role brief instead of silently rerouting itself; it escalates the mismatch to its
owner via the inbox. Router condition 2 applies only to fresh sessions. Sessions not owned by the
dashboard follow the host harness's ordinary rules.

Hat-collapse is sanctioned only for the owner/developer-facing architect seat in solo or flat
runs. Spawned role seats never absorb another role brief and never become a different role in
place.

## Minimal Decision-Item Relay

The ARCHITECT/ORCHESTRATOR split uses the existing operator inbox now. No full queue schema or
dashboard reform is introduced here.

- Backend seats post one `messageKind: decision-item` inbox row at a time to the architect. The row
  states what is being decided, the options, the consequences, and the durable evidence refs.
- The architect presents one item at the developer's pace, records the ruling in the durable task
  surface (`openQuestions` / decision logs, with notes for analysis), and returns one
  `messageKind: decision-ruling` inbox row to the backend seat.
- If the item is underspecified, the architect sends a single clarification row back instead of
  guessing. The backend does not open a second item until the active item has a durable ruling or
  clarification state.

## The Minimal Frame (the only machinery every session shares)

Every session in a managed repo may be a **lifecycle**: six signals — `lifecycle_start` ·
`lifecycle_phase` · `lifecycle_turn_end_notification` · `worktree_attach` · `switch_lifecycle` ·
`lifecycle_end` (plus the automatic `worktree_start` promotion) — record where it
is and what it waits on, so work is observable and resumable across chat deaths. Lifecycle
correlation is server-side and anchored in the worktree contract; it is not model state.

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
runs one; it never adopts its spawner's. The session↔task-seat association is the catalog binding
made at dispatch: canonical task document plus role, not lifecycle adoption. Sprint roles bind to
the sprint document, managers to master documents, workers/curators to leaf documents, and
reviewers bind to the exact leaf, master, or sprint document whose review seam they adjudicate.
The plane stamps that reviewer's canonical parent document+role; it never derives the parent from
an occupant id. Different roles may coexist on one document; only a second live occupant of the same
`(task document, role)` seat collides.

**Notify-and-stop is safe by design (HFX2-L1..L4, landed):** ending a turn on
`lifecycle_turn_end_notification` — or simply stopping once your artifact is written and nothing is
pending — is never a liveness gap. Silence is supervised: the HFX2-L2 agent-notifier sweep evaluates
seat-state facts on its own mechanical tick and relays them to owners (turn-ended/completed
state-signals, compound-idle, non-reaction residue); owners interpret and act. The timed
escalation ladder is retired — there is no renudge/skip-level/respawn machinery in the relay.
**No role watches, polls, or nudges on its own initiative — that is a banned seat-local watcher
(uniform-mechanism ruling 2026-07-07).** Every role's own liveness duty inverts to *passive*: you
will be woken with your pending signals; process and ack every item before ending your turn again.

## Shared Invariants (every role can count on these)

- **Continuity lives in the `task_doc` + durable artifacts, never in transcripts** — which is why
  short-lived workers and reviewers are safe, and why every seat writes its artifact of record.
- **Escalation ladder:** worker → manager → orchestrator → architect → developer; system-specialist
  → orchestrator. No rung is skipped, ever.
  Each role file states only its own rung.
- **Observability:** coordination seats are `task_doc` leaves with attached chats; the developer
  can walk into any seat at any level.
- **Decision-needing questions land in the task doc's `openQuestions`** — the rendered decision
  surface; `notes/` carries the analysis behind them.
- **Dependency graph, not habit, decides sequencing.** Parallelize independent work by default up
  to the applicable `orchestration.concurrency` cap. Sequential execution is the exception and
  must name a gate, a shared-file one-writer dependency, or an explicit ruling.

## The Three-Party Loop (one home — this section owns the loop doctrine)

**OWNER → BUILDER → REVIEWER → owner, at every level that owns work.** The owner never
self-approves; the builder never lands; the reviewer never decides — verdicts are evidence. The
owner checks the verdict and either redispatches a builder or escalates. Role files reference this
section; they do not restate it.

| Level | Owner (holds the deliverable, rules, lands) | Builder | Reviewer |
| --- | --- | --- | --- |
| Leaf | the leaf's owning seat (manager; architect in tight/flat mode) | spawned worker (no-commit contract) | spawned reviewer, criteria catalog + liberty |
| Master | the manager | the leaf workers | the master-exit seam reviewer (verdict rides `master-handover-approval`) |
| Portfolio plan | the architect | strategist when approved; orchestrator on a sanctioned strategist skip | reviewer with the plan-review catalog |

**Independent route review is mandatory after every code-change session.** Once implementation
and its focused acceptance are stable, the owning seat partitions the changed surface by
material major route (architecture/control-plane ownership boundary, informed by governing route
overviews and the import/call graph). The reviewer chair fans out one independent reviewer per
affected major route. Each route reviewer reads the diff and its surroundings, tests likely side
effects, and reports source-backed findings; the chair records a route-coverage table and one
verdict. One reviewer may not silently collapse several routes into a generic diff skim. No code
change proceeds to curator, closeout, integration, or handover without this verdict. A fix returns
to the same builder and the same route reviewer delta-verifies it; touching a new major route adds
that route to the review partition. This mandatory post-code gate also applies to direct/solo work:
independence requires another agent, never builder self-review. The reviewer seat is also never
the author/implementer seat itself (no self-review of one's own leaf), and every requirement
verdict must cite evidence of the requirement's class: rendering/visibility requirements need
mounted-UI proof, scheduling/ordering requirements need operation-level proof, and data-model
requirements need artifact-level proof — evidence of the wrong class is verdict laundering, not
a pass.

**Requirement compilation precedes task topology.** After intent and scope are established, the
architect compiles every independently falsifiable obligation into a canonical requirement index
with a stable ID and explicit version. Clauses that can be violated, reviewed, owned, evidenced, or
superseded independently are separate requirements. Before any sprint/master/leaf task document is
created, every ID + version has one self-contained, version-addressed packet using
`../w-02-light-task-workflow/requirement-packet-template.md`, including the problem, required
behavior, rationale, scope and exclusions, preservation boundaries, failure/recovery behavior,
examples, forbidden overreach, expected evidence, authority/provenance, dependencies, and open
truth gaps. Material state, sequence, ownership, and interaction relationships get diagrams.

A fresh agent cold-reads each packet without the planning transcript and must be able to explain
what changes, what stays unchanged, the important failure states, and proof of conformance. The
architect presents the complete corpus for developer approval and creates task topology only after
that approval; every approved packet records the durable ruling. Masters and leaves carry filtered
ID + version + canonical-packet links, never
rewritten requirement contracts. Each leaf owns exactly one primary requirement revision; several
leaves may implement independently executable manifestations of one revision, while adjacent
requirements are dependency/preservation context only. A requirement change increments its
version, cites durable developer approval, invalidates affected acceptance state, and rebriefs
affected leaves.

**Requirement acceptance is per stable ID and version, never aggregate.** Before dispatch, the owner
projects the leaf's one owned primary revision, with its stable ID + version, approved packet, and
durable corpus-ruling citation, plus separately labelled dependency/preservation context into the
builder brief. Adjacent context is verified as a constraint and cannot be claimed closed by this
leaf. The builder's handoff contains one acceptance block for the owned primary revision:
`satisfied`, `blocked`, or `approved-change`; delivery/implementation rationale and citations;
verification rationale that
states both the demonstrated behavior and the failure it would catch; verification citations; and
the exact command/result or durable evidence reference. Code citations name file paths and
symbols. Non-code work uses the same contract with deliverable paths plus sections/anchors instead
of invented code fields. A `blocked` or `approved-change` block also explains why the original
requirement cannot be delivered unchanged, names the changed delivery when one exists, and cites
the durable developer ruling. General prose or an aggregate "requirements addressed" claim is not
an acceptance envelope.

The independent reviewer inspects the owned primary packet revision and cited artifacts itself and
adjudicates that exact manifestation as `accepted` or `rejected`, with its own rationale. Missing
rationale, an unapproved packet revision, missing or wrong-class
evidence, invalid citations, or missing developer approval forces rejection of that requirement;
the overall verdict cannot pass while any requirement is rejected. An accurately reported
`blocked` row may be accepted as a truthful handoff, but it still requires a BLOCK recommendation
until the requirement is delivered or becomes an approved change. The durable-evidence
stable-contract-or-expiry promotion hold point remains a separate review dimension and cannot
substitute for requirement acceptance evidence.

**Requirement revisions and delivery attempts are separate axes.** The canonical `ID@version`
states semantic intent and changes only through explicit developer approval. A leaf-local attempt
ID states what one exact candidate was handed to independent review for one leaf manifestation of
that revision. The builder advances that ID only when handing a candidate to review, or when a
reviewer rejection requires a successor handoff. Internal implementation, test, and evidence
reruns do not mint attempts; preserve them separately as experimental protocol events with the
candidate identity, command, result, failure cause, repair, and expected proof for the next run.

Before review handoff, the builder appends an immutable worker attempt record to the leaf's
detailed journal. It binds the revision, manifestation, predecessor and carried findings when
present, exact candidate tree/commit or appropriate non-code digest/anchors, and its own
requirement-specific status, rationale, citations, findings, failure class, and a content-addressed
reference to immutable expanded evidence. The frozen expanded artifact carries shared definitions
and complete command results; do not duplicate the complete master acceptance envelope or
experimental-run body inside every attempt. After rejection, the repaired candidate is handed off
through a successor attempt. No prior worker record is edited or deleted; an unrelated later
candidate does not reopen an accepted attempt.

Validate the complete worker record before append. Append plus exact-candidate review handoff is
one logical formal-attempt boundary. A malformed pre-handoff row is preserved, receives an
append-only `non-attempt-correction`/void reference, and consumes no attempt ID; the corrected row
uses that same next ID at handoff. A malformed handed-off row is already a formal attempt: the
independent reviewer rejects it, and the worker may append a successor only at the next review
handoff. The worker never self-rejects or silently replaces either row.

The independent reviewer appends a separate reviewer record against that exact attempt and exact
candidate after inspecting the artifacts itself. It chooses `accepted` or `rejected`, supplies its
own rationale/citations, and classifies every rejection finding as exactly one of `implementation
defect`, `evidence gap`, `requirement contradiction/overconstraint`, `test/tool defect`, or
`external blocker`. A requirement contradiction/overconstraint is rejected and routed through the
architect for developer-approved revision; builders and reviewers may propose a revision but never
rewrite or approve one. The reviewer does not modify the worker record, and acceptance never floats
to a later candidate.

Rejection closes that attempt and a repair appends a successor citing the predecessor and findings.
Accepted attempts remain closed unless an independent reviewer proves a direct regression against
that exact accepted delivery and the owning manager (architect in a flat run) records a bounded
invalidation citing the accepted attempt, reviewer record, regressing candidate, and affected set;
the other trigger is a developer-approved new semantic requirement version and its bounded affected
set. A worker, reviewer, changed candidate, or summary cannot reopen acceptance unilaterally. Same-reviewer
delta verification, shrinking findings, and the three-round delegation cap stay in force; an
architect takeover continues the same attempt lineage.

The detailed per-leaf worker and reviewer records are authority. A master maintains a rebuildable
summary linking those records and showing attempts, rejection history, current state, and dominant
open failure class per requirement manifestation. The summary is a disposable observation only:
it is never a requirement contract, lifecycle/closeout gate, queue authority, or task-authoring
lock. Missing or stale summary state is rebuilt from leaf journals and cannot block work.

The chair persists the passing or blocking result through
`task_doc(operation="record_route_review", review={verdict, verdictRef, routes:[...]})` after the
durable verdict and every route evidence file exist. The control plane, not the chair or manager,
stamps the exact current Git candidate tree and review time into the leaf document. Curator dispatch
and closeout recompute that tree and refuse an absent, blocking, stale, or missing-artifact record.
This is the executable post-code gate; prose, a chat claim, or an unbound evidence reference does
not satisfy it.

**Complexity-scored tiers (per leaf, at dispatch).** The owning seat scores three axes — blast
radius (doctrine/enforcement/public surface vs leaf-local) · novelty (new subsystem vs
pattern-following) · size (files × steps) — into three tiers: **direct** (ordinary build channel
plus the mandatory independent route review; no additional loop machinery),
**builder-verified** (builder implements; owner additionally verifies report-vs-artifact; the
mandatory route review still runs), **full loop** (builder + independent reviewer rounds, with the
mandatory route partition as the review scope floor). The
strategist's blast-radius register is the scoring input when an orchestration task exists. A
leaf's loop mark (tier + scope: manager | orchestrator — the owning level runs the loop with ITS
agent set) is recorded on the leaf doc with a decision-log entry. A master whose leaves all score
`direct` avoids iterative full-loop machinery, but its code leaves still receive independent
route review. The knobs tune review depth and round machinery; they never disable the post-code
independence gate.

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
orchestrator → architect → developer) with the full round history attached**; the escalation packet IS the
upper seat's visibility.

**Quo-vadis (the written developer-escalation criterion).** A question is developer-worthy when it
is a **high-blast-radius truth** — answered wrong it means big rewrites later (architecture
direction, security posture, doctrine contradictions, irreversible data/branch operations, where
agent settings live). Quo-vadis questions escalate IMMEDIATELY to the architect relay,
regardless of round count.
Presentation-grade choices (2px vs 3px) never do — the owner rules and logs.

**Criteria catalogs (the reviewer as test bench).** Criteria are never made up on the spot: every
review runs its type's standing catalog from `criteria/` (code-seam · doctrine ·
onboarding-memory · report-verification · plan-review) plus an exploratory mandate, under the
promotion ratchet (each catalog carries it). `roles/reviewer.md` binds them.

**Per-level agent sets.** Each level runs its loop with its own harness/model/effort set — the
orchestrator-level set (the strongest models) and the manager-level set (cheaper, possibly
workflow-free) are configured per level in the `orchestration.loops` settings block (schema in
`docs/reference/settings-json.md`; stored in the global agentic settings file with repo-local
override, parsed by the kernel agentic-settings loader — L13, landed). The architect proposes a
strategist pre-run, and it occurs only after developer approval; settings cannot auto-run it.

## Delegated Series Authority

Once the developer accepts an orchestrated series/portfolio plan, that acceptance is standing
authority for the owning seats to execute the subordinate edges in that series. Managers govern
their workers, leaf readiness, and released leaf closeouts. The orchestrator governs managers,
the portfolio queue, organizational leaf → super releases, atomic master → super handovers, and
the same closeout/finalize/cleanup mechanics when it wears a manager or worker hat in a flat/direct
run. These edges do **not** stop for a new developer approval just
because a commit, lifecycle finalization, cleanup, or integration command is next; the owner runs
the preview/check, records the accepted-series authority in the intent note or decision log, and
continues.

This does **not** weaken the escalation ladder. Developer approval is still required for the final
completed super integration branch / PR-carryover gate, for any human-pinned gate that is actually
raised (`integration-approval`, `push-approval`, `cleanup-approval`), for scope changes beyond the
accepted plan, for red checks that cannot be fixed inside the task, and for quo-vadis decisions.
Owner-never-self-approves means verdicts and delegated gates need the configured distinct decider;
it does not force a developer hand-off for mechanical closeout of in-scope work the owning seat
performed directly under standing series authority.

## Knob Block & Capability Doctrine (no per-harness files)

Role files are **model-interpreted markdown, never an executor**. Each carries a portable **knob
block** (harness / model / effort / tools) — the defaults the terminal host injects at spawn.
Resolution: **role-file defaults < settings.json orchestration block.** There are deliberately
**no per-harness role files** (developer decision 2026-07-05): harness-specific ABILITIES —
sub-agent fan-out and the like — are covered inside the portable files as capability-conditional
doctrine any coding agent can apply, and harness PREFERENCE is deployment configuration
(settings), not doctrine. Hard-coding a vendor would fork the doctrine per harness. For spawning
seats, `dispatch_agent` is itself the harness-independent fan-out: a harness with no sub-agent
facility still dispatches a canonical document+role seat through the framework — the DBMS
principle: one behavior, any engine.
For ordinary spawned seats, settings are the sole developer-controlled spend surface:
`dispatch_agent` callers declare the canonical task document, role, brief, and optional label;
they never declare harness/model/effort or direct launch/session spend controls.

### `dispatch_agent` has two disjoint caller kinds

| Caller kind | Recognition | Authority | Forbidden shortcut |
| --- | --- | --- | --- |
| Plane-hosted seat | Plane-injected hosted identity is present | Current seat plus direct-child scope policy | Treating a plane authorization failure as ambient |
| Ambient launcher | Plane-injected hosted identity is absent | Canonical target-document resolution plus target role-altitude validation; there is no parent seat | Fabricating caller identity or using ambient mode as an in-hierarchy escape |

The public request is identical in both modes: canonical target document, target role, complete
brief, and optional label. Caller kind comes only from process context; the request never chooses
it. Both modes use the same settings resolution, internal seat creation, readiness proof, exact
brief pinning, rollback, and canonical `(task document, role)` publication. A stale, invalid,
mismatched, unbound, or unauthorized plane identity remains a plane refusal and never falls back.

### Role dispatch is one structural transaction

Every launcher or role that dispatches a hosted role calls `dispatch_agent` once with the target's
real task document, role, and complete brief. The control plane performs the internal transaction:

1. select caller kind from process identity, then either authorize the plane seat's direct-child
   relationship or validate the ambient launcher's target document and role altitude;
2. resolve source lineage from that canonical task document before process creation: an
   organizational leaf requires super → leaf, while an atomic path requires super → master → leaf,
   for code and external memory when enabled; a manager's admission proves its nature-appropriate
   source edge before it can read or dispatch;
3. create and bind the child using settings-owned launch knobs only when every applicable edge is
   current;
4. prove readiness privately;
5. persist exactly one internally exact-pinned initial dispatch brief before delivery;
6. return only structural status (`dispatched` or `dispatch-queued`) and delivery state.

A `source-lineage-stale` or `source-lineage-unavailable` result means no child process was created.
Use its ordered, contract-addressed `worktree_sync` recovery, resolve any retained mechanically
derivable merge conflict through the advertised continuation, then dispatch the same document+role
again. A conflict requiring a genuinely semantic ruling follows the ordinary escalation path; it
is not silently converted into abandonment. Never ask for branch commit ids or occupant ids: task
identity is the input and the control plane owns the Git proof. This early refusal prevents a fresh
seat from reading stale code or stale onboarding before anyone notices the master or leaf fell
behind its parent.

The model never receives the spawned occupant's runtime id and never calls readiness, exact inbox,
attach, or raw retire operations. A queued brief is already durable and follows the ordinary
notifier retry path; never duplicate or respawn it. If persistence fails before the brief exists,
the control plane retires the unbriefed child. Settings-owned `sessionCommands` remain launch
configuration; settings-owned `promptKeywords` ride the initial brief exactly once.

## settings.json Orchestration Block

Machine/user overrides layer over the role-file defaults, in the **global agentic settings file**
(`<coordination-root>/system/settings.json`), with `<code-repo>/system/settings.json` as the
repo-local override layer (leaf-key deep merge, arrays replace, unknown `orchestration.*` keys
fail loud — schema in `docs/reference/settings-json.md`, Agentic Settings). Precedence: role-file
defaults < global settings < repo-local settings.

```jsonc
{
  "orchestration": {
    // Illustrative installed catalog values: choose exact model keys and model-local efforts from
    // each native adapter's dynamic advertise result on the target install/account.
    "roles": {  // role → knob override; validated: harness/model/effort · free-form: launchArgs/promptKeywords/sessionCommands
      "architect":    { "harness": "claude", "model": "claude-fable-5", "effort": "max" },
      "orchestrator": { "harness": "codex", "model": "gpt-5.6-sol", "effort": "high" },
      "strategist":   { "harness": "claude", "model": "claude-fable-5", "effort": "max" },
      "reviewer":     { "harness": "claude", "model": "claude-fable-5", "effort": "xhigh" },
      "system-specialist": { "harness": "claude", "model": "claude-fable-5", "effort": "high" },
      "curator":      { "harness": "codex", "model": "gpt-5.6-sol", "effort": "medium" },
      "worker":       { "harness": "codex", "model": "gpt-5.6-sol", "effort": "medium" }
    },
    "rolesPerLevel": {  // per-LEVEL agent sets (leaf|master|portfolio), deep-merged over roles
      "master":    { "reviewer": { "model": "claude-fable-5", "effort": "xhigh" } },
      "portfolio": { "reviewer": { "model": "claude-fable-5", "effort": "max" } }
    },
    "concurrency": { "maxParallelMasters": 2, "maxParallelLeaves": 3, "maxSubAgents": 4 },
    "spawn": { "harness": "claude" },  // fallback when no role/level knob supplies one
    "gateDelegation": {
      "policy": "manager-decides-leaf-gates",
      "requireReviewerVerdictAtSeams": true
    }
  }
}
```

**As-built (L13 + L16):** the whole block is parsed by the kernel agentic-settings loader
(`kernel/agentic_settings.py`) — typed models for `roles` / `rolesPerLevel` / `concurrency` /
`spawn` / `harnesses` / `loops`,
read PER-USE (an edit applies on the next use, no restart). `orchestration.gateDelegation` is
parsed and **enforced** (`controlplane/gate_policy.py` — all-human default, opt-in delegation,
human-pinned kinds `integration-approval` / `push-approval` / `cleanup-approval`, owner never
self-approves); it is the ONE boot-snapshot key — read from the global file at MCP boot, a change
needs a restart (an authority-file value is a one-cycle legacy fallback with a boot warning).
`requireReviewerVerdictAtSeams` **binds delegated seam decisions** (`master-handover-approval`) to
attached reviewer-verdict evidence; the named policy `manager-decides-leaf-gates` routes leaf gates
to the manager and the master-exit handover to the **orchestrator** (human review concentrates at
the super gate). The plane-owned structural dispatcher resolves spend knobs (260703-L16 +
HFX2-L10) as
repo-local level override > global level override > repo-local role default > global role default
> detection-gated default — the dispatcher declares its `level` (leaf|master|portfolio, default
leaf) and the resolved level rides spawn provenance. Legacy caller-supplied `harness`/`model`/
`effort`, direct launch/session controls, `AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT`, or harness-native
spend/endpoint env keys refuse before spawning with `spend-override-unsupported`. Resolved knobs are
**applied** through one typed native launch selection. `AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT` remain
spawn provenance, not a second authority. Before the configured vendor session starts, the native
adapter advertises its token-free, per-install/account catalog and validates the exact model plus
that model's launch-settable effort. Claude uses native `--model`/`--effort`, Pi uses the exact
provider-qualified key through `--model` plus `--thinking`, and Codex sends model plus
`model_reasoning_effort` in `thread/start` configuration. Missing, stale, unsupported, or conflicting
native selections fail through the hosted control state; there is no static builtin vocabulary,
vendor-default substitution, or normalized model/effort composer paste. The free-form escape hatch (`launchArgs` verbatim argv,
`promptKeywords` riding the post-readiness brief exactly once, `sessionCommands` applied during
fresh-session launch) remains explicitly user-authored, is never validated, and is never synthesized
from normalized model/effort. It is recorded in spawn provenance; `orchestration.harnesses` teaches the framework new
TUIs or pre-customizes builtin launches (manual: `docs/reference/harnesses.md`).
`orchestration.loops` (the three-party-loop knobs: per-level loop sets, round cap, reviewer
reuse, complexity thresholds) lives in the same block — meaning in
`docs/reference/settings-json.md`; no knob touches the master-exit seam gate.

## Companion Files

- `lenses.md` — the four job lenses for the scoping seats.
- `roles/…` — the nine self-contained role lifecycles (the registry above).
- `templates/…` — turn-report · worker-brief · manager-brief (`ROLE BRIEF — manager`; the
  orchestrator compiles a manager's session start from it) · curator-brief (`ROLE BRIEF — curator`;
  the manager compiles a curator's session start from it, feeding the leaf's landed change set +
  task doc + notes/ — never spawned before builder code and the reviewer verdict exist) ·
  master-handover-packet · conversation-handover-packet · verdict · impact-analysis ·
  onboarding-coherency · deep-research-report · orchestration-task (the strategist's sprint plan).
  Spawning seats compile briefs FROM these; sub-agents fan out and fill them, so analysis survives
  compaction.
- `criteria/…` — the reviewer criteria catalogs (code-seam · doctrine · onboarding-memory ·
  report-verification · plan-review), the review test bench the three-party loop binds; maintained
  through the promotion ratchet, never made up on the spot.

## The Super Integration Branch (orientation only — the doctrine lives with its owner)

```
main
  └── super-integration (orchestrator-owned, off main)
        ├── organizational master A (logical owner; leaves land directly on super)
        ├── atomic master B (isolated branch; all leaves land there, then B lands once)
        ├── later leaves refresh from moved super before closeout
        └── … final: super → main PR + memory carry-over + push
```

The full topology — canonical graph, execution-nature classification, ready-frontier recomputation,
landing procedures, conflict routing, and leaf moves — lives in **`roles/orchestrator.md`** and
only there.

## Credits

This skill absorbs and supersedes `l-01-session-job-lifecycle` and `l-02-agent-orchestration`
(converged 2026-07-05: lifecycle and job are one entity — one lifecycle per agent type). The
orchestration vocabulary adopts the parked `260619_agentic-control-plane` spec — jobs as
model-interpreted markdown (D6), the knob block (D7), role + lens in one file (D10), the
ambient-singleton rule (D11), per-harness variants (D12), the judge rung, short-lived workers with
structured handoff, dev-talks-to-one-architect (D15) — which in turn credits **Archon** and the
**agent-control-plane** project (D14); that credit carries forward.

## Relationship To Other Instructions

This skill extends — never replaces — the coordinator `AGENTS.md`, the `w-02-light-task-workflow`
task format, and the memory layer (`c-…` skills). Each `roles/<role>.md` is self-contained for its
seat; read exactly the one the router selects.

# Core — Seat Authority (shared by every role)

The five core files are authoring references; their actionable shared rules reach every started role through the admitted digest `core/every-role.md`, beside its role and operation capsule.

## One lifecycle per agent type

Lifecycle and job are one entity. Every role runs its own self-contained lifecycle under this skill. Its supplied role, operation and delivered details state its obligations; a started role does not load another role's file to invent its own duties.

Authority in this system is **structural, never textual**: a seat's task document plus its role
name is its address, and AR — not a prompt or a chat claim — records which agent holds it when
`role_start` starts it.

## Seat binding

| Altitude | Canonical document | Ordinary occupants |
| --- | --- | --- |
| Projects | none — the start names no document; the sprint document holds the developer's request, and the developer names it to the architect in the dashboard | architect |
| Sprint | the sprint document | orchestrator; Investigator when its selected concern binds the sprint |
| Master | the master document | manager; Investigator when its selected concern binds the master |
| Leaf | the leaf document | worker, curator and every Reviewer, including a review of a master or sprint seam |
| Portfolio artifact | none — a notes draft only | strategist's draft before adoption |

- A task-bound seat is its canonical document plus role; a second live occupant of that same seat collides. Architect and Bootstrap starts name no document. An Investigator takes the supplied taskless, sprint or master concern, never a leaf. Other starts carry the exact references their role requires.
- A started agent's parent is the agent that called `role_start` for it; a role started from the
  dashboard or by a chat without a role has none.
- A spawned seat keeps its role. Architect direct coordination is only the developer-chosen exception and grants no Worker, Reviewer or Curator execution; it permits paired closeout only where the kept closeout procedure authorizes it.

## Role-seat immutability (dashboard-owned sessions)

When the dashboard owns a session, its role is fixed for the session lifetime.

- A pasted brief for another role is **refused**, not absorbed: the seat sends the mismatch to
  its parent with `role_message` instead of silently rerouting itself.
- Roles expand **horizontally** by starting new, individually addressable role agents
  (`role_start` with the role and its canonical task references). A role seat is never a
  harness-native sub-agent of another role seat.
- Harness sub-agents work inside the seat's assignment with its permissions and working folder.
  The seat organises their work, verifies and credits it, and performs all boundary acts itself.
  Helpers hold no AR seat, start no role and cannot supply independent review of their author.
- Sessions not owned by the dashboard follow their host harness's ordinary rules.

## Starting a role agent

A role starts another with `role_start` once, the role, a chosen request ID and its exact required task references. The same ID and selection reconcile the same start. Settings supply harness, model and effort unless explicitly overridden; the call states no mode. Report a refusal and its reason without another-tool fallback. Start a leaf Worker first, its Reviewer when the build is ready, and its Curator after code PASS; `role_start_set` is not the stage-start route.

Keep the per-start override route. After an override start, the starter checks the seat’s mode and features and sets them. The current code drops them; record that as a product issue rather than removing overrides.

## Developer-declared task-seat takeover

When the developer says *"you are the orchestrator/manager/worker for task X"* (or equivalent),
that is a **task-seat takeover**, not a loose role hint. Before analysis, profile checks, a start,
or implementation, resolve the named task document and converge on that role's canonical seat at
its canonical altitude (table above; a reviewer binds the leaf, master, or sprint document its
exact review seam adjudicates).

1. Resolve the canonical JSON-primary task document and the role being claimed.
2. Start the named role on that document with `role_start` once. An architect is started without
   a document reference: the sprint document holds the request, and the developer names it in the
   dashboard.
3. Tell the developer that the agent is in the dashboard.

A chat without a role makes the same `role_start` call. Takeover never means replacing a live
incumbent: a start without a binding takes no replacement. If `role_start` refuses the selection,
say which document or role is missing — never improvise another way to attach.

## Escalation ladder

**worker → manager → orchestrator → architect → developer.** An Investigator escalates to the parent that started it, or asks the developer without one. **No rung is skipped, ever.** Each role file states only its own rung.

A question is developer-worthy when it is a **high-blast-radius truth** — answered wrong it means
big rewrites later (architecture direction, security posture, doctrine contradictions, irreversible
data/branch operations, where agent settings live). Such a question escalates **immediately** to
the architect relay, regardless of any loop's round count. Presentation-grade choices (2px vs 3px)
never do: the owning seat rules and logs.

## Developer clarification triage

When the developer clarifies a rule, boundary, or desired behavior during an active task, decide
whether it is **current implementation** or **future queue** before writing only a note. Read the
active queue first: the current leaf, parent/master, neighboring leaves, decision log, open
questions, and in-flight branch state. The question is not whether a note is useful; it is whether
the developer is effectively steering the work already in hand.

- Current implementation names the current task, resolves its exposed defect or improves the same intended doctrine or code path. Make the scope visible in the task record; do not turn a proposed restriction or a changed promise into an approved requirement without the developer.
- **Future queue** — it names a later release, a separate subsystem, a large scope jump, work whose
  correctness depends on another unfinished master, or a change that would reorder already-running
  leaves. Record it in the right durable queue or ask the owning seat to plan it later.
- If the intent is genuinely ambiguous after reading the queue, send the question to your parent with `role_message` on `agents-remember-task`; without a parent, ask in your own chat. Do not
  silently downgrade a close/current/small change into future speak, and do not silently expand the
  active leaf when the fit is unclear.

## Delegated series authority

Acceptance of a series plan authorizes its owners to execute its subordinate edges. Managers own leaf readiness, gate acceptance and released closeouts; Orchestrators own the portfolio and delegated master coordination. An Architect performs direct coordination only when the developer chose it. For authorized mechanical transactions, preview, record the standing authority and continue; do not request a fresh approval merely because the next step is integration or finalization.

This does not weaken the ladder. Developer approval is still required for the final completed super
integration branch / PR-carryover gate, for any human-pinned gate that is actually raised
(`integration-approval`, `push-approval`, `cleanup-approval`), for scope changes beyond the accepted
plan, for unresolved transaction conflicts or scope blockers, and for quo-vadis decisions.
Owner-never-self-approves means verdicts and delegated gates need the configured distinct decider;
it does not force a developer hand-off for mechanical closeout of in-scope work.

## Minimal decision-item relay

A question for the developer goes up the chain with `role_message`; `operations/orientation.md`
states how.

- Send the developer question to your parent, naming the decision, what you hold, your recommendation and evidence; continue independent work. Without a parent, ask in your own chat.
- The architect presents one item at the developer's pace, records the ruling in the durable task
  surface (`openQuestions` / decision logs, with notes for analysis), and returns the answer with
  `role_message`.
- If the item is underspecified, the architect sends a single clarification back instead of
  guessing.

## Notify-and-stop is safe by design

A silent turn end is not an artifact handover. The host treats a Worker, Reviewer or Curator with no hand-off, block or declared helper wait as silent: after 60 seconds the first two consecutive ends wake that seat; from the third it sends the parent a marked line. Other roles ordinarily give the parent their end line; permission waits and the host-held helper states keep their own boundaries.

**Watcher ban (uniform-mechanism ruling 2026-07-07):** no role watches, polls, nudges, or
timer-loops on its own initiative — that is a banned seat-local watcher, and there is no per-seat
variance. Every role's own liveness duty inverts to *passive*: you are woken by a message; answer
it before ending your turn again.

Collect helpers and continue ordinary work. Preserve native within-turn collection. On the declared ACP helper-wait path supplied at launch, end a waiting turn with `WAITING FOR SUBAGENTS:` and each helper's progress; answer the host's progress check in your own chat, collect completed work, then declare again or continue. Send no parent progress message while waiting and create no watcher, polling loop or second wake mechanism.

## Change authority

- **Doctrine and instruction text** is changed only through a task;
  a seat never rewrites its own role file or another role's file to resolve a disagreement.
- **Requirements** are owned by the architecture seat that compiled them; a claimed contradiction
  or overconstraint is diagnosed and proposed, never self-approved, and never silently rewritten.
- Authorized closeout and integration publish only their approved code and prepared memory pair. Run the focused checks the change touches, with truthful failed and not-run results. Full code quality, full-suite tests and independent review need an explicit task or developer request; curation still runs complete memory quality and closeout carries that result without rerunning it.

Publishing—push to the sprint line, pull requests and release—belongs to the developer unless explicitly delegated.

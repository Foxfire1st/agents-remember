# Reference — Durable Rulings This Corpus Implements

> **Reference-only, and normative in one direction only:** each row records a ruling the corpus
> obeys, the anchor that carries the ruling, and what it superseded. A seat follows the rule where it
> is authored (a `core/`, `operations/`, or `roles/` file); this file is where the *reason* and the
> *supersession* are recorded. Frequency of repetition is never authority — these citations are.

## The ruling index

| Ruling | Date / anchor | What it decides | Supersedes |
| --- | --- | --- | --- |
| Lifecycle and job are one entity | converged 2026-07-05, `../SKILL.md` credits | Each agent type runs its own self-contained lifecycle; one roof over all of them | `l-01-session-job-lifecycle` + `l-02-agent-orchestration` |
| No per-harness role files | developer decision 2026-07-05 | Harness-specific *abilities* are capability-conditional doctrine inside portable files; harness *preference* is settings, not doctrine | the D12 per-harness-variant reading |
| Designer is an inline architect hat | developer decision 2026-07-04 | Design routes through the architect, which may wear `roles/designer.md` at the front of the pipeline and mid-flight; a separate chair is optional | one implicit do-it-all role |
| Strategist is spawn-first, designer is not | developer decision 2026-07-05 | The strategist's solitary heavy analysis runs as its own process; the designer stays interactive and inline | applying the designer precedent to the strategist |
| Developer owns the loop by talking to one architect | developer ruling 2026-07-06 | Backend churn reaches the developer only as one decision item through the architect relay | backends talking to the developer |
| The reviewer is also every requested loop's reviewer seat | developer ruling 2026-07-06 (L12-Q2) | Reuse, not a separate loop-checker: same role file, same verdict template, catalog picked by review type | a distinct "loop checker" role |
| Two adversarial seams | developer decision 2026-07-03 | Master-exit (manager → orchestrator) and super-exit (orchestrator → architect/developer) | ad-hoc review placement |
| The strategist pass is proposed, never auto-run | ruled 2026-07-09 | The architect asks; a sanctioned skip makes the orchestrator author the same reasoned plan and explicit topology choice | the 2026-07-06 "mandatory strategist pre-run" rule |
| Free chat is a launcher, not a role seat | ruled 2026-07-09 | Condition 3 of the router dispatches the architect once and hands over | assuming the architect role in the developer chat |
| The spool-up chain is fixed and self-driving | ruled 2026-07-09 | Free chat → architect → orchestrator → managers → workers; no seat waits to be told what to spawn | per-request spawning |
| No seat-local watchers | uniform-mechanism ruling 2026-07-07 | One mechanism (the agent-notifier sweep); every seat's liveness duty inverts to passive | the timed escalation ladder, renudge/skip-level/respawn machinery |
| Review independence and evidence-type matching | added 260815-DAG-L15 | The reviewer seat is never the author seat, and every requirement verdict must cite evidence of the requirement's class | passing a requirement on projection-only evidence |
| Orchestration seats use no native sub-agents | doctrine ruled 2026-08-05 | Every agent is the seat's own main loop or a role seat dispatched through AR; AR state mutations stay in the owning seat's main loop | native fan-out beside the orchestration machinery |
| A candidate change cannot reopen acceptance | loop doctrine, `../core/loop.md` | A changed candidate, source, requirement version, model, seat, route, or report label does not reset the sealed review | implicit re-review on change |
| Optional review stays optional | developer-approved simplification | Review runs only when the developer or the approved brief requests it; closeout and integration never require or launch it | review as an automatic closeout gate |
| Git transactions do not re-acquire a tracked ledger leg | LCA, already in the IAS baseline | The ledger is an ignored consumer cache derived from memory commit trailers; it neither grants nor blocks landing authority | a ledger commit leg or ledger-prerequisite gate |
| The computed ledger cache is preserved | LCA (`260913_ledger-commit-attribution`), integrated in IAS | Computed, untracked ledger behaviour is baseline behaviour and must not regress | — |

## The LOCR rulings carried into this corpus

The ambient checkout stood on `ar/260831_lifecycle-owned-completion-relay` (LOCR), ahead of IAS, and
carried **accepted role-instruction doctrine that IAS lacked**. The instruction deltas below were
carried in as applicable developer rulings. Their anchor is the LOCR diff
`ar/260713_improved-agentic-system..ar/260831_lifecycle-owned-completion-relay -- skills/`.

| # | LOCR anchor | Ruling carried in | New home |
| --- | --- | --- | --- |
| LOCR-1 | `skills/l-01-agent-lifecycles/SKILL.md` — new section *Completion Truth And Handoff Acceptance*. | Terminal truth is mechanical and acceptance is the owner's: a canonical `completed` outcome means only that the provider turn ended; it does not attest that the artifact exists, is current, or satisfies its requirement. A terminal `interrupted` outcome remains an interruption. The relay derives and delivers the state signal and never opens, parses, or evaluates an artifact; the owner alone validates before advancing lifecycle state; a missing/malformed/stale artifact is an owner-detected handoff defect; the subordinate's artifact obligation is unchanged and needs no second model-authored completion post | `core/acceptance.md` (one home), with per-seat sides in `roles/worker.md`, `roles/manager.md`, `roles/curator.md`, `roles/reviewer.md`, `roles/strategist.md`, `roles/system-specialist.md`, `roles/orchestrator.md` |
| LOCR-2 | `roles/manager.md` — the passive process-and-ack contract | The sweep never opens or evaluates the artifact and never infers expectations from artifacts; a canonical `completed` outcome never attests that a report exists; the manager opens and validates the artifact, candidate identity, evidence, and acceptance envelope before advancing lifecycle state, and treats a missing/malformed/stale artifact as its own detected handoff defect after wake | `roles/manager.md` § Normal Workflow / § Stop And Escalation Cases |
| LOCR-3 | `roles/worker.md` — the report-before-ending rule | Ending the turn once the report is written is safe; a report never written is a handoff defect the owning seat detects after the turn-ended state signal wakes it; the relay itself never inspects the artifact | `roles/worker.md` § Completion And Handoff |
| LOCR-4 | `roles/reviewer.md` — Comms Protocol | The verdict artifact is the durable handoff; terminal/finalizer truth attests only that the turn ended and wakes the decider, who validates the verdict independently; no second completion row, no carried decider identity | `roles/reviewer.md` § Completion And Handoff |
| LOCR-5 | `roles/curator.md` — Comms | Write the report before ending; terminal/finalizer evidence then attests only that the turn ended and wakes the owner, who validates the report | `roles/curator.md` § Completion And Handoff |
| LOCR-6 | `roles/strategist.md` — adopted-plan handover | The artifact write is unconditional; terminal/finalizer truth then attests only that the turn ended and wakes the orchestrator, which validates the artifact | `roles/strategist.md` § Completion And Handoff |
| LOCR-7 | `roles/system-specialist.md` — Comms | The report/fix artifact is the durable record; terminal/finalizer state then attests only that the turn ended and wakes the orchestrator, which validates the report | `roles/system-specialist.md` § Completion And Handoff |
| LOCR-8 | `roles/orchestrator.md` — execution loop | The signals the sweep wakes this seat with are seat-turn state-signals, nudges, and escalation intake — not turn-report artifacts; the seat still never watches for them itself | `roles/orchestrator.md` § Normal Workflow |
| LOCR-9 | `templates/master-handover-packet.md` — rule 5 and the preamble | The packet is durable, and terminal/finalizer truth — which attests only that the manager's turn ended — wakes the orchestrator, who validates the packet; ordinary completion comes from the packet plus that truth, and the terminal outcome attests only that the turn ended | `templates/master-handover-packet.md` |
| LOCR-10 | `templates/turn-report.md` — preamble | The relay never inspects the report: it derives and delivers the worker's turn-ended state signal, and the manager — never a seat-local watcher — detects a missing report after that wake and nudges | `templates/turn-report.md` |

**Explicitly not imported.** LOCR's serving and runtime changes — the terminal observer, owner
signals, app routes, and dashboard work — are unrelated to this experiment's scope. They were read as
evidence and left alone; the LOCR branch was not merged.

# Core — The Minimal Lifecycle Frame

The only machinery every session shares. Six signals record where a seat is and what it waits on, so
work is observable and resumable across chat deaths. Lifecycle correlation is server-side and
anchored in the worktree contract; it is not model state.

| When | Signal | Effect |
| --- | --- | --- |
| Trust checkpoint passes (managed repo) | `lifecycle_start` | begin a **fleeting** lifecycle (guarded: one per session; no id) |
| Entering a phase | `lifecycle_phase` | move the phase axis (`request` / `trust-checkpoint` / `reframe-research` / `decide` / `build` / `close`) |
| A developer hand-off | `lifecycle_turn_end_notification(summary=…)` | set `awaiting-developer`, surface the attention item, return immediately; the **next turn's first AR call auto-resumes** — never resume by hand |
| `worktree_start` | *(automatic promotion)* | the fleeting lifecycle becomes **persistent**, anchored in the contract |
| Resuming an existing task | `worktree_attach` | re-adopts the contract's lifecycle (contract-resolved) |
| Leaving unsaved fleeting work | `switch_lifecycle` (`on_unsaved=save`\|`discard`) | the save gate — never dropped silently |
| Close | `lifecycle_end` (`completed`\|`abandoned`) | the terminal record |

Rules:

- A tool call outside any lifecycle is **dropped, never misattributed**. `paused` is system-owned.
- A spawned role that never touches mutating AR tools simply never instantiates a lifecycle — that
  is correct, not a violation. A spawned role runs its **own** lifecycle when it runs one; it never
  adopts its spawner's.
- The seat binding is the catalog binding made at dispatch (canonical task document plus role), not
  lifecycle adoption. Sprint roles bind to the sprint document, managers to master documents,
  workers/curators to leaf documents, and reviewers bind to the exact leaf, master, or sprint
  document whose review seam they adjudicate.
- A holder of one lifecycle never advances another seat's. Owning seats own the machinery their own
  role file names, and no other agent calls `task_doc`, gates, `dispatch_agent`, or closeout on
  their behalf.

## Which signals a seat runs

| Seat | Lifecycle use |
| --- | --- |
| Developer-facing seats (architect) and portfolio/coordination seats (orchestrator, manager) | run their own lifecycle; the manager's is promoted to persistent by `worktree_start` on a leaf |
| Build/report seats (worker, curator, reviewer, strategist, designer, system-specialist) | a short-lived seat that never touches a mutating AR tool never instantiates one; where it does mutate, it runs its own |
| Ambient launcher | a launcher, not a seat: condition 3 of `../SKILL.md` routes it, and `core/launcher.md` owns its obligations |

## The trust checkpoint

The opening detail the **architect and orchestrator** run before relying on memory or providers
(each role file binds whether its seat runs it; a spawned role never repeats it, because its
spawner compiles the facts into the brief):

1. `context_packet(repo_id="<repo-id>", include_providers=true, include_drift=true,
   include_freshness=true)`.
2. Report the packet facts before relying on memory or providers: repository/branch/dirty state;
   memory + onboarding roots; provider state; drift status and actionable count; branch freshness
   (`behind`/`diverged` → fast-forward the local official line first). Reconcile actual memory
   content or ancestry conflicts; missing cache rows or attribution for unchanged memory do not
   block an otherwise valid code/memory pair.
3. Drifted/missing/orphaned onboarding on committed, non-dirty source: **emit a decision item to the
   architect** before refreshing via `c-05-create-or-update-onboarding-files` — drift handling is
   approval-gated. Drift tied to dirty source is active work-in-progress, not maintenance.
4. Providers stopped/degraded: run the matching provider/runtime operations, re-check, report;
   `indexing` means healthy-but-busy (partial results).

## Provider degradation, as every seat meets it

A `degradation-alert` event pauses provider **starting**, not the seats.

- Managers and other leaf-altitude seats: stop starting providers until an all-clear — no worktree
  provider setup, no `provider_watchers start`, no watcher restart, no `retry_provider_setup`.
  Continue providerless/native-read work that remains valid and report provider-dependent blockers
  upward. **No provider kill authority**: no docker-kill, no container stop, no provider teardown.
- The orchestrator owns investigation dispatch, the remediation order, and the provider stop
  (through the system-specialist protocol).
- This iteration is providers-only. Sentry or a future system monitor may feed the detector later;
  that does not change the response protocol: detect → report → explicit orchestrator order →
  fix or stop.

# Core — The Minimal Lifecycle Frame

The only machinery every session shares. The worktree tools record where a task is, so
work is observable and resumable across chat deaths. Lifecycle correlation is server-side and
anchored in the worktree contract; it is not model state. Nothing else begins, blocks or hands
over a lifecycle.

| When | Effect |
| --- | --- |
| `worktree_start` | adopts the lifecycle of the new contract, anchored in the contract |
| Resuming an existing task | `worktree_attach` re-adopts the contract's lifecycle (contract-resolved) |
| Close | the terminal record (`completed`\|`abandoned`) |

Rules:

- A tool call outside any lifecycle is **dropped, never misattributed**. `paused` is system-owned.
- A role that never calls `worktree_start` or `worktree_attach` has no lifecycle — that
  is correct, not a violation. A started role never adopts its starter's.
- The seat binding is made when `role_start` starts the agent (canonical task document plus role), not
  by lifecycle adoption. The architect is started without a document; the other sprint roles bind
  to the sprint document, managers to master documents, workers/curators to leaf documents, and
  reviewers bind to the exact leaf, master, or sprint document whose review seam they adjudicate.
- A holder of one lifecycle never advances another seat's. Owning seats own the machinery their own
  role file names, and no other agent calls `task_doc`, `role_start`, or closeout on
  their behalf.

## The trust checkpoint

The opening detail the **architect and orchestrator** run before relying on memory or providers
(each role file binds whether its seat runs it; a started role never repeats it, because the AR
handover carries its task facts and workspace bindings):

1. `context_packet(repo_id="<repo-id>", include_drift=true,
   include_freshness=true)`.
2. Report the packet facts before relying on memory: repository/branch/dirty state;
   memory + onboarding roots; drift status and actionable count; branch freshness
   (`behind`/`diverged` → fast-forward the local official line first). Reconcile actual memory
   content or ancestry conflicts; missing cache rows or attribution for unchanged memory do not
   block an otherwise valid code/memory pair.
3. Drifted/missing/orphaned onboarding on committed, non-dirty source: **send the question up the
   chain with `role_message`** before refreshing via `c-05-create-or-update-onboarding-files` — drift handling is
   approval-gated. Drift tied to dirty source is active work-in-progress, not maintenance.
4. Providers stopped/degraded: run the matching provider/runtime operations, re-check, report;
   `indexing` means healthy-but-busy (partial results).

## Provider degradation, as every seat meets it

A `degradation-alert` event pauses provider **starting**, not the seats.

- Managers and other leaf-altitude seats: stop starting providers until an all-clear — no worktree
  provider setup, no watcher start or restart.
  Continue providerless/native-read work that remains valid and report provider-dependent blockers
  upward. **No provider kill authority**: no docker-kill, no container stop, no provider teardown.
- The orchestrator owns investigation dispatch, the remediation order, and the provider stop
  (through the investigator protocol).
- This iteration is providers-only. Sentry or a future system monitor may feed the detector later;
  that does not change the response protocol: detect → report → explicit orchestrator order →
  fix or stop.

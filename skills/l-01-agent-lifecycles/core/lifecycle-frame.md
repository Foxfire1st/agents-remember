# Core — The Minimal Lifecycle Frame

The worktree tools record where a task is, so work is observable and resumable across chat
deaths. The landing chain keeps its own lifecycle in the worktree contract.

| When | Effect |
| --- | --- |
| `worktree_start` | creates the task's worktree and records its location in the contract |
| Resuming an existing task | `worktree_attach` resolves the contract and reports its worktree |
| Close | the landing chain records completion or abandonment in the contract |

Rules:

- A task-bound seat is bound by its canonical document plus role. Architect and Bootstrap start without documents; an Investigator uses its supplied taskless, sprint or master scope. Managers bind masters, Orchestrators sprints, and every Reviewer binds a leaf, including a Reviewer of an integration seam.
- Owning seats own the machinery their own
  role file names, and no other agent calls `task_doc`, `role_start`, or closeout on
  their behalf.

## The trust checkpoint

Orientation uses the role, exact canonical assignment and workspace bindings the handover supplies, not an invented trust packet.

1. Read the supplied rendered task and requirement files with native file tools; change canonical task documents only through `task_doc`.
2. Confirm role, task, requirement version, roots, contract and report path agree; name the owning source and field of any missing binding.
3. Before repository planning, the Architect reads the relevant knowledge and resolves the memory layer; onboarding changes belong to the Curator, not a started role's invented refresh.
4. Provider problems are investigated read-only; no role acquires a provider state-changing operation from this checkpoint.

## Provider degradation, as every seat meets it

A `degradation-alert` event pauses provider **starting**, not the seats.

- Managers and other leaf-altitude seats: stop starting providers until an all-clear — no worktree
  provider setup, no watcher start or restart.
  Continue providerless/native-read work that remains valid and report provider-dependent blockers
  upward. **No provider kill authority**: no docker-kill, no container stop, no provider teardown.
- An Investigator investigates and reports within its supplied concern. Provider state changes go to the owning Worker; remediation requires the explicit authorized order of the developer or the named parent, and a no-start alert remains binding.
- The provider protocol is detect, report, obtain the owning authority's explicit order, then perform only the authorized remediation; a possible future monitor grants no present authority.

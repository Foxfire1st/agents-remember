# Operation — Orientation

**What it covers:** the opening of any seat's turn — reading the brief and its durable inputs,
establishing trust facts, and saying the current state back before acting.

**When it is selected:** the first turn of a seat; any resumed or reconnected session; any turn
whose context was compacted or cleared and whose binding must be re-established.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| architect | full trust checkpoint, then portfolio + decision-surface catch-up (task docs, open questions, architect-addressed inbox rows, backend reports awaiting a ruling), then a plain-language state read-back |
| orchestrator | trust checkpoint, `lifecycle_start`, then portfolio orientation — what exists, what is in flight, what is blocked on whom, what awaits the architect relay |
| manager | read the master + leaf docs and require `executionNature` and the sprint graph reference; the source edge is proved by structural admission, not re-derived here |
| worker | read the brief, then the leaf spec/`task_doc` it names, then the files to be touched |
| curator | read the brief, leaf task doc, approved packets, builder report, and the fed change set |
| reviewer | scope the review — the exact candidate, the task docs, and the seam's rubric |
| strategist | read the brief and every referenced durable artifact; do not re-run the trust checkpoint |
| designer | meta-question the ask and establish the evidence model within the master's scope |
| system-specialist | read the orchestrator brief and the degradation event first |

The trust checkpoint itself is the architect's and orchestrator's shared opening detail and is
authored once in `../core/lifecycle-frame.md`.

## Required inputs

- The dispatch brief — which for a spawned seat **is** its session start. A workspace session-start
  notice is not addressed to a spawned seat.
- The canonical task document(s) the brief names, and the leaf/master/sprint documents one rung up
  that the seat must not mutate.
- The exact applicable requirement revisions by stable ID + version, with their canonical packets.
- For a worker: the code worktree (the only write area besides its report) and the memory worktree
  as read-only context.
- The resolved memory layer's `system/tools.md`, `system/coding-guidelines.md`, and
  `system/git-workflow.md` where the seat's work touches code, commits, or checks.

## Normal workflow

1. **Run the takeover checklist first** when the developer declared this chat the seat for a named
   task: converge on the canonical `(task document, role)` seat before any analysis, profile check,
   dispatch, or implementation. The checklist is in `../core/authority.md`.
2. **Read the brief fully**, then the documents it names — not the other way round. A brief that
   names a leaf, master, or sprint replacement is the map; the document is the territory.
3. **Establish the seat's own trust facts.** A spawned seat uses the facts its spawner compiled into
   the brief and does not repeat the checkpoint. The architect and orchestrator run the checkpoint
   itself.
4. **Confirm the binding before acting.** The `(task document, role)` row exists, the seat's
   altitude matches the document's altitude, and the brief carries everything the role file requires
   under "Required inputs".
5. **Say the state back in plain terms** before asking anyone to decide anything, leading with any
   accumulated catch-up digest.
6. **Only then** route to the operation the turn actually needs.

## Authority gates

- A seat edits nothing outside the surfaces its brief names.
- A takeover never replaces a live incumbent by hand; only the lifecycle-owned transaction may
  retire a generation it has positively proved failed.
- A spawned role never re-runs its spawner's trust checkpoint, and never asks the developer for
  facts the brief already carries.

## Failure handling

- **Missing or malformed brief identity** — an unresolved placeholder, two colliding IDs, a packet
  version that disagrees with the brief, or an absent requirement/rationale reference makes the
  dispatch incomplete: refuse it and report the structural blocker rather than inventing or
  repairing an identity. The role file names what its own seat must refuse.
- **Unresolvable role identity** (`AR_SPAWN_ROLE` with no matching `roles/<value>.md`) or hosted
  identity without its matching role is malformed plane identity and **fails closed**; it never
  falls through to the role-brief or free-chat condition.
- **A valid role-env session whose brief never arrives** announces itself on the inbox and waits; it
  never improvises a task.
- **`source-lineage-stale` / `source-lineage-unavailable`** — no child was created. Follow the
  refusal's ordered, contract-addressed recovery and retry the same document + role.
- **A referenced artifact that is missing or unreadable** is a finding in the seat's own artifact,
  not a licence to improvise around it.

## Handoff / exit

Orientation ends when the seat can state, from durable state alone: which task it is on, which
operation this turn needs, what it may write, and what it is waiting on. Nothing is dispatched,
edited, or decided during orientation that the seat's role file does not already authorize.

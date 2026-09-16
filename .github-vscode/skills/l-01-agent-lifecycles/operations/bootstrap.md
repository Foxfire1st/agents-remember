# Operation — Session Bootstrap

**What it covers:** the new user's first hour, end to end, with the memory repository as its centre of
gravity: what Agents Remember is for, the decisions only the developer can make, initializing the
memory root, scaffolding the first onboarding, adopting the first attributed baseline, and verifying
the result.

**When it is selected:** a repository has no working memory root yet, or has one that does not resolve
— no memory root, a memory root with no baseline, the removed repo-local layout, a coordination root
that will not resolve, or providers configured but not indexing. Once a repository has an attributed
baseline and a resolving context, its ordinary work belongs to the lifecycle roles; this operation is
not a maintenance posture and re-entering it to "tidy" a working installation is out of scope.

**How the carrier is started:** as a **free agent**, not a task seat. A regular session opens a
session with `role=bootstrap` and **no task document**, and the role travels into the new session's
environment (`AR_SPAWN_ROLE`). The role is admitted by name as a taskless seat, so that call opens
rather than refusing. There is no brief, because nothing dispatches it; the operation's instructions
reach that session as the compiled capsule. This operation therefore never waits for a task document to
appear, and never treats its absence as a condition to repair.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| bootstrap | the only carrier, and a **free agent** rather than a task seat: establish the developer's situation and their decisions, run the setup through the surfaces that own each step, hand the procedure to its `c-*` owner where one exists, and report the resulting state |

No other role carries this operation. A seat that is already in a sprint, master, or leaf does not
become a bootstrap seat to fix its own context; it resolves context through
`c-08-ar-coordination-context-resolver` and, if setup is genuinely missing, says so and stops.

## Required inputs

This agent is started **before** any task document exists, so its required inputs are facts about a
workspace, not about a task:

- Which code repository the developer means, and its absolute root.
- The resolved `coordinationRoot` and `workspaceRoot`, from `server_info`.
- Whether a memory root already resolves for that repository, and what state it is in.
- The developer's decisions, asked one at a time and in plain language:
  1. which code branch is the foundation (the **spear**) of the repository's memory;
  2. whether they are scaffolding a new memory repo or pointing at an existing one;
  3. whether providers should index now or be deferred.

A brief, ticket, leaf document, worktree contract, or gate is **not** an input this operation has, and
their absence is normal rather than an error to repair.

## Normal workflow

Each step names the surface that owns it. This operation **runs** that surface and **points at** the
`c-*` skill that documents it; it never restates the skill's procedure.

1. **Say what Agents Remember is and what the setup will change**, in plain language, before asking
   anything to be decided. The developer must know: their code is where it is, their memory lives in a
   separate Git repository beside the coordination root, onboarding documents live there rather than
   beside the code, and a small mapping records which memory content belongs to which code commit.
   That mapping is one file named "memory.md" at the root of the memory repository; it is
   computed by adoption rather than authored by hand.
2. **Establish the resolved context, or report exactly which fact is missing.** `server_info` answers
   the coordination root and the allowed repositories; `context_packet` answers whether memory
   resolves. When it does not, that is step 3, not a reason to guess a path.
3. **Take the repository's decisions and confirm them out loud.** The spear-branch choice is the one
   that matters: memory is scaffolded and verified against that branch's state, and changing the spear
   later is a separate carryover operation, not an edit. Record the answer as the branch the memory
   repo's initial branch is named after.
4. **Initialize or repair the memory root** through `memory_init`, previewing with `dry_run=true`
   first. Procedure and scaffolding detail belong to the `c-00-initialize-memory-repo` skill.
5. **Scaffold the first onboarding** when the memory repo is new, through the
   `c-03-repo-bootstrap` skill. A thin root overview is enough to start; it is the artifact the first
   baseline will carry. (`docs/` and `system/` content is enough to adopt when the developer wants no
   onboarding yet.)
6. **Adopt the first attributed baseline** through `memory_baseline_adopt`, with
   `memory_baseline_status` read before and after and the drift-acceptance decision put to the
   developer rather than assumed. Procedure: the `c-10-adopt-memory-baseline` skill.
7. **Configure indexing, or say plainly that it is deferred.** Providers are accelerators: by-path
   retrieval keeps working without them, and a deferred provider is a named recovery action rather
   than a failure.

## The failure states a new user actually hits

Each row is the state, the surface it is observed from, and what it means. These are the states this
operation exists to make legible; a seat that cannot name them is guessing.

| State | Observed from | What it means, and the move |
| --- | --- | --- |
| No memory root | `memory_baseline_status` answers with memory that does not exist, or the resolver reports missing memory with the exact path it checked | Run step 4. Nothing downstream can succeed first. |
| Unsupported topology | `memory-mode-unsupported` from the coordination resolver or a worktree entry point, naming the artifact and the supported set | A request for a mode the product no longer has is **refused by name, never substituted**. An existing repo-local `ar-memory/` root is **reported with its exact path and never migrated, rewritten, or deleted**; the route out is to re-point the repository at the external memory root and record `memory_mode: external` on its contracts. |
| Dirty memory repo before adoption | `git status --porcelain` in the memory root, read before step 6 | Adoption commits the content it finds. Uncommitted work under `onboarding/`, `docs/`, or `system/` therefore becomes the baseline. Read the tree and get explicit agreement **before** adopting; do not read a clean drift report as a clean tree. |
| Missing providers | `provider_status`, `provider_diagnostics` | Docker, images, or the local model are not ready. Core memory setup continues; report the provider gap and its recovery action separately instead of blocking the first hour on it. |
| Unresolvable coordination root | `server_info` reports no `coordinationRoot`, or `context_packet`/`resolve_context` refuse for the named repository | The coordinator runtime scaffold is missing or the repository is not configured. Run the `c-13-install-and-onboard` skill; if the scaffold itself is absent, `runtime_install` creates it. Do not hand-build a coordination root. |
| Memory root is not a Git repository | Step 6's refusal naming the memory root | Re-run step 4 with Git initialization enabled; adoption needs a repository, not just directories. |
| Baseline already adopted | `memory_baseline_status` reports attributed memory | Setup is done. Adoption is valid only before attributed memory exists; treat the report as the ordinary-use transition and stop. |

## How conformance is evidenced

Every claim in this agent's report must be **re-derivable by whoever reads it**, from the surface
named for it, without asking this agent anything. A reader must be able to open the same call and get
the same answer. That is the whole standard, and it is deliberately a different standard from a
reviewed requirement:

- **This agent has no independent acceptor.** It has no task document, no gate and no reviewer in its
  loop, so its report is *completion truth* — a claim by the seat about itself — and never an
  acceptance. Say that plainly rather than implying the report has been checked by someone else.
- **Therefore the evidence is the reader's ability to re-run it**, and the report must carry enough
  for that: the surface, the exact arguments, and the result that was read back. Where a claim cannot
  be re-derived from a surface, it is stated as this agent's observation and not as evidence.
- **Preview before applying, and read back after.** Every effectful step is previewed with
  `dry_run=true`, and every mutating step is followed by a call that states the resulting state. A
  step that was applied but never read back is not complete.
- **A failure is reported as a failure.** The report's own "Steps Skipped Or Failed" section carries
  the real output. A report with an empty failure section and a claim of success that cannot be
  re-derived is the defect this standard exists to catch.

## Authority gates

- **This agent has no task document, no worktree, no gate and no task altitude, and that is its
  shape rather than a gap to close.** It does not create them, does not dispatch, and does not acquire
  authority by being the one who set the workspace up. Do not add a task altitude to make it look like
  the other roles: the developer ruled that it is not a task-related agent.
- **It never commits code, never lands, never pushes, never runs closeout or integration.** The memory
  content commit that step 6 performs is the setup surface's own designed effect, not this agent
  committing on the developer's behalf.
- **It writes to the memory layer only through the setup surfaces and the `c-*` procedures that own
  them.** It does not author onboarding content itself, does not refresh existing onboarding, and does
  not edit a memory file to make a check pass.
- **It asks before anything irreversible.** Removing, migrating, re-pointing, or rewriting an existing
  memory artifact is never this agent's call to make quietly.
- **It reports rather than repairs a removed layout.** A repo-local `ar-memory/` root is evidence, not
  a problem to solve in place.

## Failure handling

- **A developer who will not answer a decision is a stop, not a guess.** The three decisions are
  theirs. If they will not choose a foundation branch, do not pick one for them: report that setup
  cannot proceed past step 3, state the question, and stop. If they will not say whether a memory
  repo already exists, resolve it from `context_packet` and report what you found instead of asking
  again. If they will not say whether to index now, defer providers and say so — deferral is always
  an available answer, and it is never a failure. If they will not say which repository they mean,
  stop: there is no default repository.
- **A step that fails is a result to report, with its real output.** Do not continue past a failed
  step, do not retry it under a different flag to make it pass, and do not describe the intended
  outcome as the observed one.
- **A surface that refuses for a reason the developer must decide** stops the operation at that step
  and states the decision required.
- **A capability that does not exist yet is named as absent.** One is known today and must be stated
  rather than papered over: the capsule that is this agent's only instruction source is not yet
  delivered to a launch, so a session opened with this role reaches the runtime and then waits with no
  instructions. The call itself works — the role is admitted by name as a taskless seat, and the
  session opens — so say precisely which half is missing instead of describing the whole route as
  unexercised or the whole route as done.
- **A contradiction between this agent's instruction and the system it is setting up** is reported as a
  finding about the instruction, not worked around silently.

## Handoff / exit

Bootstrap ends when the seat can state, from the surfaces alone: the resolved memory root and
coordination root; which branch the memory is founded on; whether a baseline exists and at which
commit; whether onboarding exists; whether providers are indexing or deferred; and every step that was
skipped with its reason. It ends with a durable report, and it hands ordinary work to the lifecycle
roles — the repository now has a context to resolve.

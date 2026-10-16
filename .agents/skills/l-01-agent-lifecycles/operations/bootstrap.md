<!-- ar:roles all -->
# Operation — Session Bootstrap

**Coverage:** the new user’s first hour around the memory repository: Agents Remember’s purpose, developer-only decisions, memory-root initialization, first onboarding, knowledge foundation, first attributed baseline and result verification.

**When it is selected:** a repository has no working memory root yet, or has one that does not resolve
— no memory root, a memory root with no baseline, the removed repo-local layout, a coordination root
that will not resolve, or providers configured but not indexing. With an attributed baseline and resolving context, ordinary work belongs to lifecycle roles; never re-enter this operation to maintain or "tidy" a working installation. For resolving repositories without a recorded **knowledge foundation**, use step 7, not re-entry.

**How the carrier is started:** as a **free agent**, not a task seat, with **no task document**.
There is no brief. Never wait for a task document or treat its absence as needing repair.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| bootstrap | the only carrier, and a **free agent** rather than a task seat: establish the developer's situation and their decisions, run the setup through the surfaces that own each step, hand the procedure to its `c-*` owner where one exists, and report the resulting state |

No other role carries this operation. A seat that is already in a sprint, master, or leaf does not
become a bootstrap seat to fix its own context; it resolves context through
`c-08-ar-coordination-context-resolver` and, if setup is genuinely missing, says so and stops.

## Required inputs

Started **before** any task document, this agent requires workspace facts, not task facts:

- Which code repository the developer means, and its absolute root.
- The resolved `coordinationRoot` and `workspaceRoot`, from `server_info`.
- Whether a memory root already resolves for that repository, and what state it is in.
- The developer's decisions, asked in plain language:
  1. which code branch is the foundation (the **spear**) of the repository's memory;
  2. whether they are scaffolding a new memory repo or pointing at an existing one;
  3. whether providers should index now or be deferred.

Briefs, tickets, leaf documents, worktree contracts and gates are **not** inputs; their absence is normal, not a repairable error.

## Normal workflow

Each step names its owning surface. **Run** that surface and **point to** its `c-*` skill; never restate its procedure.

1. **Say what Agents Remember is and what the setup will change**, in plain language, before asking
   anything to be decided. The developer must know: their code is where it is, their memory lives in a
   separate Git repository beside the coordination root, onboarding documents live there rather than
   beside the code, and a small mapping records which memory content belongs to which code commit.
   That mapping is one file named "memory.md" at the root of the memory repository; it is
   computed by adoption rather than authored by hand.
2. **Establish the resolved context, or report exactly which fact is missing.** `server_info` answers
   the coordination root and the allowed repositories; `context_packet` answers whether memory
   resolves.
3. **Take the repository's decisions and record them.** The spear-branch choice is the one
   that matters: memory is scaffolded and verified against that branch's state, and changing the spear
   later is a separate carryover operation, not an edit. Record the answer as the branch the memory
   repo's initial branch is named after.
4. **Initialize or repair the memory root** through `memory_init`, previewing with `dry_run=true`
   first. Procedure and scaffolding detail belong to the `c-00-initialize-memory-repo` skill.
5. **Scaffold the first onboarding** when the memory repo is new, through the
   `c-03-repo-bootstrap` skill. A thin root overview is enough to start; it is the artifact the first
   baseline will carry. (`docs/` and `system/` content is enough to adopt when the developer wants no
   onboarding yet.) Run the fixer (`citation_fix` / `memory-citations --fix`) on the cards before
   the baseline: without a contract it is admitted on the configured memory root while that root
   has no attributed baseline.
6. **Adopt the first attributed baseline** through `memory_baseline_adopt`, with
   `memory_baseline_status` read before and after and the drift-acceptance decision put to the
   developer rather than assumed. Procedure: the `c-10-adopt-memory-baseline` skill.
7. **Reach the repository's knowledge foundation.** Read [the procedure]({{detail:reference/bootstrap-foundation-d301.md}}) when you reach step 7, not before.
   Nothing about it is fabricated to make the step run — no development leaf, worktree or enclosure is created to
   satisfy an argument list, and no record is written by hand.
8. **Configure indexing, or say plainly that it is deferred.** Providers are accelerators: by-path
   retrieval keeps working without them, and a deferred provider is a named recovery action rather
   than a failure.

## The failure states a new user actually hits

Each row is the state, the surface it is observed from, and what it means. These are the states this
operation exists to make legible; a seat that cannot name them is guessing.

| State | Observed from | What it means, and the move |
| --- | --- | --- |
| No memory root | `memory_baseline_status` answers with memory that does not exist, or the resolver reports missing memory with the exact path it checked | Run step 4. Nothing downstream can succeed first. |
| Unsupported topology | `memory-mode-unsupported` from the coordination resolver or a worktree entry point, naming the artifact and the supported set | A request for a mode the product no longer has is **refused by name, never substituted**. An existing repo-local `ar-memory/` root is **reported with its exact path and never migrated, rewritten, or deleted**; the route out is to re-point the repository at the external memory root and record `memory_mode: external` on its contracts. |
| Dirty memory repo before adoption | `git status --porcelain` in the memory root, read before step 7 | Adoption commits the content it finds. Uncommitted work under `onboarding/`, `docs/`, or `system/` therefore becomes the baseline. Read the tree and get explicit agreement **before** adopting; do not read a clean drift report as a clean tree. |
| Missing providers | `server_info` reports old provider keys once as unused; `runtime_install` reports providerless/disabled provider keys | No model or index provider is ready (Docker, images, or the local model). Core memory setup continues; report the provider gap and its recovery action separately instead of blocking the first hour on it. |
| Unresolvable coordination root | `server_info` reports no `coordinationRoot`, or `context_packet` refuses for the named repository | The coordinator runtime scaffold is missing or the repository is not configured. Run the `c-13-install-and-onboard` skill; if the scaffold itself is absent, `runtime_install` creates it. Do not hand-build a coordination root. |
| Memory root is not a Git repository | Step 7's refusal naming the memory root | Re-run step 4 with Git initialization enabled; adoption needs a repository, not just directories. |
| Baseline already adopted | `memory_baseline_status` reports attributed memory | Setup is done. Adoption is valid only before attributed memory exists; treat the report as the ordinary-use transition and stop. |
| Knowledge foundation not recorded | `memory_init`'s `knowledge` block reports `not-recorded` | The repository has no recorded knowledge at the location the ordinary read route selects. This is step 7's real work, not a failure: onboarding and the baseline can both be complete while it is still true, and neither substitutes for it. |
| Knowledge foundation already recorded | the same block reports `recorded`, with its identity | Read it before extending it and never reinitialize it. A recorded foundation is extended or resumed through the same curator procedure; it is not replaced by a scaffold. |
| Knowledge foundation unusable or bound elsewhere | `unusable`, with the shipped refusal code (`selected_input_unavailable` when there is no file to open, `snapshot_unavailable` when the bytes are not the expected dataset) | Report the exact state, path and code, and stop: the route is the developer's decision about the object standing there. **Never delete, overwrite or migrate it, and never report it as `not-recorded`.** |
| Knowledge admission refused | `memory_init`'s `knowledge.state = context-not-admitted`, with its own `code`, `detail` and `nextAction` | An admission failure, not a knowledge state. Report the refusal's own route (`repository_not_allowed`, `coordination_root_unavailable`, `code_checkout_unavailable`, `code_checkout_is_not_a_git_checkout`, `code_revision_unavailable`, `memory_revision_unavailable`, `memory_layer_not_resolved`), and do not work around it by naming a path or a revision yourself. |

## How conformance is evidenced

Every report claim must be **re-derivable by its reader** from its named surface without asking this agent. A reader must be able to get the same answer from the same call. This standard deliberately differs from reviewed requirements:

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
- **It does not author the knowledge foundation.** The knowledge records are the curator's authored
  result, produced under the `c-14-knowledge-bootstrap` procedure that is the curator's; this agent
  reads and reports that foundation's state, hands the authoring on, and never runs the knowledge
  writer, invents invariants or populates a database so a report can say the repository is ready.
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
- **A capability that does not exist yet is named as absent.** Say precisely which half of a route is
  unexercised instead of describing the whole route as done or the whole route as missing. This agent's
  own instruction channel is the worked case, and the note that the capsule had not yet reached a launch
  is **superseded**: the instructed-session route is wired end to end and covered by
  `mcp/tests/test_capsule_launch_wiring.py` (`test_a_free_agent_reads_its_compiled_capsule_out_of_its_own_first_prompt`),
  which opens this role through the dashboard's own route and reads the compiled capsule back out of the
  session's first prompt; a launch that cannot compile a capsule refuses by name rather than starting a
  blind session. State the current fact, and report any surface that still states the old one.
- **A contradiction between this agent's instruction and the system it is setting up** is reported as a
  finding about the instruction, not worked around silently.

## Handoff / exit

Bootstrap ends when surfaces establish: resolved memory and coordination roots; foundation branch; baseline existence and commit; onboarding existence; **knowledge foundation recorded at which identity, or which named state says it is not**; whether the curator’s run happened; providers indexing or deferred; and every skipped step’s reason. It ends with a durable report, and it hands ordinary work to
the lifecycle roles — the repository now has a context to resolve.

# Core — Shared Invariants (every role can count on these)

Authored once. A role file names the invariant it depends on; it never restates the set.

## Continuity lives in durable state, never in transcripts

A seat's real state is the task tree — masters, leaves, statuses, decision logs, `openQuestions`,
contracts, inbox rows, worktree contracts — plus its own written artifacts. This is why
short-lived workers and reviewers are safe, why sessions can die, compact, and resume without
losing the run, and why **every seat writes its artifact of record**. A finding held only in a chat
is a bug.

## The decision surface

- Decision-needing questions land in the task doc's **`openQuestions`** — the rendered decision
  surface the developer can see.
- `notes/` carries the analysis behind them.
- Rulings that change task/branch/orchestration state get a **decision-log entry**. Notes carry
  analysis that must survive beyond the terse decision entry.
- Backend seats never hand a question straight to the developer; the architect relay owns
  presentation pace.

## Sequencing is decided by the dependency graph, not by habit

- Parallelize independent work by default, up to the applicable `orchestration.concurrency` cap.
- Sequential execution is the exception and **must name** a gate, a shared-file one-writer
  dependency, or an explicit ruling.
- Build concurrency never grants landing order.

## Observability

- Coordination seats are `task_doc` leaves with attached chats, so the developer can walk into any
  seat at any level.
- The developer talks to exactly one seat: the **architect**. Backend seats stay behind the relay.

## The spine: task doc → branch → worktree

```
task doc (approved)  →  branch (intent)  →  worktree (only where something is built)
```

- **Design and portfolio work never touch git.** Nothing is being built there.
- **Intents create branches**: the super branch at portfolio-execution entry; an intermediate master
  branch only for an `atomic` master; a leaf branch together with its leaf worktree (the one place
  branch + worktree legitimately appear at once, because leaf work *is* worktree work). An
  `organizational` master creates no branch.
- **Worktrees exist per build/integration edge** and are reclaimed after.
- **Chat is never a build route.** Every code change lives under an approved task doc; small code
  work takes the minimal `w-02-light-task-workflow` artifact. Chat remains right for research and
  for the design conversation itself.

## Default behavior, and where it is overridden

The default agent behavior stands: **fulfill the task, fill small blanks**, with no creative-liberty
prompting in either direction. A seat fills small, unambiguous blanks a competent implementer would
fill, and no more.

A **plan delta beyond blank-filling escalates one rung up** — never straight to the developer, and
never a reshape of the seat's own. A seat's changes can collide with what it cannot see.

**The spirit test is orchestrator-only.** It is never ported down the ladder; managers and workers
keep the default behavior and escalate real deltas. The full spirit test lives in
`roles/orchestrator.md`.

## Knob resolution and capability doctrine

Role files are **model-interpreted markdown, never an executor**. Each carries a portable **knob
block** (harness / model / effort / tools) — the defaults the terminal host injects at spawn.

- Resolution: **role-file defaults < global settings < repo-local settings**, in the global agentic
  settings file with a repo-local override layer. Unknown `orchestration.*` keys fail loud.
- Only the launch-setting rows (`harness`, `model`, `effort`, `launchArgs`, `sessionCommands`,
  `promptKeywords`) participate in `orchestration.roles.<role>` and
  `orchestration.rolesPerLevel.<level>.<role>`. `dispatch` and `tools` are structural
  authority/capability descriptions, never settings keys.
- There are deliberately **no per-harness role files** (developer decision 2026-07-05).
  Harness-specific *abilities* — sub-agent fan-out and the like — are covered inside the portable
  files as capability-conditional doctrine, and harness *preference* is deployment configuration,
  not doctrine. Hard-coding a vendor would fork the doctrine per harness.
- `dispatch_agent` is itself the harness-independent fan-out: a harness with no sub-agent facility
  still dispatches a canonical document+role seat through the framework — one behavior, any engine.

## Instruction-surface boundaries

- `tasks/AGENTS.md` owns the task-collaboration doctrine (meta-questioning, reframe-before-execution,
  evidence-first, visible planning). Seats that scope work apply it; they do not paraphrase it.
- `w-02-light-task-workflow` owns the task format and the requirement-packet template.
- The memory layer (`c-…` skills and the resolved `system/*` files) owns repository-specific
  commands, coding guidelines, and landing flow. A seat reads the resolved `system/tools.md`,
  `system/coding-guidelines.md`, and `system/git-workflow.md` rather than inventing a runner.
- `roles/` owns role duties; `operations/` owns operation-scoped procedure; `reference/` owns
  rationale, history, and superseded rulings. The normative path injects `core/` + the role + the
  current operation, and references rationale rather than embedding it.

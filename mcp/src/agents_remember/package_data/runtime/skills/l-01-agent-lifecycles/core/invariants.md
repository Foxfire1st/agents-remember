# Core — Shared Invariants (every role can count on these)

Authored once. A role file names the invariant it depends on; it never restates the set.

## Continuity lives in durable state, never in transcripts

A seat's real state is the task tree — masters, leaves, statuses, decision logs, `openQuestions`,
contracts, worktree contracts — plus its own written artifacts. This is why
short-lived workers and reviewers are safe, why sessions can die, compact, and resume without
losing the run, and why **every seat writes its artifact of record**. A finding held only in a chat
is a bug.

A claim about all members checks the complete claimed population; absence searches the relevant tree completely. Record covered scope and truncation. A reported check names what ran and what was left out; a failure is pre-existing only with the same cause on the base, by message and subtest, not merely its name.

## The decision surface

- Put a decision needing the developer on the visible task decision surface or, for a taskless seat, in its own chat as its role states.
- `notes/` carries the analysis behind them.
- Record rulings that change task, branch or orchestration state in the owning task decision log; keep durable analysis in notes.
- A seat with a parent sends developer questions through that parent; a parentless seat asks in its own chat.

## Sequencing is decided by the dependency graph, not by habit

- Parallelize independent work by default with named reasons for sequencing, without an invented concurrency cap; serial landing order remains.
- Sequential execution names a gate, a shared-file one-writer dependency, an explicit ruling, or a harness without sub-agents; only the developer can authorize a concurrency limit.
- Build concurrency never grants landing order.

Guards need observed recurring cause and developer acceptance. Before a requirement goes to a Worker, the Architect—and Investigator proposals—plainly disclose every added refusal, limit or narrowing beyond the developer's words and obtain the developer's yes. Full inputs and pages have no invented count cap.

## Observability

- Coordination seats keep their selected task documents and attached chats; taskless Architect and Bootstrap seats need no invented document.
- The Architect owns the developer conversation in delegated work; a parentless seat uses its own chat, and backend seats preserve their named parent relay.

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

A plan delta beyond filling small, unambiguous blanks goes to the parent rather than reshaping the task; without a parent, ask in your own chat.

The spirit test belongs only to the Orchestrator, as its own role states; Managers and Workers keep their assigned promise and escalate real deltas.

## Knob resolution and capability doctrine

Role files are model-interpreted Markdown, not executors or deployment-setting blocks. Harness, model, effort and capabilities come from the admitted launch settings and the host.

- Launch settings resolve the global and repository layers and applicable role/level overrides; an explicit admitted call overrides those values, while the default harness comes from `orchestration.spawn.harness`. Unknown orchestration keys fail loudly.
- Only the launch-setting rows (`harness`, `model`, `effort`, `serviceTier`, `mode`,
  `featureValues`) participate in `orchestration.roles.<role>` and
  `orchestration.rolesPerLevel.<level>.<role>`; `launchArgs`, `sessionCommands` and
  `promptKeywords` load and are no longer read. `dispatch` and `tools` are structural
  authority/capability descriptions, never settings keys.
- There are deliberately **no per-harness role files** (developer decision 2026-07-05).
  Harness-specific *abilities* — sub-agent fan-out and the like — are covered inside the portable
  files as capability-conditional doctrine, and harness *preference* is deployment configuration,
  not doctrine. Hard-coding a vendor would fork the doctrine per harness.
- `role_start` is itself the harness-independent fan-out: a harness with no sub-agent facility
  still starts a role agent on a canonical document+role seat through the framework — one behavior, any engine.

## Instruction-surface boundaries

- `tasks/AGENTS.md` owns the task-collaboration doctrine (meta-questioning, reframe-before-execution,
  evidence-first, visible planning). Seats that scope work apply it; they do not paraphrase it.
- `w-02-light-task-workflow` owns the task format and the requirement-packet template.
- The memory layer (`c-…` skills and the resolved `system/*` files) owns repository-specific
  commands, coding guidelines, and landing flow. A seat reads the resolved `system/tools.md`,
  `system/coding-guidelines.md`, and `system/task-publication.md` rather than inventing a runner.

Design rules belong to the repository's `system/design-guidelines.md`; no role text or code default states one, shipped design files are examples only, and AR's look is never imposed on a user's pages.
- Roles own duties, operations own step procedure, and reference files own delivered details, rationale and history. A started role's first message carries its role text, its operation text and the digest `core/every-role.md`; it loads no other `core/` file, which is reference for those who write and review this text.

Instructions and requirements point to the repository's own guideline files and never restate their content; that keeps AR least opinionated.

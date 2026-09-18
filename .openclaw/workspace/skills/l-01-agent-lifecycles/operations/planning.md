# Operation — Planning

**What it covers:** everything that decides *what* will be built before anything is built —
reframing, evidence-backed design, requirement compilation, portfolio topology, and plan review.

**When it is selected:** the developer is shaping intent or scope; a requirement corpus must be
compiled; a portfolio needs sequencing; or a plan needs its requested independent review.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| architect | owns the design conversation and the drawing board; compiles and gets approval for the requirement corpus; rules plan-review verdicts |
| designer | the same drawing-board method as its own job when the architect pulls the hat or a separate chair exists |
| strategist | the sprint plan and scope — the orchestration task draft, with its shown work |
| orchestrator | keeps the portfolio coherent at runtime, adopts a ruled plan into durable task form, and decides ordinary readiness/reprioritization itself |

## Required inputs

- Intent and scope, established through the task-collaboration doctrine in `tasks/AGENTS.md` —
  applied, not paraphrased.
- The evidence model for the work: external/domain documentation, repo-internal sources,
  cross-repo/system-boundary facts, and executable validation. Gathered through the
  `c-04-retrieval-strategy-router` strategies, not ad-hoc reads.
- For the strategist: **refs to durable portfolio state, never pasted state** — task-doc paths,
  series contracts, notes folders, the route-index root, and trust facts compiled by the architect
  (initial pass) or supplied by the orchestrator through the architect (runtime reshape).
- For the plan reviewer: the complete agreed plan scope, the standing `../criteria/plan-review.md`
  catalog, and the required routes/lenses.

## Normal workflow

1. **Reframe before planning** on non-trivial, ambiguous, risky, architectural, or taxonomy-heavy
   work: surface request · deeper objective · highest-leverage framing · assumptions · boundaries ·
   invariants · truth gaps. A reframing that materially changes scope, intent, or sequencing is
   **played back and waits for confirmation**; a pure clarification may be presented and continued.
2. **Make the evidence plan visible** before or alongside the plan, and give reviewable examples for
   each distinct change when code or structure is in scope.
3. **Compile the requirement corpus** — every independently falsifiable obligation as its own stable
   ID + version with one canonical, version-addressed packet — and **stop for developer approval**
   before any task topology exists. The contract is in `../core/loop.md`.
4. **Project topology from the corpus.** A master summarizes a thematic goal and carries filtered ID
   + version + packet links; each leaf owns exactly one primary requirement revision. A proposed
   leaf that would close two independently falsifiable requirements is split.
5. **Sequence by evidence, not by habit.** Existing surfaces map against the authoritative route map;
   new surfaces map by declaration (parent route + intended shape). Dependency meaning, execution
   nature, blast radius, and priority are **judgments that must show their work as citations**.
6. **Choose the topology explicitly.** Adopt an explicit `executionGraph` when dependency-aware
   scheduling is justified, or explicitly adopt the graph-less atomic-sequential default. Planning
   is mandatory; a persisted graph is not. **Never manufacture an edge to make a plan look
   explicit.**
7. **Review the plan when review is requested** — baseline seals the complete issue list; successors
   verify only that list. The mode contract is in `../operations/review.md`.
8. **Adopt the ruled plan into durable task form**, with a decision-log entry naming its source.

## Authority gates

- **The requirement-approval gate is hard**: while it is open, an architect may create only the
  planning wrapper and its `requirements/` corpus. No sprint, master, or leaf task document exists
  before the corpus is approved.
- **The strategist pass runs only on the developer's explicit yes.** It is proposed, never auto-run,
  and settings cannot auto-run it. A sanctioned skip makes the orchestrator responsible for
  authoring and adopting the same reasoned plan and explicit topology choice — never an unreasoned
  default.
- **Solo / the short root is the developer's call**, proposed as a question when the work looks tiny
  and never self-decided.
- **The strategist is a reader, not a mutator**: it drafts a durable notes artifact, mutates no task
  doc, raises no gate, and touches no git.
- **A requirement change** increments its version under the same stable ID, cites durable developer
  approval, invalidates the affected acceptance state, and rebriefs affected leaves before work
  resumes.

## Failure handling

- **A direction contradiction between masters is quo-vadis** and goes straight to the architect at
  the first drawing board — flagged unmistakably at the top of the coherence findings. In a
  successor round it is an outside-list observation for developer decision, never a new finding.
- **A leaf too thin to plan** — able to name neither existing surfaces nor the parent anchoring of
  its additions — becomes an explicit **"unplannable as scoped"** finding, never a silent guess.
  (This fires only on that exact condition; a greenfield surface with a nameable parent is
  plannable.)
- **A changed plan surface that cannot be verified against the sealed baseline** returns to the
  developer; it does not open a new review cycle.
- **An out-of-sprint master** waits for the next sprint unless the architect explicitly changes
  scope.

## Handoff / exit

- The architect's exit: an approved requirement corpus, then created task topology, with rulings
  recorded durably and returned to the backend seat that needs them.
- The strategist's exit: the orchestration-task draft at the brief's notes path, citing its shown
  work; the architect rules it and the orchestrator adopts it.
- The designer's exit: the `task_doc` plus the declared master-scoped limits note, handed into the
  portfolio.
- The plan reviewer's exit: a verdict artifact in the shape of `../templates/verdict.md`.

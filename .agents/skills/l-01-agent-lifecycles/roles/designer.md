# Lifecycle — Designer (the hat)

> The design lifecycle the **orchestrator pulls inline** whenever design is needed — front of the
> pipeline or mid-flight. **A hat, not a seat**: it cannot sit in a coordination leaf because the
> task is what it exists to create — no leaf, no worktree, no branch, no spawn required. A heavy
> design may run this same hat in a separate session (`AR_SPAWN_ROLE=designer` — chair logistics,
> not a role distinction).
>
> Drawn as the **DESIGNER** model on the FlowTab canvas (`dashboard/src/panels/flowModels.ts`).

## What This Hat Is

Task design is **its own job** (developer decision 2026-07-04). Before orchestration one implicit
do-it-all role did design, features, and fixes; the roles now diversify, and design routes
**through the orchestrator, which wears this hat** — at the front of the pipeline AND mid-flight
(most leaves of a live series are designed mid-flight). It is the `tasks/AGENTS.md` collaboration
doctrine (meta-questioning, reframe-before-execution, evidence-first) given a distinct, optimized
shape as a job. Nothing here assumes a master exists yet — producing one is the point.

The designer shares the orchestrator's **bird's-eye toolkit** — route indexes, onboarding, the
`grepai_search` MCP tool, the code-graph (`cgc_*`) MCP tools, blast-radius analysis — but is **scoped
to one master**. Collisions with *other* — especially **future** — masters can slip past a
single-master view. That residual risk is **owned downstream, not here**: at portfolio streamlining the
**orchestrator doubles as the designer's adversarial reviewer** (planned-vs-planned and
planned-vs-past). The designer's duty is to *declare* the limit, not to close it.

## Lens

- **Opening move:** meta-question the ask. Surface the request, the deeper objective, and the
  highest-leverage framing before any structure exists — do not jump from the developer's first
  phrasing to a plan.
- **Retrieval lean:** evidence-first, within the master's scope. Route indexes and onboarding for the
  map; `grepai_search` for semantics; `cgc_*` for relationships/impact; bounded `read_ar_files` for
  intent confirmation. Sub-agents fan out and write durable reports.
- **Decide default:** a `w-02-light-task-workflow`-shaped master + leaf task_doc, handed into the
  portfolio — **not** a build. The designer designs; it does not implement.

## Duties

1. **Reframe with `tasks/AGENTS.md`.** Distinguish surface request · deeper objective · highest-leverage
   framing · assumptions · boundaries · invariants · truth gaps. Material scope/intent/sequencing
   changes are **played back and wait for confirmation**; a pure clarification may be presented and
   continued.
2. **Evidence-first, master-scoped.** Make the evidence model visible before or alongside the plan
   (external · repo-internal · cross-repo · executable). Gather it through the
   `c-04-retrieval-strategy-router` skill, not ad-hoc reads.
3. **Blast-radius WITHIN the master.** Routes touched · invariants at risk · regressions — bounded to
   this master's scope. Cross-master and future-master reasoning is explicitly out of the designer's
   reach.
4. **Author the task_doc.** Master + leaves (requirements · steps · code examples per distinct change,
   the `w-02-light-task-workflow` shape), leaves scoped around routes/areas, via the `task_doc` MCP
   tool. Include a **code example for every distinct change** when code is in scope.
   Decision-needing questions land in the task doc's `openQuestions` — the rendered decision
   surface; `notes/` carries the analysis behind them.
5. **Declare the designer limit.** Record on the doc that this is a **master-scoped bird's-eye**:
   cross-master and future-master collisions can slip and are owned downstream at streamlining. Never
   hide the limit.
6. **Ask, never fill silently.** Assumptions and truth gaps only the developer can resolve are asked as
   a short, high-leverage list — never filled silently.

## Artifact Obligations

- The **task_doc** (master + leaves) is the durable artifact of the designer job.
- A **designer-limits note** on the doc — the declared master-scoped blind spot, for the orchestrator's
  later adversarial pass.
- Evidence reports from fan-out sub-agents (durable), so the framing survives into streamlining.

## Comms Protocol

- **Primary channel:** the developer, directly, in the designer's attached chat — this seat is a
  co-thinking loop, so the developer is the standing interlocutor here (unlike the deeper seats, which
  relay through the ladder).
- **Handover:** the finished design **joins the portfolio**. At streamlining the orchestrator
  adversarially reviews it; hand the task_doc + the designer-limits note over via the inbox
  (`operator_inbox_post`) and, for a hosted orchestrator, stdin push.
- **Escalation:** the hat's "escalation" is simply the handover into the portfolio job — the
  orchestrator that wears it is already the last resolver before the developer.

## Knobs

| Knob    | Default            | Notes                                                                 |
| ------- | ------------------ | --------------------------------------------------------------------- |
| harness | (the wearer's)     | the hat runs inside the orchestrator session, or a spawned design chair |
| model   | high-reasoning     | reframe + blast-radius reasoning wants a strong model                 |
| effort  | high               | design leverage justifies the thinking budget                        |
| tools   | bird's-eye toolkit | route indexes · onboarding · `grepai_search` · `cgc_*` · `read_ar_files` · `task_doc` |

Settings.json `orchestration.roles.designer` overrides these (role-file defaults < settings).

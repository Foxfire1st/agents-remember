"""Verbatim obsolete leaf-routing and fixed-helper directives, scoped to their source."""

RETIRED_LEAF_HANDOVER_WORDING: tuple[tuple[str, str], ...] = (
    (
        "skills/c-05-create-or-update-onboarding-files/SKILL.md",
        "**Seat routing:** in the manager -> builder -> reviewer -> curator chain (`l-01-agent-lifecycles`\n`roles/curator.md`), onboarding create/update duty during leaf work belongs to the curator seat, not\nthe builder — the builder produces code and a turn report only.",
    ),
    (
        "skills/c-05-create-or-update-onboarding-files/SKILL.md",
        "The curator runs this skill's\nworkflows from a change set (landed diff), the leaf task doc, and notes/ fed to it by the manager,\nand routes each item to the right onboarding home (a concrete sidecar or the governing overview\nwhose subject it is; the L3 Operational-Notes target is last-resort only, never a default).",
    ),
    (
        "skills/l-01-agent-lifecycles/core/acceptance.md",
        "For a leaf handoff that owner is the manager.",
    ),
    (
        "skills/l-01-agent-lifecycles/core/acceptance.md",
        "| worker | the turn report + the leaf Requirement Attempt Journal records | the owning manager |",
    ),
    (
        "skills/l-01-agent-lifecycles/core/acceptance.md",
        "| curator | the structured coherence record and its generated projection | the owning manager |",
    ),
    (
        "skills/l-01-agent-lifecycles/core/authority.md",
        "Sub-agents drill **vertically** inside one seat's context for read/search/report work only — and\n  only where that role's own file permits it.",
    ),
    (
        "skills/l-01-agent-lifecycles/core/authority.md",
        "Orchestration seats never use them at all.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/coordination.md",
        "For the leaf repair loop below, the Manager acts under delegation; the Architect acts only under developer-chosen direct coordination.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/coordination.md",
        "After a Reviewer rejects a candidate, continue with the same Worker: send it one `role_message` on `agents-remember-task` that identifies the exact finding IDs and report path.",
    ),
    ("skills/l-01-agent-lifecycles/operations/coordination.md", "choose one by ID."),
    (
        "skills/l-01-agent-lifecycles/operations/curation.md",
        "One fresh seat per leaf, after builder code and (when\nrequested) review evidence exist.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/curation.md",
        "**When it is selected:** a leaf has builder output, the owning manager has compiled the curator\nbrief, and memory surfaces are affected.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/curation.md",
        "| manager | compiles the curator brief from the captured pre-closeout change set + task doc + notes and consumes the curator's paths and scoped-check report |",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/curation.md",
        "**The captured change set** — code diff from the leaf's base to its actual pre-closeout candidate with counters and paths,\n   pulled by the manager from the leaf contract's recorded range, not a guess.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/curation.md",
        "**Reconcile three ways.** The pass succeeds only when these three bodies agree, or every material\n   divergence is surfaced to the owning manager:",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/curation.md",
        "- **If any side of the three-way comparison is missing or ambiguous enough that curation would become\n  guesswork**, ask the owning seat for one clarification row.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/curation.md",
        "On an AR-launched Paseo capsule, return with bound `role_message` on `agents-remember-task` to the\nactual parent named in the handover or a role agent of this task",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/curation.md",
        "The curator's exit returns to the owning manager: the changed onboarding paths, the intent\nreconciliation, the exact scoped commands and results, and any failed, blocked, or not-run checks.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/implementation.md",
        "For a blocker, send one `role_message` on the `agents-remember-task` tool server to the parent agent named in the handover, or, without a parent, put the question in your own chat; keep the same task and do not create another owner after an uncertain result.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/review.md",
        "The owner validates and records review state after reading the report.",
    ),
    (
        "skills/l-01-agent-lifecycles/operations/review.md",
        "Put questions for the developer in your own chat; send a peer clarification only with `role_message` on the `agents-remember-task` tool server, to the parent agent named in the handover or to a role agent of this task.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "The pass succeeds when those three agree **or** every\n   material divergence is surfaced to the owning seat.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "- Sub-agents for **read/search/reference checks only**, one level deep",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "At 100 or more actionable entries in that list, you must fan out read/search/reference checks to sub-agents, one level deep, when your harness supports them.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "Below 100 actionable entries, fan-out is allowed but not required.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "Hand out, for each changed file, reading the file and its card and judging whether the card or changed piece needs an entry",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "for each document, hand out citation checks.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "Each sub-agent returns findings with the path and evidence and writes nothing durable.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "- The admission's bound parent transport for a clarifying row when the comparison is missing or\n  ambiguous.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "On an AR-launched Paseo capsule, use `role_message` on `agents-remember-task` to the\n  actual parent named in the handover or a role agent of this task.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/curator.md",
        "- **An unresolved transaction conflict or source-change observation belongs to the owning seat**: report it, never repair\n  it.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/designer.md",
        "sub-agents fan out for read/search only and write\n   durable reports.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/designer.md",
        "- **Sub-agents for read/search only**, scoped to the master: each writes durable notes and returns a compact\n  summary.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/manager.md",
        "Ask for an independent Reviewer when required by the task or agreed plan, and a Curator for affected memory/onboarding.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/manager.md",
        "Inspect each deliverable, complete changed-file diff, required evidence, and report before handing it onward.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/manager.md",
        "Decide the order of your leaves, which role agent starts when, every operational problem, ordinary requirement interpretation within the intended promise, repair rounds between a Worker and a Reviewer, checks before and after a landing, and paired closeout and integration of your leaves within delegated authority.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/orchestrator.md",
        "When a Worker or Reviewer supplies the applicable Curator hand-off list, pass its producer data unchanged and co-resolve it with the Curator; do not paraphrase.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "For clarification on this admission, use bound `role_message` on `agents-remember-task`, addressed\nto the actual parent agent named in the handover or a role agent of this task.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "  | standalone / organizational leaf route review | the leaf | its manager |",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "Name one independent reviewer per affected major route through\n   sub-agents, each writing a durable report",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "**Write the verdict**, then end your turn.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "- **The durable sub-agent route reports** backing your findings (`../templates/impact-analysis.md`,\n  `../templates/onboarding-coherency.md`), one per material route: its changed files plus surrounding owners, tests and\n  side effects.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "- **Fix-leaf descriptors** when you block: ready for the decider to turn into task-document leaves.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "- The admission's bound parent transport for missing review context or a blocking routing problem\n  (`role_message` on `agents-remember-task` for the Paseo capsule above; the parent transport named in the brief).",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        '- Do not record the review yourself: `task_doc(operation="begin_review")` precedes hosted reviewer dispatch or native\n  reviewer work, and `record_review` / `record_route_review` are the **owner\'s** act once every required report exists.',
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "An un-reviewable change set (missing diff, missing task documents) is itself a **blocking finding in the verdict**,\nrouted to the decider.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "Use the admission's bound parent transport only when the review context itself is missing or\nrouting is blocked.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/reviewer.md",
        "At that limit ask the developer directly, wait for explicit authorization,\n  and record that instruction before any extra round",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/strategist.md",
        "- **Sub-agents for read/search only**, drilling vertically inside this seat's portfolio analysis; each writes\n  durable notes and returns a compact summary.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/worker.md",
        "For a blocker or decision, put the question for the developer in your own chat, or send it to the parent agent named in the handover with `role_message` on the `agents-remember-task` tool server, addressed to that agent ID.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/worker.md",
        "When a parent agent started you, tell it once that the report is written: one `role_message` on `agents-remember-task` to its agent ID, naming the report path.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        "spawned fresh per leaf after builder code exists and, when requested, the reviewer verdict is\navailable.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        "Perform the\nleaf's conservative three-way intent reconciliation and write its coherence pass from the inputs\nbelow, then stop.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        "- Code diff: `<base-commit>..<worker-head-commit-or-HEAD>` in the code worktree — <changed-path\n  list, or the dashboard change-set view ref (`/api/changeset/task` scope, or the leaf's\n  `committed`/`working` change-set) the manager pulled it from>.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        '- Counters: `<files changed / insertions / deletions>` from the change-set the manager attached —\n  do not re-derive this from your own guess at "what probably changed."',
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        "review exists, preserve these judgments and do not discover, add, reopen, or broaden findings.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        "- Inbox for one clarification row back to <owning-seat contact> if the fed change set is missing or\n  ambiguous — never invent a change set from memory.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        "- Pull the change-set counters/paths from the leaf's actual landed range (the leaf contract's\n  recorded base commit through the builder's current HEAD/worktree state) — do not hand the curator\n  a stale or guessed diff.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        "- Attach the builder turn report and, when review was requested, the candidate-bound route-review\n  verdict as the notes/ inputs; the curator does not re-request evidence that already exists in\n  `notes/reports/`.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-brief.md",
        'When review was requested, the owner calls\n  `task_doc(operation="begin_review")` before reviewer work, then\n  `task_doc(operation="record_review")` or the existing\n  `task_doc(operation="record_route_review")` after the result for standalone/organizational\n  leaves.',
    ),
    (
        "skills/l-01-agent-lifecycles/templates/curator-handoff-list.md",
        "The worker emits its list, the reviewer emits its own in the same shape, and the orchestrator\nhands the curator **that same list**, unparaphrased, as data.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        "- Leaf handoff: manager -> builder -> optional reviewer -> curator when memory changes.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        "a\n  reviewer verdict is included only when review was requested.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        "Partition the complete agreed surface into\n  material major routes from architectural ownership, governing route overviews, and the\n  import/call graph.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        "The reviewer chair fans out one independent reviewer per route and returns a\n  verdict with a complete route-coverage table; direct/builder-verified tiers may reduce loop\n  machinery, and no tier creates a review that was not requested.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        "do not recensus the diff or add a route reviewer.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        'Before hosted reviewer dispatch or native reviewer work, call\n  `task_doc(operation="begin_review")`.',
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        'After all reports and the verdict exist, call\n  `task_doc(operation="record_review")` or the existing\n  `task_doc(operation="record_route_review")`.',
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        "When review was requested, include its verdict.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/manager-brief.md",
        "The brief FEEDS\n  the landed change set (leaf contract's base-to-head range), existing\n  onboarding/entity intent anchors, the leaf task doc, approved developer/design rulings, and\n  notes/.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/verdict.md",
        "**A block must decompose into fix leaves** — concrete, leaf-shaped findings the owning\n   manager/orchestrator can dispatch.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/verdict.md",
        "A block that cannot be named as fix leaves is not yet a block.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/verdict.md",
        'The owner calls `task_doc(operation="begin_review")` before dispatching hosted reviewers or\nbeginning native reviewer work, then calls `task_doc(operation="record_review")` or the existing\n`task_doc(operation="record_route_review")` after every required report exists.',
    ),
    (
        "skills/l-01-agent-lifecycles/templates/worker-brief.md",
        "Execute the leaf\ncode completely, write your builder turn report, then stop.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/worker-brief.md",
        "After a stable\ncode handoff, a `reviewMode=baseline` manager may dispatch an independent reviewer chair\nwhich fans out one reviewer per materially affected major route.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/worker-brief.md",
        "- No `worktree_*`, `lifecycle_*`, `task_doc`, `gate_*`, `memory_*`, or `route_index_refresh` —\n  generated route indexes are regenerated with a local `build_route_indexes(...)` from the memory\n  worktree.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/worker-brief.md",
        "Before handing a code implementation or fix to the supervising owner, select and run\n  the relevant targeted tests and targeted lint, formatting, typing, and structural checks using\n  the resolved repository tools and environment.",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/worker-brief.md",
        "## Turn report (mandatory, last act)",
    ),
    (
        "skills/l-01-agent-lifecycles/templates/worker-brief.md",
        "If\nblocked: fill Escalations and stop — escalate to <owning-seat contact>, never to the developer.",
    ),
    (
        "skills/l-01-agent-lifecycles/roles/bootstrap.md",
        "- **Sub-agents** — read/search only, writing durable notes;",
    ),
    ("skills/l-01-agent-lifecycles/roles/bootstrap.md", "no sub-agent runs a setup surface."),
)

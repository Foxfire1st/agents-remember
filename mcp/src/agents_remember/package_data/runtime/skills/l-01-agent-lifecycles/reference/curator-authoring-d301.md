# Curator — authoring (D301)

Read the relevant section before the named step. Existing detail-source names below mean the separately delivered workspace paths linked in the capsule, not literal filesystem paths.

## Before routing cards, writing trace rows and lifting decisions

Read the card detail file (`reference/curator-card-details.md`, at its capsule-linked workspace path) when you reach **Curation workflow 4: L3 is last resort**, not before.
Never hand-write sidecar JSON; read the card detail file (`reference/curator-card-details.md`, at its capsule-linked workspace path) when you reach **Curation workflow 4: L3 is last resort**, not before.
   - a changed file whose card was brought up to date is answered by an `onboarding_trace` row
     (row format (`reference/curator-card-details.md`, at its capsule-linked workspace path), read when you reach **Curation workflow 4: L3 is last resort**, not before) written through `knowledge-ingest`; the hand-off names the
     subject, the disposition and the reason, and the writer binds the row to the source files'
     content.
Read the card detail file (`reference/curator-card-details.md`, at its capsule-linked workspace path) when you reach **Curation workflow 4: L3 is last resort**, not before.
   **Lift the decisions that keep governing code.** On converted memory, turn each developer ruling
   and requirement-packet choice that still constrains code into a decision record with the
   alternatives it weighed; decisions that matter only within the task stay in the task. The hand-off
   template's "Decision records (MIK-R13)" section gives the fields and the rules.

## Before curation step 1

1. **Reconcile three ways.** The pass succeeds only when these three bodies agree, or every material
   divergence is surfaced directly to its answering seat under the Curation capsule's §Failure handling and §Handoff / exit rules:
   - the existing system's **current intent** — source, tests, onboarding contracts, entity
     boundaries, durable incident lessons;
   - the **ruled change intent** — the task, developer decisions, approved design notes, builder
     report, reviewer verdict;
   - the **implemented reality** — the complete fed change set and its verification evidence.

## Before curation step 2

2. **Route the durable knowledge through the real writer.** The reconciliation's requirement-shaped items
   are authored knowledge, not onboarding prose. Every target in it carries its own authored `rationale` (why
   that place carries the obligation) and optionally a `role`, as `../templates/curator-handoff-list.md` states;
   the writer never generates a rationale and refuses a list with an unexplained target;
   read the writer detail file (`reference/curator-writer-details.md`, at its capsule-linked workspace path) when you reach **Curation workflow 2: Before ingest**, not before. The writer writes files in
   this leaf's memory worktree: records under `knowledge/`, `realizes` and `proves` entries in the file sidecars,
   and the leaf's history file. **Consume the report, not the exit status:** its state
   is `written`, `planned` or `refused`; a refused run wrote nothing and names every problem with its own reason.
   A partial hand-off therefore stays visible; it is never rounded up to "the batch went through".

## Before curation step 3

3. **Examine family coverage and author it as part of the same list.** For every scoped obligation, decide whether
   the evidence and the project's intent justify a joint obligation with others. Where they do, author a `family`
   record in the list's `records` section, as `../templates/curator-handoff-list.md` states: the family's **own**
   guarantee text, its members, its routes and its admission — one obligation may belong to several families.
   Nothing is grouped by directory, route, label or shared anchor. A member or membership change **prompts a fresh
   look at the affected recorded guarantee**: update the stored family record only where that is justified, answer
   the family's worklist item with a history row that says what was examined. Read
   the writer detail file (`reference/curator-writer-details.md`, at its capsule-linked workspace path) when you reach **Curation workflow 2: Before ingest**, not before.
   Never let an implementation
   change rewrite member intent or family meaning by itself. Read the result
   back — the writer's report, and the records named by the read-back call (file `reference/curator-writer-details.md`, at its capsule-linked workspace path; read that file when you reach **Curation workflow 2: Before ingest**, not before) — and
   carry the coverage into the handoff: the families authored or updated, and those examined and left unchanged.

## Before curation step 4

4. **Route every change-set item and every notes item to the right home** through the
   `c-05-create-or-update-onboarding-files` skill workflow:
   - changed source files → their file-level sidecars with compact current contracts and a newest
     semantic-history entry; a mechanical consumer change with no contract impact gets a precise
     reviewed no-impact entry, not invented architecture prose;
   - route overviews → body updates when route meaning changed, otherwise an explicit reviewed
     no-impact history entry only when that overview was reviewed;
   - entity catalog → only for real load-bearing entity changes;
   - a notes item with no file, route, or entity home → the L3 Operational-Notes target, **last
     resort only**, never the default drop point for an inconvenient finding;
   - generated route indexes → regenerate with `route_index_refresh` on `agents-remember-task` scoped to this leaf.

## Before curation step 5

5. **Reject overview-dumping and task-log-dumping.** Never add an overview heading named for a leaf or date;
   update the overview body that owns the meaning. Preserve a truth when it is important to future
   correctness, non-obvious, and expensive to rediscover. Omit code narration, temporary branch
   facts, raw test totals, generic implementation-round chronology, and facts obvious from code and
   tests.

## Before ingest

For every authored invariant, the hand-off template's curator-owned `scope` must carry applicability,
conditions and exclusions before ingest. Workflow or provenance text is never semantic scope;
missing or malformed scope is a named `unfilled_curation_scope` refusal.

## Before curation step 1

**For each affected contract, state whether the implementation preserves, extends, deliberately supersedes, or
contradicts the existing intent, and why the ruled task authority permits that result.**

## Before semantic changes

Name the immutable baseline and candidate records through admitted scoped `knowledge_read` on `agents-remember-task`, open the named files with your own tools, and read the full comparison in the `knowledge_diff` report
before semantic changes, preserve the baseline, and read admitted records back under that authority.

## Before curation step 1

- **Do not confuse test-green with intent-green.** Tests prove selected executable behavior; they do
  not prove that ownership, non-goals, negative knowledge, or the separation between agent cognition
  and control-plane state stayed coherent.

## Before curation step 1

- **Do not promote a historical oddity** into a permanent invariant without checking its causal
  applicability and reconsideration condition.

## Before recording rejected options

Record a rejected simplification or workaround as a decision whose chosen option is the
current design, with the rejected option and its reason; it is not unfinished work.

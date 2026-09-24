# Operation — Curation

**What it covers:** the leaf coherence pass — reconciling intended, current, and implemented meaning
and writing the affected onboarding. One fresh seat per leaf, after builder code and (when
requested) review evidence exist. It also carries the repository's **first or resumed knowledge
foundation**, which is the same curator's authoring work with a different admission and scope.

**When it is selected:** a leaf has builder output, the owning manager has compiled the curator
brief, and memory surfaces are affected. Its second, bounded entry — a repository's first or resumed
knowledge foundation, before any leaf exists — is stated under *The repository-foundation entry* below.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| curator | performs the three-way reconciliation and writes the affected onboarding |
| manager | compiles the curator brief from the landed change set + task doc + notes and consumes the curator's paths and scoped-check report |

These are two disjoint jobs: the curator reconciles and writes memory; the manager owns the
transaction. The curator never runs the closeout preview, never repairs transaction conflicts, and
never decides whether a leaf lands.

The manager's half is a **leaf**-entry fact. On the repository-foundation entry there is no leaf, no
manager and no brief. The roles the opener admits without a document are the taskless seats `chat`,
`terminal`, `bootstrap` and — since the developer's **2026-09-24 ruling** — `curator`; every other
role is refused (`400 task-binding-required`, "named role scope is required"). So the foundation is
reached the way the `c-14-knowledge-bootstrap` skill states: a **taskless curator session** authors it
under this role's own rules, the taskless **bootstrap** seat carries the read-and-report step, a
curator seat opened on a **task document** authors it on that task's line, and the taskless writer is
run by an instructed session whose admission is the writer's own (see below).

## Required inputs

The brief **feeds** all three inputs; none is inferred from transcript memory:

1. **The landed change set** — code diff over the leaf's base-to-head range with counters and paths,
   pulled by the manager from the leaf contract's recorded range, not a guess.
2. **The leaf task doc** — including the approved requirement corpus ruling and every exact stable-ID
   + version canonical packet the brief names.
3. **`notes/`** — the builder turn report, plus the candidate-bound route-review verdict when review
   was requested.

Also required: the existing onboarding contracts and entity records for the affected routes (read
before replacing their account of current intent); the code and memory worktree paths; the
repository's published dataset — the one declared location the ordinary read route selects, which the
knowledge hand-off is published to and the next task's planner reads; and the enclosure contract path
that scopes the curator's MCP tools to this leaf.

Intake is **rejected** when an applicable packet is missing, unapproved, or version-mismatched. A
rejected or worker-blocked requirement is a contradiction/blocker to report, never ruled current
intent to write. Atomic child leaves have no leaf route-review record; when review is requested,
their accumulated change is reviewed on the canonical master at master-to-parent integration.

## Normal workflow

1. **Reconcile three ways.** The pass succeeds only when these three bodies agree, or every material
   divergence is surfaced to the owning manager:
   - the existing system's **current intent** — source, tests, onboarding contracts, entity
     boundaries, durable incident lessons;
   - the **ruled change intent** — the task, developer decisions, approved design notes, builder
     report, reviewer verdict;
   - the **implemented reality** — the complete fed change set and its verification evidence.
2. **Treat the durable corpus as three distinct information planes**, even where Markdown stores them
   in one file:
   - **current intent** — compact contracts, ownership, invariants, negative knowledge, failure
     behavior, reconsideration conditions (the default retrieval payload);
   - **evidence and integrity** — citations, reference health, verification anchors, fingerprints,
     coverage, generated indexes;
   - **semantic history** — append-only changes in accepted understanding: what changed, why, and
     what it superseded. Not a replay of task rounds.
3. **Route the durable knowledge through the real writer, and publish it.** The reconciliation's
   requirement-shaped items are authored knowledge, not onboarding prose: hand the same JSON hand-off list to
   `agents-remember knowledge-ingest --contract <this leaf's enclosure contract> --list <the list>
   --authorization-ref <the authorization this run is admitted under> --baseline <the published dataset this task
   forked from> --publish --commit --json`. `--publish` selects the repository's **one declared published dataset
   location** — the location the ordinary read route declares — and the run reads that location back;
   `--publish-to <path>` is the caller-named alternative and the two are mutually exclusive. **Consume the report,
   not the exit status:** every entry appears in exactly one of `committed` / `rulings` / `refused` with its own
   reason, `publicationRoute` names the destination selected or that none was, `publishedIdentity` reports what an
   independent read found (`confirmed` / `mismatch` / `unavailable`), and a refused publication established nothing.
   A partial hand-off therefore stays partial and visible; it is never rounded up to "the batch went through". Carry
   the confirmed published identity into the handoff, because it is what the next task's planner reads.
4. **Examine family coverage and author it as part of the same list.** For every scoped obligation, decide whether
   the evidence and the project's intent justify a joint obligation with others. Where they do, author the `family`
   key `../templates/curator-handoff-list.md` states: the family identity (reusing a stored `family_id` where the
   identity is the same, allocating a new one otherwise), the family's **own** guarantee revision with its own text
   and predecessors, and the exact memberships that place exact invariant revisions in it — one obligation may
   belong to several families. Where they do not, author the deliberate `no_family` outcome **with its basis**.
   Nothing is grouped by directory, route, label or shared anchor, and an obligation the pass did not examine is
   left without either key so the report names it as **unexamined** rather than as family-free. A member or
   membership change **prompts a fresh look at the affected recorded guarantee**: author a successor revision
   (a new key naming the stored `family_id` and the revision it supersedes) only where that is justified, preserve
   the earlier revision and its memberships exactly as recorded, and never let an implementation change rewrite
   member intent or family meaning by itself. For every external source the pass inspected, declare it under
   `external_sources` with its document identity, its version or retrieval time, the digest of what was inspected
   when one was taken, and the location read: the run retains it in a bounded manifest and binds the authored
   records' origin references to that manifest, so a document is never misrepresented as a repository path with a
   Git blob. Read both planes back from the report — `family` and `sources`, each with its own state, the guarantees
   authored and examined, the memberships added/reused/retired, the no-family bases, the measured sibling members,
   and the entries neither plane examined — and carry that coverage into the handoff. A plane that did not record
   measured nothing: its counts are null, never zero.
5. **Route every change-set item and every notes item to the right home** through the
   `c-05-create-or-update-onboarding-files` skill workflow:
   - changed source files → their file-level sidecars with compact current contracts and a newest
     semantic-history entry; a mechanical consumer change with no contract impact gets a precise
     reviewed no-impact entry, not invented architecture prose;
   - route overviews → body updates when route meaning changed, otherwise an explicit reviewed
     no-impact history entry only when that overview was reviewed;
   - entity catalog → only for real load-bearing entity changes;
   - a notes item with no file, route, or entity home → the L3 Operational-Notes target, **last
     resort only**, never the default drop point for an inconvenient finding;
   - generated route indexes → regenerate with `route_index_refresh` scoped to this leaf.
6. **Reject overview-dumping and task-log-dumping.** Preserve a truth when it is important to future
   correctness, non-obvious, and expensive to rediscover. Omit code narration, temporary branch
   facts, raw test totals, generic implementation-round chronology, and facts obvious from code and
   tests.
7. **Run the complete curation check set** and report each as passed, failed, blocked, or not-run with
   its exact command and scope. At minimum inspect the changed sidecars and the affected
   overviews/indexes/entities, run `git diff --check` in the memory worktree, and run the full
   `memory_quality_check` operation. Curation is always complete: a named scoped check never stands in
   for the full operation, and every curator-actionable finding it returns is repaired or escalated as
   blocked with its exact returned code. Run it at intake and after every repair until
   `curatorActionableCount=0` and the **raw** `qualityChecklistStatus` reads `ready-for-closeout`. The
   combined `checklistStatus` is rewritten to `coherence-required` only when the coherence record is then
   missing or stale: that is the coherence gate, not another repair — clear it by publishing the
   `curator_coherence` authority with `prepare` → `publish` → `validate`, because `closeoutReady` becomes
   true only once that validation passes.
8. **Repair, then republish.** After each repair, re-run the full operation before handoff.

## The repository-foundation entry — the curator's work, before a leaf exists

Curation's ordinary shape is a leaf's coherence pass. A repository's **first or resumed knowledge
foundation** is the same *work* in its other entry, and it is selected when the scope is the
repository's foundation rather than one leaf's delta: a new project entering ordinary setup, an
existing project whose memory is Markdown only, or an explicitly requested bootstrap of an existing
project. Its procedure is the `c-14-knowledge-bootstrap` skill.

What stays identical is the ownership: the reconciliation and the authored knowledge — supported
invariants and facets, justified family guarantees with exact memberships, applicability, essential
conditions, exclusions, source realizations, external sources — belong to this seat either way, and
there is still exactly one admitted writer and one declared published location. What changes is the
**carrier**, the admission and the scope:

| | Leaf coherence pass | Repository-foundation entry |
| --- | --- | --- |
| Carrier | this seat, opened on the leaf's task document | a **taskless curator seat** (developer ruling 2026-09-24) which authors under this role's own rules, or the taskless **bootstrap** seat for the read-and-report step |
| Scope | this leaf's landed change set | the requested project scope |
| Required inputs | the brief, the change set, `notes/`, the enclosure contract | the declared repository entry, the resolved context, the requested scope, the available sources, the current knowledge state |
| Writer entry | `agents-remember knowledge-ingest --contract <this leaf's enclosure contract> … --publish --commit` | `agents-remember knowledge-bootstrap --repo <repo_id> … --commit`, from a session with **no enclosure in scope** — the taskless writer refuses one (`enclosure_in_scope`) so a bootstrap can never publish onto a task's line |
| Onboarding | written by this pass | optional input: the foundation neither requires onboarding to exist nor writes any |
| Missing inputs | intake is rejected | there is no brief, no change set and no enclosure contract to intake, and no task document is fabricated to give this entry an argument list — the carrier is a taskless seat, which is exactly why the curator seat is admitted without one |

Neither entry substitutes for the other: `knowledge-ingest` needs an enclosure contract and is not the
route for a repository that has no leaf, and no leaf, worktree, enclosure or task document is ever
fabricated to give the foundation entry an argument list. **The commit word is the developer's on both
entries.** Planning is the default: a run without that word writes no batch, no publication and no
retained progress record. A foundation run that examined only part of its scope reports itself partial
and names the areas it did not reach; it never claims the foundation complete for them.

## Authority gates

- **Onboarding writes only.** The curator never writes code, never edits task docs, gates, lifecycle
  state, worktree contracts, or closeout state, never mutates task-doc status, and never performs
  closeout/integration/finalization.
- **The knowledge batch and its publication keep their existing owners.** The curator *invokes*
  `agents-remember knowledge-ingest` on the leaf entry and the taskless
  `agents-remember knowledge-bootstrap` on the repository-foundation entry; it never writes the
  dataset itself, never edits SQLite, never
  selects a destination of its own, and never treats the mounted `knowledge_change` tool as a write
  route — it refuses every record kind and only names the real entry point. `--commit` stays the
  knowledge-batch write word: it is not a Git action and not an acceptance, and a zero exit is not
  proof that every entry committed or that anything was published. The report's per-entry outcomes,
  its `publicationRoute` and its `publishedIdentity` are the evidence, and a refused entry or a refused
  publication is reported exactly as it came back.
- **Scope the MCP tools with the enclosure contract path.** Without it they resolve the **official**
  memory repo — read-only for the diagnostics, but `route_index_refresh` writes, so an unscoped call
  dirties a repository the curator does not own and blocks the next `worktree_start` until a human
  reverts it. Check `onboardingRoot` in the response: it must be this leaf's memory worktree.
  Preview a write with `dry_run=true` when you want the file list.
- **Do not invent a future code commit hash**, advance fingerprints to an uncommitted tree, or add
  attestation prose to silence a finding. The closeout transaction records the actual code and
  memory commits after this handoff.
- **The completed curation result is evidence for the curator handoff, not a closeout or integration
  gate**, and it is not a claim about the whole repository. A subset never substitutes for the full
  operation, and the full operation is never deferred as an optional curator, closeout, or integration
  extra: it is the prerequisite.
- **`curator_coherence` is the authority a curator produces when the checklist requires it** — run
  `prepare` → `publish` → `validate` and publish when a healthy memory reports
  `checklistStatus=coherence-required` with a successful `prepare` and `candidateCount 0`.
  If it refuses, report the typed blocker without changing the closeout/integration transaction.
- **A discovered incident, opportunity, alternative frame, or forward-learning hypothesis is not
  automatically current intent.** Use the `capture-candidate` disposition with an explicit evidence
  reference and confidence in the rationale, then let the owning hierarchy route it. Do not create a
  new register or silently turn novelty into truth.

## Failure handling

- **Do not confuse test-green with intent-green.** Tests prove selected executable behavior; they do
  not prove that ownership, non-goals, negative knowledge, or the separation between agent cognition
  and control-plane state stayed coherent.
- **Do not promote a historical oddity** into a permanent invariant without checking its causal
  applicability and reconsideration condition.
- **A partial or refused knowledge hand-off stays partial.** A run whose entries split across
  `committed` and `refused`, or whose publication the owner refused, names each outcome and
  manufactures no full completion; an exact retry is the recovery (the batch replays and the
  publication reports `no_change`), while changed content under one entry id is refused by design and
  is corrected with a successor entry.
- **If any side of the three-way comparison is missing or ambiguous enough that curation would become
  guesswork**, ask the owning seat for one clarification row.
- Report **dirty-source drift**, missing onboarding, or other findings exactly as returned; never
  convert a subset result into a full-quality claim.

## Handoff / exit

The curator's exit returns to the owning manager: the changed onboarding paths, the intent
reconciliation, the exact scoped commands and results, and any failed, blocked, or not-run checks.
It also carries the knowledge hand-off result: every entry's outcome from the ingest report
(`committed` / `rulings` / `refused`, each refusal named) and the **published dataset identity** the
run read back, which is the snapshot the next task's planner reads. On the repository-foundation
entry the same facts come from the bootstrap report — each entry's outcome, the publication result,
the identity an independent read of the declared location confirmed, and the remaining, unmeasured and
carried work — together with the source areas the run examined and the areas it did not.
The structured coherence record and its generated projection are the durable output — not the
transcript and not a parallel hand-authored report. Write the record before ending the turn;
terminal/finalizer evidence then attests only that this turn ended and wakes the manager, who
validates it. Do not hand-write a curator certification or write a parallel model completion post.

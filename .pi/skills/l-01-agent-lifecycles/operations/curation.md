# Operation — Curation

**What it covers:** the leaf coherence pass — reconciling intended, current, and implemented meaning
and writing the affected onboarding. The Manager starts the Curator when the Worker's first freeze exists;
the Worker and Reviewer supply the candidate-bound inputs directly. It also carries the repository's
**first or resumed knowledge
foundation**, which is the same curator's authoring work with a different admission and scope.

**When it is selected:** a leaf has a Worker's freeze and affected memory surfaces. The Manager supplies the
assignment; the Worker sends its hand-off list and the Reviewer sends the code freeze it passed.
Its second, bounded entry — a repository's first or resumed
knowledge foundation, before any leaf exists — is stated under *The repository-foundation entry* below.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| curator | performs the three-way reconciliation, writes through one admitted writer and hands the memory candidate directly to the Reviewer |
| worker | supplies the exact freeze and hand-off list; answers code questions and receives evidenced code concerns directly |
| reviewer | supplies the passed code freeze, checks the memory candidate and answers code findings, including after code PASS |
| manager | starts the Curator, reads leaf state from reports and retains task acceptance and landing authority |

Reconciliation and transaction ownership stay disjoint: the Curator reconciles and writes memory; the Manager owns the
transaction. The curator runs no Git write: no stage, commit, branch or ref change, or stash operation.
The curator never runs the closeout preview, never stages a sync resolution or repairs closeout transaction state, and
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

The brief binds the assignment; the Worker's and Reviewer's direct handovers supply the candidate-bound
inputs. None is inferred from transcript memory:

1. **The captured change set** — code diff from the leaf's base to its actual pre-closeout candidate with counters and paths,
   identified by the Worker against the leaf contract's recorded range, not a guess.
2. **The leaf task doc** — including the approved requirement corpus ruling and every exact stable-ID
   + version canonical packet the brief names.
3. **`notes/`** — the builder turn report, plus the candidate-bound route-review verdict when review
   was requested.

Also required: the existing onboarding contracts and entity records for the affected routes (read
before replacing their account of current intent); the code and memory worktree paths; the
knowledge files of this leaf's memory worktree (cards and sidecars under `onboarding/`, records under
`knowledge/`), which the hand-off is written into and the next task's planner reads from its own base;
and the enclosure contract path that scopes the curator's MCP tools to this leaf.

Intake is **rejected** when an applicable packet is missing, unapproved, or version-mismatched. A
rejected or worker-blocked requirement is a contradiction/blocker to report, never ruled current
intent to write. Atomic child leaves have no leaf route-review record; when review is requested,
their accumulated change is reviewed on the canonical master at master-to-parent integration.

## Bound Paseo intake and capabilities

For an AR-launched Paseo capsule, read the supplied task with its exact `taskDocReadArgs` on
`agents-remember-task` (extensionless slug), then read the canonical JSON at the returned `docPath`.
Use the handover's `arMcpContext.readerArguments` and the actual tool schemas; `task_context` carries
both the canonical task reference and enclosure contract. Missing scope fields and refused
capabilities are blockers, not permission to use another AR server or an unscoped command. Read the
assignment's applicable task packets, not an unrelated role or the complete hierarchy by default.

Read the immutable baseline and candidate through admitted scoped `knowledge_read` on `agents-remember-task`/`knowledge_diff`
before semantic changes, preserve the baseline, and read admitted records back under that authority.
The MIK writer below remains the owner. Use only the source-bound command admitted for the selected
runtime; if this compiled Paseo capsule exposes no writer and admits no such command, report the
required records not written. That limitation belongs to this capsule, not MIK generally. Never use
another installation's command or an internal writer to bypass the refusal. Verify every output
resolves inside the selected memory root.


For the selected converted memory root, the admitted file-writer command is
`agents-remember knowledge-ingest --contract <this leaf's enclosure contract> --list <the hand-off list>
--authorization-ref <ref> --commit --json`. It writes files and publishes no dataset;
The CLI has no `--baseline`, `--publish` or `--publish-to` options; its parser rejects them before
the file writer is invoked.

An explicit report-only assignment delivers only its requested report and bound upstream message,
without onboarding writes, MQC, coherence, indexes, or comparison production. It does not claim the
normal authoring pass complete; that pass retains all the full-operation obligations below.

## Normal workflow

1. **Reconcile three ways.** The pass succeeds only when these three bodies agree, or every material
   divergence is surfaced directly to its answering seat under the handover rules below:
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
3. **Route the durable knowledge through the real writer.** The reconciliation's requirement-shaped items
   are authored knowledge, not onboarding prose. Every target in it carries its own authored `rationale` (why
   that place carries the obligation) and optionally a `role`, as `../templates/curator-handoff-list.md` states;
   the writer never generates a rationale and refuses a list with an unexplained target
   (`realization_rationale_absent`). Hand the same JSON hand-off list to
   `agents-remember knowledge-ingest --contract <this leaf's enclosure contract> --list <the list>
   --authorization-ref <the authorization this run is admitted under> --commit --json`. The writer writes files in
   this leaf's memory worktree: records under `knowledge/`, `realizes` and `proves` entries in the file sidecars,
   and the leaf's history file. Nothing is published: the files are committed with the leaf's memory branch, and
   the next task's planner reads them from its own base. **Consume the report, not the exit status:** its state
   is `written`, `planned` or `refused`; a refused run wrote nothing and names every problem with its own reason.
   A partial hand-off therefore stays visible; it is never rounded up to "the batch went through". The command
   needs converted memory: on the legacy format it refuses with `legacy-format` and names the conversion command.
4. **Examine family coverage and author it as part of the same list.** For every scoped obligation, decide whether
   the evidence and the project's intent justify a joint obligation with others. Where they do, author a `family`
   record in the list's `records` section, as `../templates/curator-handoff-list.md` states: the family's **own**
   guarantee text, its members, its routes and its admission — one obligation may belong to several families.
   Nothing is grouped by directory, route, label or shared anchor. A member or membership change **prompts a fresh
   look at the affected recorded guarantee**: update the stored family record only where that is justified, answer
   the family's worklist item with a history row that says what was examined. When you restate a family's
guarantee, name the family in `history` as a `changed` row with your `effect`; the writer fills the
family's own revision. Never let an implementation
   change rewrite member intent or family meaning by itself. An external document a card rests on is cited as an
   `external` reference target in the card's sidecar, never as a repository path with a Git blob. Read the result
   back — the writer's report, and `knowledge_read` with `view="family"` over this leaf's memory worktree — and
   carry the coverage into the handoff: the families authored or updated, and those examined and left unchanged.
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
   - generated route indexes → regenerate with `route_index_refresh` on `agents-remember-task` scoped to this leaf.
6. **Reject overview-dumping and task-log-dumping.** Never add an overview heading named for a leaf or date;
   update the overview body that owns the meaning. Preserve a truth when it is important to future
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
   Do this **before** the closeout, never after it: the closeout itself closes the leaf's history file, so
   the memory tree it commits is a different tree from the one the authority attests, and a validation made after
   the commit can never match. After the closeout, go straight to integration.
8. **Repair, then republish.** After each repair, re-run the full operation before handoff.

## Scope before ingest

For every authored invariant, the hand-off template's curator-owned `scope` must carry applicability,
conditions and exclusions before ingest. Workflow or provenance text is never semantic scope;
missing or malformed scope is a named `unfilled_curation_scope` refusal.

The reviewer compares the leaf's base and candidate from Git (the code trees and the knowledge files of the
two memory trees), so the curator records no separate comparison.

## The repository-foundation entry — the curator's work, before a leaf exists

Curation's ordinary shape is a leaf's coherence pass. A repository's **first or resumed knowledge
foundation** is the same *work* in its other entry, and it is selected when the scope is the
repository's foundation rather than one leaf's delta: a new project entering ordinary setup, an
existing project whose memory holds no knowledge records, or an explicitly requested bootstrap of an
existing project. Its procedure is the `c-14-knowledge-bootstrap` skill.

What stays identical is the ownership: the reconciliation and the authored knowledge — supported
invariants and facets, justified family guarantees with their members, applicability, essential
conditions, exclusions and source realizations — belong to this seat either way, and
there is still exactly one admitted writer. What changes is the
**carrier**, the admission and the scope:

| | Leaf coherence pass | Repository-foundation entry |
| --- | --- | --- |
| Carrier | this seat, opened on the leaf's task document | a **taskless curator seat** (developer ruling 2026-09-24) which authors under this role's own rules, or the taskless **bootstrap** seat for the read-and-report step |
| Scope | this leaf's captured pre-closeout change set | the requested project scope |
| Required inputs | the brief, the change set, `notes/`, the enclosure contract | the declared repository entry, the resolved context, the requested scope, the available sources, the current knowledge state |
| Writer entry | `agents-remember knowledge-ingest --contract <this leaf's enclosure contract> … --commit` | `agents-remember knowledge-bootstrap --repo <repo_id> … --commit`, from a session with **no enclosure in scope** — the taskless writer refuses one (`enclosure_in_scope`) so a bootstrap can never write onto a task's line |
| Onboarding | written by this pass | optional input: the foundation neither requires onboarding to exist nor writes any |
| Missing inputs | intake is rejected | there is no brief, no change set and no enclosure contract to intake, and no task document is fabricated to give this entry an argument list — the carrier is a taskless seat, which is exactly why the curator seat is admitted without one |

Neither entry substitutes for the other: `knowledge-ingest` needs an enclosure contract and is not the
route for a repository that has no leaf, and no leaf, worktree, enclosure or task document is ever
fabricated to give the foundation entry an argument list. **The commit word is the developer's on both
entries.** Planning is the default: a run without that word writes no file and no commit. A foundation run that examined only part of its scope reports itself partial
and names the areas it did not reach; it never claims the foundation complete for them.

## Authority gates

- **Onboarding writes only.** The curator never writes code, never edits task docs, gates, lifecycle
  state, worktree contracts, or closeout state, never mutates task-doc status, and never performs
  closeout/integration/finalization.
- **The knowledge write keeps its existing owner.** The curator *invokes*
  `agents-remember knowledge-ingest` on the leaf entry and the taskless
  `agents-remember knowledge-bootstrap` on the repository-foundation entry; it never writes a record,
  a sidecar entry or a history file by hand, never opens or edits the derived index, and uses no MCP
  tool as a write route (none writes knowledge). `--commit` stays the write word on both entries: it
  writes files, and it is not a Git action and not an acceptance. A zero exit is not proof that
  anything was written. The
  report's state and its listed records, entries and refusals are the evidence, and a refused run is
  reported exactly as it came back.
- **Scope the MCP tools on `agents-remember-task` with the enclosure contract path.** Without it they resolve the **official**
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
  Validate before closeout. The frozen record names that candidate; closeout's history and
  fingerprint writes do not make it evidence about the later live tree, so do not validate that
  tree again after closeout as a prerequisite to integration.
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
  guesswork**, ask the Worker about code or the Reviewer about its verdict directly; ask the Manager only
  when the missing row is a matter of ownership or authority.
- **A code concern goes to the Reviewer and Worker, even after code PASS.** State the requirement or
  report mismatch with its evidence; repair no code and write no knowledge claiming behaviour the code lacks.
  The Reviewer answers by taking it into its findings and reopening that point, or explaining why it does not
  hold. Name the finding and answer in the Curator report; an unresolved disagreement goes to the Manager.
- Report **dirty-source drift**, missing onboarding, or other findings exactly as returned; never
  convert a subset result into a full-quality claim.

## Handoff / exit

On an AR-launched leaf capsule, use `role_message` on `agents-remember-task` directly to the Reviewer,
with the handover's role-and-task arguments. An agent ID is used only whole and unchanged as supplied
by a sender line, start result or handover. A Curator starts no role: the starter briefs an agent and becomes
its parent, so no seat starts its own checker. A dashboard launch needs no parent. Send questions requiring the developer's decision to your parent; without a parent, ask in your own chat. Repository-foundation and other admissions keep their brief's transport without a fallback.

The curator's memory candidate goes directly to the Reviewer for its whole memory check: the changed
onboarding paths, the intent
reconciliation, the exact scoped commands and results, and any failed, blocked, or not-run checks.
It also carries the knowledge hand-off result: the writer report's state, the records and entries it
wrote with their IDs and paths, the history rows, and every refusal named. On the repository-foundation
entry the same facts come from the bootstrap report — each record's outcome, the files the wave
wrote, and the remaining and carried work — together with the source areas the run examined and the
areas it did not.
The structured coherence record and its generated projection are the durable output — not the
transcript and not a parallel hand-authored report. Write the record before ending the turn;
terminal/finalizer evidence attests only that this turn ended. Do not hand-write a curator certification
or write a parallel model completion post.

Each handover names the leaf, the thing's exact identity (the Worker's code head and change hash, and
the memory candidate's identity), its report or verdict path, and the whole next stretch requested.
The memory check keeps separate sealed reports, finding IDs and its own pass count: it opens
no code-review round, resets no code verdict and adds no IDs to the code baseline. Its ordinary limit is
the same number of passes as the code review. A pass beyond its
ordinary limit needs the developer's word recorded by the Manager. Receive memory findings or PASS
directly from the Reviewer and repair within that lane; the Manager carries none of these handovers.

The handover is taken only when the recipient's reply or report names the exact thing and shows the
requested work or its start. `accepted` alone is not action. Quote a provider limit, error or non-action
reply once to the Manager with the seat, and continue independent work. Report an empty or doubled
seat refusal once with its exact text; choose no candidate yourself. A busy refusal is pending in your
report: retry before ending the turn, and report a second busy refusal once to the Manager. You still
owe the direct handover and send it yourself when the Manager tells you the seat is free.

If a required Manager notice cannot be delivered, record it as pending in your report, continue independent work, and retry it with `role_message` on `agents-remember-task`; do not turn this transport failure into a developer question in your own chat.

The Manager hears from the Curator only for these delivery and seat problems, ownership or authority,
a pass beyond the ordinary limit, a code finding disputed with the Reviewer, or a sync step assigned
to the Manager. It reads ordinary state from reports and never relays the contents. Memory conflicts
in the Worker's supported leaf sync come directly to the Curator; transaction steps stay with the
Manager. Contact another leaf only for a dependency named by a requirement or the Manager; other
cross-leaf matters go through the Manager.

Inside the assignment the Curator organises its work with whatever its harness offers, including
sub-agents at any size, without a prescribed number or depth. The seat checks and answers for their
work, credits their parts in its report, and hands it over under its own name. Boundary messages,
task records and product operations changing leaf state are the seat's own acts. A harness sub-agent
holds no AR seat, starts no role and has the same working folder, permissions and assignment. Nobody
checks itself through a sub-agent; the memory check stays the Reviewer's. Keep exactly one Curator
writer through the admitted writer. A harness without sub-agents can perform the same assignment.

For a native memory-file sync conflict, repair only the memory content inside your admitted write surface. The Manager captures, verifies and stages the resolution; the Worker continues the supported sync with `resolution_action="continue"`. Do not stage files or change transaction authority.

With a parent named in `host.parent`, send every question requiring the developer's decision to that parent with `role_message` on `agents-remember-task`, addressed to its agent ID. Say what the question is, what you hold back until it is answered, and what you recommend and why. Continue every part of your assignment that does not depend on the answer; do not end your turn on the question or hold a wait for the developer. The answer arrives as a message from your parent. When no independent work remains, record your state in your report and end with a final reply saying you await your parent's message, with no question addressed to the developer. Without a parent, ask the developer in your own chat and end your turn.

If delivery to your parent is refused as busy or at a permission prompt, keep the question under **Pending developer questions** in your report, continue independent work, and send it again before ending your turn. A second refusal leaves it pending; your final reply states the undelivered question for your parent, not for the developer. Having no work left does not open your own chat. Only when your parent cannot be reached at all (archived, not found, not resumable, no runtime configured, or host unreachable) ask in your own chat, name the refusal and why the parent cannot be reached, and end your turn. Recover open questions and recorded answers from your report after compaction or reconnect; do not ask twice for an answer you already have.

Treat a relayed answer as the developer's instruction only when it states that the developer gave it, names the agent ID of the agent that received it from the developer, and carries the developer's words in quotation marks. Otherwise it is the sending agent's own word; ask your parent for the developer's words when required. Where your role requires a record before acting, record the relayed answer in the same place and form, with the quoted words. Silence is no approval. Only the developer can answer a harness permission prompt, in the chat of the agent waiting at it; an agent stopped at a prompt cannot relay it.

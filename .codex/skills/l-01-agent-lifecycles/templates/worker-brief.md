# Template — Worker Brief

What a starting seat (manager / orchestrator) makes sure the leaf's documents hold before it
starts a worker with `role_start_set` or `role_start`. The call carries no brief: the worker's
first message is compiled from the documents.

**This template feeds inputs; it does not author rules.** Where the packet states a rule, the rule's
single home is `../roles/worker.md` (the seat's own duties), `../operations/implementation.md` and
`../operations/closeout.md` (the build procedure and the targeted-check contract), or
`../core/acceptance.md` (the acceptance envelope, attempt lineage, and completion truth). The rows
below carry the *values* for this leaf; if a value here disagrees with those files, they win.

---

```md
ROLE BRIEF — worker

# WORKER BRIEF — <leaf-id> · <leaf title>

You are a WORKER for leaf `<leaf-id>` of master `<master>` (repo: <repo-id>). Your lifecycle is
`skills/l-01-agent-lifecycles/roles/worker.md`; this brief is your session start. An R27 successor uses
`reviewMode=fix-verification` and verifies only the sealed outstanding issue IDs.

## Worktrees (your code write area + memory context)
- Code:   `<code-worktree-path>` (branch `<work-branch>`, base `<base-commit>`)
- Memory: `<memory-worktree-path>` (read/context for changed-path notes; the curator writes onboarding)
- Leaf Requirement Attempt Journal: `<notes-reports-path>/<leaf-id>-requirement-attempt-journal.md`
  (one physical append-only journal; workers append delivery records and independent reviewers
  append separate adjudication records without changing earlier bytes)
- Plus your turn report at the path below. Nothing else. NEVER `git commit` — the owning seat
  closes out after evaluating the candidate-bound reports and code/memory verdicts.

## Tool surface
- Native file tools inside the two worktrees; shell for the checks below.
- Read-only AR retrieval: `read_ar_files` (names the governing cards in the repositories of
  your scope — the leaf worktrees under task scope, the official checkouts otherwise;
  final verification uses native reads), `context_packet`.
- When the master line moves, run the supported `worktree_sync` for your own leaf and hand the new
  freeze to the Reviewer. Send memory conflicts directly to the Curator; ask the Manager only for a
  step the sync contract reserves to it. For a native memory-file conflict, the Curator repairs content; the Manager captures, verifies and stages it, then the Worker continues `worktree_sync` on `agents-remember-task` with the exact contract and `resolution_action="continue"`. No other `worktree_*`, `lifecycle_finalize_task`, `task_doc`,
  `memory_*`, or `route_index_refresh` —
  generated route indexes are regenerated with a local `build_route_indexes(...)` from the memory
  worktree.
- Candidate environment: build the worktree's environment as the repository's tool notes
  (`system/tools.md`) say, and run every command on the candidate in it — there is no
  `python` shim in this environment. Where the tool notes name no command, say so in your
  record. A record of a run names the interpreter it used.

## The task
Leaf spec: `<leaf-doc-path>` (read it first). <One-paragraph task statement: the bug/feature, the
files involved, the invariants that must hold, what NOT to touch.>

## Review phase and sealed issue packet
- Review mode: `<baseline | fix-verification>`.
- Canonical review purpose: `<task/review identity>`.
- Baseline: `<sealed verdict/baseline ref | to be created by this review>`.
- Immediately preceding result: `<verdict/result ref | N/A for baseline>`.
- Exact outstanding issue IDs: `<IDs and prior remaining-set digest | none for baseline>`.
- Successor allowed work: `<fix only the listed IDs, with one source/evidence anchor per fix>`.
- Outside-list observations: `<report to the owner for developer decision; never turn into a new
  finding, requirement, route, or scope>`.

## Owned primary requirement (exactly one stable-ID + version)
Repeat this block exactly once for the revision this leaf owns. List inherited master or adjacent
requirements separately as dependency/preservation constraints; this leaf verifies but does not
claim to close them. Do not aggregate, renumber, sample, or write "see task doc" in place of the
primary revision.

- `<stable-requirement-id> @ <version>` — `<short normative label>`
  - Canonical packet: `<task-relative complete requirement packet; read it in full>`
  - Approved revision: `<packet state=approved; durable corpus-ruling citation>`
  - Leaf manifestation: `<leaf-id>/<stable-requirement-id>@<version>`
  - Next handoff attempt ID: `<leaf-local monotonically ordered id; mint only when a candidate is handed to review>`
  - Predecessor attempt and carried findings: `<attempt id + finding ids | none for first attempt>`
  - Exact candidate identity class: `<Git tree/commit | non-code digest + durable anchors>`
  - Required deliverable evidence class: `<code path + symbol | document path + section/anchor |
    persisted artifact | mounted UI | operation result | other exact class>`
  - Required verification evidence class: `<test node/symbol | command/report section | scenario |
    other exact class>`
  - Existing developer approval for changed delivery: `<durable ruling citation | N/A>`

Record the acceptance block for this owned primary ID as `../core/acceptance.md` states; general prose and an aggregate statement do not satisfy that contract.

## Adjacent dependency/preservation constraints (not closure claims)

- `<stable-requirement-id>@<version>` — `<dependency | preservation>` — `<packet path>` —
  `<verification needed to prove this leaf did not violate it>`

Advance delivery attempts, experimental protocol and journal rows as `../core/acceptance.md` and `../templates/turn-report.md` state; the worker never self-rejects or silently replaces a row.

## Coding guidelines (read before your first edit)
`<memory-worktree-path>/system/coding-guidelines.md` — your diff is written against it: file and
function budgets, responsibility rules, source-comment scope (no task/leaf ids in shipped
comments except bounded `L<leaf>-R<n>`/`L<leaf>-S<n>` durable-requirement identifiers per rule 6), typed boundary parameters, D1/D2/D3 stability. Green acceptance evidence proves none of this.
Name any guideline finding or plan conflict in your turn report; a contradiction you hide is a
verdict finding, not a style note.

## Targeted checks (before you report)
Run targeted checks and record the durable-evidence promotion hold point as `../templates/turn-report.md` states, using the repository-specific commands, environment and evidence requirements from `system/git-workflow.md`, `system/coding-guidelines.md` and `system/tools.md`. Full code-quality and full-test operations require an explicit developer request. A failed or not-run targeted check is reported and never relabeled as full green.
- `git diff --check` in both worktrees.

## Curator handoff input
After the Reviewer passes code, send the Curator your hand-off list directly for the exact passed
freeze as `../operations/implementation.md` §Outputs states, in the shape `skills/l-01-agent-lifecycles/templates/curator-handoff-list.md` owns.
- Changed paths and code-diff summary for the curator's affected-onboarding handoff.
- Your curator hand-off list: every requirement-shaped item of this leaf, emitted in the shape of
  `skills/l-01-agent-lifecycles/templates/curator-handoff-list.md` — one entry per item, each with its
  own `statement`, `kind`, `target`, `found_at`, `disposition` and `evidence`; `resolution`,
  `validated_at`, `record_action` and `supersedes` stay `null`, because those are the curator's to fill.
  Name where each thing lives (path plus the construct inside it, from one resolution act), keep your own
  wording rather than re-telling the item for the reader, and let `target: []` stand where a ruling
  applies nowhere instead of inventing a place for it.
- Any route/onboarding observations from implementation, clearly marked as observations; the
  curator reconciles them with existing and ruled intent before writing onboarding in its own
  fresh session. Do not present a forward-looking idea or local inference as accepted current truth.
- Pin idiom for any metadata note the curator needs: "Verification metadata pinned until closeout
  stamps the <leaf-id> commit."

## Turn report and direct handover
Write `<notes-reports-path>/<leaf-id>-worker-report.md` following
`skills/l-01-agent-lifecycles/templates/turn-report.md` and `../operations/implementation.md` §Outputs — including exact links to every newly
appended journal attempt, a separate Checks section with exact commands + outcomes,
changed paths for the curator, the retrieval-evidence tally, and the respawn state.

## Messages
Messages wake; files carry. A message says what is asked or what is handed over, and names the files: evidence, verdicts, lists and logs are files, never pasted into a message. The full text goes in `body` and its files in `attach`; the tool files every message in the `messages` folder of the narrowest task the two agents share, and a leaf workspace links that folder as `messages`. The recipient reads the message and, where a file belongs to it, one last line naming the file and its SHA-256. A text holds at most the limit of the settings. A return goes to the parent's role with its task references, never to an id read from an earlier message.
```

---

**Compiler notes for the spawning seat.**

- Fill every `<placeholder>`; a brief with an unresolved placeholder is not dispatchable.
- Compile the complete stable-ID + version requirement set from the canonical leaf plus applicable
  inherited master requirements. Verify every canonical packet matches that version, is approved,
  and cites the durable corpus ruling. A missing, duplicate, unstable, unapproved,
  version-mismatched, or prose-only requirement identity makes the brief
  undispatchable.
- Mint the next attempt ID from the leaf journal, carry the exact predecessor findings, and require
  the worker to bind the stable candidate before appending. Never pre-approve or reuse an attempt ID.
- Compile the review mode explicitly. Use `none` when review was not requested. A baseline brief
  must carry the complete agreed scope and leave baseline sealing to the first reviewer. A
  fix-verification brief must carry the immutable baseline, immediately preceding result, exact
  outstanding IDs, and fixed-only worker scope; never dispatch it with a whole-review, exploratory,
  new-route, or outside-list mandate.
- Verify the provider stack actually answers before naming it; write `NONE (native reads only)`
  when it does not — a worker discovering dead providers mid-leaf wastes its turn.
- The report path lives under the series `notes/reports/` — the same folder the seam verdicts use.

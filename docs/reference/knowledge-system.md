# Knowledge System

Agents Remember stores current repository knowledge as text in a
[memory repository](../concepts.md#memory-repository). Markdown explains the code; JSON records and sidecars hold the
structured claims and anchors. Git versions both. The source of truth is this
text tree, not the knowledge index derived from it (see [Derived index and reads](#derived-index-and-reads)) or a dashboard display.

## Files and ownership

Paths below are relative to the selected memory root.

| Path | Contents |
| --- | --- |
| `knowledge/layout.json` | The layout marker of the memory tree, schema `ar-memory-layout/v2`, with the format version. `memory_init` writes it when it creates the memory. It can also hold `dependent_links`, a lookup that the knowledge writer (the tool `knowledge_write`; see [Writes and producer lists](#writes-and-producer-lists)) derives from the dependent records. |
| `knowledge/<kind-directory>/<ID>-<slug>.json` | One shared record with its schema, stable ID, origin, revision, the [writer stamp](#state-writer-and-review) of its current revision (`writer`), a `status` where its kind has one or the record is retired, and kind-specific content. An optional Markdown file with the same stem holds explanation. |
| `onboarding/<source path>.md` | Current file-level prose, with numbered citation markers. |
| `onboarding/<source path>.json` | Its `ar-onboarding-file/v1` sidecar: source path, numbered references, `realizes` entries and, on test files, `proves` entries. |
| `onboarding/<route>/overview.md` and `overview.json` | A route overview and its `ar-onboarding-route/v1` reference sidecar. The root route is `.`. |
| `knowledge/history/<owner-id>.json` | Curator-authored `ar-history/v1` judgment rows for a task, a foundation wave or a sync, beside the parts that only the product writes: the revisions the owner wrote (`written`) and the fact of its passed memory review (`review`). |
| `knowledge/history/<task id>-attempt-<n>.json` | A reopened task's later [attempt](../concepts.md#attempt), starting at 2; its earlier closed files keep their bytes. |

Record directories are `invariants`, `families`, `decisions`, `incidents`,
`assumptions`, `limitations`, `failure-modes`, `scenarios`, `diagnostics`, `terms`,
`weaknesses` and `dependents`. An invariant owns its statement and admission;
realization and proof relationships belong to file sidecars. A family owns its
members and routes. Other record kinds own their outgoing links. Stable IDs
survive filename-slug changes; revisions change when meaning changes.

The members of a [family](../concepts.md#family) are invariants (its field `members`). A file
realizes an invariant when its sidecar holds a `realizes` entry for it, a
[realization](../concepts.md#realization-and-proof). A route of a family is a code directory that
holds realizations of the family's members, and a family can list several routes. Every
realization of a member must lie under a route
(`R04.2-coverage`), and every route must hold at least one realization (`R04.2-non-empty`). <!-- docs-check: identifier: the product prints this code -->
Each code of the form `R<n>.<n>-<name>` on this page is the id of a rule of the
[validator](../concepts.md#validator); a violation names its file, its field and this id in square brackets.

A new invariant, family or decision record states an admission criterion and justifies it in one
sentence of words. The admission criterion, in the record's field `admission`, says why the record
earns its place: for an invariant `spans_locations`, `guarded_by_test`, `family_guarantee` or
`prevents_costly_mistake`; for a family `joint_guarantee`; for a decision `real_alternatives` or
`constrains_future_work`. A new record without a criterion has the wrong shape: the validator refuses it and names the field
`admission.criteria`. With `R27.2-new-record` the validator refuses a new record whose justification <!-- docs-check: identifier: the product prints this code -->
only cites a task or a requirement, and one whose criterion `spans_locations` or `guarded_by_test`
the sidecars of the tree do not support.

Invariant statements, family guarantees and history reasons follow one
[writing profile](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/writing/technical-english-profile.md)
of the skill. It asks for one promise per invariant that can be tested, for a named actor, and for
stated conditions, quantifiers and exceptions. The Curator's instructions name the profile before
the Curator drafts such a text.

A weakness record names the file in which a weakness of the code occurs. Its ID begins with
`WKN-`, as the ID of every record begins with the prefix of its kind. Its kind says what the
weakness is: `context`, `compute`, `storage` or `time` for code that wastes an agent's context,
compute, storage or time, `fault` for a fault, and `debt` for a debt of the code. A weakness
record is no finding and refuses nothing. `knowledge_read` with the view `weakness` lists the
open ones, selected by folder (`sourcePath`) or by kind (`weaknessKind`); `includeClosed` adds
the closed ones.

A weakness closes with the task whose change removed its cause. A closure names that task's code
commit or, where the closing task removes the cause itself, one sentence of reason and the
running task; the writer refuses a commit that is not the full ID of a real commit of the code
repository. The reading tool gives the commit from the `Code-Commit` trailer of the memory commit
that closed the record, and before the closeout it says that the commit comes with the task's
closeout.

A decision record holds its `context` and at least two `alternatives`, of which exactly one has
the status `chosen`. Each alternative gives its `reason`, and each `rejected` or `deferred`
alternative states a `reconsider_when` condition. The decision links each record, route, anchor
or requirement that it governs, with one of three relations: it `explains` the target, it
`constrains` the target, or it is `motivated_change_to` the target. A `reconsider_on` link ties a
`rejected` or `deferred` alternative to a target whose change should reopen it. The validator
refuses a decision that breaks one of these rules.

A dependent record names one document of the code repository and each place the document stands
on: an anchor in a file, a whole file, or a revision of an invariant or family. The record holds no
text of the document. Whether the document is stale is derived from these links and never stored.

A file link has not moved when its recorded blob and the blob at the selected tree differ only
where `project.version` of `mcp/pyproject.toml` in the code tree stands: the version named at the
record's confirmation, replaced by the version of the selected tree, at complete version tokens and
with an optional `v` before them. Every other changed byte moves the link, and a changed dependency
version is such a byte.

A record is never deleted. It leaves use by `status: retired`. The validator refuses at every
route a tree that lacks a record its comparison base holds (`R27.4-record-kept`). <!-- docs-check: identifier: the product prints this code -->

Reference sidecars bind numbered Markdown markers to their targets. Anchors for a
file sidecar's own source omit the redundant path; route-sidecar anchors name a
path. Route-index fields belong in generated `overview.index.json` caches, not in
`overview.json`.

Symbol anchors support Python, JavaScript, TypeScript, TSX and Lean. In a `.lean` file,
a named declaration can be anchored by its name as written or by its full
namespace-qualified name. A full-name match takes precedence. When a written
name matches several verified declarations, use the full name. Cite a line range
when the declaration has no name or uncertain scope makes both name forms
unverifiable.

A file sidecar that holds numbered references needs its Markdown card: the validator refuses a
new sidecar with references and without the card (`R22.3-sidecar-without-markdown`). A new file <!-- docs-check: identifier: the product prints this code -->
sidecar that holds only `realizes` or `proves` entries is valid without a card.

Current cards hold current truth. A record's meaning history is its Git log;
judgment history is the set of Curator-authored rows about it. Rows explain a
change or a supported `no_impact` judgment at the relevant revision, with the
required coverage or examined members. They are not generated by a diff. Closeout
freezes an owner's history file with `closed: true`; later attempts do not rewrite
it.

A [judgment row](../concepts.md#judgment-row) is one entry of a history file
`knowledge/history/<owner-id>.json`. The Curator writes it in the section `history` of a
[hand-off list](../concepts.md#hand-off-list), and the writer stores it (see
[Writes and producer lists](#writes-and-producer-lists)).
A judgment history has a row kind for each subject it judges. An `onboarding_trace` row judges
one card or route overview as `updated` or `no_impact`. A `membership` row with the disposition
`no_family` records that a new invariant belongs to no family. A `record` row judges a decision,
incident, assumption, limitation, failure mode, scenario, diagnostic or term record as `changed`,
`retired` or `no_impact`.

A family row with the disposition `changed` carries an effect label: `restore`, `clarify`,
`introduce`, `strengthen`, `weaken`, `replace`, `split`, `merge` or `retire`. The label says how
the change moves the obligation: `restore` brings back one that was dropped or broken, `clarify`
rewords it without changing what it requires, `introduce` states a new one, `strengthen` adds to it
or tightens it, and `weaken` relaxes it or removes part of it. `replace` puts a different
obligation in its place, `split` divides one into several records, `merge` joins several records
into one, and `retire` ends it. The writer stores
the label with the family's own revision and refuses a `changed` family row without it. The
dashboard's knowledge reader shows the label, or `effect not recorded` for a row that has none.

## State, writer and review

A revision of a knowledge record is the record at one revision number. Every revision of an
invariant, a family or another kind of record has one [state](../concepts.md#record-state), which
the product derives from the history files of the same memory tree and never stores in the record:

- **proposed**: an agent wrote the revision, and no review of it is recorded. A new revision starts
  proposed, whatever the state of the revision before it.
- **reviewed**: the history file of the task that wrote the revision holds a review fact.
- **consolidated**: a Consolidator kept or wrote the revision, and that work was reviewed and
  closed out.

A revision written outside a task, a foundation wave or a crossing has no history file that could
hold its review, and reads proposed. A retired record shows no state. A reopened task speaks for
each revision through the attempt that wrote it: a revision that a later attempt wrote reads
proposed until that attempt is closed out with a recorded pass, and a revision of an earlier attempt
keeps its state. A new `written` row carries the revision's `contentDigest`, a SHA-256 digest
of its meaning fields. The reader first checks eligible review facts against the record ID,
revision and meaning in each named review tree. The record's filename may have changed. A legacy
record that still stores `proposed` or `accepted` reads proposed with an unknown writer until
conversion. Before stamping that revision, the conversion checks the reviewed meaning to
distinguish its writing owner from other history claims at the same number. If multiple
legacy owners have exact proof of the same retained meaning, the revision reads reviewed and
counts once under the unknown writer.

Two lines can write different contents under one revision number. A crossing records that fact in
its own history even if one of the source records has no stamp or authoring row. A legacy review
or a Consolidator's keep can keep its credit at that number when the fact's named review tree
contains the exact revision and meaning. A fact proven to cover different content gives no
credit. If different content shares the number, a fact without exact proof gives no credit.
The crossing records the selected writer evidence when the retained record has no stamp and
the two sides infer different writers. Equal record bytes take their standing from the merged
histories. The [knowledge index](../concepts.md#knowledge-index) resolves this evidence during
construction, so subsequent reads open neither the history files nor the reviewed Git trees.

A record carries no mark of acceptance beside its state: the three states are the whole of what
the product records about a revision's review. A review of a particular kind is described in the
memory's publication rules.

No agent names a state. `knowledge_write` refuses an entry, or the fields of a record, whose
`status` is `proposed`, `accepted`, `reviewed` or `consolidated`, and its refusal says that the
product sets the state. `status: retired` retires a record. A decision's `status` (`active` or
`under_reconsideration`), a weakness's `state` (`open` or `closed`) and the derived staleness of a
dependent are words of their own kinds and no states.

**The writer stamp.** When the writer stores a revision, it stamps the revision with the role,
harness, model, effort and seat of the caller, the task, the owner of the write (`leaf`, `wave` or
`crossing`) and the time (`at`). Role, seat and task come from the caller's launch binding, and
harness, model and effort from its launch receipt; the input of a tool never reaches the stamp. A
value that the product cannot resolve reads `unknown`, and the field `unknown` gives the reason for
each such value; the write is not refused for it. `by` says what made the stamp: `agent` for an
agent's call of the writer, `product` for a write the product makes itself, with the operation's
name in `operation`, and `conversion` for a stamp the product gave an existing revision without
writing it. A second write of the same revision by the same task renews the stamp, so it names the
last writer of that revision; a write that leaves the revision number as it was leaves the stamp as
it is. The record carries the stamp of its current revision in `writer`. The history file of the
owner lists every revision the owner wrote, a record it created included, as
`{record, revision, writer}` in `written`, so the stamps of earlier revisions are read from the
same tree. A revision that neither its record nor a history file stamps reads as written
by `unknown`.

A memory sync that merges two lines merges their records without `writer`. Each record it stores
then keeps the stamp of the line whose record it equals, or no stamp where that line's record has
none. A record that combines both lines' edits is a revision the product wrote. It carries the
product's stamp with the operation `crossing-merge`: role, harness and seat `product`, model and
effort `none`, and the later commit time of the two merged lines. The history file of the syncing
leaf, or on a master's line its crossing file, names that revision. Two lines' different stamps
make no conflict; only their meaning fields do.

**The review fact.** When closeout closes a task's history file, the product writes into it, in the
same write, the memory review that passed the task's change: `round`, the reference to the verdict
(`verdictRef`), the memory tree the Reviewer passed (`tree`), the Reviewer's stamp (`reviewer`) and
the stamp of the agent that handed the memory change to the review (`handedOverBy`). It takes them
from the last memory review round of the task document, and only when that round passed and names a
usable verdict reference: trimmed, printable text within the reference length, read the same way by
the one-time state conversion: a round recorded as a block is never a pass, and a pass that a later
round followed is no longer the task's review. The round stores the passed memory tree and the stamp
of the agent that recorded it. `handedOverBy` is the Curator or Consolidator registered as active on
the leaf when the passed round is recorded: the one seat whose current launch receipt names the leaf
(its repository and path, read as the product reads a task document reference) with one of those
roles and is neither stopped nor rejected; receipts of one seat count once. Its time is the time the
round is recorded, since the product knows no time of a hand-over; no message decides it. Where no
such seat is registered, or more than one is, or its receipt holds a value no stamp can hold, it
reads unknown with the reason, and the round is recorded all the same. The stamp belongs to the
round as it was first recorded: a record-only correction of the same round, its verdict or its
verdict reference, renews the recorder and keeps `handedOverBy`; a changed memory needs a new round,
which is stamped anew. The write touches only files that the product owns; the records, families and
cards of the tree the Reviewer passed stay as they were. Where no pass is recorded, closeout writes
no fact, its answer says in one sentence that the task's revisions stay proposed, and it goes on:
the review fact is no gate.

## Worklist, validator and gate

A task's [worklist](../concepts.md#worklist-and-knowledge-gate) compares four sides: the base code
tree, the base memory tree, the candidate code tree and the candidate memory tree (the
[candidate](../concepts.md#candidate) is what the task wants to commit). For a leaf, the base
trees come from the commits in its [worktree contract](../concepts.md#worktree-contract).
The recorded memory base must be an ancestor of the memory worktree's HEAD for a live
comparison, or of the candidate memory commit when judging a pair of commits.

The worklist records the knowledge the changes reach, the facts for each item and any
declared planned effects. Each item has a
kind: `touched_invariant` and `stale_invariant` for invariants, `reached_family` and
`family_route_condition` for families and their routes, `unexplained_hunk` and `unexplained_file`
for source changes that no knowledge explains, `onboarding_trace` for cards and route overviews,
`unassigned_invariant`, `touched_record`, `reconsideration_candidate`, `planned_untouched` for a
declared planned effect that the change did not meet, `link_target`, `weakness_anchor` and
`changed_document`. An unreadable input makes the run `incomplete`, rather
than implying an empty worklist.

A record link whose code target the task removed raises a `link_target` item. No row answers this
item: the Curator removes or restates the link in the candidate memory.

A code repository can declare its generated copies in the file `generated-copies.json` at its
root. The file holds `version` (`1`) and `copies`; each copy names its `source`, its `targets`
(each with `label` and `path`), `ignoreNames` and `ignoreSuffixes`. The worklist raises no item
for a changed copy whose bytes equal what its source gives. A copy that differs keeps its item,
and the item names the source.

The Curator answers each item with a current satisfying judgment in the task's
history. A row satisfies an item when it has the row kind that the item's kind asks for and the
item's subject. It is current when what it names still matches the candidate trees, for example
the record's revision in the candidate memory or the content of the source files it covers. The
[knowledge gate](../concepts.md#knowledge-gate) finds rows by subject and checks their currentness; a saved
item ID alone does not establish satisfaction. A full task-scoped
`memory_quality_check` recomputes and persists the worklist. A contract-scoped
`knowledge_integrity_check` returns the latest persisted worklist beside its
validator report, so an old or absent worklist is visible.

A changed hunk or a changed file that no recorded knowledge explains has three answers: the
Curator attaches the change to an invariant, authors a new invariant, or writes a judgment row of
the kind `unexplained` with the disposition `no_invariant` and a reason.

For a changed source file, the worklist raises one onboarding item for the card of the file and
one for the nearest route overview. Only an `onboarding_trace` row answers such an item:
`updated` with a counted change of the card or of its sidecar, or `no_impact` with a reason. A
refresh of anchor fields alone is no counted change, and neither is a change of `realizes` or
`proves` entries alone. The writer binds the row to the content of the source files it covers.
When one of these files changes again, the gate asks for the row again.

The worklist raises a family whose routes do not fit the code: a route directory is absent, a
route holds no realization, a realization lies outside every route, or the family has no route.
The closeout is refused until a family row of the Curator answers the item with the disposition
`rerouted`, `assigned`, `changed` or `retired`.

When a task changes what a `rejected` or `deferred` alternative of a decision is linked to, the
worklist raises that decision for reconsideration. The Curator answers with a judgment row whose
subject is `reconsider:<decision ID>#<alternative index>`: `still_rejected` with a reason when the
rejection holds, or `raise` when the alternative goes to you. A `raise` sets the decision's
`status` to `under_reconsideration` and adds an open question to the task document.

The validator checks every registered rule against the candidate memory tree,
comparison bases and paired code tree: document shape and identity, anchors and
references, relationships, routes, history and admission. Its report distinguishes
refusing violations from report-only observations; a report-only observation is
never publication permission. Each report-only rule has a stated reason and names the
route that refuses in its place; `agents-remember knowledge-validate --help` lists these
rules.

The writer and every route that commits memory run the validator first: the closeout, every
landing, the adoption of a baseline, and a sync after its memory merge. A refusal names each
violation. No argument skips the validator.

Two of the rules concern links and routes. A link from a record to code must name a file or
directory of the paired code tree (`R22.6-link-target-path`). A family lists no route inside <!-- docs-check: identifier: the product prints this code -->
another of its routes (`R04.1-nested-routes`). <!-- docs-check: identifier: the product prints this code -->

Three rules run only where a task is published: at closeout, at a direct landing
(`direct_landing`) and at a recorded landing (`worktree_record_landing`, which records a commit
that the landing target already holds). They refuse an overview that is out of order, and the refusal names the file and the
rule. The first section after the title and an optional `Governing Overview` section must be the
purpose, headed `What This Repo Is`, `What This Area Is` or `Purpose` (`R80.1-purpose-first`). No <!-- docs-check: identifier: the product prints this code -->
section heading may hold a task id or a date (`R80.2-no-leaf-sections`). All citation lines must <!-- docs-check: identifier: the product prints this code -->
stand together at the end of the file (`R80.7-citations-at-end`). `agents-remember <!-- docs-check: identifier: the product prints this code -->
knowledge-validate` does not report these three rules.

The mandatory knowledge gate recomputes the worklist on the exact candidate trees
and combines open items, unreadable inputs and refusing validator violations.
Each open item is one repair finding. The closeout preview (`worktree_closeout_preview`, which
commits nothing) and the closeout itself (`worktree_closeout_apply`) judge the exact trees; a
refusal is `knowledge-gate-refused` with findings.
The landing of a task judges the closed-out pair again. The closeout judges the currentness of
the entries at the paths the task's own code diff changed, exactly as the master's landing does.
Master and checkpoint landing validate their selected memory commit against the parent line and
check current realizations at paths changed by the master's net code diff. A master's recorded
landing keeps an entry at such a path as the master's debt instead of refusing, when the landing
chain's own proof passes and that stale entry is its only finding; [Worktrees And
Closeout](worktrees-c09.md) tells that answer and the cleanup that follows it. The gate itself has
no waiver. A clean structural report does not supply a missing semantic judgment or
a validated [coherence record](../concepts.md#coherence-record), the record that the Curator
publishes with the tool `curator_coherence`.

A landing also derives from the dependent records which documents it made stale. It reports them
in `staleDocuments` of its answer and is not refused for them. Only the landing that finishes a
master is refused, with `knowledge-dependents-stale-at-master-end`, while a document that this
master made stale is still stale. The remedy is a task of that master that changes the document
or confirms it.

A run of `memory_quality_check` on a task writes its whole result to the log file that its answer
names under `log`. That result carries these compact blocks when their worklist/gate applies. The
answer itself is bounded in size (see [Tool answers](mcp-tools.md#tool-answers)) and carries status,
counts, blockers and artifact locations; it does not inline these blocks:

| Block | Fields |
| --- | --- |
| `knowledgeWorklist` | `state`, persisted `path`, `digest`, `itemCount`, `itemsByKind`, and `incomplete` inputs. |
| `knowledgeGate` | `state` (`pass` or `refused`), `owner`, `worklistState`, `worklistDigest`, `worklistPath`, `findingCount`, `openItemCount`, `incompleteCount`, `violationCount`, `reportOnlyCount`. |

The persisted worklist holds every item's facts. `reportOnlyCount` does not
contribute refusing findings. Gate findings contribute to
`curatorActionableCount`; a report-only `knowledgeReview` section does not. Read
actual quality/checklist states and the coherence result before treating a
candidate as ready.

A full `memory_quality_check` of a task, the tool that checks the memory of a task before closeout
(see [Memory](mcp-tools.md#memory)), also reports two findings that refuse nothing and do not
count as actionable. `landing-cut-card` names a card that lost text against the task's memory
base, the memory commit that the task's worktree contract records as its base: it removes at least 8 lines and more than twice the lines it adds. `landing-added-file`
names a file that the task adds outside `onboarding/`, `knowledge/` and `system/`.

## Derived index and reads

The [knowledge index](../concepts.md#knowledge-index), an SQLite file, is derived from the
selected memory text tree and keyed by its content and the source digest held by the loaded
build. Its default cache is `<coordination-root>/runtime/knowledge-index/`, outside Git
working trees, with separate files for each memory tree and build. A read reuses an index
only when both match, or rebuilds it; deleting the cache loses no authored knowledge.
Do not stage or hand-edit it.

On a cache miss, callers in one process wait for the build of the same index.
One file lock per cache directory serializes cold builds, with process-local coordination
if the file lock cannot exclude or the 60-second wait expires.
After a build, eviction removes temporary files whose builders are known to be gone.

The build's identity is the package's source digest held by the process from its start,
including for an installed package, a clean checkout or a checkout with uncommitted changes.
It stays the same after a restart of the same build. A change to package source changes the
identity; a change to a document outside that source does not. A process whose identity is
unknown builds an index for its own use, takes no other process's cache file and leaves none
that another process can reuse. A cache file named only by the memory-tree key is neither
reused nor written by this build; the age and size eviction removes such files along with
files named by build and tree.

MCP `knowledge_read` names attributed records and memory-file locations for a
selected view. Open those named files with the harness's file tools; the response
is not record text. `read_ar_files` names the cards and recorded knowledge for
bounded source paths, including action-asking records. `knowledge_diff` reports
the files' Git differences; it infers no semantic effect.

The dashboard's knowledge reader selects a memory tree and presents records,
relationships, realizations/proofs, currentness and timelines. Its GET route is
`/api/knowledge/reader/{view}`, with `repo` and a `commit` selection (`published`,
a memory commit or `leaf:<scope>`). The review page compares the task's base and
candidate code/memory trees and presents the source change set, knowledge and
stored judgments. A comparison or read is not acceptance. Unavailable, stale or
moved selections are reported explicitly rather than silently serving another
tree.

A read measures currentness on the code the caller sees. For a clean checkout it measures the
commit. For a checkout with uncommitted changes it measures the working tree and says so:
`read_ar_files` and the dashboard's knowledge reader answer `codeState` as `working-tree` or
`commit`. `knowledge_read` with `repositoryRoot` follows the same rule, and
`codeTreeId: "working-tree"` selects the working tree explicitly.

## Writes and producer lists

The Curator writes knowledge through the knowledge writer: the tool `knowledge_write` of the tool
server it runs on, or the same writer as a command, `agents-remember knowledge-ingest` for a task and
`agents-remember knowledge-bootstrap` for a [foundation wave](../concepts.md#knowledge-foundation).
The skill prefers the tool, and before a command that writes it has the Curator bind the command to
the [serving build](../concepts.md#serving-build) of that tool server, as its
[serving-build reference](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/knowledge/serving-build.md)
describes. A task write is bound to its contract and memory worktree.
Without `commit`/`--commit`, the writer plans and validates; with it, the writer
writes knowledge files and makes no Git commit. The report identifies written
records, entries, rows and refusals. A `raise` row adds its open question to the task document,
which `knowledge-ingest` reaches only through settings it trusts. When the command cannot reach the
task document, its refusal names the cause and the call that can: `knowledge_write` with the same
hand-off list, given as `contractPath`, `handoffPath`, `handoffName` and `authorizationRef`.

The writer refuses, in the same run, each row that the gate would refuse and that the writer can
know. A refused run writes nothing and reports `<n> outcomes planned before refusal; nothing was
written`. The writer undoes a write that fails part-way; when it cannot undo it completely, its
refusal says `write failed; rollback incomplete or unverifiable`.

The [hand-off list reference](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/templates/curator-handoff-ingestion.md)
gives every key of a hand-off list. A proof names a JavaScript or TypeScript test case as
`<path>::<title>`. When no name reaches the case, the proof names its exact lines with a
`line_range` locator, `{"kind": "line_range", "start": S, "end": E}`, from the first line of the
case to the line that closes it. The writer refuses a case marked `.skip`, `.todo`, `.skipIf`, `.runIf` or
`.fails`, on the case or on an enclosing suite, and names the modifier. An entry attaches to a
record that the same list authors by writing `handoff:<entry id>` where an ID is expected.
`replace_evidence: true` replaces the stored hand-off evidence of a record that the task authored;
without it, new evidence is added to the old. A `still_rejected` row can carry `repoint` to move a
`reconsider_on` link that raised the item, and whose code the task removed, to the place where
that code lives. The writer records the target before and after and refuses `repoint` on a `raise` row.

Before a producer, the Worker or the Reviewer who writes a hand-off list, sends the list to the
Curator, `agents-remember handoff-check` checks its shape and
candidate anchors against the exact tree. With a memory root it also checks
record links. It writes nothing and is not the writer: the judgment of a `reconsider_on` link that
fired stays with the writer, and a passing `handoff-check` does not authorize publication.

```text
agents-remember handoff-check handoff.json --code /path/to/code --tree <commit-or-tree> --memory /path/to/memory
agents-remember knowledge-worklist --contract /path/to/series-contract.md
agents-remember knowledge-ingest --contract /path/to/series-contract.md --list handoff.json --authorization-ref <authority>
agents-remember knowledge-validate /path/to/memory --code /path/to/code --base <memory-base> --json
```

These examples inspect or plan; add the writer's explicit `--commit` only under
its admitted write authority. `knowledge-worklist` computes the worklist of a task (`--contract`)
or of four sides that the call names: `--code` with `--base` and `--candidate` or
`--code-worktree`, and `--memory` with `--memory-base` and `--memory-candidate` or
`--memory-worktree`. Invocation details, callers and examples for every
knowledge command and tool are in the [knowledge command
reference](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/knowledge/commands.md).
The [MCP reference](mcp-tools.md#knowledge-operations) names the mounted tools.

## Maintenance commands

The same registered CLI also exposes these knowledge-maintenance operations. Preserve
the selected code/memory bindings and each operation's write authority.

| Command | Purpose |
| --- | --- |
| `knowledge-format` | Canonicalize knowledge JSON; `--check` reports formatting changes without rewriting files. |
| `knowledge-routes` | Read family routes, their state and mechanical route suggestions. A family whose routes differ from the suggestion is marked `differs`, in the text output and in the JSON output. |
| `knowledge-index` | Build or reuse the derived index of one memory tree and print its state. The tree is a working tree (`--memory-root`) or a commit of a memory repository (`--repository` with `--revision`). A duplicate record ID gives a `partial` index that names the second file. |

`knowledge-validate` with the option `--coordination-root` also lists every requirement endpoint
of the tree with the state of its owner, beside the findings. These observations refuse nothing.

## Memory merges in a sync

A sync is a call of `worktree_sync`: it merges the [official line](../concepts.md#official-line)
into a task's worktrees. When a sync merges memory, the knowledge files and the cards merge by their
structure. Markdown merges by line; sidecars merge references by number and
entries by ID; records merge by field. Reference numbers are aligned with their Markdown markers. Differently
changed authored fields remain explicit conflicts for the Curator; mechanical
anchor changes do not justify choosing between meanings. Record-conflict repair
uses the writer and advances revision above both sides. Validation continues to
refuse unresolved conflict markers. A sync that stops on a conflict names the next call in its
answer: `worktree_sync` with `resolution_action: "continue"`. Make that call after the conflict is
resolved and the resolved files are staged in the worktree (see [Worktrees](mcp-tools.md#worktrees)).

# Curator handoff-list ingestion

Curators use this reference to author their handoff-list fields, prepare writer input, admit records and record decisions. The [producer contract](curator-handoff-list.md#the-entry) owns the thirteen entry fields and the producer/Curator boundary.

## Curator-authored semantic scope

Before an entry becomes a durable invariant, the curator adds `scope` alongside the producer's
unchanged fields. It carries the invariant revision's existing semantic fields:

```json
"scope": {
  "applicability": "Retry attempts admitted by the shared deadline budget.",
  "conditions": ["The caller supplies its remaining deadline."],
  "exclusions": ["Interactive retries outside this budget."]
}
```

All three keys are required; `[]` means the curator examined that field and found no clauses.
Missing or malformed scope is `unfilled_curation_scope`, reported per entry before its knowledge
write, including in a dry run. The producer does not guess these fields. Applicability describes
where the obligation holds; conditions and exclusions describe its real boundaries. Ingest workflow,
disposition and evidence provenance are not substitutes for semantic scope.

The writer carries these fields into the invariant record (`applicability`, `conditions`,
`exclusions`). A rerun of the same list updates the record this leaf created from the same entry. A
stored invariant is changed by naming it in `invariant_id`; a record another leaf authored also
needs a `history` row about it (see [the writer's input sections](#the-writers-input-sections) below). Any list without authored
scope remains unfilled curation.

## The curator's authored keys beside the thirteen fields

The thirteen fields in [the producer contract](curator-handoff-list.md#the-entry) do not change: nine producer fields and
four curator fields. The curator adds its own keys to an entry, and a producer emits
none of them:

- `scope` is required before an entry becomes a new invariant, in the shape stated in [Curator-authored semantic scope](#curator-authored-semantic-scope).

- `admission`, `status`, `invariant_id`, `supersedes` and `proofs` are described in [the writer's input sections](#the-writers-input-sections) below.

The Curator authors a joint obligation as a `family` item in `records`, never as an entry key. Give it `title`, its own `guarantee` text, `members`, `routes` and `admission` with criterion `joint_guarantee` and its justification.

Use invariant identifiers or `handoff:<entry id>` handles for members. Do not use members' statements as the family's guarantee. Only an authored declaration creates a family; paths, directories, routes, labels and shared anchors do not infer one.

To change a stored guarantee or member list, update that family record by `id` and explain it in the leaf's `history` section.

A `changed` family row needs your judgment of its effect: give it `effect`, one of `restore`,
`clarify`, `introduce`, `strengthen`, `weaken`, `replace`, `split`, `merge`, `retire` — the same
label vocabulary as an invariant's `changed` row. The writer refuses a `changed` family row
without it, and refuses an `effect` on an `assigned`, `rerouted`, `no_impact` or `retired`
family row. You never write the family's `revision`: the writer records the family's own
revision from the record it just wrote, next to the revision of each examined member.

**An external document is a reference target of a card.** A card that rests on a document outside
the repository cites it in its sidecar's numbered references as `{"kind": "external", "document":
{"document": "<URL or name>", "version": "<revision, when known>"}}`. An external
document is never written as a repository path with a Git blob, and the writer keeps no manifest of
external sources.

The file writer does not read the entry keys `family` and `external_sources`.
Do not write them: a family goes into `records`, and an external document into a card's references.

## Where entries become knowledge files

Knowledge files in the memory repository are canonical. This section describes the files the Curator's writer creates from the entries.

Before interpreting a stored record’s format, read [knowledge use](../reference/knowledge/use.md#bind-and-complete-the-knowledge-read) for naming and opening its governing knowledge and source.

- **Records** are one JSON file each, flat per kind, named `knowledge/<kind-dir>/<ID>-<slug>.json`
  with an optional `.md` beside it. The schema is `ar-<kind>/v1`. An invariant carries `statement`,
  `applicability`, `conditions[]`, `exclusions[]`, `supersedes[]`, `revision`, `status`, `admission`
  and `origin`. It never lists its code locations, tests, families or decisions: each relationship
  is written once, on its owner's side.

- **IDs** are minted by the writer, never by a producer. The form is `<KIND>-<6 Crockford base32>`,
  for example `INV-7K3F9Q`, with the prefixes `INV FAM DEC INC ASM LIM FLM SCN DGN TRM`, and `RLZ` or
  `PRF` for entries. Some existing record IDs have eight characters after the hyphen.

- **A target becomes a `realizes` entry** in the file sidecar `onboarding/<path>.json`, with the
  fields `id`, `invariant`, `anchor`, `role` and `rationale`, and an optional `origin` of
  `{ leaf, handoffEntry? }`. The anchor is `{ locator, blob, content }`, and it omits `path` because
  the sidecar belongs to that file:
  - `locator` is `{kind: symbol, name}`, `{kind: line_range, start, end}` or `{kind: file}`;
  - `blob` is the recorded Git blob;
  - `content` is `sha256:` of the located bytes.

  The role vocabulary is `primary-authority`, `enforcement`, `propagation-persistence`, `support`,
  `presentation` and `unclassified`. `incidental` has no spelling in the file format; the writer
  writes it as `support`.

- **Test evidence becomes a `proves` entry** in the test file's sidecar, with the fields `id`,
  `invariant`, `anchor` (the test symbol) and `facet` (what the test demonstrates).

- **Origin.** Every record's `origin` names `task`, then `leaf` or `wave`, and optionally
  `handoff` (the list path and any carried evidence text), `handoffEntry` and `legacyId`.

- **Formatting.** Every JSON file is written in one canonical formatting: 8-bit Unicode Transformation Format (UTF-8), two-space indent,
  sorted keys, identified entries sorted by `id`, and one trailing newline.
  `agents-remember knowledge-format` rewrites a file into it, and `--check` only reports.

### Family routes

The family record owns its `routes`: repository directories where part of the family's code lives.
Overviews never list families, and a family is never repeated at an ancestor overview. The curator
places the routes; nothing assigns them automatically.

- **Coverage:** every `realizes` entry of every member lies under at least one route. **Non-empty:**
  every route contains at least one such entry. `proves` entries do not count.

- **Place routes as deep as makes sense.** Several local routes are right when the family's code
  lives in several subtrees. One broad route such as `mcp/` over everything passes the rules
  but is the wrong placement. The repository root route is spelled `.`; it is a route only when a
  family genuinely has no narrower home, such as a realization in a file at the root.

- **Family route states:** `unrealized_family` means no member has a realization yet, so Non-empty
  is waived. A live family with `routes: []` violates Coverage. A retired family is exempt.

- **A route the change adds must be a directory of the code tree.** A route carried from the base
  whose directory is gone is a route-maintenance finding.

Before using a family-route finding to decide whether writing, merging or publication may proceed,
read [worklist and gate response blocks](../reference/curator/worklist.md#worklist-and-gate-response-blocks)
and use the returned judgment and each finding's declared treatment.

- `agents-remember knowledge-routes <memory root> --code <code checkout>` shows each family's routes,
  its route state and a mechanical suggestion labelled `mechanical`. The suggestion starts from the
  directories that hold the realizations and collapses sibling directories into their parent only
  when that parent holds nothing but family code. It proposes `.` only when a realization file lies
  directly at the root. It is a starting point, and the command never writes a route.
  `agents-remember knowledge-validate` runs the rules.

## The writer's input sections

The procedures below call the base code tree B and the candidate code tree C.

`agents-remember knowledge-ingest` and the mounted `knowledge_write` use the task's leaf
enclosure contract and the same curator file writer. Repository foundation work enters through
the user-started Architect's regular task under [the repository foundation detail](../reference/curator/repository-foundation.md), with this same writer and no taskless authoring session. The writer requires the knowledge layout marker
in the memory tree. The producer's thirteen fields do not
change: everything below is the **curator's**.

Before a leaf run, compute and supply its persisted worklist. The writer refuses a row that
cannot govern a changed invariant revision or family guarantee and says to name the `changed`
row again. It refuses an itemless `still_rejected`, `planned:` or `hunk:` subject; a record
first authored in this leaf needs no row unless an item names it. A `raise` needs no item;
a wave or crossing owner has no leaf worklist. A refused run lists no records, entries, rows
or carried work as done: its count is planned and nothing was written.

**A `raise` the command cannot publish.** `knowledge-ingest` writes a raise's question to the
leaf's task document through Model Context Protocol (MCP) authority settings it trusts. When it has none — no trusted
tool-server settings in the user config folder, the checkout guard refusing an undeclared run from the
primary checkout, a settings file it cannot load, or settings for another coordination root — it
refuses the WHOLE batch, writes nothing and publishes no question. Its text names the cause in one
fixed clause and the route that works: send the same hand-off through the mounted `knowledge_write`
with `contractPath` (absolute), `handoffPath` (absolute), `handoffName` and `authorizationRef`, then
preview and `commit: true`. `--config` is not an option: no route reads it.

For identical changes at uncovered paths, judge the representative once: the row names all
of the grouped paths and their candidate blobs. Two card-bearing paths keep their own items,
and covered paths are never grouped. Equal copies declared by the candidate repository's
generated-copies.json need no unexplained row; a hand-edited copy retains its item, which names
the canonical file. Ordinary non-generated files keep their obligations.

A `link_target` item is open while the candidate still holds the broken target key: restate
or remove that link through the writer. No history row answers it. A reconsideration
`refresh_not_current` candidate is answered by a `still_rejected` row that names the item in
`items`, optionally with a `repoint` for where the code lives now; it closes once the link is
current at C. A stale reconsideration refusal is cleared when you recompute
the worklist and judge its current item.

Record a rejected simplification or workaround as a decision whose chosen option is the
current design, with the rejected alternative and its reason, not as a gap or later work.

The writer reads the list you already have, or an object with three sections:

```json
{ "entries": [ "<the producer's entries, with the curator's keys>" ],
  "records": [ "<curator-authored records of any kind>" ],
  "history": [ "<the leaf's judgment rows>" ] }
```

### Invariant entries

Each entry with a `target`, an `admission` or an `invariant_id` authors one invariant
record from its verbatim `statement`. An entry with none of the three names no record: it is
written only when a `records` item names it in `entry` (its evidence then lives in that record's
`origin`). Otherwise the writer refuses the run, and the curator either attaches the entry to a
record or keeps it task-local and leaves it out of the list. `proofs` on such an entry are refused.

- `scope` (as above) gives `applicability`, `conditions` and `exclusions`. It is required for a new
  invariant.

- `admission` is `{criteria, justification}` and is required for a new invariant.

- `status` is optional. A new record starts `proposed`.

- `invariant_id` names a stored invariant, or `"handoff:<entry id>"` authored by this document.
  The authoring entry runs first regardless of list order, and the attaching entry's statement
  must equal it. Without the key, a new invariant is created, or the leaf's same entry is reused.

- `supersedes` names the invariants the new one supersedes: an `INV-…` ID, or the `id` of an entry
  in this list.

- Each `target` becomes a `realizes` entry in `onboarding/<path>.json`. The role `incidental` is
  written as `support`.

- `proofs` is `[{ "test": "<path>::<name>", "facet": "<what this test demonstrates>" }]`, and each
  item becomes a `proves` entry in the test file's sidecar. `test` may also be `{path, symbol}`.
  In JavaScript/TypeScript `.test.` or `.spec.` files, a plain-string `it` or `test` case
  has the enclosing `describe` titles and its own joined by ` > `; a unique bare title also
  resolves. Hand-off evidence names it as `<path>::"<title>"`.

- **A case that runs but that no name reaches is proved by its lines.** For an `it.each`, a `for`
  table, a title bound twice, or a case under a computed title (its own or an enclosing
  `describe`'s), write the case's whole extent instead:
  `"test": {"path": "<path>", "locator": {"kind": "line_range", "start": S, "end": E}}`.
  The range must be exactly one case's extent: its first line to the line that closes it (the outer
  call, never the inner `it.each([...])`). The writer pairs the proof with the evidence quoting the
  case's own title, bare or behind its plain-titled `describe` titles; an expanded or interpolated
  title does not pair. A writer refusal for an unresolved case name offers the exact range.

- **A case that does not run proves nothing.** `.skip`, `.todo`, `.skipIf`, `.runIf` and `.fails`
  prove no invariant, and no line range reaches them either: the refusal names the modifier, read on
  the case and on every enclosing `describe`. Prove the invariant with a case that always runs.

- `replace_evidence: true` replaces `origin.handoff.evidence` when this owner authored the record,
  including an empty replacement; without it, new evidence is appended.

- `evidence` is stored in the record's `origin.handoff.evidence` when this leaf authored the record.
  A record another leaf authored keeps its origin exactly: the run then needs a `history` row about
  that record, and the writer appends the evidence to that row's `reason`. Without the row the run is
  refused.

- A rerun removes the entries this leaf wrote earlier from the same entry that the list no longer
  names, and reports them `removed`. Entries another leaf wrote are never removed. The report lists every
  test the evidence names, as a test ID `path::name` or as a path plus symbol (`path -k name`):
  - `proof_written` when `proofs` carries its facet;
  - `needs_facet` when it resolves but has no facet yet. The report offers the statement as a draft;
    the facet you author says what this test demonstrates, not the invariant's statement;
  - `unresolvable` when it names no test at C, or names a test file without a test in it.

- **Proofs are shown and counted.** `knowledge_read`'s `invariant` and `family` views name
  the `proofs` of the invariant or of every member (ID, kind, state, location). The curator checklist lists
  the invariants without any proof, with the tests their recorded evidence names; the list is
  information, not a gate. A record's stored evidence text sits in `origin.handoff`. When the
  Curator's pass touches such a record, turn each resolvable test that the evidence names into a
  proof through `proofs`, with an authored facet. Report any test it names that cannot be resolved
  as `unresolvable`.

### Other authored records

A record item is:

```json
{ "key": "<local handle>", "kind": "decision", "entry": "<the entry it came from, optional>",
  "id": "<a stored ID to update; omit to create>", "slug": "<display slug, required to create>",
  "fields": { "<the kind's own fields: status, admission, context, alternatives, links …>" } }
```

- `kind` is one of `invariant`, `family`, `decision`, `incident`, `assumption`, `limitation`,
  `failure_mode`, `scenario`, `diagnostic` or `term`.

- `fields` never holds `id`, `schema`, `origin` or `revision`.

- The `entry`'s evidence is stored in the record's `origin`.

- A link target may be `{path, locator}`, and the writer resolves the anchor.

### History rows

A history item is `{subject, disposition, reason, items?, …}`:

- `items` names the worklist item IDs the row answers. Leave it out: the writer then lists the
  items of the leaf's persisted worklist that the row answers. Name an item only where a row kind
  below asks for it.

#### Invariant cover fields

An invariant row adds `covers`, `effect` and `because`. Each cover is one of:

- an entry ID, re-anchored at C with its own locator;
- `{id, locator}`, re-anchored at a new locator;
- `{id, rationale: "<text>"}`, which also replaces a realization entry's rationale in place (the
  entry keeps its ID); use it when the re-anchored code no longer does what the old rationale
  says. It may stand beside `locator`. A proof entry has a `facet`, not a rationale;

- `{id, remove: true}`, which removes the entry;
- `{handoff: "<entry id>"}`, which covers every entry this list wrote for the subject.

#### Preserve changed-invariant rows

An invariant whose record this leaf changed is governed by its `changed` row (converted memory).
While the record's revision differs from the parent line's, the closeout and a recorded landing
are refused if a later `no_impact`, `moved` or `extended` row about it is the leaf's latest row
(the gate names the open `touched_invariant` item; the validator reports `R09-history-rows`,
"replaces this leaf's changed row"). This matters once the leaf has closed out without being
integrated: its rows then go to its next attempt file, and the latest row about a subject
governs.

A row named again replaces the earlier row, and its covers replace the earlier row's. So the
row named again must name again every cover the earlier row named, and may add covers for new
entry work. If a cover is left out, the gate reopens the item ("does not cover …").

A `changed` row named again at an unchanged revision may correct `effect`, `because` and
`reason`, and may carry covers for entry work (re-anchor, add, remove, revise a rationale); it
may not change the revision or stand for a second change of the statement. There:

- to correct the `effect`, `because` or `reason` of a change the leaf already made, name the
  `changed` row again with the corrected values and with every cover the earlier row named.
  The record is not edited and its revision stays; the writer accepts this only for the
  revision step the earlier `changed` row made;

- to re-anchor, add or remove an entry of that invariant, name the `changed` row again, with
  its effect and because, with every cover the earlier row named, and with the entries
  worked on as covers. To move an entry to another file, write a `moved` row with the `path`
  cover first, then name the `changed` row again in a second run, with every entry of the
  invariant that the leaf touched as covers: it replaces the `moved` row and covers the moved
  entry where it now is;

- to change the record's meaning again, edit it as usual and write a new `changed` row. The
  revision goes up once more, so the leaf holds one `changed` row per step.

#### Family judgments

A family row adds `examined`, the member IDs the curator examined. A family has one governing
row, the leaf's latest row about it, and that one row answers every item about the family:

- its `reached_family` item, through `examined` (every member, at its current revision);
- each `family_route_condition` item, through its disposition, which must be `rerouted`,
  `assigned`, `changed` or `retired`, never `no_impact`.

So choose the disposition for everything the leaf does to the family:

- `changed` when the leaf changes the family's guarantee, also when it assigns or reroutes
  routes in the same leaf. A `changed` row answers the route conditions too; say in the reason
  what was done to the routes;

- `assigned` or `rerouted` when only the routes change;
- `no_impact` when the record is as it was.

A `changed` family row needs your judgment of its effect as [the authored-key section](#the-curators-authored-keys-beside-the-thirteen-fields) states above.

While the family's guarantee differs from the parent line's (the item's facts say
`guarantee-changed`), a row of another disposition does not answer the family: the gate holds
its `reached_family` item open, and once the leaf has closed out without being integrated the
validator reports `R09-history-rows` for a later `no_impact`, `assigned` or `rerouted` row that
would replace the `changed` row. Name the `changed` row again, with `examined`. In the same
way a later `no_impact` row after an `assigned` or `rerouted` row reopens the route condition:
name that row again with its disposition.

#### Onboarding judgments

An onboarding row (converted memory only) has the subject
`onboarding:<source path>` or `onboarding:<route>/overview` (`onboarding:overview` for the
root route). Its disposition is `updated` when the card or overview was brought up to date —
which needs a counted change of the card between the memory base and the candidate (its
Markdown, or its sidecar's `references` beyond mechanical anchor fields; a
`realizes`/`proves`-only edit is not counted, and the writer refuses an `updated` row without
one) — or `no_impact` when the reviewed file needs no change, and nothing else. The hand-off
names the subject, the disposition and the reason; the writer binds the row to every source
file the item covers, with each file's blob at the code candidate (`sources`). When a covered
file changes again after the row was written, the item reopens: name the row again. Rows without `sources` remain valid in closed history files, but answer nothing in open ones.

#### Membership judgments

A membership row (converted memory only) records that a new invariant belongs
to no family. Its subject is `membership:<invariant ID>`; its disposition is `no_family`; it
adds `considered`, the family IDs examined (which may be empty), and nothing else. It answers
an `unassigned_invariant` worklist item: a live invariant of the candidate that no family lists
and that is new, or that a family of the memory base listed. The row must be at the invariant's
current revision; listing the invariant in a family removes the item. The writer refuses the row
for an invariant that a family of the produced tree lists.

#### Other record judgments

A record row (converted memory only) records a judgment about a decision,
incident, assumption, limitation, failure mode, scenario, diagnostic or term record. Its subject
is the record's ID; its disposition is `changed` (the record's meaning changed, with its
`effect`), `retired` (its status became `retired`, with effect `retire`), or `no_impact`,
and it adds `effect`/`because` like an invariant row. It answers a `touched_record` worklist
item: such a record of the memory base whose JSON or Markdown differs in the candidate (a new
record raises nothing; the writer's own `under_reconsideration` marking for a `raise` row
raises nothing). Evidence for a record another leaf authored is stored in this row.

#### Planned effects

A planned row (converted memory only) answers a `planned_untouched` worklist item: an
effect the leaf's task document declared in `expectedKnowledgeEffects` that no row delivered.
Its subject is the item's, `planned:<declared subject>#<effect>`; its disposition is
`realized_elsewhere`, `deferred` or `dropped`; it adds `ref` and nothing else:

- `realized_elsewhere`: `{row: "ROW-…"}` or `{invariant: "INV-…"}`, which must exist;
- `deferred`: `{requirement: {task, packet, id, version}}` or `{leaf: "<leaf id>"}`;
- `dropped`: `{decision: "<at>"}`, the `at` of exactly one decision entry in the leaf's task
  document; the writer asks the task owner and refuses a decision that does not resolve.
A `no_impact` row about the declared invariant does not answer the item; a `changed` row with
the declared effect (or, for `retire`, a `deleted` row that retires it) delivers the effect and
raises no item.

#### Changes with no invariant

A no_invariant row (converted memory only) answers an `unexplained_hunk` or
`unexplained_file` worklist item in a covered file: a change no entry covers that carries no
invariant. Its subject is the item's `facts.row` (`hunk:<item id>`, or the item's own
`file:<path>@<blob>`); its disposition is `no_invariant`; its `reason` says why, and it adds
nothing else. The other answers need no such row: attach a `target` to a stored invariant
(`invariant_id`, not a retired one) or author a new invariant over the change. A delete-only hunk
admits only `no_invariant`. An item in an uncovered file is answered by the file's onboarding
trace (its current `onboarding:<path>` row; a counted change of its card alone never
answers), not by this row.

#### Reconsidering a rejected alternative

A reconsideration row (converted memory only) answers a `reconsideration_candidate`
worklist item: a target a decision's `reconsider_on` link names changed in this leaf. Its subject
is the item's, `reconsider:<DEC-ID>#<alternative index>`; it carries the common fields and nothing
else. The disposition is one of:

- `still_rejected`: the rejection still holds; `reason` says why, against the item's facts. The
  writer refreshes the links whose trigger fired to what you judged: a requirement link is
  re-pointed to the item's `latestApproved` version, and an anchor link is re-anchored at C (a
  line range is mapped through the leaf's diff). The decision's revision goes up once in the
  leaf, like any change to its links. So the same condition is not raised again, while a later
  change to the anchored code, in this leaf or a later one, is raised as a new item. A link
  anchor whose base target has no image at C is re-pointed through `repoint` (below), or raises
  instead. A link anchor that went stale (`anchor_stale`) is raised too, and
  `still_rejected` re-anchors it. When a newer version is approved, or the anchored code changes
  again, after you refreshed a link, rerunning the same row is refused: answer the new item by
  naming its ID in `items`;

- `raise`: the alternative goes to the developer. The writer sets the decision's `status` to
  `under_reconsideration` and appends a question to the leaf's task-document `openQuestions`
  through `task_doc`, keeping every existing question. When the question cannot be written, the
  `raise` is refused and the item stays open.

**`repoint`: where the code lives now.** A `still_rejected` row may carry
`repoint: [{link, path, locator}, …]`, one item per fired link of the answered alternative. `link`
is the link's 0-based position in the decision's `links`, named once. `path` and `locator`, in the
hand-off locator form, say where the code the alternative's `reconsider_when` is about lives at C.

A re-point must name a fired link of the answered alternative, and the fired base target must be known with its path. The new target must resolve at C.

- For an `anchor` or `anchor_stale` trigger, the fired base target must have no image at C. The leaf's current link must still be that base target or already this same re-point. A target that still maps is refreshed instead; a re-authored current target is refused.
- For a `refresh_not_current` trigger, name the answered item's ID in the row's `items`. The current link must hold the earlier refresh that the item identified, or already this same re-point. A current base target or a target from an intervening answer is refused; recompute the worklist or raise as the refusal directs.

An identical re-point rerun preserves the same bytes. `path` and `locator` alone do not establish continuity with an earlier refresh; the recorded target identity must agree.

The row records `before` and `after` with their paths. Its `reason` says, against the new target,
why the condition is not met. Other links whose triggers fired on the answered item follow the
[`still_rejected` refresh rules](#reconsidering-a-rejected-alternative) above. Links whose triggers
did not fire keep their targets. `relation` and `alternative` never change.

When the re-point bumps the decision's revision, order the history rows as follows:

1. List the reconsideration row.
2. Follow it with a `changed` record row about the decision:
   `{ "subject": "<DEC-ID>", "disposition": "changed", "effect": "clarify", "reason": … }`.
   A record row takes the revision stored when it is read. This row answers the `touched_record`
   item the new revision raises.

**When the condition may be met, raise.** If the reason leaves open whether the condition may be
met, the row must be a `raise`, not `still_rejected`:

1. Raise the alternative to the developer. A `raise` carries no `repoint` and leaves the links as
   they are. Nothing re-points, keeps a rejection or answers an item on its own.
2. Record the developer's answer against the question's key.
3. Write the reconsidered revision in `records` — a `changed` row whose `reason` cites the answer —
   and point the links at the code the answer names.

A `route:` target fires when its directory had files at B and none at C (`route_absent`),
or a rerouting family row names that route or a route inside it. A decision superseded in
either memory side is never raised; its successor carries its own links. Never reverse a
decision yourself: `raise` it. When alternatives change without restating links, the writer
moves each stored reconsideration link by the exact unique old option text. If that text
is missing or ambiguous, restate `links`; never rely on an ordinal to retarget it.
A same-leaf `still_rejected` refresh with no fired item restores a current base link.

### Local handles

`"handoff:<key>"` names the record this document authors under that entry `id` or record
`key`. Use it wherever an ID goes: link targets, `members`, `subject`, `examined` or `supersedes`.

### Values filled by the writer

- IDs are minted, and a rerun reuses the ID recorded in `origin.handoffEntry`.

- Anchors are resolved at the leaf's code candidate C, with `blob` and `content`. A symbol must bind
  exactly once.

- `revision` goes up by one when a record's meaning differs from the memory base. `admission`,
  `status` and `origin` are not meaning.

- `origin` records the task, the leaf or wave, the list, the entry and the evidence.

- Each history row gets its row ID and the revision of the invariant, family, membership invariant
  or record the row is about. It also gets each examined member's revision and each cover's
  `before` anchor (from the memory base) and `after` anchor. The `after`
  anchor is written into the entry in the same run.

- An onboarding row gets its `sources`: one `{path, blob}` per covered source file at the code
  candidate (`absent` for a deleted file), read from the code tree the writer runs against. A
  membership row gets the invariant's current revision; a record row the record's.

- Every file is written in the canonical formatting.

- Every row of the leaf's history file must still agree with the result: covered anchors, the
  invariant's or family's own revision and each examined member's revision. A row a later run
  contradicts refuses the run until the row is named again in `history`, so the writer rewrites
  it. When the family
  record changes again (also by a sync that brings in another leaf's family change), name the
  family row again — a `changed` row with its `effect` again — so the writer records the current
  revision.

### Checks performed by the writer

- The writer runs the knowledge validator over the resulting tree.

- Any problem refuses the whole run. The report names every problem and nothing is written.

- Report-only findings are listed but never refuse. The family route rules (route
  directory, Coverage, Non-empty) are reported inside the writer too, so a leaf can place routes
  across several runs; the closeout and every other commit route still refuse them. The same holds
  for these five refusing rules (`R13.3-governs`, `R27.2-existing-record`,
  `R04.1-carried-route-absent`, `R04.4-unrealized-family`, `R22.6-carried-stale`): inside the
  writer they report, and every commit route refuses.

- Without `--commit`, the run plans, validates and reports, and writes nothing.

- `--authorization-ref` must not be blank. The report records it.

## Admission of new records

A record is **new** when its ID is absent from every comparison base and it is not an export.
At a merge, compare every parent; without a base, every record that is not an export is new.
An invariant or family is **exported** only when its `origin.legacyId` derives its ID through
the conversion’s record-ID derivation. Merely setting `legacyId` does not make it exported;
a decision is never an export.

Every **new** invariant, family and decision record states which admission criterion it meets, with
a one-sentence justification: `"admission": {"criteria": [...], "justification": "..."}`. The
knowledge validator enforces it on converted memory, inside the writer and at every commit route. A
code change alone is never an admissible reason: most knowledge is local and stays prose.

| Record | Criterion | Meaning |
| --- | --- | --- |
| invariant | `spans_locations` | realized in more than one file (checked: its `realizes` entries sit in two or more files) |
| invariant | `guarded_by_test` | at least one proof entry names it (checked: a `proves` entry) |
| invariant | `family_guarantee` | needed to state a family's guarantee |
| invariant | `prevents_costly_mistake` | guards a plausible, costly error; the justification names the error |
| family | `joint_guarantee` | the members together promise something none promises alone |
| decision | `real_alternatives` | at least one alternative was seriously considered |
| decision | `constrains_future_work` | the choice binds later work |

- **A new record is refused** when it carries `legacy-unassessed`, has no criterion, or has a
  justification made only of task, leaf, requirement, step or section
  references, developer ruling IDs (`D14`), commit hashes, dates and provenance words ("Per ruling
  D14", "Added in commit a4eba7b7", "L43's acceptance criteria": the reason must be stated in
  words), or claims `spans_locations` or `guarded_by_test` that its entries do not support. The
  refusal names the record and the criterion. This admission rule does not refuse a retired record.

- Before judging admission findings for any other record (exported, already in the base, or
  retired), read [worklist and gate response blocks](../reference/curator/worklist.md#worklist-and-gate-response-blocks)
  and use the returned judgment and each finding's declared treatment.

- **Local stays local.** A statement that meets no criterion is not a record. It stays prose in the
  onboarding Markdown, under "Boundaries".

- **Demotion** moves the statement into the card's onboarding prose and retires the record with a
  `deleted` history row, effect `retire`. A retired record's file stays.

- Only presence, shape and the two checkable criteria are mechanical. The reviewer judges whether
  each justification is plausible.

**Admitted:**

- "Landing pairs code and memory commits", `spans_locations`: "Realized in
  `mcp/src/agents_remember/worktrees/modules/integrate.py`, which refuses an unpaired landing, and
  in `mcp/src/agents_remember/worktrees/ledger_projection.py`, which derives the ledger rows."

- The decision to give a family several local routes, `real_alternatives`: "One owning route per
  family was weighed and rejected, because a family spanning subtrees collapses to the repository
  root."

**Refused:**

- "Every source-content expansion carries admission", with the justification "introduced by L43":
  the justification is only a leaf reference.

- An invariant claiming `guarded_by_test` with the justification "test_direct_landing covers it",
  when no `proves` entry names it: the claim is not supported. Add the proof through `proofs`, or
  claim a criterion that holds.

**Local, no record:** "Execution is synchronous and journaled" describes one file. It stays prose in
the card of `mcp/src/agents_remember/worktrees/direct_landing.py`.

## Decision records

A decision record keeps a choice that **keeps governing code**, with the alternatives that were
weighed, so a rejected design can come back up when its grounds change. On converted memory it is a
`records` item of `kind: decision`. Its `fields` are:

- `context`: the situation that forced a choice.

- `alternatives`: at least two, in a fixed order. Each is `{option, status, reason,
  reconsider_when?}`, with `status` one of `chosen`, `rejected` or `deferred`:
  - **exactly one** is `chosen`;
  - every `rejected` or `deferred` alternative states `reconsider_when`: the condition under which
    to look at it again. It is prose for people; nothing evaluates it.

- `consequences`, `decider` (who decided, for example the developer and the ruling), `supersedes`
  (the `DEC-…` IDs this decision replaces) and `admission` (`real_alternatives` or
  `constrains_future_work`).

- `status` starts `active`. `superseded` is **never stored**: a decision is superseded when a
  later decision's `supersedes` names it, and readers derive it. `under_reconsideration` is set
  only when a reconsideration is raised to the developer.

- `links`, each `{relation, target, alternative?}`:
  - `explains`, `constrains` and `motivated_change_to` name what the decision **governs**. The
    decision is read from each target, so link every record, route (`route:<dir>`), anchor
    (`{path, locator}`, resolved by the writer) or requirement it governs. A decision with none of
    these links is reported: nothing would show it.
  - `reconsider_on` names a target whose change should reopen one alternative, with `alternative`
    set to that alternative's index in `alternatives` (0 is the first). The alternative must be
    `rejected` or `deferred`. This link, not the prose, is what brings the decision back to the
    worklist when the target changes. Link the record whose change would meet the
    `reconsider_when`, for example the assumption it rests on.
  - A requirement target is `{task: {repository, path}, packet, id, version}`, where `path` is the
    task directory under `tasks/<repository>/`. The writer asks the requirement owner to resolve it
    and reports each one in `requirementEndpoints` as `resolved` or `unresolved`, with the owner's
    reason. An unresolved endpoint is written as authored and never refuses the run.

- Name the ruling or requirement the decision came from in the `entry` it is attached to: that
  entry's `evidence` is stored in the record's `origin.handoff`. `origin.task` is filled in.

The validator refuses a decision with fewer than two alternatives, with no chosen alternative or
several, with a rejected or deferred alternative that has no `reconsider_when`, with a stored
`superseded`, with a `reconsider_on` link to an alternative that does not exist or is `chosen`, or
with a linked alternative moved to another index against the memory base.

**Lifting decisions at closeout.** Decisions are born in planning: developer rulings, requirement
packets and the task's decision log. At closeout the curator lifts into records the decisions that
**keep governing code**:

- a developer ruling or a requirement packet's choice between real designs that constrains code or
  knowledge beyond the leaf;

- with the alternatives the ruling or packet itself names, their reasons and their reconsider
  conditions. Do not invent alternatives nobody weighed; a choice without a real alternative is not
  a decision record.

Decisions that matter only within the task stay in the task: sequencing, which leaf does what, a
fixture choice, a rebind of a leaf to a new packet version. The decision authority stays with the
developer. A curator records a decision and links it; it never reverses one. A record never replaces
the task's decision log.

**Hypothetical example.** A retry-policy decision chooses "use the caller's remaining deadline"
and rejects "use a fixed retry count", because attempts can have different durations.
Reconsider the rejected alternative when every attempt has a fixed duration and the caller requires
an exact attempt limit. The decision `constrains` the retry routes, is `motivated_change_to` the
governing retry-policy requirement packet, and has `reconsider_on` alternative 1 to the assumption
"retry attempts can have different durations".


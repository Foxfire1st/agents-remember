# Template — Curator Hand-off List

The **producer's output shape** for the requirement-shaped items a leaf's builder and reviewer already
produce. The worker emits its list, the reviewer emits its own in the same shape, and the orchestrator
hands the curator **that same list**, unparaphrased, as data. `roles/worker.md`, `roles/reviewer.md`,
`roles/orchestrator.md` and `roles/curator.md` own the seats' sides of this contract.

**The contract's authority is the owning seat's schema note, the increment's own
*260915-KS-curator-handoff-list-schema.md*, at revision 1** — its thirteen fields (nine producer,
four curator), its revision-1 rules 1–9, and the reasons the rules exist are authored there once. This file transposes revision 1
into the shape a producer writes, with **one stated supersession**. Revision 1's rule 1 makes `target` a
list of `{path, locator, governing_route}`. Here each `target` element is
`{path, locator, governing_route, rationale, role}`, and **this file's element shape supersedes rule 1's**
(the rest of rule 1, that `target` is a list, stands). The supersession carries the developer ruling of
2026-09-28T13:05+02:00: every realization target carries an authored rationale, and the writer refuses a new
target without one (see *Realization rationale and role* below). A producer following revision 1's element
shape alone produces a refused entry. On that element shape this file wins; everywhere else, where the two
disagree, the schema note wins. The
worked example that priced revision 1 is the 41-entry fixture beside it, the increment's
*260915-KS-curator-handoff-fixture-L23-rev1.json* with its note. Both live with the increment that
produced them, outside this repository; the names are what a reader can look up.

Emit it as **JSON, either as a fenced block in the report or as a file beside it** — one document per
list, so the curator ingests a list rather than reading a table.

## The entry

**Thirteen fields, and each has a job: nine are the producer's and four are the curator's.** Ownership
is the whole point of the split — the producer answers *what is true, and where*; the curator answers
*what the record does about it*. A producer that fills a curator field has made the decision the
curator exists to make, and a curator that re-derives a producer field has destroyed the evidence it
was supposed to compare against.

The nine the producer supplies are `id`, `statement`, `kind`, `target`, `found_at`, `disposition`,
`disposition_source`, `evidence` and `authority`; the four the curator decides or verifies are
`resolution`, `validated_at`, `record_action` and `supersedes`. Every row below states which of the
two owns it, and the shape beneath the table carries exactly those thirteen keys — so a producer can
count the fields it owes rather than infer them. (`disposition_source` is a producer field: it says
where the *producer's* verdict came from, which only the producer knows.)

| field | who | what it carries |
| --- | --- | --- |
| `id` | producer | the entry's stable identity, in the producer's own spelling — `KS-R22@v1 §3.2`, `A-1`, `S-4`, `item 32`. It is the curator's handle for the entry and the thing a later revision names in `supersedes`. |
| `statement` | producer | **what is true**, as one sentence: the invariant, not "the case passes" but what the passing case asserts about the world. |
| `kind` | producer | `clause` \| `finding` \| `carried-defect` \| `decision` \| `measurement` — which of the five shapes it came from, so a reader knows what its `disposition` vocabulary means. |
| `target` | producer | **where it applies** — a list of `{path, locator, governing_route, rationale, role}`. This is the attribution: an invariant is a citation beside code. Each target's `rationale` is its own authored explanation of why that place carries the obligation, and the writer requires it; `role` is optional. See *Realization rationale and role* below. |
| `found_at` | producer | **where it was evidenced** — a list of `{path, locator, commit}`; it may be plural, because one invariant is often realized in several places while applying in one sense. |
| `disposition` | producer | the producer's own verdict in its own vocabulary (`satisfied`, `partial`, `refused`, `fixed`, `recorded`), carried verbatim — the curator's job is not to re-derive it but to decide what it means for the record. |
| `evidence` | producer | the case name, the command, the report path — the pointer that would let a reader check the claim. |
| `disposition_source` | producer | where the verdict came from, when it did not come from the producer's own list. |
| `authority` | producer | the governing route and the task document the entry descends from, so the record can be read back without the list. Absent when the list's own document attribution is the whole of it. |
| `resolution` | curator | the **resolved** target: the actual extent the locator names, with the moment it was resolved. |
| `validated_at` | curator | when the resolved target was last proved to hold — the field that goes red when code moves. |
| `record_action` | curator | `add` \| `revise` \| `supersede` — the curator's decision, which is the whole point of it being the curator and not the producer. |
| `supersedes` | curator | the `id` this entry replaces, when it replaces one. |

**Every entry carries all thirteen keys, and every curator field is `null` in the producer's hands.** A
producer emits no `resolution`, no `validated_at`, no `record_action`, no `supersedes`. `kind` may be an
enum; **`disposition` may not** — it is free text carried verbatim.

## Shape

```json
[
  {
    "id": "<the producer's own stable spelling>",
    "statement": "<what is true, as one sentence>",
    "kind": "clause | finding | carried-defect | decision | measurement",
    "target": [
      {
        "path": "<repo-relative path>",
        "locator": { "kind": "symbol", "value": "<symbol>" },
        "governing_route": "<route; omit the key when no route governs this target>",
        "rationale": "<why this place carries the obligation, in the author's words; omit only when the entry states realization_rationale>",
        "role": "<primary-authority | enforcement | propagation-persistence | support | presentation | incidental | unclassified; omit the key to inherit the entry's realization_role>"
      }
    ],
    "found_at": [
      { "path": "<path>", "locator": { "kind": "line_range", "start": 1, "end": 1 }, "commit": null }
    ],
    "disposition": "<the producer's verdict, verbatim>",
    "disposition_source": null,
    "evidence": "<case name | command | report path — the checkable pointer>",
    "authority": { "governing_route": "<route; omit the key when no route governs the entry>", "task_document": "<task document>" },
    "resolution": null,
    "validated_at": null,
    "record_action": null,
    "supersedes": null
  }
]
```

`target[].locator` is `{kind: "symbol", value}` \| `{kind: "line_range", start, end}` \|
`{kind: "file"}`, and a `target[].locator` is **required**: a target that names a path and no construct
is refused as an omission rather than read as a whole-file citation, because "the whole file is the
place" is a claim a producer has to make explicitly. Line ranges are **one-based and inclusive**, so
`{start: 0}` names nothing and is refused with that reason. `found_at[].locator` may additionally be
**`null`** — "the source says it was found, not where" is information, and different from an empty
list.

### Realization rationale and role: one authored explanation per target

Each target becomes one realization claim, and that claim stores the author's own explanation of what
this place does for the obligation. The writer never generates one.

- **`target[].rationale` is required.** Write why *this* place carries the obligation, specific to the
  construct its locator names. Two constructs cited by one entry usually do different work, so give
  each its own explanation. "The statement is realized at <path>" is not a rationale: it restates the
  path and explains nothing.
- **`target[].role` is optional, and it is one of the shipped role words:** `primary-authority`,
  `enforcement`, `propagation-persistence`, `support`, `presentation`, `incidental` or `unclassified`.
  There is no `absent` value: to state nothing, **omit the key**. An omitted `role` inherits the entry's
  `realization_role`; only when neither level states a role is the claim stored `unclassified`. Write
  `unclassified` yourself only to say that nobody assessed this place although the entry states a role.
  Any other word, including the string `"absent"`, refuses the entry with `realization_role_unknown`; it
  is never stored and never replaced by the entry's role.
- **Entry-level `realization_rationale` and `realization_role` are an explicit default.** They apply to
  every target of the entry that does not state its own, and a target's own value always wins. Use the
  default only when one explanation is genuinely true of every place the entry cites. The same role
  vocabulary applies to `realization_role`.
- **Each value is one JSON string.** A list, object, number or boolean in `rationale`, `role`,
  `governing_route`, `realization_rationale`, `realization_role` or `authority.governing_route` refuses
  the entry with `realization_value_not_text`, naming where it was written; the writer never renders
  another value as text.
- **A target with no rationale at either level refuses its whole entry** with
  `realization_rationale_absent`, and a rationale longer than 20000 characters refuses it with
  `realization_rationale_too_long`. Each refusal lands before any identity is minted or any row is
  written, names the entry and every offending target by position, path and locator, and leaves the
  other entries of the list to commit. Correct them and run the list again.
- **These checks apply to entries that would write new realizations.** An exact retry of an entry the
  candidate already committed writes nothing, so it is not checked again: it replays, and it can publish,
  exactly as before. That includes entries committed before targets carried a rationale. Changing the
  content of an already committed entry is still refused as `allocation_content_conflict`; author a
  successor entry instead.

The producer states the rationale when it emits the target, because it knows why it named the place.
A curator who receives a target without one authors it from the evidence before ingest. That is
supplying a missing explanation, not re-deriving a producer field. A target's own `rationale` and
`role`, and the entry-level defaults, are part of the entry's retry content: changing any of them under
an already-minted entry key is changed content, not an exact retry.

## Rule 1 — co-resolution: name where the thing lives, not where you looked

### Curator-authored semantic scope

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

The ordinary ingest carries these fields into the existing invariant revision and its retry digest.
Changing scope under an already-minted entry key is changed content, not an exact retry: author an
explicit successor with the stored invariant and predecessor revision identities. Historical records
are never rewritten or silently migrated; an old list without authored scope remains unfilled curation.

**A `target` entry is a path *and* the construct inside it, produced by one resolution act.** Resolve
the place once, in the same act that identifies the construct, and emit both together: a `symbol`
locator when the invariant is a named construct, a `line_range` when it is an extent, `file` only when
the file really is the whole of the place. A path with no construct and a construct with no path are
not two halves that compose into one citation — two independent resolutions do not compose, they
disagree, and the disagreement surfaces later as a citation that resolves to the wrong thing.

*Why the rule is stated this hard:* on a real leaf, of 40 requirement entries **20 named no path at
all**, and **6 of the 20 that did named a file that did not hold the construct** — each one a citation
that would have been recorded, resolved, and wrong. Naming where you looked (the file you searched, the
report you read) satisfies nothing here; name where the thing lives.

**A place the source names without a path is not a target.** A class, a phrase, or a derived location
("the leaf briefs", "the mounted tool registration + tests") has no path to quote: leave it out of
`target`, or leave `target` empty and say so — never invent a path from where a search happened to land.

**`target` is a list because one requirement can apply in several places.** One defect that names two
registries, or one measurement that names four modules, is **one entry with several targets** and must
not be split: splitting it breaks `supersedes`, because the second half of one defect would read as a
new entry rather than the rest of the first, and an entry whose list is short reads as resolved when
only one of its places was touched.

**`target` is identity; `found_at` is evidence.** Prefer `symbol` in `target` — it survives a move.
A line range in `found_at` is a **snapshot of where it was when written**, which is what `commit` and
`validated_at` exist to date: a line-precise `.md` citation went stale inside the increment that
produced it. Only `found_at` is allowed to be plural *or* to move without the statement changing.

## Rule 2 — no paraphrase: the entry is carried, not rewritten

**Carry `statement`, `kind`, `disposition`, `evidence` and every `found_at` record verbatim from the
producer's own list.** Do not re-word the statement for the curator, do not normalise the disposition
into an enum, do not tighten the evidence pointer into a summary, and do not re-order the entries. A
producer that rewrites its own items for the curator destroys the evidence the curator is supposed to
compare against: the curator's job is to compare the list against the code and the record, which it
cannot do if the list it was handed is a fresh account of the same thing.

*The bar this sets, measured:* 41 entries in, 41 entries out, with `statement`, `kind`, `disposition`
and `evidence` **byte-identical** to the pre-revision list.

**What may legitimately change is spelling, and only for the two fields that are spellings.**
`id` is re-spelled when a later source names the same entry differently (`item 32` → `item 32 / A-1`) —
that is why `id` is stable identity rather than the entry's wording. `target`/`found_at` **paths** are
re-spelled to the contract's own convention. Everything else is carried.

**Read `found_at[].locator` as a producer's honest record, not a defect when it is `null`:** 13 of 58
real evidence records carried none, and none used `file`. Null locators and empty target lists are
honest encodings; a filler path is not.

## Worked examples, verbatim from the fixture

The two shapes a producer most often gets wrong are **one requirement in several places** and **a
requirement with no code place at all**. These two entries are carried from the increment's own
*260915-KS-curator-handoff-fixture-L23-rev1.json* with `evidence` elided for length. The fixture
predates per-target rationale, so its targets carry none: before ingest, each of them also needs its
authored `rationale` (see *Realization rationale and role* above).

**One defect, two places, one entry** — `target` is a list, the second place carries no
`governing_route` because none was named, and one `found_at` record has no locator at all:

```json
{
  "id": "item 12",
  "statement": "The pytest budget defaults declared in the test conftest are stale and never in effect, so the declared defaults must equal the enforced ones or the dead declaration must be removed and the live rail named once.",
  "kind": "carried-defect",
  "target": [
    { "path": "mcp/tests/conftest.py", "locator": { "kind": "line_range", "start": 73, "end": 75 }, "governing_route": "mcp/tests" },
    { "path": "pyproject.toml", "locator": { "kind": "line_range", "start": 244, "end": 245 } }
  ],
  "found_at": [
    { "path": "mcp/tests/conftest.py", "locator": { "kind": "line_range", "start": 73, "end": 75 }, "commit": null },
    { "path": "mcp/tests/test_suite_budget.py", "locator": null, "commit": null }
  ],
  "disposition": "fixed",
  "disposition_source": null,
  "evidence": "…elided…",
  "authority": { "governing_route": ".", "task_document": "260915_knowledge-substrate" },
  "resolution": null,
  "validated_at": null,
  "record_action": null,
  "supersedes": null
}
```

**A finding with one whole-file target and three evidence places** — a `file` locator is honest when
the file is the place, and the evidence side keeps the line precision the producer could afford, dated
by the commit it was measured at:

```json
{
  "id": "item 32 / A-1",
  "statement": "The frozen candidate's own test population is red — unit 2190 passed / 1 failed and integration 377 passed / 2 failed at c5a74a85, deterministically — because a later landing raised the current schema generation to 9 and left three earlier leaves' generation-bound literals pinned to the value that is now current.",
  "kind": "finding",
  "target": [
    { "path": "mcp/tests/test_knowledge_family_composition.py", "locator": { "kind": "file" }, "governing_route": "mcp/tests" }
  ],
  "found_at": [
    { "path": "mcp/tests/test_knowledge_family_composition.py", "locator": { "kind": "line_range", "start": 270, "end": 270 }, "commit": "c5a74a85" },
    { "path": "mcp/tests/test_knowledge_portable_boundaries.py", "locator": { "kind": "line_range", "start": 215, "end": 215 }, "commit": "c5a74a85" },
    { "path": "mcp/tests/test_knowledge_read_boundaries.py", "locator": { "kind": "line_range", "start": 804, "end": 804 }, "commit": "c5a74a85" }
  ],
  "disposition": "fixed",
  "disposition_source": null,
  "evidence": "…elided…",
  "authority": { "governing_route": "mcp/tests", "task_document": "260915_knowledge-substrate" },
  "resolution": null,
  "validated_at": null,
  "record_action": null,
  "supersedes": null
}
```

## The remaining encodings a producer must get right

- **`kind` has a mapping rule, because it had five values and no rule.** `clause` = a numbered clause
  of a requirement packet · `finding` = an entry a reviewer minted with a stable ID (`A-n`, `B-n`,
  `S-n`) · `carried-defect` = a defect carried forward in a plan's item list · `decision` = a ruling or
  a recorded choice · `measurement` = a report obligation with no code subject.
- **`target: []` is the honest encoding for a ruling that applies nowhere** — a report obligation, a
  decision the leaf owes, or a bare commit with no code place. Its `kind` is what says why, and the
  content lives in the statement; a filler path is what a missing rule produces. Eight of the
  fixture's 41 entries are this shape; none of them was invented into a place.
- **`governing_route` may be missing, and missing is spelled by omitting the key.** Never write the
  word `"absent"` (or any other filler) in its place: the writer reads a target's route as a path, so
  the word would author a route named `absent` and govern the anchor with it. The word is matched in any
  case once trimmed (`"Absent"`, `" ABSENT "`). Written at a target or at
  `authority.governing_route`, it refuses the entry with `realization_governing_route_absent_literal`,
  naming where it was written; `null` is read as no route. As with the other realization checks, an
  exact retry of an entry the candidate already committed is not checked again. Missing is honest:
  of the fixture's 41 entries, 3 of its 40
  real target places had no memory route at all (`system/tools.md`, the repository-root
  `pyproject.toml`, and a task-tree path), and a fourth entry's whole target list is empty. Never
  invent a route to fill the field. (An earlier revision of this line said "11 of 41", which counted
  the eight empty-`target` rulings as places and was wrong by more than three times.)
- **A memory path is written once, without the `onboarding/` prefix** — memory-root-relative, because
  two spellings of one card are two records for one file, and the prefix is a rendering detail.
- **`disposition` records the verdict the producer actually holds, and `disposition_source` records
  where it came from when that is not the producer's own list.** Never invent a verdict to fill the
  field: a verdict imported from another document is an input the curator must be able to see, and an
  unaudited verdict has already cost a master one sealed review finding.

## The curator's three authored keys beside the thirteen fields

The thirteen fields above are the producer's contract and they do not change: nine producer fields and
four curator fields, at revision 1. Three further keys belong to the **curator** — a producer emits
none of them. `scope` is required before durable invariant authoring and has the semantic shape stated
above. `family` and `external_sources` record what the curator examined beyond the producer's finding.
These two remain optional: omission is **unexamined**, which differs from examining and recording an
explicit outcome. Optional family/source coverage never makes semantic scope optional.

**`family` — the justified joint obligation and this entry's exact memberships.** A family exists only
where the curator declared one; nothing here infers a family from a path, a directory, a route, a label
or a shared anchor, because that inference is the bulk import this shape exists to refuse.

```json
"family": { "state": "member",
  "memberships": [
    { "family": "<the local key this list spells>",
      "basis": "<why this obligation shares this joint guarantee>",
      "declares": { "label": "<display label>", "version": "v1",
                    "guarantee": "<the family's own text, never its members' statements>",
                    "predecessor_revision_ids": [],
                    "family_id": "<absent for a new family; the stored identity when revising one>" },
      "family_revision_id": "<absent unless this membership joins a revision already stored>" } ],
  "retire": [ "<a stored membership identity this run can read>" ] }
```

```json
"family": { "state": "no_family", "basis": "<why no joint obligation is supported>" }
```

- **`basis` is required wherever you decide**: on every membership, and on `no_family`. The run refuses
  a blank one rather than storing an unexplained claim, and the `no_family` basis is recorded in the
  revision's own conditions so the dataset — not only the report — distinguishes a family-free
  obligation from an unexamined one.
- **One declaration per key.** The key is a local handle for one creation operation, not an identity.
  An entry that declares a key authors the family identity (unless it names a stored `family_id`) and
  its guarantee revision; every other entry naming that key **joins** it and carries no `declares`.
- **Reuse is by identity.** `family_id` must name a family the dataset holds, and `family_revision_id`
  a recorded revision; naming either without the record present is refused rather than written.
- **A changed guarantee is a successor**, declared under a **new key** that names the stored `family_id`
  and the revision it supersedes in `predecessor_revision_ids`. Re-authoring a changed guarantee under
  an already-allocated key is refused: one key names one declaration operation.
- **A retirement names a stored membership identity** the run can read, so a removal is never authored
  against a row nobody read. `memberships` may be absent when `retire` is present.
- An ordinary membership cites *this entry's own* exact revision. A successor declaration can also
  retain exact stored sibling revisions through `retain_memberships`, as below. The family and
  invariant revisions remain separate endpoints; an older membership is never rewritten.

### Retain exact siblings when adding new obligations to a family successor

Read the existing family revision through `knowledge_read` with `view="family"` and the exact
`familyRevisionId`, following its continuation when needed. A `family_member` row's
`subject.record_id` is the membership ID; its statement names the exact invariant revision. Select
the retention set deliberately. Do not infer it from all predecessor members, a latest head, labels,
or another task's allocation journal, and do not revise an unchanged invariant just to add its edge.

For example, the entry for a genuinely new paging obligation can declare the successor and keep two
unchanged siblings. This is the entry's `family` portion; the ordinary producer fields and
curator-authored `scope` still apply. Replace the labelled placeholders with IDs read from the store:

```json
"family": {
  "state": "member",
  "memberships": [{
    "family": "review-guarantee-v2",
    "basis": "The new paging obligation supports the existing review guarantee.",
    "declares": {
      "family_id": "<existing family UUID>",
      "label": "Coherent review",
      "version": "v2",
      "guarantee": "Selected intent and complete recorded family context remain reachable together.",
      "predecessor_revision_ids": ["<exact predecessor family revision UUID>"],
      "retain_memberships": [
        {"member_id": "<stored membership UUID for sibling A>", "basis": "Its unchanged statement still supports the guarantee."},
        {"member_id": "<stored membership UUID for sibling B>", "basis": "Its existing scope and statement remain necessary here."}
      ]
    }
  }]
}
```

Each reference must exist in the selected dataset and belong to this family's explicitly declared
predecessor revisions. A malformed, absent, foreign or mismatched reference refuses; repeated IDs
and different old memberships resolving to the same new-family/invariant endpoint also refuse.
Omission or `[]` retains none. Nothing is copied implicitly. Changing a nonempty set or its authored
bases under an allocated declaration key is a content conflict; author a new successor declaration.

The writer adds new membership edges to the new family revision. It does not rewrite the retained
invariant's identity, revision, statement, scope or provenance, nor the old family or its memberships.
Retaining and retiring the same source membership in one handoff refuses, including across separate
entries; the historical membership must remain. In the report, membership
`state="added"` means a new **edge**; `retainedFromMemberId` names the exact old membership whose
invariant revision was kept. `unchangedSiblingMembers` includes those unchanged revisions after
publication, while uncommitted coverage remains explicitly projected. Read the published family
back and check its exact roster. This syntax extends a declaration carried by a genuine authored
obligation; it does not introduce a separate family-only authoring operation.

**`external_sources` — the bounded manifest, and the origin reference that names it.** An external
document is not a repository path with a Git blob, so it never becomes a source anchor: the run records
it in a bounded manifest beside the candidate and binds every authored record's `origin_refs` to that
manifest's own digest.

```json
"external_sources": [
  { "id": "<local id>", "document": "<URL or document identity>",
    "version": "<document revision | null>", "retrieved_at": "<ISO instant | null>",
    "content_digest": "<sha256 of what was inspected | null>",
    "location": "<the passage or section it was read at>" } ]
```

- **At least one of `version` / `retrieved_at`.** A source nobody can find again is refused by name.
- **`content_digest` is the digest of what you inspected, or `null`** when none was taken. It is never
  filled with a favourable default, and the report says how many declared sources carried one.
- **`external_sources: []` means you examined and declared none. Omitting the key means you did not
  examine it.** The report keeps the two apart.
- At most 32 sources per entry: a manifest is bounded and attributable, not a second store.

## Where an entry lands once knowledge is text (MIK-R21)

**Nothing here changes what a producer emits today.** Until the text-storage cutover (MIK-R37) the
installed ingest still writes the knowledge database. This section records the file formats the
curator writer (MIK-R12) will write the same entries into, so a producer and a curator can read what
their fields become. The models, the ID helper and the formatter live in
`mcp/src/agents_remember/models/knowledge_files/`; their docstrings are the format reference.

- **Records** are one JSON file each, flat per kind, named `knowledge/<kind-dir>/<ID>-<slug>.json`
  with an optional `.md` beside it. The schema is `ar-<kind>/v1`. An invariant carries `statement`,
  `applicability`, `conditions[]`, `exclusions[]`, `supersedes[]`, `revision`, `status`, `admission`
  and `origin`. It never lists its code locations, tests, families or decisions: each relationship
  is written once, on its owner's side.
- **IDs** are minted by the writer, never by a producer. The form is `<KIND>-<6 Crockford base32>`,
  for example `INV-7K3F9Q`, with the prefixes `INV FAM DEC INC ASM LIM FLM SCN DGN TRM`, and `RLZ` or
  `PRF` for entries. Exported legacy records have 8 characters derived from their legacy ID.
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
- **Formatting.** Every JSON file is written in one canonical formatting: UTF-8, two-space indent,
  sorted keys, identified entries sorted by `id`, and one trailing newline.
  `agents-remember knowledge-format` rewrites a file into it, and `--check` only reports.

### Family routes (MIK-R04)

The family record owns its `routes`: repository directories where part of the family's code lives.
Overviews never list families, and a family is never repeated at an ancestor overview. The curator
places the routes; nothing assigns them automatically.

- **Coverage:** every `realizes` entry of every member lies under at least one route. **Non-empty:**
  every route contains at least one such entry. `proves` entries do not count.
- **Place routes as deep as makes sense.** Several local routes are right when the family's code
  lives in several subtrees (D12). One broad route such as `mcp/` over everything passes the rules
  but is the wrong placement. The repository root route is spelled `.`; it is a route only when a
  family genuinely has no narrower home, such as a realization in a file at the root.
- **Reported, never refused:** `unrealized_family` (no member has a realization yet, so Non-empty is
  waived) and `route_unassigned` (an exported family with `routes: []` and `admission:
  legacy-unassessed`, so Coverage is waived until routes are assigned). Any other family with
  `routes: []` violates Coverage. A retired family is exempt.
- **A route the change adds must be a directory of the code tree.** A route carried from the base
  whose directory is gone is reported for route maintenance (MIK-R06), not refused.
- `agents-remember knowledge-routes <memory root> --code <code checkout>` shows each family's routes,
  its route state and a mechanical suggestion labelled `mechanical`. The suggestion starts from the
  directories that hold the realizations and collapses sibling directories into their parent only
  when that parent holds nothing but family code. It proposes `.` only when a realization file lies
  directly at the root. It is a starting point, and the command never writes a route.
  `agents-remember knowledge-validate` runs the rules.

## The file writer's sections (MIK-R12)

On a **converted** memory tree (one that holds the knowledge layout marker, MIK-R21 rule 1), `agents-remember knowledge-ingest`
and `agents-remember knowledge-bootstrap` write through the curator file writer instead of the
database. On unconverted memory, which is every memory tree until the cutover (MIK-R37), nothing
below applies and the installed ingest is unchanged. The producer's thirteen fields do not change
either: everything below is the **curator's**.

The writer reads the list you already have, or an object with three sections:

```json
{ "entries": [ "<the producer's entries, with the curator's keys>" ],
  "records": [ "<curator-authored records of any kind>" ],
  "history": [ "<the leaf's judgment rows>" ] }
```

**Entries.** Each entry with a `target`, an `admission` or an `invariant_id` authors one invariant
record from its verbatim `statement`. An entry with none of the three names no record: it is
written only when a `records` item names it in `entry` (its evidence then lives in that record's
`origin`). Otherwise the writer refuses the run, and the curator either attaches the entry to a
record or keeps it task-local and leaves it out of the list. `proofs` on such an entry are refused.

- `scope` (as above) gives `applicability`, `conditions` and `exclusions`. It is required for a new
  invariant.
- `admission` is `{criteria, justification}` and is required for a new invariant.
- `status` is optional. A new record starts `proposed`.
- `invariant_id` names a stored invariant the entry updates. Without it, a new invariant is created,
  or on a rerun the one this leaf already created from the same entry is reused.
- `supersedes` names the invariants the new one supersedes: an `INV-…` ID, or the `id` of an entry
  in this list.
- Each `target` becomes a `realizes` entry in `onboarding/<path>.json`. The role `incidental` is
  written as `support`.
- `proofs` is `[{ "test": "<path>::<name>", "facet": "<what this test demonstrates>" }]`, and each
  item becomes a `proves` entry in the test file's sidecar. `test` may also be `{path, symbol}`.
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
- **Proofs are shown and counted (MIK-R28).** `knowledge_read`'s `invariant` and `family` views of a
  converted tree return the `proofs` of the invariant or of every member. The curator checklist lists
  the invariants without any proof, with the tests their recorded evidence names; the list is
  information, not a gate. A migrated record's "Evidence: …" text sits in `origin.handoff`: a curator
  pass turns each resolvable test it names into a proof through `proofs`, with an authored facet.

**Records.** A record item is:

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

**History.** A history item is `{subject, disposition, reason, items?, …}`:

- `items` names the worklist item IDs the row answers. Leave it out: the writer then lists the
  items of the leaf's persisted worklist that the row answers. Name an item only where a row kind
  below asks for it.
- An invariant row adds `covers`, `effect` and `because`. Each cover is one of:
  - an entry ID, re-anchored at C with its own locator;
  - `{id, locator}`, re-anchored at a new locator;
  - `{id, rationale: "<text>"}`, which also replaces a realization entry's rationale in place (the
    entry keeps its ID); use it when the re-anchored code no longer does what the old rationale
    says. It may stand beside `locator`. A proof entry has a `facet`, not a rationale;
  - `{id, remove: true}`, which removes the entry;
  - `{handoff: "<entry id>"}`, which covers every entry this list wrote for the subject.
- An invariant whose record this leaf changed is governed by its `changed` row (converted memory).
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
- A family row adds `examined`, the member IDs the curator examined. A family has one governing
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

  While the family's guarantee differs from the parent line's (the item's facts say
  `guarantee-changed`), a row of another disposition does not answer the family: the gate holds
  its `reached_family` item open, and once the leaf has closed out without being integrated the
  validator reports `R09-history-rows` for a later `no_impact`, `assigned` or `rerouted` row that
  would replace the `changed` row. Name the `changed` row again, with `examined`. In the same
  way a later `no_impact` row after an `assigned` or `rerouted` row reopens the route condition:
  name that row again with its disposition.
- An onboarding row (MIK-R30, converted memory only) has the subject `onboarding:<source path>` or
  `onboarding:<route>/overview` (`onboarding:overview` for the root route) and the disposition
  `no_impact`, and nothing else. It records that a changed source file's card, or its governing
  route overview, was reviewed and needs no change. A counted change of the card or overview
  (its Markdown, or a sidecar field other than an anchor's `blob`, line numbers and `content`)
  needs no row.
- A planned row (MIK-R11, converted memory only) answers a `planned_untouched` worklist item: an
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
- A no_invariant row (MIK-R10, converted memory only) answers an `unexplained_hunk` or
  `unexplained_file` worklist item in a covered file: a change no entry covers that carries no
  invariant. Its subject is the item's `facts.row` (`hunk:<item id>`, or the item's own
  `file:<path>@<blob>`); its disposition is `no_invariant`; its `reason` says why, and it adds
  nothing else. The other answers need no such row: attach a `target` to a stored invariant
  (`invariant_id`, not a retired one) or author a new invariant over the change. A delete-only hunk
  admits only `no_invariant`. An item in an uncovered file is answered by the file's onboarding
  trace (a counted change of its card, or its `onboarding:<path>` row), not by this row.
- A reconsideration row (MIK-R14, converted memory only) answers a `reconsideration_candidate`
  worklist item: a target a decision's `reconsider_on` link names changed in this leaf. Its subject
  is the item's, `reconsider:<DEC-ID>#<alternative index>`; it carries the common fields and nothing
  else. The disposition is one of:
  - `still_rejected`: the rejection still holds; `reason` says why, against the item's facts. The
    writer refreshes the links whose trigger fired to what you judged: a requirement link is
    re-pointed to the item's `latestApproved` version, and an anchor link is re-anchored at C (a
    line range is mapped through the leaf's diff). The decision's revision goes up once in the
    leaf, like any change to its links. So the same condition is not raised again, while a later
    change to the anchored code, in this leaf or a later one, is raised as a new item. A linked
    anchor that cannot be mapped or no longer resolves refuses the row: re-author the link in
    `records` first. A link anchor that went stale (`anchor_stale`) is raised too, and
    `still_rejected` re-anchors it. When a newer version is approved, or the anchored code changes
    again, after you refreshed a link, rerunning the same row is refused: answer the new item by
    naming its ID in `items`;
  - `raise`: the alternative goes to the developer. The writer sets the decision's `status` to
    `under_reconsideration` and appends a question to the leaf's task-document `openQuestions`
    through `task_doc`, keeping every existing question. When the question cannot be written, the
    `raise` is refused and the item stays open.

  A `route:` target is raised by a row of the leaf that reroutes, retires or deletes it (a
  family row about a family that has the route). A superseded decision is never raised; its
  successor carries its own links. Never reverse a decision yourself: `raise` it. Keep a linked
  alternative at its index; the validator refuses a reorder that would move a `reconsider_on` link
  to another alternative, so append new alternatives after the linked ones.

**Handles.** `"handoff:<key>"` names the record this document authors under that entry `id` or record
`key`. Use it wherever an ID goes: link targets, `members`, `subject`, `examined` or `supersedes`.

**What the writer fills in.**

- IDs are minted, and a rerun reuses the ID recorded in `origin.handoffEntry`.
- Anchors are resolved at the leaf's code candidate C, with `blob` and `content`. A symbol must bind
  exactly once.
- `revision` goes up by one when a record's meaning differs from the memory base. `admission`,
  `status` and `origin` are not meaning.
- `origin` records the task, the leaf or wave, the list, the entry and the evidence.
- Each history row gets its row ID and the invariant's revision. It also gets each examined member's
  revision and each cover's `before` anchor (from the memory base) and `after` anchor. The `after`
  anchor is written into the entry in the same run.
- Every file is written in the canonical formatting.
- Every row of the leaf's history file must still agree with the result: covered anchors, the
  invariant's revision and each examined member's revision. A row a later run contradicts refuses
  the run until the row is named again in `history`, so the writer rewrites it.

**What the writer checks.**

- The writer runs the knowledge validator over the resulting tree.
- Any problem refuses the whole run. The report names every problem and nothing is written.
- Report-only findings are listed but never refuse. The family route rules (MIK-R04: route
  directory, Coverage, Non-empty) are reported inside the writer too, so a leaf can place routes
  across several runs; the closeout and every other commit route still refuse them.
- Without `--commit`, the run plans, validates and reports, and writes nothing.
- `--authorization-ref` must not be blank. The report records it.

## The admission rule (MIK-R27)

Every **new** invariant, family and decision record states which admission criterion it meets, with
a one-sentence justification: `"admission": {"criteria": [...], "justification": "..."}`. The
knowledge validator enforces it on converted memory, inside the writer and at every commit route. A
code change alone is never an admissible reason: most knowledge is local and stays prose.

| Record | Criterion | Meaning |
| --- | --- | --- |
| invariant | `spans_locations` | realized in more than one file (checked: its `realizes` entries sit in two or more files) |
| invariant | `guarded_by_test` | at least one proof entry names it (checked: a `proves` entry, MIK-R28) |
| invariant | `family_guarantee` | needed to state a family's guarantee |
| invariant | `prevents_costly_mistake` | guards a plausible, costly error; the justification names the error |
| family | `joint_guarantee` | the members together promise something none promises alone |
| decision | `real_alternatives` | at least one alternative was seriously considered |
| decision | `constrains_future_work` | the choice binds later work |

- **New** means the record's ID is absent from the memory base and it is not an export (an export's
  `origin.legacyId` derives its ID; a hand-written `legacyId` does not make a record exported). A
  new record is **refused** when it has no criterion, carries `legacy-unassessed` (only the export
  writes that), has a justification made only of task, leaf, requirement, step or section
  references, developer ruling IDs (`D14`), commit hashes, dates and provenance words ("Per ruling
  D14", "Added in commit a4eba7b7", "L43's acceptance criteria": the reason must be stated in
  words), or claims `spans_locations` or `guarded_by_test` that its entries do not support. The
  refusal names the record and the criterion.
- **Every other record** (exported, already in the base, or retired) is never refused for admission.
  A checkable criterion that no longer holds, for example a deleted test, is **reported**, and the
  records still `legacy-unassessed` are counted, until migration assesses or demotes each one.
- **Local stays local.** A statement that meets no criterion is not a record. It stays prose in the
  onboarding Markdown, under "Boundaries".
- **Demotion** (migration) moves the statement into the onboarding Markdown as prose and retires the
  record with a `deleted` history row, effect `retire`. The record file is never deleted.
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

## Decision records (MIK-R13)

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
  (the `DEC-…` IDs this decision replaces) and `admission` (MIK-R27: `real_alternatives` or
  `constrains_future_work`).
- `status` starts `active`. `superseded` is **never stored**: a decision is superseded when a
  later decision's `supersedes` names it, and readers derive it. `under_reconsideration` is set
  only when a reconsideration is raised to the developer (MIK-R14).
- `links`, each `{relation, target, alternative?}`:
  - `explains`, `constrains` and `motivated_change_to` name what the decision **governs**. The
    decision is read from each target, so link every record, route (`route:<dir>`), anchor
    (`{path, locator}`, resolved by the writer) or requirement it governs. A decision with none of
    these links is reported: nothing would show it.
  - `reconsider_on` names a target whose change should reopen one alternative, with `alternative`
    set to that alternative's index in `alternatives` (0 is the first). The alternative must be
    `rejected` or `deferred`. This link, not the prose, is what brings the decision back to the
    worklist when the target changes (MIK-R14). Link the record whose change would meet the
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

**Example.** D18 (text files are the source of truth) as a record: chosen "text files in Git";
rejected "canonical SQLite", because it took about 22,000 lines of storage-only machinery and left
knowledge unreadable, reconsider when the record count grows past about 100,000 or writers appear
outside Git. It `constrains` the knowledge routes, is `motivated_change_to` the MIK-R21 packet, and
has `reconsider_on` alternative 1 to the assumption "knowledge stays under about 100,000 records and
every writer uses Git".

## What this template is not

It is not the curator's side. The curator consumes this list as data, fills `resolution`,
`validated_at`, `record_action` and `supersedes`, and decides what the record does about each entry —
`roles/curator.md` owns that side, and `templates/curator-brief.md` feeds the curator its inputs. This
file states only the shape a producer emits.

# Template — Curator Hand-off List

The **producer's output shape** for the requirement-shaped items a leaf's builder and reviewer already
produce. The worker emits its list, the reviewer emits its own in the same shape, and the orchestrator
hands the curator **that same list**, unparaphrased, as data. `roles/worker.md`, `roles/reviewer.md`,
`roles/orchestrator.md` and `roles/curator.md` own the seats' sides of this contract.

**The contract's authority is the owning seat's schema note, the increment's own
*260915-KS-curator-handoff-list-schema.md*, at revision 1** — its twelve fields, its revision-1
rules 1–9, and the reasons the rules exist are authored there once. This file transposes revision 1
into the shape a producer writes and nothing more; where the two disagree, the schema note wins. The
worked example that priced revision 1 is the 41-entry fixture beside it, the increment's
*260915-KS-curator-handoff-fixture-L23-rev1.json* with its note. Both live with the increment that
produced them, outside this repository; the names are what a reader can look up.

Emit it as **JSON, either as a fenced block in the report or as a file beside it** — one document per
list, so the curator ingests a list rather than reading a table.

## The entry

Twelve fields, and each has a job. **Ownership is the whole point of the split:** the producer answers
*what is true, and where*; the curator answers *what the record does about it*. A producer that fills a
curator field has made the decision the curator exists to make, and a curator that re-derives a producer
field has destroyed the evidence it was supposed to compare against.

| field | who | what it carries |
| --- | --- | --- |
| `id` | producer | the entry's stable identity, in the producer's own spelling — `KS-R22@v1 §3.2`, `A-1`, `S-4`, `item 32`. It is the curator's handle for the entry and the thing a later revision names in `supersedes`. |
| `statement` | producer | **what is true**, as one sentence: the invariant, not "the case passes" but what the passing case asserts about the world. |
| `kind` | producer | `clause` \| `finding` \| `carried-defect` \| `decision` \| `measurement` — which of the five shapes it came from, so a reader knows what its `disposition` vocabulary means. |
| `target` | producer | **where it applies** — a list of `{path, locator, governing_route}`. This is the attribution: an invariant is a citation beside code. |
| `found_at` | producer | **where it was evidenced** — a list of `{path, locator, commit}`; it may be plural, because one invariant is often realized in several places while applying in one sense. |
| `disposition` | producer | the producer's own verdict in its own vocabulary (`satisfied`, `partial`, `refused`, `fixed`, `recorded`), carried verbatim — the curator's job is not to re-derive it but to decide what it means for the record. |
| `evidence` | producer | the case name, the command, the report path — the pointer that would let a reader check the claim. |
| `disposition_source` | producer | where the verdict came from, when it did not come from the producer's own list. |
| `authority` | producer | the governing route and the task document the entry descends from, so the record can be read back without the list. Absent when the list's own document attribution is the whole of it. |
| `resolution` | curator | the **resolved** target: the actual extent the locator names, with the moment it was resolved. |
| `validated_at` | curator | when the resolved target was last proved to hold — the field that goes red when code moves. |
| `record_action` | curator | `add` \| `revise` \| `supersede` — the curator's decision, which is the whole point of it being the curator and not the producer. |
| `supersedes` | curator | the `id` this entry replaces, when it replaces one. |

**Every entry carries all twelve keys, and every curator field is `null` in the producer's hands.** A
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
        "governing_route": "<route | absent>"
      }
    ],
    "found_at": [
      { "path": "<path>", "locator": { "kind": "line_range", "start": 0, "end": 0 }, "commit": null }
    ],
    "disposition": "<the producer's verdict, verbatim>",
    "disposition_source": null,
    "evidence": "<case name | command | report path — the checkable pointer>",
    "authority": { "governing_route": "<route | absent>", "task_document": "<task document>" },
    "resolution": null,
    "validated_at": null,
    "record_action": null,
    "supersedes": null
  }
]
```

`target[].locator` is `{kind: "symbol", value}` \| `{kind: "line_range", start, end}` \|
`{kind: "file"}`. `found_at[].locator` may additionally be **`null`** — "the source says it was found,
not where" is information, and different from an empty list.

## Rule 1 — co-resolution: name where the thing lives, not where you looked

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
*260915-KS-curator-handoff-fixture-L23-rev1.json* with `evidence` elided for length.

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
- **`governing_route` may be absent**, and absent is honest: 11 of 41 real target places had no memory
  route at all (`notes/reports`, `notes`, `system`, the repository-root `pyproject.toml`). Never invent
  a route to fill the field.
- **A memory path is written once, without the `onboarding/` prefix** — memory-root-relative, because
  two spellings of one card are two records for one file, and the prefix is a rendering detail.
- **`disposition` records the verdict the producer actually holds, and `disposition_source` records
  where it came from when that is not the producer's own list.** Never invent a verdict to fill the
  field: a verdict imported from another document is an input the curator must be able to see, and an
  unaudited verdict has already cost a master one sealed review finding.

## What this template is not

It is not the curator's side. The curator consumes this list as data, fills `resolution`,
`validated_at`, `record_action` and `supersedes`, and decides what the record does about each entry —
`roles/curator.md` owns that side, and `templates/curator-brief.md` feeds the curator its inputs. This
file states only the shape a producer emits.

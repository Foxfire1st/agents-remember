# Curator handoff list

Workers and Reviewers use this template to hand the Curator the requirement-shaped items they already produced. Each producer sends its own list directly, with its data unchanged.

Managers and Orchestrators read reports and relay no list. The [Worker](../roles/worker.md), [Reviewer](../roles/reviewer.md), [Orchestrator](../roles/orchestrator.md) and [Curator](../roles/curator.md) role files own their respective duties.

## Contract authority

This template is the producer's contract; the writer's checks enforce it. Where the template and the writer disagree, that is a defect to raise.

`target` is a list. Each element uses `{path, locator, governing_route, rationale, role}`. The rationale, default and optional-role rules are in [Realization rationale and role](#realization-rationale-and-role-one-authored-explanation-per-target) below. The writer refuses a new target without an authored rationale.

Emit one JavaScript Object Notation (JSON) document per list, either in a fenced report block or in a file beside the report. The Curator ingests that list.

## Writing and meaning review

Before authoring invariant statements, family guarantees or history reasons, read the [shared writing profile inspired by Simplified Technical English](../reference/writing/technical-english-profile.md) for clear prose that preserves obligation and scope.

The producer-owned fields remain verbatim: a clearer record draft never changes the producer's original statement or its evidence. Keep proposed wording separate.

Before classifying proposed wording as `clarify` or publishing it through the normal writer, compare its obligation and scope against the exact existing revision and obtain the independent fact-check. A changed obligation needs the appropriate authored effect, not a cosmetic label.

## Messages

Before sending a message or handover, read [Send a message that names the files](../reference/message-handover.md#send-a-message-that-names-the-files) for its sender and payload duties, and [Message files and receipts](../operations/coordination.md#message-files-and-receipts) for the message fields, file delivery and return address. Before handling a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.

## The entry

Each entry has thirteen fields: nine supplied by the producer and four decided or verified by the Curator. The producer states what is true and where; the Curator decides what the record does about it.

A producer must not fill a Curator field. A Curator must not re-derive producer data: doing so destroys the evidence it must compare.

A Curator receiving a target without a rationale authors it from the evidence before ingest. This supplies a missing explanation; it does not re-derive a producer field.

The producer supplies `id`, `statement`, `kind`, `target`, `found_at`, `disposition`, `disposition_source`, `evidence` and `authority`. The Curator owns `resolution`, `validated_at`, `record_action` and `supersedes`.

`disposition_source` belongs to the producer because only it knows where its verdict came from. The table and shape below show all thirteen keys.

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
| `authority` | producer | the governing route and the task document the entry descends from, so the record can be read back without the list. `authority` carries `null` when the list's own document attribution is the whole of it. |
| `resolution` | curator | the **resolved** target: the actual extent the locator names, with the moment it was resolved. |
| `validated_at` | curator | when the resolved target was last proved to hold — the field that goes red when code moves. |
| `record_action` | curator | `add` \| `revise` \| `supersede` — the curator's decision, which is the whole point of it being the curator and not the producer. |
| `supersedes` | curator | the `id` this entry replaces, when it replaces one. |

**Every entry carries all thirteen keys.** In a producer list, the four Curator fields carry `null`: the producer supplies no value for `resolution`, `validated_at`, `record_action` or `supersedes`. `kind` may be an enumeration; `disposition` is free text carried verbatim, never an enumeration.

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

`target[].locator` is required. Its admitted shapes are `{kind: "symbol", value}`, `{kind: "line_range", start, end}` and `{kind: "file"}`.

A path without a locator is refused as an omission. Claiming that the whole file is the place requires an explicit file locator. Line ranges are one-based and inclusive; `{start: 0}` names nothing and is refused for that reason.

`found_at[].locator` admits the same `symbol`, `line_range` and `file` shapes, and also `null`. A null locator records that the source named evidence without saying where inside it; it differs from an empty evidence list.

### Realization rationale and role: one authored explanation per target

Each target becomes one `realizes` entry in the file's sidecar, and that entry stores the author's own
explanation of what this place does for the obligation. The writer never generates one.

- **`target[].rationale` is required.** Write why *this* place carries the obligation, specific to the
  construct its locator names. Two constructs cited by one entry usually do different work. Give each its own explanation. "The statement is realized at <path>" is not a rationale: it restates the
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
  `realization_rationale_too_long`. Each refusal lands before any ID is minted or any file is
  written and names the entry and every offending target by position, path and locator. The writer
  refuses the whole run and writes nothing. Correct the entries and run the list again.

- **These checks apply on every run.** A rerun reads the whole list again, so an entry written by an
  earlier run is checked like a new one.

The producer states the rationale when it emits the target, because it knows why it named the place.
A rerun whose list changes a
target's `rationale` or `role` rewrites the `realizes` entry this leaf wrote from that entry; the
entry keeps its ID.

## Rule 1 — co-resolution: name where the thing lives, not where you looked

Resolve the target path and its construct in one act. Use a `symbol` locator for a named construct, a `line_range` for an extent, and `file` only when the whole file is the place.

A path without a construct and a construct without a path do not make a citation when combined later. Independent resolutions can disagree and identify the wrong place.

Name where the obligation lives, not the file you searched or the report you read.

A class, phrase or derived location without a path is not a target. Leave it out of `target`, or leave `target` empty and state that no path was supplied. Do not invent a path from a search result.

One requirement can apply in several places. Keep one defect that names two registries, or one measurement naming four modules, as one entry with multiple targets.

Splitting that entry breaks `supersedes`: a second part can appear new, and a shortened target list can appear complete while some places remain untouched.

`target` identifies the place; `found_at` identifies evidence. Prefer a symbol locator for a target because it survives movement.

A `found_at` line range records where evidence was when written. `commit` and `validated_at` date that snapshot. The evidence location may move without changing the statement. The target list identifies the statement's places, and may contain several of them.

## Rule 2 — carry producer data verbatim

Carry `statement`, `kind`, `disposition`, `evidence` and every `found_at` record verbatim from the producer's list. Do not reword the statement, turn the disposition into an enumeration, summarize the evidence pointer or reorder entries.

The Curator compares the list against code and records. Rewriting it for the handover destroys the evidence of what the producer supplied.

The input and output lists must retain the same entries, with `statement`, `kind`, `disposition` and `evidence` byte-identical.

**What may legitimately change is spelling, and only for the two fields that are spellings.**
`id` is re-spelled when a later source names the same entry differently (`item 32` → `item 32 / A-1`) —
that is why `id` is stable identity rather than the entry's wording. `target`/`found_at` **paths** are
re-spelled to the contract's own convention. Everything else is carried.

A null `found_at[].locator` is an honest evidence record, not a defect. Null locators and empty target lists are valid encodings; filler paths are not.

## Worked examples

The examples below show a requirement applying in several places and one whole-file target with several evidence places. Evidence is elided.

Before ingest, author the missing rationale for every target as [Realization rationale and role](#realization-rationale-and-role-one-authored-explanation-per-target) requires.

**Hypothetical defect: two places, one entry** — `target` is a list, the second place carries no
`governing_route` because none was named, and one `found_at` record has no locator at all:

```json
{
  "id": "item 12",
  "statement": "The retry policy and its configuration must declare the same attempt limit.",
  "kind": "carried-defect",
  "target": [
    { "path": "src/retry_policy.py", "locator": { "kind": "line_range", "start": 10, "end": 12 }, "governing_route": "src" },
    { "path": "config/retry-policy.json", "locator": { "kind": "line_range", "start": 3, "end": 4 } }
  ],
  "found_at": [
    { "path": "src/retry_policy.py", "locator": { "kind": "line_range", "start": 10, "end": 12 }, "commit": null },
    { "path": "tests/test_retry_policy.py", "locator": null, "commit": null }
  ],
  "disposition": "fixed",
  "disposition_source": null,
  "evidence": "…elided…",
  "authority": { "governing_route": ".", "task_document": "<the example task document>" },
  "resolution": null,
  "validated_at": null,
  "record_action": null,
  "supersedes": null
}
```

**Hypothetical finding: one whole-file target and three evidence places** — a `file` locator is honest when
the file is the place, and the evidence side keeps the line precision the producer could afford, dated
by the commit it was measured at:

```json
{
  "id": "item 32 / A-1",
  "statement": "The retry-policy test modules use the same configured attempt limit.",
  "kind": "finding",
  "target": [
    { "path": "tests/test_retry_policy.py", "locator": { "kind": "file" }, "governing_route": "tests" }
  ],
  "found_at": [
    { "path": "tests/test_retry_policy.py", "locator": { "kind": "line_range", "start": 10, "end": 10 }, "commit": "<example code commit>" },
    { "path": "tests/test_retry_config.py", "locator": { "kind": "line_range", "start": 20, "end": 20 }, "commit": "<example code commit>" },
    { "path": "tests/test_retry_deadline.py", "locator": { "kind": "line_range", "start": 30, "end": 30 }, "commit": "<example code commit>" }
  ],
  "disposition": "fixed",
  "disposition_source": null,
  "evidence": "…elided…",
  "authority": { "governing_route": "tests", "task_document": "<the example task document>" },
  "resolution": null,
  "validated_at": null,
  "record_action": null,
  "supersedes": null
}
```

## The remaining encodings a producer must get right

- **Map `kind` to the source item:** `clause` = a numbered clause
  of a requirement packet · `finding` = an entry a reviewer minted with a stable ID (`A-n`, `B-n`,
  `S-n`) · `carried-defect` = a defect carried forward in a plan's item list · `decision` = a ruling or
  a recorded choice · `measurement` = a report obligation with no code subject.

- **Any `kind` may carry `target: []`.** There is no permitted-kind list. The `kind` explains why the target is empty; it does not grant permission for emptiness.

  Report the entry as a ruling that applies nowhere, carrying its `kind` and `disposition` verbatim.

  Never refuse it as malformed merely because `target` is empty, and never turn it into a claim about code. A report obligation, a decision or a bare commit with no code place needs no invented path; its content lives in `statement` and `evidence`.

- **`governing_route` may be missing, and missing is spelled by omitting the key.** Never write the
  word `"absent"` (or any other filler) in its place: the writer reads a target's route as a path, so
  the word would author a route named `absent` and govern the anchor with it. The word is matched in any
  case once trimmed (`"Absent"`, `" ABSENT "`). Written at a target or at
  `authority.governing_route`, it refuses the entry with `realization_governing_route_absent_literal`,
  naming where it was written; `null` is read as no route. Never invent a route to fill the field.

- **A memory path is written once, without the `onboarding/` prefix** — relative to the onboarding root, because
  two spellings of one card are two records for one file, and the prefix is a rendering detail.

- **`disposition` records the verdict the producer actually holds, and `disposition_source` records
  where it came from when that is not the producer's own list.** Never invent a verdict to fill the
  field: a verdict imported from another document is an input the curator must be able to see.

## The two record kinds a producer may name

A producer names these in its hand-off list; the curator records them through the writer or says in
its report why not (`roles/curator.md` owns the recording side).

**A weakness.** What an agent met in the code outside the scope of its task: a waste of
an agent's context, compute, storage or time, a fault, or a debt. One entry, with the producer's
words verbatim:

```json
{
  "kind": "weakness",
  "fields": {
    "kind": "time",
    "statement": "one sentence: what the code does and what that costs, or what is wrong",
    "anchor": {"path": "cli/paseo_role_tools.py", "locator": {"kind": "symbol", "value": "_line_receipts"}},
    "seen": {"task": "<the task that saw it>", "role": "worker"},
    "state": "open",
    "measurement": {"number": 0.4, "unit": "s", "how": "one message at 2,300 receipts", "commit": "<40 hex>", "date": "2026-10-09"}
  }
}
```

The writer fills `blob` and `content` at the code candidate. `measurement` is present only where the
producer took one. A weakness that belongs to no file is not recorded here, and a statement beyond
the writer's one-sentence bound is refused with its field. It carries no priority and no proposal.

**A dependent.** One document that describes code, and what the document stands on:

```json
{
  "kind": "dependent",
  "fields": {
    "document": "docs/reference/settings-json.md",
    "stands_on": [
      {"kind": "anchor", "heading": "The host pin", "anchor": {"path": "kernel/primitives/paseo_runtime_settings.py", "locator": {"kind": "symbol", "value": "PaseoRuntimeSettings"}}},
      {"kind": "file", "path": "mcp/src/agents_remember/package_data/paseo_host/contract.json"},
      {"kind": "record", "id": "INV-…"}
    ],
    "confirmed": {
      "task": "<the task that confirms it>",
      "role": "curator",
      "statement": "one sentence: this task's judgment of the document as it now stands"
    }
  }
}
```

**Every dependent write is a confirmation, so every confirmation carries its sentence.** `statement`
is this task's own one-sentence judgment of the document — not a summary of the code, and not the
previous confirmer's words. It is required whether the document itself changed, stood still while the
code under its links moved, or was confirmed as it stands: the writer refuses a confirmation without
it, because a confirmation that states nothing claims agreement nobody gave.

The writer reads every recorded value at the task's tip -- an anchor's content, a whole file's blob,
a record's revision -- and fills `confirmed.commit`. No value is typed in. A document has at most
one dependent record; a record without a link, or for a path that is not a tracked file, is refused
with its field. The record holds no text of the document and no summary of it.

## What this template is not

This template defines the producer's list. The [Curator handoff-list ingestion reference](curator-handoff-ingestion.md#the-curators-authored-keys-beside-the-thirteen-fields) defines the Curator-authored keys and [writer input sections](curator-handoff-ingestion.md#the-writers-input-sections). The Curator consumes producer data unchanged, fills `resolution`, `validated_at`, `record_action` and `supersedes`, and decides what the record does about each entry. The [Curator role](../roles/curator.md) owns that workflow, and the [Curator brief](curator-brief.md) supplies its inputs. This template grants no producer authority to fill Curator-owned fields.

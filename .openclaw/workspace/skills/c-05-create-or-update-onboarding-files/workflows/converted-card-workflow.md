# Converted Card Workflow

Use this workflow instead of `file-level-onboarding-workflow.md` when the memory tree is **converted**: it holds
`knowledge/layout.json` (MIK-R21 rule 1). Every agents-remember memory line is converted from the cutover on; an
unconverted line is only read until it crosses (MIK-R24 rules 8 and 9).

## What a converted card is

A converted card is two files at the mirrored path:

```text
<onboarding-root>/<source path>.md     the prose: title, governing overview, purpose, commentary, evidence
<onboarding-root>/<source path>.json   the sidecar: references (and the realizes/proves entries)
```

- The Markdown has **no metadata table, no `## Update History` and no citation tables.** Its kind and its source are
  its place in the tree; Git keeps its history; `lastVerifiedCommit*` does not exist.
- Its evidence is `- <finding> [n]` lines, usually under one `## Evidence` section. Each `[n]` names
  `references["n"]` in the sidecar: the targets and the `note`.
  - The target kinds are `code`, `test`, `requirement`, `external` and `unresolved`.
  - A `code` or `test` target is an **anchor**: the blob, the `content` hash and a locator. The locator kinds are
    `symbol`, `line_range` and `file` (the whole file).
- A route's `overview.md` has the same shape. Its sidecar is `overview.json`, and every anchor names its path.
- **Never write sidecar JSON by hand.** The fixer authors `references` (below). The writer
  (`knowledge-ingest`, the hand-off's `entries` section) authors `realizes` and `proves`.

## Create a card for a new source file

1. Confirm the mirrored path and the nearest governing `overview.md`, exactly as for any card.
2. Write the Markdown:
   - `# <source path>`;
   - `## Governing Overview` with a relative link to that overview;
   - `## Purpose` and `## Code Commentary`;
   - `## Evidence` holding a citation table in the familiar grammar.

   ```markdown
   ## Evidence

   | Finding | Anchor | Source |
   | --- | --- | --- |
   | The journal is replayed on resume. | `replay_journal` | src/pkg/journal.py:10-40 |
   | The whole module is the adapter. | | src/pkg/adapter.py |
   ```

   - **To get a `symbol` anchor:** a backticked identifier in `Anchor` binds to that symbol when the extractor finds
     it exactly once in a file cited with a `path:start-end` range.
   - A bare path becomes a `code` (or `test`) target with a `file` locator, and a URL an `external` target. A row
     with nothing to cite (`n/a` in both cells) stays as prose.
3. Run the fixer on this one card. On converted memory `--document` needs no `--expected-snapshot`:

   ```text
   agents-remember memory-citations --fix --repo <repo> --contract <this leaf's enclosure contract> \
       --document <source path>.md
   ```

   The `citation_fix` MCP tool takes the same `document` (the card's path relative to the onboarding root). With a
   document, the run touches that card and its sidecar only. Without one it runs over every card that holds a
   citation table, and re-records moved anchors across the tree. On a converted tree it:
   - turns each table row into `- <finding> [n]`, where `n` is the card's next free reference number;
   - resolves the row's targets against the **code working tree**, through the conversion's own rules;
   - writes the sidecar, creating it (`ar-onboarding-file/v1`, or `ar-onboarding-route/v1` for an overview) when
     the card has none.
4. Read the response's `authoring` block:
   - `authoredReferences`, `createdSidecars` and `authoredCards`: what was written;
   - `unresolvedTargets`: rows whose citation resolved to nothing. Correct the row and run again;
   - `refused`: each card that was not authored, with the reason. Nothing of a refused card is written, and the
     run is not `ok`. The reasons are:
     - its sidecar is not valid JSON, or does not parse as a sidecar, before or after the authoring;
     - a row re-authors `[n]` while another evidence line still cites `[n]` (see below);
   - `unreadableSidecars`: sidecars the re-recording step could not read. They are named and skipped.

   The fixer checks every card before it writes anything, so a run never stops half-way with some cards written.
5. Rerun the full `memory_quality_check`. The census now reads the new card, so its `missing` blocker and the
   missing-onboarding row are gone.

## Refresh a card after the code changed

- **Prose:** edit the Markdown. Any Markdown change counts as the card's onboarding change (MIK-R30 rule 3).
- **One reference:** replace its `- <finding> [n]` line with a one-row citation table whose `Finding` ends in `[n]`.
  The fixer then re-authors reference `n` in place: it resolves the targets again at the working tree and replaces
  the note. **Replace the line, do not keep it:** if the old `- <finding> [n]` line is still there, the fixer
  refuses the card by name, because that line would silently point at the re-authored reference.

  ```markdown
  | Finding | Anchor | Source |
  | --- | --- | --- |
  | The journal is replayed on resume, once. [4] | `replay_journal` | src/pkg/journal.py:10-44 |
  ```

- **Mechanical moves:** the same fixer run re-records every anchor whose content is unchanged but whose blob or line
  numbers moved: in the named card with `--document`, across the tree without it. Such a change never counts as an
  onboarding change, and the census does not count it as an edit.
- **A stale reference** (its content changed) is reported, never failed. Refresh it by re-authoring, as above, when
  the card should say so.

## Remove a reference

1. Delete its `- <finding> [n]` line, and any prose that cites `[n]`.
2. Run the fixer on that card (`--document <source path>.md`). With a document named, the fixer removes every
   reference the card's Markdown no longer cites and reports the count as `removedReferences`. A tree-wide run
   never removes a reference.

Numbers are not reused or renumbered: the remaining references keep theirs, and a new row gets the next number
after the highest one.

## A source file moved

Do the entries first, then the card.

1. **Entries** (`realizes` / `proves`): through the writer. For each invariant the file realizes, hand in a
   `moved` history row whose covers name the entry and its new path: `{ "id": "<entry id>", "path": "<new source
   path>" }`. The writer moves each entry into the new file's sidecar, creating that sidecar when needed.
2. **Card:** move the Markdown to the new mirrored path (`git mv`). Its `[n]` lines now cite references the new
   sidecar does not hold. Rewrite those lines as a citation table against the new path, **without** their old
   numbers, and run the fixer with `--document <new source path>.md`. It numbers the rows and authors the
   references into the new sidecar.
3. **Old sidecar:** delete the file `<old source path>.json`. Do not move or edit it: its `path` names the old
   source, and a sidecar whose Markdown is gone may hold no references.
4. **Other cards** that cite the moved file are reported stale (`R22.6-carried-stale`). Re-author each such
   reference by its number, citing the new path.

## A source file was deleted

1. **Entries:** through the writer. For each invariant the file realized, hand in a row whose covers remove the
   entry: `{ "id": "<entry id>", "remove": true }`. The row's disposition says what happened to the invariant:
   `changed` (with its effect) when it is still realized elsewhere, `deleted` when it is retired. A `moved` or
   `no_impact` row cannot remove an entry.
2. **Card:** delete `<source path>.md` and `<source path>.json`, after proving the documented behaviour did not
   move to another file (then it is a move, above).
3. **Other cards** that cite the deleted file are reported stale. Re-author the reference to cite what replaced
   the file, or remove it (above).
4. **The governing route** still needs its answer: a counted change of its overview, or an
   `onboarding:<route>/overview` trace row.

## When the card needs no change

The onboarding gate (MIK-R30) asks, for every changed source file and its nearest governing route, for one of two
things:
- a counted change of the card or overview (Markdown, or a sidecar field other than an anchor's blob, line numbers
  and content); or
- a trace row in the leaf's history file.

The row is written through the writer (`knowledge-ingest`, the hand-off's `history` section):

```json
{ "history": [
    { "subject": "onboarding:<source path>", "disposition": "no_impact", "reason": "<why the card still holds>" },
    { "subject": "onboarding:<route>/overview", "disposition": "no_impact", "reason": "<why the route still holds>" } ] }
```

## Never on converted memory

- A metadata table, a `## Update History` entry, `lastVerifiedCommitHash` / `lastVerifiedCommitDate`, or a citation
  table left in place after the fixer has run.
- Hand-written or hand-edited sidecar anchors, or a reference number you allocated yourself.
- `memory_carryover_apply`. It writes the legacy format and refuses a converted target.

Route indexes (`overview.index.json`) are uncommitted caches. Regenerate them with `route_index_refresh`, scoped to
this leaf's contract.

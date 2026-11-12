# Refresh Stale Cards

A card is stale when the code that it cites has changed. Stale cards are expected, because every
task changes code. An agent must learn that a card is stale before it relies on the card as a
description of the code.

## Detect Drift

Each card has a JSON sidecar. The numbered references of the sidecar anchor the statements of the
card to places in code and test files. A [drift check](../concepts.md#drift-check), a call of
`context_packet` with `include_drift=true`, compares every anchor with the code in the code
repository.

Ask the agent of your harness to measure:

```text
context_packet(repo_id="<repo-id>", include_drift=true)
```

Without `include_drift=true` the call measures nothing. The drift check changes no card and
refuses nothing. When stale anchors exist, the answer carries one line in `actions` that gives
their number. The answer's field `log` names a file in the folder of the
[tool log](../concepts.md#tool-log); that file holds the whole result, and its block `drift` holds
the counts.

Inside a task, the Curator runs `memory_quality_check` with the contract of the task. A full run
writes the [Curator's checklist](../concepts.md#curators-checklist) anew, the file
`<enclosure>/reports/curator-memory-quality.md`. The file lies outside both Git worktrees and is
removed with the enclosure.

A drift check of one repository or of a few cards does not replace the Curator's work on a task:
the curation, the full `memory_quality_check` run and the task's
[coherence record](../concepts.md#coherence-record), which the Curator publishes.

## Interpret Results

The drift check gives every anchor one of two states:

| State | Meaning | Action |
| --- | --- | --- |
| `current` | The cited file has the recorded blob, or the cited lines or symbol hold the recorded content. | None. |
| `stale` | The content differs, the symbol does not resolve to exactly one place, or the path is absent. | Refresh the card, as the next section describes. |

The block `drift` reports these numbers:

| Field | Meaning |
| --- | --- |
| `status` | `checked` after a drift check, `notChecked` without one, `error` when the drift check failed. |
| `sidecarCount`, `count` | The sidecars read and the anchors observed. |
| `states` | The number of anchors that are `current` and the number that are `stale`. |
| `actionableCount`, `actionableSample` | The number of stale anchors, and the first of them. |
| `violationCount`, `violationSample` | The number of validator findings that refuse a commit and are not stale anchors, and the first of them. |
| `reportOnlyCount` | The number of findings that are reported and refuse nothing. |
| `unreadableSidecars` | The sidecars that the drift check could not read. |

The drift check reports nothing for a source file without a card. A Curator writes the card of a
file inside a task that touches the file.

Knowledge records have a currentness of their own. A knowledge read reports an invariant as
`current`, `stale`, `unverifiable` or `unrealized`; see the
[knowledge system](../reference/knowledge-system.md).

## Refresh Through the Curator

Two repairs exist, and neither `context_packet` nor `memory_quality_check` performs one.

**The anchor only moved.** When the cited content is unchanged and only its position or the blob
of its file changed, `citation_fix` records the anchor again:

```text
citation_fix(repo_id="<repo-id>", contract_path="<contract>", documents=["<card path>"], dry_run=true)
```

Each path in `documents` is relative to the folder `<memory-root>/onboarding/`, and a call takes
at most 2048 paths. Preview with `dry_run=true`, then repeat the call without it.

**The code changed.** A Curator refreshes the card inside a task, by the
[card procedure](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md),
the Curator's instructions on the format of a card and its numbered citations. The Curator:

1. reads the current source;
2. compares each statement of the card with what the code does;
3. removes the statements that are false for the current code;
4. adds the current facts that a later agent needs;
5. writes the evidence of the changed statements into the card as citation input, a table with
   the columns `Finding`, `Anchor` and `Source`, and runs `citation_fix`, which turns each row
   into a numbered evidence line of the card and a reference of its sidecar.

A card holds current prose and numbered evidence lines.

## Use Directional Trust Carefully

A stale card can still show where to look. An agent that reads one says so, and it confirms each
statement in the source before it plans against that statement. A stale card is not a
description of the current code.

## After Code Changes

A task that changes a source file can make anchors into that file stale. The Curator of the task
refreshes the cards of the changed files before the closeout.

The closeout and every landing refuse a memory tree that holds a stale anchor into a code or test
file. The refusal names the rule `R198.1-reference-anchor` of the validator, which requires each <!-- docs-check: identifier: the product prints this code -->
such anchor to name the file's blob in the code tree of the commit and to find its recorded
content there.
[Memory Quality Control](../reference/drift-c02.md) describes the checks and the Curator's
checklist in full.

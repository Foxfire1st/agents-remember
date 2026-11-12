# Criteria catalog: onboarding and memory review

The Reviewer uses this catalog for sidecars, route overviews, route indexes and judgment history. It applies at master-exit, super-exit and loop reviews carrying onboarding, as the [Reviewer binding table](../roles/reviewer.md#bind-the-seam-and-catalogs) specifies.

Before starting a baseline or fix-verification, read the [shared catalog review rules](code-seam.md#shared-catalog-review-rules). They define standing checks, baseline exploration, fixed-list verification and criterion promotion, demotion and automation. Preserve the developer-ruling exceptions stated below.

## Scope and demotion

OM-2, OM-4 and OM-5 apply only to converted, text-format memory. Only a developer ruling may demote them.

## Standing criteria

### OM-1 — Staleness diff vs as-landed code

Check every present-tense sentence against the as-landed code, including sentences in dated change sections. Read the affected card or overview body, paired notes and sibling cards found by a bounded search. Require repair or withdrawal of every current instance of an encountered false-claim class.

Check that historical accounts are in history rows rather than cards. Keep true statements about the current state. Check every changed claim, count and prose line reference against code; a prose line reference must lie inside its citation anchor. Passing structural checks and authentic anchors do not establish that the prose is true.

Read the whole affected body. A true paragraph added beside a contradictory claim does not repair it.

### OM-2 — Independent fact-check of curated knowledge

A role agent that wrote none of the leaf's cards or records checks curation before closeout. On converted memory, check every changed card, sidecar and record statement by statement against the leaf's final code. Check every history row's disposition, effect and reason against the code diff, and state whether important information about the change was omitted.

Require repair of blocking findings before closeout. Judgment history belongs in content-bound Curator rows; a counted card edit alone never closes a trace.

### OM-3 — History-row coverage of the worklist

Match every open worklist item to its required content-bound history row. Include a family row where owed and report every missing row.

This standing criterion checks coverage. [OM-2](#om-2--independent-fact-check-of-curated-knowledge) independently judges each row's disposition, effect and reason; OM-3 adds no second per-row semantic check.

### OM-4 — Admission justifications are plausible

Judge whether every new invariant, family and decision record has a plausible admission justification for its claimed criterion. The validator checks presence, shape, justifications made only of task, leaf, requirement or ruling references, commit hashes and provenance words, and the two checkable criteria `spans_locations` and `guarded_by_test`. The Reviewer judges the rest.

Report these implausible justifications:

- `prevents_costly_mistake` names no plausible costly error.
- `family_guarantee` is not needed to state the family's guarantee.
- `joint_guarantee` is already promised by one member alone.
- `real_alternatives` names a rejected option that was never seriously considered.
- The record restates one leaf's acceptance criteria, or is motivated only by a code change.

A statement that does not qualify stays prose in the onboarding Markdown.

### OM-5 — Repoint and reconsideration judgments

A `repoint` on a reconsideration row is a judgment that the Reviewer checks. For every item, verify that the new target is the code addressed by the alternative's `reconsider_when` condition. The row's `reason` must explain, against that code, why the condition is not met.

If the reason leaves open whether the condition may be met, report that the row should have been a `raise`.

For a reconsidered revision written after a `raise`, check all three:

- The alternative's status matches the developer's recorded answer.
- A link repointed in `records` names the code addressed by the condition.
- The `changed` row cites the answer.

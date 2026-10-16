# Shared STE-inspired writing profile

## Purpose and selected principles

Curators and explainer authors use this single profile for invariant statements, family guarantees,
history reasons and walkthrough prose. It selects clarity principles from
[ASD-STE100](https://asd-ste100.org/about_STE.html); it does not claim full ASD-STE100 compliance,
an "80 percent" measurement or a new controlled-language standard.

Name the actor and use concrete verbs: say who must do what, not merely that something is handled.
Write one independently testable promise per invariant. A paragraph develops one idea. Sentences
normally target about 20–25 words; precision takes priority over sentence length. Keep an exact
technical identifier or essential qualification even when the sentence is longer. Never split an
invariant automatically to meet a word target: a split is an explicit authored semantic change.

State conditions and exceptions explicitly. Keep quantifiers and negation: "every", "only", "no"
and "must not" are part of the obligation, not words to trim. Repeat the same term for the same
concept. Prefer a short explanation to an invented synonym.

## Use the project's vocabulary

Read applicable existing term records and use the product's vocabulary; do not create a second
terminology registry. These explanations orient the author without replacing those records:

| Term | Use |
| --- | --- |
| invariant | One independently testable obligation, with its applicability, conditions and exclusions. |
| family | An authored record whose members support its own joint guarantee; not a directory grouping. |
| joint guarantee | What the members together promise that none promises alone. Do not concatenate their statements. |
| realization | An attributed code place that carries an obligation, with its own rationale and role. |
| proof | Recorded evidence of a particular tested facet; not a claim that every case is established. |
| history row | The existing writer's content-bound judgment about a subject and its change. |
| revision | The exact record version the judgment or comparison addresses; never a prose edit counter supplied by the author. |
| candidate | The exact proposed code and memory state under examination, not an assumed installed or landed state. |

Keep identifiers, quoted source text and admitted effect labels exact. Distinguish
missing, unexamined, unavailable and measured absence: respectively not supplied, not checked,
not readable/obtainable, and checked with no instance found in a stated scope. None establishes
another, and none proves equivalence.

## Separate the promise from its support

State the promise in `statement`. Keep applicability, essential conditions and exclusions in their
existing semantic fields. When the statement relies on those fields, make that connection clear;
read the statement and scope together. Do not remove a condition just because another display shows it.

Keep realizations, proofs, identities and authoring provenance in their existing structural owners.
A history reason explains the concrete change and its consequence; a task ID or "wording improved"
alone explains neither. Use the existing [handoff contract](../templates/curator-handoff-list.md):
producer-owned fields remain verbatim, including the original statement and evidence. Author a
proposed record rewrite separately; do not silently replace the statement the producer handed over.
The writer continues to own IDs, anchors, revisions and row mechanics.

## Independent meaning review before publication

Use the existing [independent fact-check](../criteria/onboarding-memory.md#om-2--independent-fact-check-of-curated-knowledge-second-reader-requirement-bound-mik-r53-rule-5),
not a style-specific gate. A clearer rewrite is never proof of preserved meaning.

1. Preserve the exact old revision, before text, semantic scope, source anchors and evidence identities.
   Bind the proposed text to that baseline and the exact candidate being examined.
2. Compare old and new actor, trigger, required behavior, forbidden behavior, quantifiers, conditions and exclusions.
   Name each facet explicitly, including an unchanged field that the new statement depends on.
3. A reader who wrote none of the drafts explains each promise without oral repair and checks
   concrete conforming, violating and boundary cases against both versions. The reader records
   each ambiguity, changed obligation, missing evidence and unresolved case, not just a verdict.
4. Propose `clarify` only after independent review establishes that the obligation stays the same.
   A real change uses the existing appropriate effect and writer/history/revision route. Do not
   classify an edit from its apparent size or its word count. A family change retains its own
   authored effect, guarantee, examined members and writer-owned revision.
5. Correct or explicitly classify every disputed case before the normal writer publishes.
   An ambiguous draft stays unaccepted and names the actor, quantifier, condition or case in dispute.
   An unreadable revision or missing evidence is a reported gap, never an equivalence judgment.

### Worked comparison and deliberate failures (illustrations, not pilot evidence)

Before: "For every landing, the landing owner must publish only the accepted code and memory trees;
if either tree differs from the accepted candidate, publication must be refused."

Proposed: "For every landing, the landing owner must publish only the accepted code and memory trees.
The owner must refuse publication if either tree differs from the accepted candidate."

The proposed draft names the same actor, every landing, the same allowed trees and the same refusal
trigger. Its author has not accepted equivalence. The independent reader must test at least these cases:

| Case | Required reading of both versions |
| --- | --- |
| Conforming | Both trees equal the accepted candidate: the owner may publish under the existing admission rules. This promise alone does not grant admission. |
| Violating | The owner publishes a different memory tree even though the code tree matches: the owner violates the refusal. |
| Boundary | Only one tree differs: "either" still requires refusal. No exception for a code-only match exists. |

| Draft fault | What the independent comparison must catch |
| --- | --- |
| Removed negation | Changing "must not publish a different tree" to "must publish a different tree" reverses forbidden behavior. |
| Changed quantifier | Replacing "every landing" with "normally at landing" permits previously forbidden exceptions. It is not a clarification. |
| Lost condition | Dropping "if either tree differs" changes the refusal trigger; removing it from scope or omitting its connection in the prose does not preserve it. |
| Unrecorded effect | Calling a changed obligation cosmetic, or publishing it without the appropriate authored effect and history route, leaves the change unaccepted. |
| Altered frozen row | Editing an old closed row to make the draft appear historical changes evidence, not meaning preservation. Retain the original bytes and use the normal new revision route. |

## Three-real-invariant pilot and adoption

Select three existing invariant revisions: a direct promise, a conditional or exception-bearing
promise, and an obligation used in an authored family guarantee. Preserve the exact before text,
revision, scope, source/evidence identities and family membership where relevant. Produce proposed
rewrites and the facet/case comparison above. Record source availability separately from semantic
review: a stored evidence pointer is not evidence that the pointed-to report was read or rerun.

Prototype on real records in shared scratch clones, never the official checkout. Keep a frozen
baseline and separate drafts. An independent reader must judge all three; the author's own table
is a proposal, not acceptance. Correct or classify every ambiguity before the normal admitted
writer publishes. Do not run the writer merely to demonstrate style, rewrite frozen history,
backfill closed rows, or bulk-rewrite existing records. Scratch-clone preparation and later writer
execution require their normal authorization and isolation; this guide grants neither.

After adoption, new or revised invariant statements, family guarantees, history reasons and
walkthrough prose use this profile. Migration authors link it from their procedure before waves
start; preserve every other leaf's required guidance rather than adding a second writing guide.
Existing frozen records and closed history remain inspectable at their original identities.
Guide edits follow normal source/copy checks and curation. The normal skill-copy generator carries
this reference; its composition entry is reference metadata, not another meaning authority.

There is no automatic semantic validator here. Word-count and vocabulary heuristics cannot approve,
reject or classify meaning. Schema, identity, admission, invariant gates and normal writer refusal
and retry behavior remain unchanged. This profile provides author/reviewer guidance and examples,
not a language gate or a closeout bypass.

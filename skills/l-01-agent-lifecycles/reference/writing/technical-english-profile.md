# Shared Simplified Technical English writing profile

Curators and explainer authors use this profile for invariant statements, family guarantees, history reasons and walkthrough prose. It selects clarity principles from [ASD-STE100](https://asd-ste100.org/about_STE.html); it does not claim full compliance, an "80 percent" measurement or a new controlled-language standard.

## State one precise promise

Name the actor and use concrete verbs: say who must do what. Write one independently testable promise per invariant and one idea per paragraph.

Sentences normally target about 20–25 words, but precision takes priority. Keep exact technical identifiers and essential qualifications even when a sentence is longer. Never split an invariant automatically to meet a word target: splitting is an explicit authored semantic change.

State conditions and exceptions explicitly. Preserve quantifiers and negation: "every", "only", "no" and "must not" are part of the obligation. Use the same term for the same concept; prefer a short explanation to an invented synonym.

## Use the project's vocabulary

Read applicable existing term records and use the product's vocabulary. Do not create another terminology registry. The following explanations orient the author; they do not replace those records.

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

Keep identifiers, quoted source text and admitted effect labels exact. Distinguish these four evidence states:

- **Missing:** not supplied.
- **Unexamined:** not checked.
- **Unavailable:** not readable or obtainable.
- **Measured absence:** checked with no instance found in a stated scope.

None establishes another, and none proves equivalence.

## Keep the promise and its support in their own fields

State the promise in `statement`. Keep applicability, essential conditions and exclusions in their existing semantic fields. Make any dependence on those fields clear and read statement and scope together. Do not remove a condition because another display shows it.

Keep realizations, proofs, identities and authoring provenance with their existing structural owners. A history reason explains the concrete change and its consequence; a task identifier or "wording improved" alone explains neither.

Before rewriting a producer's statement as a proposed record, read the [handoff contract](../../templates/curator-handoff-list.md#rule-2--carry-producer-data-verbatim) for producer-owned fields and Curator authoring responsibilities. Author the proposed rewrite separately. The writer still owns identifiers, anchors, revisions and row mechanics.

## Compare meaning independently before publication

Before classifying or publishing a rewrite, use the existing [independent fact-check](../../criteria/onboarding-memory.md#om-2--independent-fact-check-of-curated-knowledge). This profile creates no style-specific gate. Clearer wording is not proof that meaning was preserved.

1. Preserve the exact old revision, before text, semantic scope, source anchors and evidence identities. Bind the proposal to that baseline and exact candidate.
2. Compare the old and new actor, trigger, required behavior, forbidden behavior, quantifiers, conditions and exclusions. Name each facet explicitly, including unchanged fields on which the new statement depends.
3. Have a reader who wrote none of the drafts explain each promise without oral repair. The reader checks concrete conforming, violating and boundary cases against both versions, recording ambiguities, changed obligations, missing evidence and unresolved cases rather than only a verdict.
4. Propose `clarify` only after independent review establishes the same obligation. A real change uses the existing effect and writer/history/revision route. Do not classify meaning from edit size or word count. A family change keeps its own authored effect, guarantee, examined members and writer-owned revision.
5. Correct or explicitly classify every disputed case before the normal writer publishes. An ambiguous draft stays unaccepted and names the disputed actor, quantifier, condition or case. An unreadable revision or missing evidence is a reported gap, never an equivalence judgment.

Keep a frozen baseline and separate drafts. Record source availability separately from semantic review: an evidence pointer does not prove that the report was read or rerun.

### Compare a worked example

These examples illustrate the comparison; they are not pilot evidence.

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

## Apply this profile

Use this profile for new or revised invariant statements, family guarantees, history reasons and walkthrough prose. For each proposed rewrite, follow the [independent meaning comparison](#compare-meaning-independently-before-publication) above. Preserve every other leaf's required guidance instead of adding another writing guide.

Do not run the writer merely to demonstrate style, rewrite frozen history, backfill closed rows or bulk-rewrite existing records. Scratch-clone preparation and later writer execution require their normal authorization and isolation; this guide grants neither.

Keep existing frozen records and closed history inspectable at their original identities. Guide edits follow normal source/copy checks and curation.

There is no automatic semantic validator here. Word-count and vocabulary heuristics cannot approve, reject or classify meaning. Schema, identity, admission and invariant gates, and normal writer refusal and retry behavior, remain unchanged. This profile supplies guidance and examples; it creates no language gate or closeout bypass.

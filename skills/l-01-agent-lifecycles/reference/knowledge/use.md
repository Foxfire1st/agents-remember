# Knowledge use at the working step

All nine roles use this file to read code with attributed knowledge, choose and complete `knowledge_read` views, distinguish recorded proofs from fresh results, and inspect the source that owns a setting. Role sections below cover the eight non-Bootstrap roles and their boundaries.

## Read code and knowledge together

Start with `knowledge_read` (`view="impact"` for files/folders or `view="search"` for concepts/terms) or a text search. Use `knowledge_read` (`view="records"`) to batch-read verbatim record text without opening JSON files one by one. Compact `read_ar_files` rows give card status and any brief path with saved freshness. Fuller replies can include rule text. Open an available saved brief at `briefs/<path>.md` under the memory root with your file tool before editing. The next write covering the file or folder renews a missing or older brief when the writer can refresh it. When current memory identifies neither a card nor a qualifying brief for a file it can assess, no rule, weakness, decision or standing document is recorded there.

Use both knowledge and code reads. Retrieved knowledge claims inform and direct inspection, and never substitute for reading source code or running tests. Attributions find what text search misses; code reads fill gaps when memory is stale or missing. Stale memory may guide orientation when you label it explicitly as stale and check current claims against code. Informative evidence is not publication-ready evidence.

## Bind and complete the knowledge read

Call `knowledge_read` on `agents-remember-task` with `memoryRoot` set to the assigned memory tree. Auto-pairing resolves the paired code tree from `contractPath` (leaf enclosure), `repo_id`, or `memoryRoot`. Pass `repositoryRoot` and/or `codeTreeId` (`"working-tree"` or a commit/tree hash) to override or pin an exact code tree. Use the handover's binding; for a taskless investigation, use the selected repository's resolved pair.

Choose the view that fits the question. Read every page through `continuation` until enumeration is complete:

- **Blast radius across files or folders:** `view="impact"` with `files=[...]` and/or `folder="..."` returns deduplicated, family-grouped text-bearing rules, family guarantees, out-of-scope siblings, open weaknesses, and standing dependents (`compact=true` gives one line per rule).
- **Keyword or concept lookup:** `view="search"` with `query="..."` searches live records via FTS5 (defaults to `kinds=["invariant","family","weakness"]`; pass `kinds` to include other live non-history kinds, or narrow with `files`/`folder` and `compact=true`).
- **Batch verbatim full text:** `view="records"` with `recordIds=[...]` (up to 25 text IDs such as `INV-…`, `FAM-…`, `WKN-…`, `DPD-…`, paginated up to 20 per page inside the answer bound) returns full untrimmed statements, conditions, exclusions, standing, realizations, proofs, and families, naming stale anchors inline.
- **Dependent triage and receipts:** `view="dependent"` with `staleOnly=true, compact=true` gives compact closeout triage; omit `compact` when an evidence receipt requires full moved-link rows.
- **Single-file or single-record structural reads:** `view="source_context"` (`sourcePath` or `familyRevisionId` for `role="chain"` family expansion), `view="invariant"` (`recordId="INV-…"`), `view="family"` (`recordId="FAM-…"`), `view="record"` (`recordId`), and `view="weakness"`.
- **Specialist consolidation views:** `view="writers"` and `view="consolidation_proposal"` remain specialist/consolidation surfaces (see [commands](commands.md#choose-a-knowledge-tool)).

Reserve native file opens for source code, tests, saved briefs (`briefs/<path>.md`), onboarding cards, and any record detail not carried by `records` or `impact`.

A proof records what a test demonstrates, not that it passed. Report a refusal, partial index or unverified currentness as a gap; none establishes an empty or complete result.

An unresolved-anchor count covers anchors not established against their exact recorded blob, including anchors whose resolution was not requested. Distinguish an unrequested check from an observed resolution failure. Currentness counts describe the named invariant population, including related invariants selected by expansion. Neither count proves semantic coverage, source-wide correctness or a passing test.

## Read the source that owns a setting

When an assignment needs a setting fact, read its authority. Use `server_info` on `agents-remember-task` for the loaded build and configured roots, the resolved memory settings for source-bound eligibility, and the user settings for role policy. Open the exact settings paths the context names; the compact packet’s omitted policy fields are not defaults you may infer. Use the existing resolver rather than constructing a second one.

## Architect: diagnose before proposing a repair

When diagnosing a failure or planning a packet, call `knowledge_read` (`impact` for blast radius and dependencies, `search` for prior decisions and requirement wording, `records` for full rule text) alongside code reads to form and check the cause. Carry the evidence and remaining gaps into the packet and Prepared context. Use `view="consolidation_proposal"` only for consolidation planning, and leave Curator checks to the Curator.

## Orchestrator: check sprint scope, overlap and triage

Use `knowledge_read` (`impact` for sprint/master overlap and leaf collisions, `search` for prior rulings and terms, `records` for decision and requirement text). Read-only `dependent(staleOnly=true, compact=true)` supports integration/landing triage only; Manager and Curator own closeout gates and repairs, and `view="writers"`/`view="consolidation_proposal"` stay out of ordinary sprint routing.

## Manager: inspect leaf scope and triage closeout

Use `knowledge_read` (`impact` for leaf scope and overlap, `search` for prior rulings, `records` for requirement and decision text). Use `dependent(staleOnly=true, compact=true)` for closeout triage only (full rows when an evidence receipt requires them); triage never replaces Curator/Consolidator authoring, repairs, or full receipts, and `view="writers"`/`view="consolidation_proposal"` remain specialist surfaces.

## Worker: read before editing and after a test fails

Before editing, call `knowledge_read` (`impact` on target files and `records` for governing invariants and families) alongside `read_ar_files` and saved briefs. Use them to find relevant code and preserve or implement the task's required guarantees, even when the handover names no invariant or family.

When a test fails, inspect its recorded `proves` links and read the named invariant and siblings (`records`/`impact`) to diagnose which guarantee failed. If no link is recorded, read the test and code and state that gap.

Do not change a sibling outside the assigned scope; report it to its owner. Do not author memory, run `view="writers"`/`view="consolidation_proposal"`, or sign off dependents.

## Reviewer: inspect every changed file

For changed files, call `knowledge_read` (`impact` on changed paths to inspect rules and missed siblings, `search` for prior findings and terms, `records` for full rule and proof text) and compare backing proofs with runs on the exact candidate. Name unrun proofs and evidence gaps. Keep memory-lane `dependent(staleOnly=true)` receipt checks intact (read-only; no memory edits, gate acceptance, or `view="writers"`/`view="consolidation_proposal"`).

Attribution adds a review dimension; it filters nothing.

## Investigator: distinguish claims, observations and hypotheses

When investigating repository code, call `knowledge_read` (`search` for claims, terms, and prior findings, `impact` for regression blast radius, `records` for verbatim record text, and `dependent` for stale-document investigations) alongside code, logs, and checks. Distinguish recorded claims, observed evidence and your hypothesis in the report; make no code, memory, or task edits.

## Curator: inspect affected rules and verify dependents

Before authoring, call `knowledge_read` (`impact` on changed files/folders, `search` for existing records and prior wording, `records` for verbatim text to reconcile). At closeout verification, use `dependent(staleOnly=true, compact=true)` for triage and full `dependent(staleOnly=true)` when the closeout receipt requires moved-link rows. `view="writers"` and `view="consolidation_proposal"` stay outside ordinary curation.

## Consolidator: verify listed families, rules and dependents

Start from the prebuilt `consolidationList` and use `knowledge_read` (`impact`/`search`/`records`) to inspect listed families, member rules, conditions, exclusions, and proofs alongside code and named tests. Use `dependent(staleOnly=true, compact=true)` for triage and full rows where evidence requires them (0 stale). Make no code or test edits and do not run `view="writers"` or `view="consolidation_proposal"`.

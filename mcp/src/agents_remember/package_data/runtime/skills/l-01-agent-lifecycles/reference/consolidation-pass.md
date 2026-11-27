# Consolidation pass procedure

Consolidators read this reference during a consolidation pass for detailed outcome rules, test-run cleanliness, `unresolved` and gap reporting, and handover report shape. The role and consolidation operation retain scope, writer, and boundary authority.

## Prepare the pass

Confirm `consolidationList` in the leaf task document before inspecting memory. If `consolidationList` is missing or empty, stop and report the missing list to the Manager.

Call `memory_quality_check` with the leaf contract to build and persist the worklist. Every listed family and every rule listed directly or through a listed family produces one `consolidation_target` item. A rule shared by multiple listed families gets one item. Unlisted, unchanged records produce no item.

## Run named proof tests cleanly

Run only the tests named as `proves` entries on the rule under examination and any existing fitting test you link as a proof during this pass. Never run a broad test suite or unrelated tests.

Keep the paired code worktree identical to its base:

- Direct caches and temporary files (`--basetemp`, `PYTEST_CACHE_DIR`, `TMPDIR`, coverage files) into the supplied scratch directory, never into the code worktree.
- Verify `git status --porcelain` is empty in the code worktree after test runs. Remove any unintended artifact created by a test tool before proceeding.
- If a named test cannot be executed in the seat (missing binary, unsupported platform, external service), record it in `testsNotRun` with the reason; the rule may still `stands` on code reading.

## Decide each outcome

Evaluate every listed rule against current code, linked decisions, card realization/proof entries, and test results. Treat record age, text similarity, and usage counts as hints, never as proof. Leave a sound rule unchanged.

- **`stands`**: Meaning and card realization/proof entries are accurate as written. Adding a missing title to a listed rule does not change meaning or raise revision, so its row is still `stands`. Give a concrete one-line `reason` explaining why it stands against the code.
- **Corrected**: Apply only allowed changes through `knowledge_write`: rule text/conditions/exclusions, family members/guarantee, card realization/proof entries and their sentences, stale text in a decision record, or a missing title. Keep every condition and exclusion intact across a merge or split. A successor names its predecessor in `supersedes`; old revisions stay in Git. When only card realization/proof entries change and the rule text stays unchanged, write today's row (`extended`, `moved`, `deleted`, or `no_impact` with covers) naming the revision read.
- **`unresolved`**: Use when rule and code contradict each other and you cannot tell which is wrong, or when a named proof test fails. Change nothing on that rule. Write an `unresolved` row naming the exact contradiction or failing test, report it up the chain, and continue with the rest of the list.

Every rule row in a consolidation task must record proof evidence: `testsRun` (list of executed test entries with pass/fail result), `testsNotRun` (list of named tests that could not be run with reason), or `noProofRecorded: true` when no proof exists. A rule without recorded proof can still `stands` when code supports it. Link an existing fitting test as a proof when one exists; write no test and no code, and report rules with no fitting test as gaps.

Families receive the same three outcomes (`stands`, corrected via `changed`/`rerouted`/`assigned`/`retired`, or `unresolved`). A family row speaks for the family record alone; each member's outcome belongs on the member's own row. When a listed family is also reached by a corrected member, or a listed rule is also touched by a newly linked proof, one row for that subject closes both items.

## Report and hand over

Write the consolidation pass report in the supplied report path naming:

- the code tree, memory tree, and `consolidationList` examined;
- every listed family and rule with its outcome, revision, and key changes;
- the exact test commands executed, their results, and confirmation that the code tree matches its base;
- every `unresolved` row (rule ID, exact contradiction or failing test, affected files) for routing to the Architect;
- every rule with no fitting test (gap list) for routing to the Architect;
- full check results (`git diff --check`, `knowledge_integrity_check`, `memory_quality_check`, `knowledge_read(view="dependent", staleOnly=true)`, and `curator_coherence`).

Hand the memory candidate (including its exact 40-character memory Git tree for `candidateTree`) and report path directly to the Reviewer with `role_message` (naming leaf refs, candidate identity, report path, and next stretch). Notify the Manager of all `unresolved` items and gaps so the Manager routes them to the Architect to open issues.

# Knowledge use at the working step

All eight roles use this file to read code with attributed knowledge, bind and complete knowledge reads, distinguish recorded proofs from fresh results, and inspect the source that owns a setting. The Architect, Investigator, Worker and Reviewer sections state their additional actions.

## Read code and knowledge together

Start with either a text search or `knowledge_read`; the tool needs a seed, file path or identifier. When a search finds candidates, use `knowledge_read` to expand them to the full map of attributed records, sibling realizations and proofs.

Use both knowledge and code reads. Attributions find what text search misses; code reads fill gaps when memory is stale or missing. Stale memory may guide orientation when you label it explicitly as stale and check current claims against code. Informative evidence is not publication-ready evidence.

## Bind and complete the knowledge read

Use `knowledge_read` on `agents-remember-task` with these bindings:

- `memoryRoot`: the assigned memory tree;
- `repositoryRoot`: its paired code checkout;
- `codeTreeId`: the exact inspected code tree.

Use the handover's binding. For a taskless investigation, use the selected repository's resolved pair.

With `view="source_context"`, set `sourcePath` to the repository-relative file to find its attributed invariants and families. Read every page through `continuation` until enumeration is complete. Then open every named record file with your own tools: rows name files, not their text.

Read a named invariant with `view="invariant", recordId="INV-…"` and a named family with `view="family", recordId="FAM-…"`. Inspect their sibling realizations and backing `proofs`, and open the named code and tests.

Inspect governing families named with `role="chain"` even when the seed path has no direct realization. To expand a family’s full source context, use `view="source_context"` and set `familyRevisionId` to the family ID named by the chain row, without `sourcePath`. Keep the same memory and code bindings, finish its pages and open the named members and evidence.

A proof records what a test demonstrates, not that it passed. Report a refusal, partial index or unverified currentness as a gap; none establishes an empty or complete result.

An unresolved-anchor count covers anchors not established against their exact recorded blob, including anchors whose resolution was not requested. Distinguish an unrequested check from an observed resolution failure. Currentness counts describe the named invariant population, including related invariants selected by expansion. Neither count proves semantic coverage, source-wide correctness or a passing test.

## Read the source that owns a setting

When an assignment needs a setting fact, read its authority. Use `server_info` on `agents-remember-task` for the loaded build and configured roots, the resolved memory settings for source-bound eligibility, and the user settings for role policy. Open the exact settings paths the context names; the compact packet’s omitted policy fields are not defaults you may infer. Use the existing resolver rather than constructing a second one.

## Architect: diagnose before proposing a repair

When diagnosing a failure before proposing a repair task, use attributed invariants, families, sibling code and proofs alongside code reads to form and check the cause. Carry the evidence and remaining gaps into the packet and Prepared context.

## Investigator: distinguish claims, observations and hypotheses

When investigating repository code, use attributed invariants, families, sibling realizations and proofs alongside code, logs and checks. Distinguish recorded claims, observed evidence and your hypothesis in the report.

## Worker: read before editing and after a test fails

Before editing, read the records attributing your files, their sibling realizations and backing proofs. Use them to find relevant code and preserve or implement the task's required guarantees, even when the handover names no invariant or family.

When a test fails, inspect its recorded `proves` links and read the named invariant and siblings to diagnose which guarantee failed. If no link is recorded, read the test and code and state that gap.

Do not change a sibling outside the assigned scope; report it to its owner.

## Reviewer: inspect every changed file

For changed files, use attributed invariants and families to check that guarantees are kept or updated as required, find missed siblings, and compare backing proofs with runs on the exact candidate. Name unrun proofs and evidence gaps.

Attribution adds a review dimension; it filters nothing.

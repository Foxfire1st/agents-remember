# Knowledge use at the working step

## Shared

You may start with a text search or with the tool; the tool needs a seed, a file path or an ID. When a search has found candidates, widen them with `knowledge_read` to the full map: attributed records, sibling realizations and proofs. Where the memory is stale or missing, read the code. Either way, use both.

Knowledge reads and plain code reads complement each other: attributions find what text search misses, and code reads fill gaps where memory is stale or missing.

Use `knowledge_read` on `agents-remember-task` with the assigned memory tree as `memoryRoot`, its paired code checkout as `repositoryRoot` and the exact inspected code tree as `codeTreeId`; use the handover's binding, or the selected repository's resolved pair for a taskless investigation. With `view="source_context"`, set `sourcePath` to the repository-relative file to find its attributed invariants and families. Read every page through `continuation` until enumeration is complete, then open the named record files with your own tools; rows name files, never their text. Read a named invariant with `view="invariant", recordId="INV-…"` and a named family with `view="family", recordId="FAM-…"`; inspect their sibling realizations and backing `proofs`, and open the named code and tests. A proof records what a test demonstrates, not that it passed. A refusal, partial index or unverified currentness is a stated gap, not an empty or complete result.

## Architect — diagnosis

When diagnosing a failure before proposing a repair task, use the attributed invariants, families, sibling code and proofs alongside your code reads to form and check the cause; carry the evidence and remaining gaps into the packet and Prepared context.

## Investigator — investigation

When investigating repository code, use its attributed invariants, families, sibling realizations and proofs alongside code, logs and checks; distinguish recorded claims, observed evidence and your hypothesis in the report.

## Worker — before editing and on a failing test

Before editing, read the records attributing your files, their sibling realizations and backing proofs to find relevant code and preserve or implement the task's required guarantees, whether or not the handover names an invariant or family. On a failing test, inspect its recorded `proves` links and read the named invariant and siblings to diagnose which guarantee failed; where no link is recorded, read the test and code and state that gap. Change no sibling outside the assigned scope; report it to its owner.

## Reviewer — changed files

For changed files, use their attributed invariants and families to check that the guarantees are kept or updated as required, find siblings the change missed, and compare backing proofs with the runs on the exact candidate; name unrun proofs and evidence gaps. Inspect the complete diff, including unattributed files; attribution adds a review dimension and filters nothing.

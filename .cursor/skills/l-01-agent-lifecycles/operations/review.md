# Operation — Review

Review is independent and runs only when the task or owner requests it. The assignment provides the exact requirement revision, candidate/base, review mode, criteria, and report path. Missing candidate or review authority is reported, not guessed.

A baseline covers the complete candidate diff: all changed files, including unattributed changes. Invariant attribution informs the review but does not filter its population. Check each requirement against its evidence class and relevant behavior; a test result is evidence, not semantic acceptance. Keep findings stable and evidence-addressed.

Fix-verification checks only the sealed outstanding findings against the same requirement and candidate lineage. Do not reset the baseline or add findings to that round. A new contradiction or scope issue is returned to the owner separately.

Write one concise pass/block recommendation with changed-file coverage, findings (stable ID, location, evidence, impact, required repair/proof), checks, and limitations. The Reviewer does not edit, record AR task acceptance, or publish Git. The owner validates and records review state after reading the report. Ask the developer through the active native conversation; send peer clarification only to an actual Orca recipient supplied or discovered for this task.

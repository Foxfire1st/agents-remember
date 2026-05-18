## Start-of-Task Onboarding Trust Gate

### Single-Repository Workflow

This gate applies ALWAYS at the start for every Task. Even for code explanations!
No matter if that touches, explains, reviews, plans around,
debugs, or changes a repository code area. Read-only analysis is not an
exception. Code explanation is not an exception. Review is not an exception.
Planning is not an exception.

Before opening, reading, summarizing, or reasoning from source file contents in
the relevant repository you must perform these six gates in order:

Gate 1: Invoke `C-08-ar-coordination-context-resolver` for the target repository and use its resolved context for the authoritative `coordination_root`, `memory_root`,
onboarding root, settings path, task root, docs root, system files, storage semantics, `pathRules`, task/worktree context, ledger path, and cross-repo allowances.

Gate 2: Run `C-02-onboarding-drift-detection` for the relevant repository and then read its drift report.
Do not for any reason skip execution of the drift detection skill.

Gate 3: If the drift report indicates any drifted, missing-verification, or orphaned onboarding, tell the developer what
the report says briefly and then ask if they want to update the onboarding before proceeding.

Gate 4: If they say yes, then orchestrate the update process and split the work to up to 5 sub agents who each handle at max 15 files.
All sub agents shall use this skill: `C-05-create-or-update-onboarding-files` and you pass it the instructions it needs to perform the job.
If the developer says no, tell them that reasoning over drifted onboardings may introduce risk of regressions.

Gate 5: Run `C-02-onboarding-drift-detection` again to confirm that all onboarding is now verified and up to date.
Do not for any reason skip execution of the drift detection skill.

Gate 6: Only after steps 1 - 5 are completed, report to the developer. Then delete the drift report file.

### Cross-Repository Workflow

When working with cross-repo enabled and one or more repos are listed, the above gate execution order changes.

For every repo in the Cross-Repo list, you run first Gate 1-3 to create individual drift reports.
Then you report to the developer about all drift reports and ask if they want to update the onboarding before proceeding.
Depending on their answer, you delegate for each approved repo a sub agent to execute Gate 4 - 6.

---

## Post-Gate Planning and Research

For onboarding-backed source reading, use `C-04-onboarding-read-mode`. C-04 owns
the overview -> route overview -> candidate source/sidecar paired-read protocol.

---

## Post-Gate Implementation

- When you make code changes, do also update or create onboardings using
  `C-05-create-or-update-onboarding-files`.
- Once the hard onboarding gate has passed for the task's repository context,
  files created or modified during the current task may still be opened, read,
  and reasoned about within that same task even though they are now pending
  verification.
- You may use a sub agent if the list of changed source files is greater than three.
- Update onboardings before you mark an implementation phase/step done.

Gate 1: After implementing a plan phase, update or create the onboarding files for changed source files
using the `C-05-create-or-update-onboarding-files` skill.

---

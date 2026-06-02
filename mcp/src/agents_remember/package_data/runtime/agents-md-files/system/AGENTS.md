## Start-of-Task Onboarding Trust Gate

### Single-Repository Workflow

This gate applies ALWAYS at the start for every Task. Even for code explanations!
No matter if that touches, explains, reviews, plans around,
debugs, or changes a repository code area. Read-only analysis is not an
exception. Code explanation is not an exception. Review is not an exception.
Planning is not an exception.

Before opening, reading, summarizing, or reasoning from source file contents in
the relevant repository you must perform these seven gates in order:

Gate 1: Invoke `c-08-ar-coordination-context-resolver` for the target repository and use its resolved context for the authoritative `coordination_root`, `memory_root`,
onboarding root, settings path, task root, docs root, system files, storage semantics, `pathRules`, task/worktree context, ledger path, and cross-repo allowances.

Gate 2: If the Agents Remember MCP server is configured, call its
`context_packet` MCP tool for the target repo with provider inspection enabled.
Provider authority comes from the MCP settings file.

```text
context_packet(repo_id="<repo-id>", include_providers=true)
```

Skip this gate only when no Agents Remember MCP server is configured or the MCP
settings report no providers.

Gate 3: Run `c-02-memory-quality-control` for the relevant repository and then read its task-start drift report.
Do not for any reason skip execution of the drift detection skill.

Gate 4: If the drift report indicates any drifted, missing-verification, or orphaned onboarding, classify the findings before proceeding.

- Drifted onboarding whose corresponding source file is not dirty in the worktree is an onboarding update candidate.
- Drifted onboarding whose corresponding source file is dirty in the worktree is active work-in-progress. Leave it alone.
- Do not infer task relevance as a reason to skip this classification.

Tell the developer what the drift report says briefly, explicitly list the update candidates and the dirty-source findings that will be left alone, and ask whether to update the candidates before proceeding.
Do not silently drop, ignore, or stop using onboarding after drift detection.

Gate 5: If they say yes, then orchestrate the update process for the update candidates and split the work to up to 5 sub agents who each handle at max 15 files.
All sub agents shall use this skill: `c-05-create-or-update-onboarding-files` and you pass it the instructions it needs to perform the job.
Do not update dirty-source drift findings unless the developer explicitly takes ownership of that active work-in-progress.
If the developer says no, tell them that reasoning over drifted onboardings may introduce risk of regressions.

Gate 6: Run `c-02-memory-quality-control` again to confirm that all onboarding is now verified and up to date.
Do not for any reason skip execution of the drift detection skill.

Gate 7: Only after steps 1 - 6 are completed, report to the developer. Then delete the drift report file.

### Cross-Repository Workflow

When working with cross-repo enabled and one or more repos are listed, the above gate execution order changes.

For every repo in the Cross-Repo list, you run first Gate 1-3 to create individual drift reports.
Then you report to the developer about all drift reports and ask if they want to update the onboarding before proceeding.
Depending on their answer, you delegate for each approved repo a sub agent to execute Gate 5 - 7.

---

## Post-Gate Planning and Research

For context-backed source reading, use `c-04-retrieval-strategy-router`. The `c-04-retrieval-strategy-router` skill
owns Semantics, Relationship, and Intent routing across optional providers,
route indexes, onboarding, and bounded source confirmation.

---

## Post-Gate Implementation

- When you make code changes, do also update or create onboardings using
  `c-05-create-or-update-onboarding-files`.
- Once the hard onboarding gate has passed for the task's repository context,
  files created or modified during the current task may still be opened, read,
  and reasoned about within that same task even though they are now pending
  verification.
- You may use a sub agent if the list of changed source files is greater than three.
- Update onboardings before you mark an implementation phase/step done.

Gate 1: After implementing a plan phase, update or create the onboarding files for changed source files
using the `c-05-create-or-update-onboarding-files` skill.

---

## Code Quality Instructions

After the `c-08-ar-coordination-context-resolver` skill resolves context, use the resolved memory layer's `system/tools.md`
for repository-specific test, lint, typecheck, build, smoke-check, branch, and
local command guidance. Use `system/coding-guidelines.md` when present for
repo-specific coding rules.

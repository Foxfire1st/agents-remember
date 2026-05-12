---
name: w-03-chat-task-workflow
description: "In-between task workflow for work that needs a durable task file, distinct implementation examples, and an approval gate but still fits a single-page implementation plan as a rule of thumb."
---

## Chat Based Coding Workflow

1. At the start of a coding workflow, invoke `C-08-ar-coordination-context-resolver` for the relevant repository, then invoke `C-02-onboarding-drift-detection` with the resolved context once for that repository. Do not plan against drifted, missing-verification, or orphaned pre-existing onboarding until it has been refreshed through `C-05-create-or-update-onboarding-files` under `Autonomous Onboarding Maintenance`. This establishes the task-start trust baseline. Do not skip this step, and do not re-trigger it solely because the current task later creates or modifies files in that repository.

2. During investigation, read each relevant source file with its verified onboarding as a pair. If the current task has already modified or created that pair after the gate passed, read the current working versions together and treat them as pending verification rather than re-verified onboarding. Do not bulk-read onboarding as detached background context, and do not defer the onboarding read until after source interpretation. After enough paired reads, show the developer the plan in chat, including code examples for every distinct change you intend to make. Wait for explicit developer approval before you start changing any code.

3. After approval, apply code changes and update the corresponding onboarding in the same editing pass whenever the change affects durable current-state knowledge. Do not postpone required onboarding changes to the end of the task. Use the appropriate code quality checks from the C-08 resolved `tools_path`.

4. When an approved chat-mode edit is small enough to stay in the current checkout, close it out through C-09 `direct-closeout` instead of hand-assembling the Git sequence. The command owns the shared-memory invariant: preview first, get explicit commit approval, commit code, refresh affected onboarding metadata to the new code commit, commit memory content, then update and commit the ledger. If required onboarding is missing, run C-05 for the affected source file and rerun the direct closeout preview.

---
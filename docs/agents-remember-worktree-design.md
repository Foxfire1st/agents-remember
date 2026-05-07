# Agents Remember Worktree Coordination Design

## Executive summary

The current `agents-remember-md` implementation has already moved in the right direction for worktree support, even though worktrees are not implemented yet.

The important foundation is `C-08-ar-management-resolver`: it separates the question “where is the active Agents Remember management context for this repo?” from the agent’s own reasoning. That becomes more valuable with worktrees, not less, because worktree paths, branches, base commits, and paired code/onboarding roots are operational state that moves frequently. Hard-coding all of that into a shared global config file would create stale-path problems and conflict-prone writes when multiple agents operate in parallel.

The recommended design is:

1. Keep `C-08` focused on resolving active management context.
2. Add a new worktree coordination layer, probably `C-09-worktree-context-resolver` plus a `W-03-worktree-task-workflow`.
3. Use the shared `ar-management/` root as the coordination place for worktree checkouts regardless of whether the target repo stores onboarding internally or in the shared root.
4. Store durable task intent separately from ephemeral checkout contents.
5. Make every worktree task carry an explicit task contract: repo identity, task identity, code branch, onboarding branch when applicable, base commits, source branches, and the resolver inputs needed to rehydrate the context.
6. Make closeout responsible for checking whether the source branch moved since the worktree was created and whether those newer changes touched the same onboarding units.

---

## Current repo foundation

### What already supports the direction

The current implementation already has the right architectural split:

- `AGENTS.md` requires the agent to infer the target repository and resolve the active `ar-management/` context before relying on onboarding, task files, docs, or tools.
- `C-08-ar-management-resolver` returns topology, repo name, target repo, management root, onboarding root, settings, task root, docs root, system root, tools path, storage mode, path rules, and cross-repo allowances.
- `C-08` supports internal and shared topology.
- `C-08` accepts explicit `target_repo`, `shared_root`, `requested_topology`, and compatibility overrides.
- Mixed workspaces are already part of the design: one repo can be internal while a neighboring repo is shared-managed.
- `C-02-onboarding-drift-detection` already works against the resolved context and writes drift reports under the resolved management root.
- `C-02` is already intended to run at task start and again near closure.
- `W-02-light-task-workflow` already treats the task file as a live contract for requirements, decisions, checklist state, and proposed implementation shape.
- `C-05-create-or-update-onboarding-files` already treats `Update History` as append-only.

This means the project is not far from worktree support conceptually. It does not need a rewrite. It needs a worktree layer that composes with the resolver and task workflow.

### What is missing

The repo does not yet have:

- a `worktrees/` folder convention,
- a worktree scaffold,
- a worktree resolver,
- a task-contract section for worktree paths and paired branches,
- a closeout gate that compares the worktree’s base commit against an advanced source branch,
- a shared-managed “paired code + onboarding worktree” contract,
- `.gitignore` coverage for `worktrees/`,
- explicit exclusion of `worktrees/**` from onboarding/path-rule scans,
- a rule that records task identity in onboarding update history.

Those should be added as first-class workflow pieces rather than hidden inside agent prose.

---

## Core design distinction

Worktree support introduces two contexts that must not be collapsed into one:

### 1. Active management context

This is what `C-08` already resolves.

For internal repos, the active management root is:

```text
<code-worktree>/ar-management/
```

For shared-managed repos, the active management root is:

```text
<shared-management-worktree>/
```

and the repo-specific onboarding root is:

```text
<shared-management-worktree>/onboarding/<repo-name>/
```

This context answers: “Where should this agent read and update onboarding, tasks, docs, settings, tools, and path rules for the repository being worked on?”

### 2. Worktree coordination context

This is new.

It should always live under the shared `ar-management/` root, regardless of whether the target repo uses internal or shared onboarding.

This context answers: “Where are the operational checkouts for this task, which branches do they use, what base commit were they created from, and how should an agent recover the same working context later?”

Do not make `C-08` own this. Add a worktree-specific resolver that can call `C-08` after it has resolved the code worktree and, when needed, the onboarding/management worktree.

---

## Proposed folder layout

### Shared coordination root

The shared coordination root should be the shared `ar-management/` folder:

```text
ar-management/
  system/
  onboarding/
  tasks/
  docs/
  notes/
  worktrees/
```

`worktrees/` is operational. It should normally be ignored by Git if the shared `ar-management/` root is itself tracked.

Recommended `.gitignore` addition in the shared management root:

```gitignore
worktrees/
```

Recommended stable settings addition:

```json
{
  "version": 1,
  "worktrees": {
    "layoutVersion": 1,
    "root": "worktrees",
    "taskContractsRoot": "tasks/worktrees",
    "branchPrefix": "ar"
  },
  "onboarding": {
    "pathRules": {
      "exclude": {
        "paths": ["worktrees/**"]
      }
    }
  }
}
```

The exact JSON shape can be adapted to the existing settings parser. The important rule is that `worktrees/**` must never be treated as source material to onboard.

---

## Layout for internal-managed repos

Internal-managed repos store onboarding inside the repo itself. For those repos, only one worktree is needed: the code repo worktree. Its internal `ar-management/` folder travels with the code branch.

```text
ar-management/
  worktrees/
    <repo-name>/
      <task-id>/
        <code-worktree-name>/
          .git
          src/
          ar-management/
            onboarding/
            tasks/
            docs/
            notes/
            system/
```

Example:

```text
ar-management/worktrees/payments-api/ARWT-20260507-token-refresh/token-refresh-code/
```

Resolver call after creation:

```bash
C-08 \
  --repo-name payments-api \
  --repo ar-management/worktrees/payments-api/ARWT-20260507-token-refresh/token-refresh-code \
  --topology internal
```

The key invariant is that the canonical repo name remains `payments-api`. The worktree folder name must not become the repo identity.

---

## Layout for shared-managed repos

Shared-managed repos need two linked worktrees:

1. a code worktree for the product/source repository,
2. an onboarding/management worktree for the shared `ar-management` repository branch that carries onboarding changes.

Recommended layout using your corrected repo-first grouping:

```text
ar-management/
  worktrees/
    <repo-name>/
      <task-id>/
        code/
          <code-worktree-name>/
        onboarding/
          <onboarding-worktree-name>/
```

Example:

```text
ar-management/worktrees/payments-api/ARWT-20260507-token-refresh/code/token-refresh-code/
ar-management/worktrees/payments-api/ARWT-20260507-token-refresh/onboarding/token-refresh-onboarding/
```

Important: even though the folder is named `onboarding/`, the checkout inside it should be a full shared management-root worktree, not only the `onboarding/` subtree. It must contain the whole management root shape:

```text
ar-management/worktrees/payments-api/ARWT-20260507-token-refresh/onboarding/token-refresh-onboarding/
  system/
  onboarding/
    payments-api/
  tasks/
  docs/
  notes/
```

Resolver call after creation:

```bash
C-08 \
  --repo-name payments-api \
  --repo ar-management/worktrees/payments-api/ARWT-20260507-token-refresh/code/token-refresh-code \
  --topology shared \
  --shared-root ar-management/worktrees/payments-api/ARWT-20260507-token-refresh/onboarding/token-refresh-onboarding
```

The paired worktrees must move as a unit. The task contract must record both branches and both base commits.

---

## Task contract design

The task contract is the key piece. It gives agents a stable way to recover the worktree context even if they lose the conversation state.

Avoid one global mutable file that lists all active worktrees. That would become a write-conflict hotspot. Use one contract per task.

### Recommended split

Use two levels:

1. Durable task contract, tracked if the shared management root is tracked:

```text
ar-management/tasks/worktrees/<repo-name>/<task-id>.md
```

2. Local operational checkout area, ignored:

```text
ar-management/worktrees/<repo-name>/<task-id>/...
```

The durable task file records stable, reviewable information: repo, task id, branch names, source branches, base commits, final commits, drift report summaries, and recovery instructions.

The ignored `worktrees/` folder contains the actual checkouts and optional local files with absolute paths or local-only status. Do not rely on ignored files as the only record of task identity.

### Minimal task contract section

Add this section to worktree-backed task files:

```markdown
## Worktree Contract

```json
{
  "version": 1,
  "taskId": "ARWT-20260507-token-refresh",
  "taskName": "Fix token refresh handling",
  "repoName": "payments-api",
  "topology": "internal",
  "coordinationRoot": "ar-management",
  "worktreeRoot": "worktrees/payments-api/ARWT-20260507-token-refresh",
  "source": {
    "branch": "main",
    "baseCommit": "<40-char-sha>",
    "baseCheckedAt": "2026-05-07T12:00"
  },
  "code": {
    "worktree": "worktrees/payments-api/ARWT-20260507-token-refresh/token-refresh-code",
    "branch": "ar/ARWT-20260507-token-refresh/code",
    "sourceBranch": "main",
    "baseCommit": "<40-char-sha>",
    "headCommit": ""
  },
  "onboarding": {
    "mode": "internal",
    "worktree": "worktrees/payments-api/ARWT-20260507-token-refresh/token-refresh-code/ar-management",
    "branch": "ar/ARWT-20260507-token-refresh/code",
    "sourceBranch": "main",
    "baseCommit": "<40-char-sha>",
    "headCommit": ""
  },
  "resolver": {
    "repoName": "payments-api",
    "targetRepo": "worktrees/payments-api/ARWT-20260507-token-refresh/token-refresh-code",
    "requestedTopology": "internal",
    "sharedRoot": ""
  },
  "integration": {
    "lastSourceCheckAt": "",
    "lastSourceCommitChecked": "",
    "sourceAdvancedSinceBase": false,
    "overlappingOnboardingChanges": []
  },
  "status": "planning"
}
```
```

For shared-managed repos, change the topology and onboarding block:

```json
{
  "topology": "shared",
  "code": {
    "worktree": "worktrees/payments-api/ARWT-20260507-token-refresh/code/token-refresh-code",
    "branch": "ar/ARWT-20260507-token-refresh/code",
    "sourceBranch": "main",
    "baseCommit": "<code-base-sha>",
    "headCommit": ""
  },
  "onboarding": {
    "mode": "shared",
    "worktree": "worktrees/payments-api/ARWT-20260507-token-refresh/onboarding/token-refresh-onboarding",
    "branch": "ar/ARWT-20260507-token-refresh/onboarding",
    "sourceBranch": "main",
    "baseCommit": "<ar-management-base-sha>",
    "headCommit": ""
  },
  "resolver": {
    "repoName": "payments-api",
    "targetRepo": "worktrees/payments-api/ARWT-20260507-token-refresh/code/token-refresh-code",
    "requestedTopology": "shared",
    "sharedRoot": "worktrees/payments-api/ARWT-20260507-token-refresh/onboarding/token-refresh-onboarding"
  }
}
```

The task contract should use relative paths from the shared coordination root where possible. Absolute paths can exist in an ignored local cache, but not as the durable source of truth.

---

## New resolver/workflow pieces

### Add `C-09-worktree-context-resolver`

Purpose: resolve and validate the worktree context for a repo/task.

Inputs:

```text
repo_name
worktree_task_id
coordination_root optional
code_worktree optional
onboarding_worktree optional
requested_topology optional
format json|text
```

Outputs:

```text
repo_name
task_id
coordination_root
contract_path
worktree_root
code_worktree
onboarding_worktree
code_branch
onboarding_branch
code_source_branch
onboarding_source_branch
code_base_commit
onboarding_base_commit
active_c08_input
active_c08_output
status
warnings
```

Responsibilities:

- find the shared coordination root,
- derive the expected repo-first worktree paths,
- read the task contract if it exists,
- validate that listed worktrees exist,
- validate that each worktree is attached to the expected branch,
- validate that `repoName` is explicit,
- call `C-08` with explicit `--repo-name`, `--repo`, `--topology`, and `--shared-root` when needed,
- return one JSON object downstream skills can consume.

Boundaries:

- It should not replace `C-08`.
- It should not implement onboarding drift detection.
- It should not silently create worktrees unless invoked in an explicit create/init mode.
- It should not write a global registry of active worktrees.

### Add `W-03-worktree-task-workflow`

Purpose: orchestrate the full lifecycle.

Phases:

1. Preflight baseline.
2. Worktree creation.
3. Task planning and implementation using C-08 + C-02 + C-05.
4. Pre-commit integration gate.
5. Commit and paired-branch finalization.
6. Cleanup or archive.

---

## Worktree lifecycle

### Phase 1 — Preflight baseline before task worktree creation

Before creating the task worktree, validate the source branch that the task will branch from.

For internal repos:

1. Resolve the source repo and source branch.
2. Ensure the source branch is clean enough to trust.
3. Run `C-08` against the source checkout.
4. Run `C-02-onboarding-drift-detection` against that resolved context.
5. If drift exists, refresh onboarding or explicitly block worktree creation.
6. Record the source branch and source commit in the task contract.

For shared-managed repos:

1. Validate the code source branch.
2. Validate the shared management/onboarding source branch.
3. Run `C-08` with explicit shared root.
4. Run `C-02` against the code source + onboarding source pair.
5. Record both base commits.

If no clean checkout of the source branch is available, the workflow may create a short-lived preflight worktree, run drift detection there, and remove it. That preflight worktree is not the task worktree.

### Phase 2 — Create worktrees

Internal-managed repo:

```bash
git -C <source-repo> worktree add \
  <coordination-root>/worktrees/<repo-name>/<task-id>/<code-worktree-name> \
  -b ar/<task-id>/code \
  <source-branch>
```

Shared-managed repo:

```bash
git -C <code-source-repo> worktree add \
  <coordination-root>/worktrees/<repo-name>/<task-id>/code/<code-worktree-name> \
  -b ar/<task-id>/code \
  <code-source-branch>

git -C <shared-management-root> worktree add \
  <coordination-root>/worktrees/<repo-name>/<task-id>/onboarding/<onboarding-worktree-name> \
  -b ar/<task-id>/onboarding \
  <onboarding-source-branch>
```

After creation, immediately run `C-09` and then `C-08` using the explicit task contract values. Do not infer repo identity from the leaf worktree directory.

### Phase 3 — Plan and implement

Use the existing discipline:

1. `C-09` resolves worktree context.
2. `C-08` resolves active management context from the code worktree and onboarding/management worktree.
3. `C-02` verifies onboarding before planning.
4. The task file carries plan, decisions, proposed code examples, worktree contract, and later closeout state.
5. Code changes and onboarding changes are updated together during implementation.

For internal repos, code and onboarding changes are in one Git branch and should usually land in the same commit or same small commit series.

For shared-managed repos, code and onboarding changes are in separate repositories and separate worktrees. They must be treated as one logical unit through the task contract.

### Phase 4 — Pre-commit integration gate

Before committing final work, the worktree must check whether its source branch changed since task creation.

Do not use an arbitrary number of commits. Use the base commit recorded in the task contract.

For each source branch:

```bash
git fetch --all --prune

git rev-parse <source-branch>
git merge-base --is-ancestor <base-commit> <source-branch>
git log --oneline <base-commit>..<source-branch>
```

Interpretation:

- If `<base-commit>..<source-branch>` is empty, the source branch did not advance.
- If it is non-empty, the source branch moved and the worktree must integrate or at least re-check against those changes.
- If `base-commit` is no longer an ancestor of the source branch, the source branch was likely rewritten; require manual review.

For local unpushed source commits, also inspect:

```bash
git log --oneline @{u}..HEAD
```

Unpushed commits should be treated as a high-risk parallel-work signal, not as the only range to inspect. Pushed commits can still contradict the current task; they are just more likely to represent already-intended canonical direction.

Required checks:

1. Rebase or merge the latest source branch into the worktree branch when appropriate.
2. Re-run `C-08` and `C-02` after integration.
3. Determine onboarding files changed by this task.
4. Determine onboarding files changed on the source branch since the task base commit.
5. If the intersection is non-empty, run a coherence review before committing.
6. Run code checks from resolved `system/tools.md`.
7. Update the task contract with the latest source commit checked.

Changed onboarding intersection for internal repos:

```bash
git -C <code-worktree> diff --name-only <base-commit>...HEAD -- ar-management/onboarding/
git -C <source-repo> diff --name-only <base-commit>..<source-branch> -- ar-management/onboarding/
```

Changed onboarding intersection for shared-managed repos:

```bash
git -C <onboarding-worktree> diff --name-only <onboarding-base-commit>...HEAD -- onboarding/<repo-name>/
git -C <shared-management-source> diff --name-only <onboarding-base-commit>..<onboarding-source-branch> -- onboarding/<repo-name>/
```

Git merge conflicts are not enough. Git catches textual conflicts. It does not reliably catch semantic contradictions in onboarding prose, invariants, or cross-repo notes.

### Phase 5 — Commit and finalize

Internal-managed repos:

- Commit code and onboarding together when practical.
- Add the task id to the commit message trailer.
- Update the task contract with final code/onboarding commit.

Example trailer:

```text
Agents-Remember-Task: ARWT-20260507-token-refresh
Agents-Remember-Repo: payments-api
```

Shared-managed repos:

- Commit code branch and onboarding branch as a logical pair.
- Do not push one without the other unless the task contract explicitly marks the pair as intentionally partial.
- Record both final commits in the task contract.
- Use the same task id in both commit messages.

Example trailers for both repositories:

```text
Agents-Remember-Task: ARWT-20260507-token-refresh
Agents-Remember-Repo: payments-api
Agents-Remember-Pair: code+onboarding
```

If the code commit and onboarding commit need to reference each other exactly, record that in the task contract rather than trying to force atomicity across two Git repositories.

### Phase 6 — Cleanup

When a task is complete:

1. Ensure the final task contract records final commits and status.
2. Ensure branches were pushed or intentionally left local.
3. Remove task worktrees with `git worktree remove`, not by manually deleting folders.
4. Run `git worktree prune` when needed.
5. Preserve the durable task file under `tasks/worktrees/<repo-name>/<task-id>.md` if it is useful audit history.
6. Delete ignored local-only contract caches if they are no longer useful.

---

## Source-branch advancement and conflict policy

The right question is not “how many commits should we go back?”

The right question is:

```text
What changed on the source branch since the base commit this worktree was created from?
```

That is the range:

```text
<task-base-commit>..<current-source-branch>
```

This should be checked for every worktree closeout.

### Pushed vs unpushed commits

Treat pushed/unpushed as a trust/risk signal, not as the definition of what to check.

- Unpushed commits on the source branch are suspicious because they may represent another local parallel task that has not been externally reviewed or synchronized.
- Pushed commits communicate stronger intent, but they can still invalidate the current worktree’s assumptions.
- Both kinds of commits should be included if they are descendants of the task base commit and ancestors of the current source branch.

Recommended policy:

1. Always check `base..source`.
2. Separately classify whether those commits are pushed or unpushed.
3. Require stricter coherence review for unpushed overlapping onboarding changes.
4. Still rebase/merge and re-run drift detection for pushed source advancement.

---

## Onboarding update history enhancement

Adding task identity to onboarding `Update History` is a good idea.

Current rule: `Update History` is append-only.

Recommended new row shape:

```markdown
## Update History

| Date-Time | Task ID | Task | Source Commit | Onboarding Commit | Summary |
| --- | --- | --- | --- | --- | --- |
| 2026-05-07T12:00 | ARWT-20260507-token-refresh | Fix token refresh handling | `<sha>` | `<sha>` | Updated refresh-token boundary notes after approved implementation. |
```

For internal repos, `Source Commit` and `Onboarding Commit` may be the same commit.

For shared-managed repos, they are usually different commits in different repositories.

Benefits:

- Agents can see which task introduced a change.
- Agents can open the corresponding task file when two onboarding updates appear related.
- Parallel worktree conflicts become easier to detect even when Git does not produce a merge conflict.
- The audit trail remains local to the onboarding unit instead of requiring a global registry.

Do not turn `Update History` into a huge global coordination mechanism. Keep it append-only and local to the file-level onboarding unit.

---

## Resolver vs configuration-only decision

A configuration-only design would work for stable facts:

- where the shared root usually lives,
- what folder pattern worktrees use,
- what branch prefix to use,
- which path rules apply,
- whether a repo is internal or shared-managed.

It is a poor fit for volatile facts:

- which task worktrees currently exist,
- which branch each worktree has checked out,
- what base commit was used,
- whether a source branch advanced,
- whether a linked worktree was removed,
- whether a shared-managed task has both code and onboarding worktrees still valid.

Those volatile facts should be resolved from a task contract, filesystem state, and Git state at runtime. That is exactly where a resolver pays off.

The best balance is:

```text
stable policy            -> settings.json
per-task durable intent  -> task contract
local checkout reality   -> C-09 resolver + git worktree list/status
active memory context    -> C-08 resolver
onboarding trust         -> C-02 drift detection
```

Do not put active worktree state into `system/settings.json`.

---

## Implementation changes to request from a coding agent

### 1. Add shared worktree coordination scaffold

Add support for creating, repairing, or documenting:

```text
<shared-ar-management>/worktrees/
<shared-ar-management>/tasks/worktrees/
```

Add `.gitignore` support:

```gitignore
worktrees/
```

Add a stable settings section for worktree conventions, but keep active worktree state out of settings.

### 2. Add `C-09-worktree-context-resolver`

Create:

```text
skills/U-01-core-skills/C-09-worktree-context-resolver/SKILL.md
skills/U-01-core-skills/C-09-worktree-context-resolver/scripts/worktree_context_resolver.py
```

The script should use only Python standard library plus shelling out to `git` where needed, matching the spirit of `C-08`.

It should output JSON.

### 3. Add `W-03-worktree-task-workflow`

Create:

```text
skills/W-03-worktree-task-workflow/SKILL.md
skills/W-03-worktree-task-workflow/template.md
skills/W-03-worktree-task-workflow/workflow.md
```

This workflow should define:

- preflight drift baseline,
- internal-managed one-worktree creation,
- shared-managed paired worktree creation,
- task contract creation,
- implementation rules,
- pre-commit integration gate,
- closeout/finalization,
- cleanup.

### 4. Extend W-02 or make W-03 opt-in

Do not force every light task into worktrees.

Recommended:

- Keep `W-02` as the normal durable task workflow.
- Add `W-03` as the worktree-backed variant.
- Let `W-03` reuse the W-02 task style but require the `Worktree Contract` section.

### 5. Extend onboarding templates

Update the file-level onboarding template’s `Update History` section to include task identity.

Recommended columns:

```text
Date-Time | Task ID | Task | Source Commit | Onboarding Commit | Summary
```

Update `C-05` so maintenance agents know to fill the new columns.

### 6. Extend drift/integration guidance

`C-02` can stay focused on drift classification, but `W-03` should require two uses:

1. task-start baseline before creating or activating the worktree,
2. closeout check after integrating any source branch advancement and before final commit.

The closeout check should explicitly compare the task base commit to the current source branch.

### 7. Exclude worktrees from onboarding scans

Add or document path-rule exclusions for:

```text
worktrees/**
```

Also ensure drift detection does not accidentally treat the shared coordination `worktrees/` directory as source material when checking the management repo itself.

---

## Serious holes to avoid

### Hole 1 — Letting the worktree folder name become repo identity

Never infer repo identity from `<worktree-name>`. Always store and pass `repoName` explicitly.

### Hole 2 — Making `C-08` do everything

`C-08` should resolve active management context. Worktree orchestration should be separate.

### Hole 3 — Storing active worktree paths in global settings

That creates stale paths and shared-file conflicts. Use per-task contracts and runtime resolution.

### Hole 4 — Ignoring the two-repo problem for shared-managed repos

A shared-managed task has two Git histories. Code and onboarding branches need paired finalization, not just “both happen somewhere.”

### Hole 5 — Assuming Git merge conflicts catch onboarding contradictions

They do not. The pre-commit gate must compare overlapping onboarding units and require a semantic coherence check.

### Hole 6 — Treating pushed commits as automatically safe

Pushed commits may represent intended history, but they can still invalidate the task. Always check `base..source`.

### Hole 7 — Putting durable task contracts only inside ignored `worktrees/`

Ignored operational files are good for local cache. The task contract should live in a durable task location if it needs to survive cleanup or be reviewed later.

---

## Minimal viable version

For a first implementation, do this and nothing more:

1. Add `worktrees/` to shared `ar-management/` and ignore it.
2. Add `tasks/worktrees/<repo>/<task>.md` contract files.
3. Add a `W-03` skill that documents the manual workflow.
4. Add a simple `C-09` resolver script that reads the contract and calls/returns the C-08 inputs.
5. Add task-id columns to onboarding `Update History`.
6. Add the pre-commit source-advance check based on `baseCommit..sourceBranch`.
7. For shared-managed repos, require both code and onboarding branches in the task contract.

That should be enough to make the workflow usable without turning it into a full orchestration system.

---

## Recommended final model

### Internal repo task

```text
coordination:
  ar-management/tasks/worktrees/<repo>/<task-id>.md

checkout:
  ar-management/worktrees/<repo>/<task-id>/<code-worktree-name>/

active memory:
  ar-management/worktrees/<repo>/<task-id>/<code-worktree-name>/ar-management/

commit unit:
  one repo, one branch, code + onboarding together
```

### Shared-managed repo task

```text
coordination:
  ar-management/tasks/worktrees/<repo>/<task-id>.md

code checkout:
  ar-management/worktrees/<repo>/<task-id>/code/<code-worktree-name>/

onboarding checkout:
  ar-management/worktrees/<repo>/<task-id>/onboarding/<onboarding-worktree-name>/

active memory:
  ar-management/worktrees/<repo>/<task-id>/onboarding/<onboarding-worktree-name>/

commit unit:
  two repos, two branches, one logical pair recorded in the task contract
```

### Resolver chain

```text
W-03 task workflow
  -> C-09 worktree context resolver
      -> task contract + filesystem + git worktree state
      -> C-08 active management resolver
          -> C-02 drift detection
          -> C-05 onboarding maintenance
```

That keeps the current architecture intact and adds the smallest missing coordination layer.

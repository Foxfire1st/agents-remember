# Agents Remember Worktree + Shared Memory Design Spec

**Status:** finalised alpha design for implementation  
**Audience:** coding agents and maintainers implementing worktree-backed memory support  
**Scope:** breaking changes are allowed; no legacy compatibility layer is required for the current alpha

---

## 1. Executive summary

Agents Remember needs two separate units:

```text
ar-memory     = durable repo memory
ar-coordination = local coordination and worktree orchestration
```

Internal memory lives inside a code repo as `ar-memory/` and is committed with the code. Shared memory lives as a separate Git repo under the local shared coordinator at `ar-coordination/memory-repos/ar-<repo-name>/`. The shared coordinator itself is not the canonical memory repo and is not tracked as a single Git repository.

The final shared-mode invariant is:

```text
one code repo <=> one memory repo
```

There is no shared monorepo memory mode. A single Git history containing multiple unrelated repo memories is explicitly rejected because it tightly couples branching, review, permissions, and recovery across repos that should remain independent.

Worktree support is implemented as a wrapper around existing task workflows. It creates and validates Git worktrees, records task/worktree contracts, invokes the selected workflow, and performs closeout checks. It is not itself a replacement task workflow.

Shared memory uses a per-branch `memory.md` ledger. Each memory branch tracks exactly one code branch. The ledger maps code commits to memory commits in a two-column, newest-first table. This lets the memory layer rewind, branch, validate, or recover in a way that mimics the natural branch coherence internal memory gets for free from Git.

---

## 2. Locked decisions

### 2.1 `ar-memory` replaces internal `ar-coordination`

Internal memory must live in:

```text
<code-repo>/ar-memory/
```

The old idea of an internal `<code-repo>/ar-coordination/` is removed. The project is still alpha, so the implementation should focus on the new architecture and avoid spending effort on legacy alias support.

Use this terminology everywhere:

```text
memory_root       = ar-memory/ or a shared memory repo root
coordination_root = ar-coordination/
```

### 2.2 `ar-coordination` is local coordination only

The shared `ar-coordination/` folder exists to coordinate work. It owns task contracts, task artifacts, notes, worktree folders, local shared settings, and checked-out memory repos.

It is not the canonical memory layer.

Recommended shared coordinator structure:

```text
ar-coordination/
  settings/
  memory-repos/
  tasks/
  notes/
  worktrees/
```

The shared coordinator should generally be untracked. If a user places it inside another Git repository, they are responsible for ignoring it.

### 2.3 One code repo maps to one memory repo

The only supported shared-memory model is:

```text
<code-repo> <=> <memory-repo>
```

Example:

```text
device-management.git <=> ar-device-management.git
billing-api.git       <=> ar-billing-api.git
```

A global shared memory repo containing multiple unrelated repo memories is not supported and should not be documented as an advanced option.

### 2.4 Shared memory repos use the `ar-` prefix

Memory repos should use the prefix:

```text
ar-<code-repo-name>
```

Examples:

```text
ar-device-management
ar-billing-api
ar-resolve-auto-editor
```

Use `ar-`, not `ar_`.

The system does not need to care where companies host those repos. The boundary of Agents Remember is local checkout/integration. A developer can clone an existing memory repo into:

```text
ar-coordination/memory-repos/ar-<repo-name>/
```

or provide an explicit remote/path during setup.

### 2.5 Tasks live in the shared coordinator, not in memory and not in worktrees

Tasks are operational artifacts, not the truth layer.

They belong in:

```text
ar-coordination/tasks/<repo-name>/<task-id-or-name>/
```

Each task folder contains:

```text
contract.md
<task workflow artifacts>
```

Tasks do not belong in the memory layer because:

1. They create too much noise in teams with many developers.
2. They are often abandoned, partial, exploratory, or superseded.
3. Tying tasks and worktrees 1-to-1 is too rigid for follow-up tasks or multi-task features.
4. Onboarding is the durable truth layer; tasks are process artifacts.

Tasks do not belong inside the worktree because the contract is supposed to help agents find the worktree. If the contract is inside the worktree, it fails that purpose.

### 2.6 Memory layer contents are intentionally small

Internal memory root:

```text
<code-repo>/ar-memory/
  onboarding/
    overview.md
  docs/
  settings/
```

Shared memory repo root:

```text
ar-coordination/memory-repos/ar-<repo-name>/
  onboarding/
    overview.md
  docs/
  settings/
  memory.md
```

`memory.md` exists only for shared memory repos because internal memory already shares the code repo's Git history.

The memory layer does not contain `tasks/`, `notes/`, or `worktrees/` by default.

### 2.7 Shared untracked area contents

The shared coordinator owns the noisy local/operational artifacts:

```text
ar-coordination/
  worktrees/      # operational code and memory worktrees
  notes/          # local scratch notes
  tasks/          # contracts and workflow artifacts
  memory-repos/   # parent folder is local; children are individual Git repos
```

The `memory-repos/` folder itself is not a single tracked repo. Its subfolders are independent Git repositories.

### 2.8 Worktree support is a wrapper

Worktree support should be implemented as a wrapper around task workflows.

It may wrap:

```text
chat
light task
heavy task
BMAD
external workflows
```

The wrapper owns Git state, worktree state, memory compatibility, task contracts, and closeout integrity. The wrapped workflow owns the actual work style.

### 2.9 Agents do not auto-commit at task finish

The worktree wrapper should not automatically commit changes simply because a task workflow says it is done.

The closeout sequence is human-in-the-loop:

1. Agent completes work and reports changes.
2. Human reviews code and memory changes.
3. Human approves or asks for corrections.
4. Only after approval does the agent perform the commit/wrap-up sequence.

This keeps parallel agent work manageable. If three agents finish tasks from the same source branch, the human reviewer can approve and integrate them one at a time. Conflicts surface during review/closeout, and the human can direct resolution.

### 2.10 No canonical/reference memory split

When worktrees are used, the agent must not use divergent memory as semi-trusted reference memory.

Memory is either compatible, reconciled, freshly bootstrapped, or disabled.

If the selected code branch has no compatible memory path, the system presents exactly three choices:

```text
1. Reconciliation using drift checks and onboarding updates
2. Clean start with a new memory branch matching the code branch
3. Disable memory for this worktree
```

No half-trusted divergent memory should be used for work.

---

## 3. Core concepts

### 3.1 Code repo

The source repository being edited.

Example:

```text
device-management
```

### 3.2 Internal memory

Memory stored inside the code repo:

```text
<code-repo>/ar-memory/
```

Internal memory is committed in the same Git history as code. It does not need `memory.md` because code and memory already share commits and branches.

### 3.3 Shared coordinator

The local `ar-coordination/` folder used for orchestration:

```text
ar-coordination/
```

It holds worktrees, task contracts, notes, settings, and local checkouts of per-repo memory repositories.

### 3.4 Shared memory repo

A separate Git repository that holds memory for exactly one code repo.

Example:

```text
ar-coordination/memory-repos/ar-device-management/
```

This repo contains `onboarding/`, `docs/`, `settings/`, and `memory.md`.

### 3.5 Worktree group

A filesystem group for paired code/memory worktrees.

Structure:

```text
ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/
```

For shared memory, this folder contains both code and memory worktrees:

```text
ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/
  <worktree-name>/
  memory-<worktree-name>/
```

For internal memory, it only needs the code worktree because memory lives inside the code repo:

```text
ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/
  <worktree-name>/
```

### 3.6 Task contract

A local markdown file under the shared coordinator that records the relationship between task, workflow artifacts, and worktree group.

Path:

```text
ar-coordination/tasks/<repo-name>/<task-id-or-name>/contract.md
```

The contract lets an agent recover:

```text
task identity
selected workflow
code repo
memory repo
worktree group
code worktree path
memory worktree path
source branch
work branch
base commits
memory mode
ledger path
```

### 3.7 `memory.md`

The shared memory branch ledger.

It lives at the root of each shared memory repo branch:

```text
ar-coordination/memory-repos/ar-<repo-name>/memory.md
```

It declares which code branch the current memory branch tracks and maps code commits to memory commits.

---

## 4. Directory layouts

### 4.1 Internal memory, no worktree

```text
<code-repo>/
  src/
  ar-memory/
    onboarding/
      overview.md
    docs/
    settings/
```

### 4.2 Shared coordinator

```text
ar-coordination/
  settings/
  memory-repos/
    ar-<repo-name>/
  tasks/
    <repo-name>/
      <task-id-or-name>/
        contract.md
        task/
  notes/
  worktrees/
    <repo-name>/
      <worktree-name>-ar/
```

### 4.3 Shared memory repo

```text
ar-coordination/memory-repos/ar-<repo-name>/
  .git/
  onboarding/
    overview.md
  docs/
  settings/
  memory.md
```

### 4.4 Internal worktree group

```text
ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/
  <worktree-name>/
    src/
    ar-memory/
      onboarding/
      docs/
      settings/
```

### 4.5 Shared worktree group

```text
ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/
  <worktree-name>/
    src/
  memory-<worktree-name>/
    onboarding/
    docs/
    settings/
    memory.md
```

The code worktree is created first. The memory worktree then follows with the `memory-` prefix.

---

## 5. Worktree naming

A worktree group is not the same thing as a task.

Use:

```text
ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/
```

Inside it:

```text
<worktree-name>/          # code worktree
memory-<worktree-name>/   # memory worktree, shared mode only
```

This decouples worktrees from tasks. A single feature worktree can support multiple tasks or follow-up tasks.

Branch names can contain slashes, such as:

```text
feature/fix-platform-status
release/1.2
hotfix/prod-timeout
```

Folder names should use the explicit `worktree-name`, not raw branch names. The contract records the real Git branch names.

Example:

```text
branch:        feature/fix-platform-status
worktree-name: fix-platform-status
folder:        worktrees/device-management/fix-platform-status-ar/
```

---

## 6. Shared memory branch ledger

### 6.1 Purpose

Internal memory gets code-memory branch coherence from Git because memory lives inside the code repo.

Shared memory recreates that coherence with `memory.md`.

The ledger answers:

```text
At code commit C, which memory commit M described that code state?
```

### 6.2 One memory branch tracks one code branch

Rule:

```text
code/main   <=> memory/main
code/dev    <=> memory/dev
code/master <=> memory/master
```

The memory branch name should match the code branch it tracks. The header of `memory.md` is the source of truth.

### 6.3 Ledger format

Example:

```markdown
---
schema: ar-memory-branch-ledger/v1
repo_name: device-management
tracked_code_branch: main
memory_branch: main
base_code_commit: 8d21c91
base_memory_commit: a71f002
last_verified_code_commit: f4c8b12
last_memory_content_commit: b9e44aa
sort_order: newest-first
---

# Memory Branch Ledger

This memory branch tracks code branch `main`.

Newest entries are always inserted at the top.

| Code commit | Memory commit |
|---|---|
| f4c8b12 | b9e44aa |
| c31a760 | d08219f |
| 8d21c91 | a71f002 |
```

### 6.4 Header fields

Required fields:

```yaml
schema: ar-memory-branch-ledger/v1
repo_name: <code repo name>
tracked_code_branch: <code branch tracked by this memory branch>
memory_branch: <current memory branch>
base_code_commit: <code commit where this memory branch began>
base_memory_commit: <memory content commit where this memory branch began>
last_verified_code_commit: <latest code commit verified by this branch>
last_memory_content_commit: <latest memory content commit paired with that code commit>
sort_order: newest-first
```

Optional fields can be added later, but v1 should stay minimal.

### 6.5 Two-column lookup table

The table must have exactly two semantic columns:

```text
Code commit | Memory commit
```

Do not add branch columns. Branch identity belongs in the header because each memory branch tracks only one code branch.

### 6.6 Newest-first invariant

Rows are always newest-first.

Required consistency checks:

```text
first row Code commit == last_verified_code_commit
first row Memory commit == last_memory_content_commit
```

Agents should be able to read the header and first row to get the current sync point without scanning the whole file.

### 6.7 Memory content commit vs ledger anchor commit

Shared memory closeout uses two memory-side commits.

Example:

```text
C2 = code commit
M2 = memory content commit
L2 = ledger commit that prepends C2 -> M2 to memory.md
```

The ledger row stores:

```text
C2 -> M2
```

But the best restore point is normally `L2`, because `L2` contains both:

```text
1. the memory content from M2
2. the ledger row proving C2 maps to M2
```

The table remains two columns. Tooling derives the ledger anchor commit by inspecting Git history for the ledger row.

---

## 7. Memory compatibility and branch creation

### 7.1 Exact lookup match is the happy path

If the current code commit appears in a memory ledger, the memory branch can be created or checked out from the corresponding ledger anchor.

Example:

```text
code/dev:  A---B---C---D---E
code/main: A---B---C
```

If `memory/dev` contains:

```text
C -> M-C
```

then `memory/main` can be created from the ledger anchor that contains `C -> M-C`.

Result:

```text
code/main   <=> memory/main
```

This gives shared memory the same behavior internal memory would have had if `ar-memory/` had existed inside the code repo at commit `C`.

### 7.2 Branch setup algorithm

Given:

```text
code repo
code branch
code HEAD
memory repo ar-<repo-name>
```

The worktree manager should:

1. Determine the desired memory branch name from the code branch name.
2. If that memory branch exists, checkout or create a memory worktree from it and validate `memory.md`.
3. If the branch does not exist, search existing memory branch ledgers for an exact `Code commit` match to the current code HEAD.
4. If an exact match is found, create the desired memory branch from the ledger anchor containing that row.
5. If no exact match is found, compute and explain the divergence state to the developer.
6. Present the three allowed choices: reconciliation, clean start, or memory disabled.

### 7.3 Compatibility states

The resolver/worktree manager may report internal states such as:

```text
compatible
missing-memory-branch
exact-ledger-match-found
no-ledger-match
memory-behind-code
memory-ahead-of-code
diverged
memory-disabled
```

But work must not proceed with divergent memory. The states are diagnostic and decision-support only.

### 7.4 No-match choices

If no useful ledger match exists, the developer chooses one of three paths.

#### Option 1: Reconciliation

Keep the current memory files, but force drift checks and onboarding updates against the selected code branch.

Use this when histories are close enough that reconciliation is practical.

The agent should report:

```text
nearest known mapped commit
commit distance from selected code branch
commit distance from memory branch
changed/orphaned onboarding candidates
expected reconciliation cost
```

#### Option 2: Clean start

Create a new memory branch with the same name as the code branch and bootstrap from scratch.

Minimum clean-start result:

```text
onboarding/overview.md
memory.md with first valid ledger row
```

#### Option 3: Memory disabled

Proceed with only the code worktree.

This exists for emergency work such as production incidents where there is no time to reconcile or bootstrap memory.

The task contract must record:

```yaml
memory:
  mode: disabled
  reason: <human-provided reason>
```

Re-enabling memory later is out of scope for v1, but should remain possible through a future bootstrap/reconciliation flow.

---

## 8. Task contract

### 8.1 Location

Task contracts live under the shared coordinator:

```text
ar-coordination/tasks/<repo-name>/<task-id-or-name>/contract.md
```

The task folder may also contain the selected workflow's artifacts:

```text
ar-coordination/tasks/<repo-name>/<task-id-or-name>/task/
```

### 8.2 Purpose

The contract lets agents recover the operational state even if context is lost.

It answers:

```text
Which repo is this task for?
Which worktree group is being used?
Where is the code worktree?
Where is the memory worktree, if any?
Which workflow is wrapped?
Which code branch and memory branch are paired?
Which base commits were recorded?
Is memory enabled, reconciled, clean-started, or disabled?
```

### 8.3 Example contract

```markdown
---
schema: ar-worktree-contract/v1
task_id: ARWT-123
task_name: fix-platform-status
repo_name: device-management
memory_mode: shared
workflow_kind: light-task

coordination:
  root: /workspace/ar-coordination
  task_root: /workspace/ar-coordination/tasks/device-management/ARWT-123
  worktree_group: /workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar

code:
  repo_path: /workspace/repos/device-management
  source_branch: dev
  work_branch: feature/fix-platform-status
  base_commit: abc123
  worktree: /workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar/fix-platform-status

memory:
  repo_path: /workspace/ar-coordination/memory-repos/ar-device-management
  source_branch: dev
  work_branch: feature/fix-platform-status
  base_commit: def456
  worktree: /workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status
  ledger: /workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status/memory.md
  state: compatible
---

# Worktree Contract — ARWT-123

## Wrapped workflow

Artifacts live in `task/`.

## Human review state

- Status: pending-review
- Approved for commit: no
```

### 8.4 Multiple tasks may share a worktree group

A task contract references a worktree group. It does not own that worktree group exclusively.

This allows:

```text
follow-up tasks
multi-step features
review fixes
manual continuation
```

The worktree manager should detect when a requested worktree group already exists and either attach to it or report the existing state.

---

## 9. Resolver changes

### 9.1 Resolver remains central

The resolver is the correct place to centralize path and context facts so agents do not have to re-derive topology from prose.

Existing resolver behavior should be updated to distinguish:

```text
coordination_root
memory_root
onboarding_root
docs_root
settings_root
task_root
worktree_group
code_worktree
memory_worktree
ledger_path
```

### 9.2 New resolver inputs

Add optional inputs:

```text
task_id or task_name
worktree_name
memory_mode: internal | shared | disabled
coordination_root
code_repo_path
memory_repo_path
code_branch
memory_branch
```

The resolver should be able to answer:

```text
Does a task contract exist?
Which worktree group does it reference?
Does the code worktree exist?
Does the memory worktree exist?
What is the active memory_root for this operation?
```

### 9.3 Resolver output examples

Internal mode:

```json
{
  "coordination_root": "/workspace/ar-coordination",
  "memory_mode": "internal",
  "code_root": "/workspace/repos/device-management",
  "memory_root": "/workspace/repos/device-management/ar-memory",
  "onboarding_root": "/workspace/repos/device-management/ar-memory/onboarding",
  "docs_root": "/workspace/repos/device-management/ar-memory/docs",
  "settings_root": "/workspace/repos/device-management/ar-memory/settings"
}
```

Shared mode without task worktree:

```json
{
  "coordination_root": "/workspace/ar-coordination",
  "memory_mode": "shared",
  "code_root": "/workspace/repos/device-management",
  "memory_repo": "/workspace/ar-coordination/memory-repos/ar-device-management",
  "memory_root": "/workspace/ar-coordination/memory-repos/ar-device-management",
  "onboarding_root": "/workspace/ar-coordination/memory-repos/ar-device-management/onboarding",
  "docs_root": "/workspace/ar-coordination/memory-repos/ar-device-management/docs",
  "settings_root": "/workspace/ar-coordination/memory-repos/ar-device-management/settings",
  "ledger_path": "/workspace/ar-coordination/memory-repos/ar-device-management/memory.md"
}
```

Shared mode with worktree:

```json
{
  "coordination_root": "/workspace/ar-coordination",
  "memory_mode": "shared",
  "task_root": "/workspace/ar-coordination/tasks/device-management/ARWT-123",
  "contract_path": "/workspace/ar-coordination/tasks/device-management/ARWT-123/contract.md",
  "worktree_group": "/workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar",
  "code_worktree": "/workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar/fix-platform-status",
  "memory_worktree": "/workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status",
  "memory_root": "/workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status",
  "onboarding_root": "/workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status/onboarding",
  "ledger_path": "/workspace/ar-coordination/worktrees/device-management/fix-platform-status-ar/memory-fix-platform-status/memory.md"
}
```

### 9.4 C-08 and C-09 split

Extend the existing resolver so it can locate task/worktree context when asked.

Add a new skill:

```text
C-09-git-worktree-manager
```

Responsibilities:

```text
create code worktrees
create memory worktrees
create/update task contracts
validate memory branches and ledgers
wrap selected task workflows
run preflight and closeout checks
perform commit/wrap-up after human approval
```

The resolver reports facts. C-09 performs worktree operations.

---

## 10. Bootstrap and scaffold changes

### 10.1 Internal memory bootstrap

For internal memory, scaffold:

```text
<code-repo>/ar-memory/
  onboarding/
    overview.md
  docs/
  settings/
```

Do not create `memory.md` in internal mode.

### 10.2 Shared coordinator bootstrap

For shared coordination, scaffold:

```text
ar-coordination/
  settings/
  memory-repos/
  tasks/
  notes/
  worktrees/
```

The shared coordinator is operational/local.

### 10.3 New shared memory repo bootstrap

Given a code repo and current code branch, scaffold:

```text
ar-coordination/memory-repos/ar-<repo-name>/
  onboarding/
    overview.md
  docs/
  settings/
  memory.md
```

Then:

1. Initialize Git in the memory repo.
2. Create or checkout a memory branch matching the code branch.
3. Generate the minimum onboarding overview.
4. Commit memory content.
5. Prepend the first ledger row to `memory.md`.
6. Commit the ledger update.

The first ledger row maps:

```text
current code commit -> first memory content commit
```

### 10.4 Existing shared memory repo integration

Agents Remember does not need remote-discovery logic.

A human can:

```text
cd ar-coordination/memory-repos/
git clone <memory-repo-url> ar-<repo-name>
```

or provide the memory repo path/URL explicitly.

The tool then validates:

```text
repo name
branch name
memory.md exists
memory.md header matches selected code branch
ledger table is newest-first
top row matches header summary
```

If no compatible branch exists, the worktree manager uses the branch setup algorithm from section 7.

---

## 11. Worktree lifecycle

### 11.1 Start

Inputs:

```text
repo name
code repo path
code source branch
work branch
worktree name
task id/name
memory mode
selected workflow kind
```

C-09 should:

1. Resolve shared coordinator.
2. Resolve or create task folder under `ar-coordination/tasks/<repo-name>/...`.
3. Create/read `contract.md`.
4. Create code worktree first:

```text
ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/<worktree-name>/
```

5. If shared memory is enabled, resolve memory repo:

```text
ar-coordination/memory-repos/ar-<repo-name>/
```

6. Validate or create memory branch/worktree:

```text
ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/memory-<worktree-name>/
```

7. Validate ledger compatibility.
8. Run initial drift checks.
9. Record base commits in the contract.
10. Invoke the selected task workflow.

### 11.2 During work

The wrapped task workflow operates normally, but must use resolver-provided roots.

It should not guess paths.

It should not write durable memory outside the active `memory_root`.

### 11.3 Pre-review finish

When the task workflow is done, the agent reports:

```text
code changes
memory changes
onboarding changes
ledger status
source branch movement, if detected
risks/conflicts
```

It does not commit automatically.

### 11.4 Human-approved closeout — internal mode

After approval, internal mode can commit code and memory together:

```text
code changes + ar-memory changes -> one Git commit or approved commit series
```

No `memory.md` update is needed.

### 11.5 Human-approved closeout — shared mode

After approval, shared mode closes out in this order:

1. Check source branches again.
2. Surface conflicts or source movement to the human if needed.
3. Commit code changes in the code worktree.
4. Record resulting code commit as `C2`.
5. Commit memory content changes in the memory worktree.
6. Record resulting memory content commit as `M2`.
7. Prepend a ledger row to `memory.md`:

```text
C2 | M2
```

8. Update header:

```yaml
last_verified_code_commit: C2
last_memory_content_commit: M2
```

9. Commit the ledger update as `L2`.
10. Update task contract closeout status.
11. Optionally clean up worktrees when the human wants them removed.

Push behavior should be explicit. The design does not require automatic push.

---

## 12. Drift and integrity gates

### 12.1 Before work starts

Run drift checks before starting mutable work.

Internal mode:

```text
check code branch
check ar-memory/onboarding against code
record base commit
```

Shared mode:

```text
check code branch
check memory branch
validate memory.md
record code base commit
record memory base commit
run onboarding drift checks
```

### 12.2 Before closeout

The important question is:

```text
What changed since the recorded base commit?
```

Not:

```text
How many commits back should we inspect?
```

For shared mode, check both sides:

```text
code source branch:   <code-base-commit>..<current-code-source-head>
memory source branch: <memory-base-commit>..<current-memory-source-head>
```

If another approved task landed first, those commits are now visible. If they conflict with the current task, the conflict should surface during review/closeout and the human decides how to proceed.

---

## 13. Settings precedence

Use the same conceptual rules for internal and shared memory.

Precedence:

```text
memory_root/settings/        # repo/team-specific rules; strongest
coordination_root/settings/  # local shared defaults
built-in defaults            # weakest
```

The memory layer's local settings should beat shared coordinator defaults. This prevents individual local coordinator settings from silently bypassing rules set by the memory repo/team.

Task contracts record operational state. They should not weaken memory policy unless a future explicit override mechanism is designed.

---

## 14. Onboarding update history

Do not add task identity to onboarding update history for v1.

The task context can be restored through:

```text
code commit message
memory content commit message
memory.md ledger mapping
task contract (not tracked so only local and optional)
task workflow artifacts (not tracked so only local and optional)
```

Onboarding files should stay focused on durable onboarding truth, not workflow bookkeeping.

---

## 15. Commit messages

Because onboarding update history stays clean, commit messages should carry enough task context.

Recommended code commit pattern:

```text
[ARWT-123] Fix platform status mapping
```

Recommended memory content commit pattern:

```text
[ARWT-123] Update onboarding for platform status mapping
```

Recommended ledger commit pattern:

```text
[ARWT-123] Ledger sync: <code-commit> -> <memory-content-commit>
```

This keeps `git log` useful on both sides.

The commit messages are to be understood as defaults. Before committing the agent needs to ask if the developer agrees with the suggested commit message or want to suggest their own.

---

## 16. C-09 Git Worktree Manager

### 16.1 Skill name

```text
C-09-git-worktree-manager
```

### 16.2 Responsibilities

C-09 owns:

```text
shared coordinator discovery
worktree group creation
code worktree creation
memory repo validation
memory worktree creation
memory.md validation
ledger match lookup
branch creation from ledger anchors
task contract creation/update
wrapper execution around selected workflows
preflight checks
human-approved closeout
cleanup support
```

### 16.3 Non-responsibilities

C-09 does not:

```text
replace chat/light/heavy/BMAD workflows
auto-commit without human approval
discover company remote repositories automatically
store tasks in memory repos
support multi-repo memory repos
use divergent memory as reference memory
```

---

## 17. Required implementation changes

### 17.1 Rename internal memory root

Change all internal-memory references from:

```text
ar-coordination/
```

to:

```text
ar-memory/
```

No legacy support required.

### 17.2 Update resolver vocabulary and output

The resolver must distinguish:

```text
coordination_root
memory_root
onboarding_root
docs_root
settings_root
task_root
worktree_group
code_worktree
memory_worktree
ledger_path
```

### 17.3 Update bootstrap/scaffold skill

Add support for:

```text
internal ar-memory bootstrap
shared ar-coordination coordinator bootstrap
new shared memory repo bootstrap
existing shared memory repo validation
```

### 17.4 Add ledger parser/writer

The ledger tool should:

```text
parse front matter
parse newest-first two-column table
validate top row against header
prepend rows
update header summary fields
find ledger anchor commits from Git history
search branches for exact code commit mappings
```

### 17.5 Add C-09 worktree manager

Implement wrapper lifecycle:

```text
start
attach
status
preflight
invoke workflow
review report
approved closeout
cleanup
```

### 17.6 Update drift detection integration

Drift detection should work against resolver-provided `memory_root` and `onboarding_root`.

It must not assume memory lives inside the code repo.

### 17.7 Update docs and examples

Docs must explain:

```text
ar-memory vs ar-coordination
internal mode
shared mode
one code repo <=> one memory repo
memory.md ledger
worktree wrapper
human-approved closeout
```

---

## 18. Safety invariants

These invariants should be enforced by tooling where possible.

```text
1. Internal durable memory lives in ar-memory/.
2. Shared coordination lives in ar-coordination/.
3. One code repo maps to exactly one memory repo.
4. Memory repos use ar-<repo-name> by default.
5. Shared memory branches track exactly one code branch.
6. memory.md is newest-first.
7. The first ledger row matches the header's last verified commits.
8. Worktree contracts live in ar-coordination/tasks/, not in worktrees and not in memory.
9. Worktrees are grouped by repo and worktree name, not by task.
10. Tasks and notes are local operational artifacts, not durable memory truth.
11. Divergent memory is not used as reference memory.
12. If no ledger match exists, the only choices are reconciliation, clean start, or memory disabled.
13. Agents do not auto-commit at workflow finish.
14. Human approval precedes commit/wrap-up.
15. Shared coordinator settings cannot override stronger memory repo settings.
16. No global shared memory repo mode exists.
```

---

## 19. Deferred / out of scope for v1

These are intentionally not solved in this design pass.

### 19.1 Remote repository creation

Agents Remember does not create company remote repositories. Humans decide where memory repos live and can clone/provide them.

### 19.2 Automatic remote discovery

No search across company Git hosts is required. The user provides a path or URL, or clones into `memory-repos/` manually.

### 19.3 Re-enabling memory after disabled mode

Memory-disabled worktrees can be supported later by a bootstrap/reconciliation command. V1 only needs to record the disabled state clearly.

### 19.4 Rebase and squash policies

The ledger is commit-hash based. Rebases and squash merges can rewrite commit identities. V1 should prefer non-rewritten memory history and explicit human intervention when history has been rewritten.

### 19.5 Automatic push

Commit and push are separate. This design requires human-approved commits but does not require automatic pushes.

---

## 20. Implementation order

Recommended order:

1. Rename internal memory root to `ar-memory/` across docs, templates, resolver, and skills.
2. Refactor resolver output to distinguish `coordination_root` and `memory_root`.
3. Update bootstrap/scaffold for internal `ar-memory/` and shared `ar-coordination/`.
4. Add shared memory repo scaffold under `memory-repos/ar-<repo-name>/`.
5. Implement `memory.md` parser, validator, and writer.
6. Implement ledger branch lookup and ledger-anchor discovery.
7. Add task contract format under `ar-coordination/tasks/<repo-name>/...`.
8. Implement C-09 worktree manager start/status/attach flows.
9. Implement C-09 closeout flow with human approval gate.
10. Integrate drift detection with resolver-provided roots.
11. Update README, AGENTS instructions, and examples.
12. Add tests for internal mode, shared mode, exact ledger rewind, no-match choices, and disabled memory mode.

---

## 21. Core thesis

Internal mode gets code-memory coherence by storing memory inside the code repo as `ar-memory/`.

Shared mode gets the same coherence by pairing each code repo with exactly one `ar-<repo-name>` memory repo and using a per-branch `memory.md` ledger to map code commits to memory commits.

Worktree mode makes this productive by keeping task contracts, worktree paths, and memory compatibility in the local `ar-coordination/` coordination layer while preserving memory as a small, durable, Git-tracked truth layer.

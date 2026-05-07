# Agents Remember Cross-Repo Mode Design Spec

**Status:** finalized alpha design for implementation  
**Audience:** coding agents and maintainers implementing safe cross-repo context retrieval  
**Scope:** cross-repo mode after the `ar-memory` / `ar-management` split and the worktree-backed memory design

---

## 1. Executive summary

Cross-repo mode lets an agent working in one repository read carefully selected clues from other repositories.

With worktrees and shared memory enabled, cross-repo mode becomes riskier: an agent can accidentally read another repo from the wrong branch, or read a memory branch that describes a different code state. If that knowledge is then written into the current repo's onboarding, the cross-repo section of onboarding can become corrupted with wrong or branch-incompatible knowledge.

The fix is simple and strict:

```text
Cross-repo context is branch-gated.
```

Each repo's own memory settings define which external repos may be used and which branch each external repo must be on. If the external repo is not on that expected branch, it is excluded from cross-repo context. If memory clues are enabled for that external repo, the external memory layer must also be on that same expected branch and its ledger/header must confirm that it tracks that branch.

Cross-repo policy must live in the current repo's durable memory settings, not in the untracked shared `ar-management/` coordination layer. The shared coordinator may resolve local paths, but it must not decide which cross-repo knowledge is allowed.

---

## 2. Locked decisions

### 2.1 Cross-repo settings are repo-owned, committed policy

Cross-repo settings belong to the memory layer of the repo that wants to consume cross-repo clues.

Internal memory mode:

```text
<code-repo>/ar-memory/settings/cross-repo.yaml
```

Shared memory mode:

```text
ar-management/memory-repos/ar-<repo-name>/settings/cross-repo.yaml
```

The settings are committed with the memory layer. They are team-visible and reviewable.

They must not live in:

```text
ar-management/settings/
```

because the shared `ar-management/` folder is local coordination, not team policy.

### 2.2 Shared `ar-management` may only resolve local paths

The untracked shared coordinator may contain local path mappings, for example:

```text
ar-management/settings/repo-locations.yaml
```

This file can help the resolver find local checkouts:

```yaml
repos:
  repo-b:
    code_path: /workspace/repos/repo-b
    memory_path: /workspace/ar-management/memory-repos/ar-repo-b
```

But this is not permission policy. It cannot enable cross-repo use by itself and cannot weaken committed memory settings.

The split is:

```text
memory_root/settings/cross-repo.yaml       = team policy
ar-management/settings/repo-locations.yaml = local path resolution only
```

### 2.3 Every cross-repo entry must declare an expected code branch

A cross-repo entry must say which branch the external repo is expected to be on.

Example:

```yaml
cross_repo:
  enabled: true
  entries:
    - repo: repo-b
      expected_branch: dev
      include_code: true
      include_memory: false
```

If `repo-b` is not currently resolved on `dev`, it is excluded from cross-repo context.

There is no default branch guessing in v1.

### 2.4 Code clues are the minimum; memory clues are explicit opt-in

For a listed cross-repo dependency, code clues are the baseline.

Memory clues from that repo are used only when explicitly enabled:

```yaml
include_memory: true
```

If `include_memory` is false or omitted, the agent may only use code-side clues from the external repo.

### 2.5 If memory clues are enabled, code and memory must both point at the expected branch

For an external repo entry like:

```yaml
repo: repo-b
expected_branch: dev
include_memory: true
```

both of these must be true:

```text
repo-b code checkout is on branch dev
repo-b memory checkout is on branch dev
```

For shared memory, `memory.md` must also confirm:

```yaml
tracked_code_branch: dev
memory_branch: dev
```

If the code checkout is on `dev` but the memory repo is on `main`, memory clues are excluded.

If the memory repo is on `dev` but the code checkout is on `main`, the entire cross-repo entry is excluded.

### 2.6 No divergent cross-repo memory is used

Cross-repo mode must not use divergent memory as a semi-trusted reference layer.

A cross-repo entry is either:

```text
included
included-code-only
excluded
```

There is no state where the agent uses a memory branch that does not track the expected branch.

### 2.7 Cross-repo mode is read-only toward external repos

Cross-repo mode consumes information from external repos. It does not update their code or memory.

When working in `repo-a`, the agent may update `repo-a` code and `repo-a` memory. It must not update `repo-b` code or `repo-b` memory as part of cross-repo context retrieval.

If `repo-b` needs its own memory updates, that is a separate task/worktree operation for `repo-b`.

### 2.8 Cross-repo facts written into onboarding require provenance

If cross-repo information is written into the current repo's onboarding, it must be clear where it came from.

A cross-repo section should include at least:

```text
source repo
source branch
source code commit
source memory commit, if memory clues were used
```

This keeps cross-repo onboarding auditable and prevents vague claims from becoming durable truth.

Example:

```markdown
## Cross-Repo Context

- `repo-b@dev` commit `abc123` exposes `PaymentStatus.SUSPENDED`, which this mapper must preserve when translating billing responses.
  Memory clue used: `ar-repo-b@dev` memory commit `def456`.
```

---

## 3. Mental model

### 3.1 Current repo

The repo the agent is actively working in.

Example:

```text
repo-a
```

Its memory layer owns the cross-repo settings.

Internal mode:

```text
repo-a/ar-memory/settings/cross-repo.yaml
```

Shared mode:

```text
ar-management/memory-repos/ar-repo-a/settings/cross-repo.yaml
```

### 3.2 External repo

A repo that may provide cross-repo clues.

Example:

```text
repo-b
```

The external repo must be explicitly listed in `repo-a`'s committed cross-repo settings.

### 3.3 External memory repo

If memory clues are enabled, the external repo's memory layer may also be read.

Internal external repo:

```text
repo-b/ar-memory/
```

Shared external repo:

```text
ar-management/memory-repos/ar-repo-b/
```

### 3.4 Expected branch

The branch the current repo expects the external repo to be on when used for cross-repo clues.

Example:

```yaml
expected_branch: dev
```

This is usually the integration/source branch relevant to the current repo's work, not necessarily the current task branch.

For example, a task in `repo-a` may work on:

```text
repo-a work branch: feature/fix-status-mapping
repo-a source branch: dev
```

but still expect cross-repo clues from:

```text
repo-b branch: dev
```

---

## 4. Settings format

### 4.1 File location

Recommended path:

```text
<memory-root>/settings/cross-repo.yaml
```

Where `memory-root` is:

```text
internal: <code-repo>/ar-memory/
shared:  ar-management/memory-repos/ar-<repo-name>/
```

### 4.2 Minimal schema

```yaml
schema: ar-cross-repo/v1
cross_repo:
  enabled: true
  entries:
    - repo: repo-b
      expected_branch: dev
      include_code: true
      include_memory: false

    - repo: repo-c
      expected_branch: dev
      include_code: true
      include_memory: true
```

### 4.3 Field semantics

```yaml
schema
```

Schema identifier. Required.

```yaml
cross_repo.enabled
```

Global toggle for cross-repo mode for the current repo. If false, no cross-repo clues are used.

```yaml
entries[].repo
```

Stable repo name used by the resolver/local path registry.

```yaml
entries[].expected_branch
```

Required branch gate. The external code checkout must be on this branch or the entry is excluded.

```yaml
entries[].include_code
```

Whether to include code-side clues. Defaults to true for listed entries. If false, the entry should normally be disabled or removed.

```yaml
entries[].include_memory
```

Whether to include memory-side clues from the external repo. Defaults to false. This must be explicit opt-in.

### 4.4 Optional future fields

The v1 schema should stay small. Future fields may include:

```yaml
paths:
  include:
    - src/contracts/**
  exclude:
    - tests/**

reason: "repo-a consumes repo-b billing status contracts"

required: false
```

These are out of scope for the first implementation unless needed immediately.

---

## 5. Resolution and validation algorithm

### 5.1 Inputs

The resolver receives or discovers:

```text
current repo name
current memory root
current task/worktree context, if any
cross-repo settings from current memory root
local repo path mappings from ar-management, if available
```

### 5.2 Algorithm

For each entry in `cross_repo.entries`:

1. Read `repo` and `expected_branch`.
2. Resolve the external code repo path.
3. If the code repo path cannot be found, exclude the entry.
4. Read the external code repo's current branch.
5. If the branch is not exactly `expected_branch`, exclude the entry.
6. Record external code commit.
7. If `include_memory` is not true, include code-only context.
8. If `include_memory` is true, resolve the external memory root.
9. If the memory root cannot be found, include code-only context with memory excluded, or exclude the entry if a future `memory_required` option exists.
10. Read the external memory branch.
11. If the memory branch is not exactly `expected_branch`, exclude memory clues.
12. If shared memory, parse `memory.md`.
13. Validate that `memory.md` declares the expected branch.
14. Validate ledger shape and newest-first invariant.
15. Classify memory freshness/compatibility against the external code commit.
16. Include memory clues only if the branch and ledger gates pass.

### 5.3 No automatic branch switching

If an external repo is on the wrong branch, the resolver must not silently switch it.

Bad:

```text
repo-b is on main, expected dev, so the agent checks out dev automatically.
```

Good:

```text
repo-b is on main, expected dev, so repo-b is excluded from cross-repo context.
```

A future explicit command may prepare dedicated read-only cross-repo worktrees, but v1 should not silently move another repo's working tree.

---

## 6. Branch gates

### 6.1 Code branch gate

Required for every entry.

```text
external_code_branch == expected_branch
```

If false, exclude the entire entry.

### 6.2 Memory branch gate

Required only when `include_memory: true`.

For internal external memory:

```text
external code branch == expected_branch
```

Because internal memory lives inside the code repo, the memory layer follows the same branch.

For shared external memory:

```text
external memory branch == expected_branch
memory.md.tracked_code_branch == expected_branch
memory.md.memory_branch == expected_branch
```

If any of these fail, exclude memory clues.

### 6.3 Ledger sanity gate for shared memory

For shared external memory, `memory.md` must satisfy:

```text
schema is recognized
header exists
table has Code commit | Memory commit
table is newest-first
first row Code commit == last_verified_code_commit
first row Memory commit == last_memory_content_commit
```

If the ledger is malformed, exclude memory clues.

### 6.4 Commit compatibility gate

If memory clues are enabled, the resolver should compare:

```text
memory.md.last_verified_code_commit
external code HEAD
```

Allowed:

```text
last_verified_code_commit == external code HEAD
```

or:

```text
last_verified_code_commit is an ancestor of external code HEAD
```

The second case means memory is branch-compatible but possibly stale. Code remains the source of truth. Any durable cross-repo claim derived from memory must be verified against code before being written into the current repo's onboarding.

Disallowed:

```text
last_verified_code_commit is not an ancestor of external code HEAD
```

This indicates divergent or incompatible memory. Exclude memory clues.

---

## 7. Result model

The resolver should return both included and excluded cross-repo entries so agents can explain what happened.

Example:

```json
{
  "cross_repo": {
    "enabled": true,
    "included": [
      {
        "repo": "repo-b",
        "expected_branch": "dev",
        "code": {
          "path": "/workspace/repos/repo-b",
          "branch": "dev",
          "commit": "abc123",
          "state": "included"
        },
        "memory": {
          "enabled_by_policy": true,
          "path": "/workspace/ar-management/memory-repos/ar-repo-b",
          "branch": "dev",
          "commit": "def456",
          "ledger": "memory.md",
          "last_verified_code_commit": "abc123",
          "state": "included"
        }
      },
      {
        "repo": "repo-c",
        "expected_branch": "dev",
        "code": {
          "path": "/workspace/repos/repo-c",
          "branch": "dev",
          "commit": "c001",
          "state": "included"
        },
        "memory": {
          "enabled_by_policy": false,
          "state": "not-requested"
        }
      }
    ],
    "excluded": [
      {
        "repo": "repo-d",
        "expected_branch": "dev",
        "reason": "code branch mismatch",
        "actual_code_branch": "main"
      },
      {
        "repo": "repo-e",
        "expected_branch": "dev",
        "reason": "memory branch mismatch",
        "actual_code_branch": "dev",
        "actual_memory_branch": "main",
        "code_included": true,
        "memory_included": false
      }
    ]
  }
}
```

Important detail: if the code branch gate passes but the memory gate fails, the implementation may include code-only clues while excluding memory clues. The result must make that explicit.

---

## 8. Interaction with worktrees

### 8.1 Cross-repo mode must respect active worktree context

When a task is running inside a worktree, cross-repo mode should resolve from the task/worktree-aware resolver context.

For the current repo, use the active worktree contract.

For external repos, use only local checkouts or worktrees that are already on the expected branch.

### 8.2 Cross-repo entries are not task-owned

Cross-repo settings live in memory settings and are not tied to a specific task.

A task contract may record a snapshot of which cross-repo entries were included during the task, but that snapshot is not policy.

Example task contract addition:

```yaml
cross_repo_snapshot:
  - repo: repo-b
    expected_branch: dev
    code_branch: dev
    code_commit: abc123
    memory_enabled: true
    memory_branch: dev
    memory_commit: def456
    state: included
  - repo: repo-d
    expected_branch: dev
    state: excluded
    reason: code branch mismatch: main
```

### 8.3 No silent preparation of external worktrees in v1

C-09 may later grow an explicit command such as:

```text
prepare-cross-repo-context
```

That command could create read-only external worktrees on the expected branches.

But the first implementation should be conservative:

```text
If the external repo is not already resolved on the expected branch, exclude it.
```

---

## 9. Cross-repo onboarding rules

### 9.1 Cross-repo sections are allowed but guarded

Onboarding may contain cross-repo context when it is useful.

Example section:

```markdown
## Cross-Repo Context

- `repo-b@dev` commit `abc123` owns the canonical `PaymentStatus` enum used by this adapter.
- `repo-c@dev` commit `c001` publishes the event payload consumed here.
```

### 9.2 Cross-repo claims must not be vague

Bad:

```markdown
Repo B does status mapping differently.
```

Good:

```markdown
`repo-b@dev` commit `abc123` maps external status `SUSPENDED` to `PaymentStatus.SUSPENDED`; this adapter must preserve that value when translating billing responses.
```

### 9.3 Memory-derived claims require code confirmation

If a clue came from another repo's memory layer, the agent must confirm it against the external repo's code before writing it as durable onboarding in the current repo.

This is especially important when the external memory is branch-compatible but stale.

### 9.4 Excluded repos must not leak into onboarding

If an external repo is excluded because it is on the wrong branch, missing, or has incompatible memory, the agent must not write conclusions from that repo into onboarding.

It may mention in a task report:

```text
repo-b was configured for cross-repo context but excluded because it was on main instead of dev.
```

But it should not turn excluded knowledge into durable onboarding.

---

## 10. Resolver changes

### 10.1 Resolver responsibilities

The resolver should own cross-repo context resolution because agents should not reconstruct policy/path/branch logic from prose.

It should:

```text
read current repo memory settings
read cross-repo policy
resolve local external repo paths
validate expected branches
validate memory opt-in and memory branches
validate shared memory ledgers
return included/excluded cross-repo context
```

### 10.2 Resolver inputs

Add optional inputs:

```text
include_cross_repo: true | false
current_task_id
current_worktree_name
coordination_root
repo_locations_file
```

### 10.3 Resolver outputs

Add:

```text
cross_repo.enabled
cross_repo.policy_path
cross_repo.included[]
cross_repo.excluded[]
cross_repo.warnings[]
```

The output should be explicit enough that a coding agent can explain why a repo was included or ignored.

### 10.4 Settings precedence

Cross-repo policy follows the same settings precedence as the memory design:

```text
memory_root/settings/        # strongest, committed repo/team policy
coordination_root/settings/  # local defaults/path hints only
built-in defaults            # weakest
```

But for cross-repo inclusion, only memory-root policy can allow entries.

Local coordinator settings may help find repos, but cannot add allowed repos by themselves.

---

## 11. C-09 Git Worktree Manager responsibilities

C-09 should not own cross-repo policy, but it should respect and surface cross-repo resolver output.

During task start, C-09 should:

```text
request cross-repo resolution if the selected workflow wants cross-repo context
record included/excluded snapshot in the task contract
warn if configured cross-repo entries were excluded
```

During task closeout, C-09 should:

```text
re-run cross-repo resolution if onboarding cross-repo sections changed
warn if any previously included external repo is now branch-incompatible
block or require human confirmation before committing onboarding changes based on now-invalid cross-repo context
```

C-09 must not:

```text
silently checkout external repos to expected branches
commit external repo changes
commit external memory changes
weaken memory-root cross-repo policy
```

---

## 12. Failure modes and required behavior

### 12.1 External code repo missing

Behavior:

```text
exclude entry
report missing local code repo path
```

Do not guess from the internet or remote host.

### 12.2 External code branch mismatch

Example:

```text
expected: dev
actual: main
```

Behavior:

```text
exclude entire entry
report branch mismatch
```

### 12.3 External memory not found

If `include_memory: true` but the memory repo is missing:

Behavior:

```text
include code-only context if code branch gate passed
exclude memory clues
warn that memory was requested but unavailable
```

### 12.4 External memory branch mismatch

Example:

```text
code repo-b branch: dev
memory ar-repo-b branch: main
expected branch: dev
```

Behavior:

```text
include code-only context
exclude memory clues
warn that memory branch does not match expected branch
```

### 12.5 External memory ledger malformed

Behavior:

```text
include code-only context
exclude memory clues
warn that memory.md is invalid
```

### 12.6 External memory diverged from code

If `memory.md.last_verified_code_commit` is not an ancestor of the external code HEAD:

Behavior:

```text
include code-only context
exclude memory clues
warn that external memory is incompatible with external code
```

### 12.7 Cross-repo settings missing

Behavior:

```text
cross-repo disabled for this repo
```

No implicit discovery.

---

## 13. Security and trust model

Cross-repo mode is an allow-list.

Only repos listed in the current repo's committed memory settings can be used.

Branch gates prevent accidental cross-branch contamination.

Memory opt-in prevents another repo's onboarding from being consumed accidentally.

Provenance requirements prevent cross-repo statements from becoming anonymous durable facts.

The current repo's maintainers decide what external repos they trust enough to consult.

---

## 14. Example scenarios

### 14.1 Code-only cross-repo context

`repo-a` settings:

```yaml
schema: ar-cross-repo/v1
cross_repo:
  enabled: true
  entries:
    - repo: repo-b
      expected_branch: dev
      include_code: true
      include_memory: false
```

Local state:

```text
repo-b code branch: dev
```

Result:

```text
repo-b code clues included
repo-b memory clues not requested
```

### 14.2 Code and memory context included

`repo-a` settings:

```yaml
schema: ar-cross-repo/v1
cross_repo:
  enabled: true
  entries:
    - repo: repo-b
      expected_branch: dev
      include_code: true
      include_memory: true
```

Local state:

```text
repo-b code branch: dev
ar-repo-b memory branch: dev
ar-repo-b memory.md tracked_code_branch: dev
memory last_verified_code_commit is ancestor of repo-b HEAD
```

Result:

```text
repo-b code clues included
repo-b memory clues included
```

### 14.3 Code branch mismatch

Settings expect:

```text
repo-b expected branch: dev
```

Local state:

```text
repo-b code branch: main
```

Result:

```text
repo-b excluded entirely
```

### 14.4 Memory branch mismatch

Settings expect:

```text
repo-b expected branch: dev
include_memory: true
```

Local state:

```text
repo-b code branch: dev
ar-repo-b memory branch: main
```

Result:

```text
repo-b code clues included
repo-b memory clues excluded
```

### 14.5 Missing memory repo

Settings request:

```text
include_memory: true
```

Local state:

```text
repo-b code branch: dev
ar-repo-b missing locally
```

Result:

```text
repo-b code clues included
repo-b memory clues excluded with warning
```

---

## 15. Required implementation changes

### 15.1 Add committed cross-repo settings file

Add support for:

```text
<memory-root>/settings/cross-repo.yaml
```

in both internal and shared memory modes.

### 15.2 Update resolver

Resolver must be able to:

```text
read cross-repo settings from memory_root
resolve local external repo paths
validate branch gates
validate memory opt-in
validate shared memory ledger gates
return included/excluded cross-repo entries
```

### 15.3 Add optional local repo registry

Add support for a local helper file such as:

```text
ar-management/settings/repo-locations.yaml
```

This is path resolution only, not policy.

### 15.4 Update onboarding update rules

When writing cross-repo sections into onboarding, require provenance:

```text
source repo
source branch
source code commit
source memory commit, if used
```

### 15.5 Update C-09 worktree manager

C-09 should:

```text
record cross-repo resolution snapshots in task contracts when used
revalidate cross-repo context before closeout if onboarding cross-repo sections changed
block or warn on branch mismatch
```

### 15.6 Add tests

Minimum tests:

```text
cross-repo disabled when settings missing
code-only include when branch matches
exclude when code branch mismatches
memory include when code + memory branches match
memory exclude when memory branch mismatches
memory exclude when memory.md header mismatches
memory exclude when ledger malformed
memory exclude when last_verified_code_commit is not ancestor of code HEAD
local ar-management settings cannot add unapproved repo
```

---

## 16. Safety invariants

Tooling should enforce these where possible:

```text
1. Cross-repo policy lives in memory_root/settings/cross-repo.yaml.
2. Shared ar-management settings can resolve paths but cannot grant cross-repo permissions.
3. Every cross-repo entry must declare expected_branch.
4. External code checkout must be on expected_branch or the entry is excluded.
5. Memory clues are opt-in per entry.
6. If memory clues are enabled, external memory must also be on expected_branch.
7. Shared external memory must have memory.md tracking expected_branch.
8. Malformed or incompatible external memory is excluded.
9. Cross-repo mode never writes to external repos or external memory repos.
10. Excluded repos must not leak into durable onboarding.
11. Cross-repo onboarding claims must include source repo, branch, and commit provenance.
12. No automatic branch switching for external repos in v1.
```

---

## 17. Deferred / out of scope for v1

### 17.1 Automatic external worktree preparation

A future command may create dedicated read-only external worktrees on expected branches.

V1 only includes external repos already resolved on the expected branch.

### 17.2 Remote repository discovery

Agents Remember does not discover company remotes for cross-repo settings. Humans provide local paths or clone repos manually.

### 17.3 Complex path-scoped cross-repo policies

Path include/exclude filters can be added later.

V1 is branch-gated repo-level inclusion.

### 17.4 Cross-repo writes

V1 cross-repo mode is read-only toward external repos.

If another repo's memory needs updating, that is a separate task for that repo.

### 17.5 Detached HEAD support

V1 requires named branches for cross-repo inclusion.

Detached checkouts are excluded unless a later schema adds explicit commit-pinned cross-repo entries.

---

## 18. Core thesis

Cross-repo mode is safe only when it is explicit, branch-gated, and repo-owned.

The current repo's committed memory settings decide which external repos may provide clues. The local shared coordinator can help find those repos, but it cannot approve them. External code must be on the expected branch. External memory must be explicitly opted in, must be on that same branch, and must prove through `memory.md` that it tracks that branch.

This keeps cross-repo context useful without letting worktrees or divergent memory branches poison durable onboarding with wrong knowledge.

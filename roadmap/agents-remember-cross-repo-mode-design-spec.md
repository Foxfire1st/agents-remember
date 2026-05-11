# Agents Remember Cross-Repo Mode Design Spec

**Status:** finalized alpha design for implementation  
**Revision:** v3 — aligned with current `settings.json` shape  
**Audience:** coding agents and maintainers implementing safe cross-repo context retrieval  
**Scope:** cross-repo mode after the `ar-memory` / `ar-coordination` split and the worktree-backed memory design

---

## 1. Executive summary

Cross-repo mode lets an agent working in one repository read carefully selected clues from other repositories.

With worktrees and branch-specific memory enabled, cross-repo mode must be branch-gated. Otherwise an agent can read another repo or memory layer from the wrong branch and write branch-incompatible facts into the current repo's onboarding.

The rule is strict:

```text
A cross-repo source is included only when its code checkout is on the expected branch.
If memory clues are enabled, its memory checkout must also be on that expected branch.
```

The current repo's committed memory settings decide which external repos may be used. The untracked shared `ar-coordination/` coordinator may help resolve local paths, but it cannot grant cross-repo permission.

This spec intentionally reuses the existing settings model:

```text
version
onboarding.storage
onboarding.pathRules
crossRepo.allow
```

The main change is that `crossRepo.allow` evolves from a simple list of repo names into a list of branch-gated cross-repo source objects.

---

## 2. Existing settings shape to preserve

The current implementation already has the right container shape. Machine-readable settings live in `settings.json`, while `settings.md` remains human-readable guidance.

The existing internal example uses:

```json
{
  "version": 1,
  "onboarding": {
    "storage": {
      "mode": "repo-sidecar"
    },
    "pathRules": {
      "include": {
        "paths": ["README.md", "docs/**", "src/**", "skills/**", "system/**"],
        "fileTypes": [".md", ".py", ".ts", ".tsx"]
      },
      "exclude": {
        "paths": ["vendor/**", "node_modules/**", "dist/**", "build/**"],
        "fileTypes": [".png", ".jpg", ".jpeg", ".gif", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": []
  }
}
```

The existing resolver already treats JSON as the preferred machine-readable source and parses `crossRepo.allow` as the cross-repo allowance field.

Therefore the new design should not introduce a parallel `crossRepo.entries` key unless there is a strong reason. The cleaner alpha design is:

```text
Keep crossRepo.allow.
Upgrade its item shape.
```

---

## 3. Updated settings locations

The settings shape is reused, but locations change because the system now separates durable memory from local coordination.

### 3.1 Internal memory mode

```text
<code-repo>/ar-memory/settings/settings.json
```

### 3.2 Shared memory mode

```text
ar-coordination/memory-repos/ar-<repo-name>/settings/settings.json
```

### 3.3 Local shared coordinator

```text
ar-coordination/settings/settings.json
```

The local coordinator settings are untracked. They may contain path hints, but not cross-repo policy.

---

## 4. Locked decisions

### 4.1 JSON only

All machine-readable settings use strict JSON.

Allowed:

```text
settings.json
```

Not allowed:

```text
settings.yaml
settings.yml
cross-repo.yaml
repo-locations.yaml
```

Implementation must use Python's standard `json` module only.

### 4.2 Cross-repo policy is committed memory policy

Cross-repo policy belongs to the durable memory layer of the repo that wants to consume cross-repo clues.

Internal:

```text
<code-repo>/ar-memory/settings/settings.json
```

Shared:

```text
ar-coordination/memory-repos/ar-<repo-name>/settings/settings.json
```

It must not be enabled from the local shared coordinator.

### 4.3 Reuse `crossRepo.allow`

The existing key stays:

```json
{
  "crossRepo": {
    "allow": []
  }
}
```

But the allowed item shape changes from string-only to object-based.

New preferred shape:

```json
{
  "crossRepo": {
    "allow": [
      {
        "repo": "repo-b",
        "expectedBranch": "dev",
        "includeCode": true,
        "includeMemory": false
      }
    ]
  }
}
```

Rationale:

```text
allow already communicates permission.
The new object adds branch and memory-safety details.
No duplicate entries/rules key is needed.
```

### 4.4 No implicit branch guessing

Each `crossRepo.allow` object must declare:

```json
"expectedBranch": "dev"
```

If the external code checkout is not on that branch, the entry is excluded.

### 4.5 Code clues are the minimum

Code is the minimum cross-repo source.

Default:

```json
"includeCode": true
```

If omitted, treat it as true.

### 4.6 Memory clues are explicit opt-in

Memory clues require:

```json
"includeMemory": true
```

If omitted, treat it as false.

### 4.7 Code and memory must both match the expected branch

For this policy:

```json
{
  "repo": "repo-b",
  "expectedBranch": "dev",
  "includeCode": true,
  "includeMemory": true
}
```

all of these must be true before memory clues are included:

```text
repo-b code checkout is on branch dev
ar-repo-b memory checkout is on branch dev
ar-repo-b memory.md declares trackedCodeBranch dev
ar-repo-b memory.md declares memoryBranch dev
```

If code is on the wrong branch, exclude the entire entry.

If code is on the expected branch but memory is missing or invalid, include code-only and report memory exclusion.

### 4.8 No divergent reference memory

Cross-repo mode must not use divergent memory as semi-trusted reference context.

An entry resolves to one of:

```text
included
included-code-only
excluded
```

There is no `foreign-reference` state for cross-repo context.

### 4.9 Cross-repo mode is read-only toward external repos

When working in `repo-a`, cross-repo mode may read from `repo-b`, but it must not update `repo-b` code or memory.

Updating external repo memory requires a separate task/worktree operation for that external repo.

---

## 5. Committed memory settings schema

### 5.1 Minimal disabled example

```json
{
  "version": 2,
  "onboarding": {
    "storage": {
      "mode": "repo-sidecar"
    },
    "pathRules": {
      "include": {
        "paths": ["README.md", "docs/**", "src/**"],
        "fileTypes": [".md", ".py", ".ts", ".tsx"]
      },
      "exclude": {
        "paths": ["vendor/**", "node_modules/**", "dist/**", "build/**"],
        "fileTypes": [".png", ".jpg", ".jpeg", ".gif", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": []
  }
}
```

No separate `enabled` boolean is required. Empty `allow` means cross-repo disabled.

### 5.2 Code-only cross-repo example

```json
{
  "version": 2,
  "onboarding": {
    "storage": {
      "mode": "repo-sidecar"
    },
    "pathRules": {
      "include": {
        "paths": ["README.md", "docs/**", "src/**"],
        "fileTypes": [".md", ".py", ".ts", ".tsx"]
      },
      "exclude": {
        "paths": ["vendor/**", "node_modules/**", "dist/**", "build/**"],
        "fileTypes": [".png", ".jpg", ".jpeg", ".gif", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": [
      {
        "repo": "repo-b",
        "expectedBranch": "dev",
        "includeCode": true,
        "includeMemory": false
      }
    ]
  }
}
```

### 5.3 Code plus memory cross-repo example

```json
{
  "version": 2,
  "onboarding": {
    "storage": {
      "mode": "repo-sidecar"
    },
    "pathRules": {
      "include": {
        "paths": ["README.md", "docs/**", "src/**"],
        "fileTypes": [".md", ".py", ".ts", ".tsx"]
      },
      "exclude": {
        "paths": ["vendor/**", "node_modules/**", "dist/**", "build/**"],
        "fileTypes": [".png", ".jpg", ".jpeg", ".gif", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": [
      {
        "repo": "billing-api",
        "expectedBranch": "dev",
        "includeCode": true,
        "includeMemory": true
      },
      {
        "repo": "payment-gateway",
        "expectedBranch": "release/2.4",
        "includeCode": true,
        "includeMemory": false
      }
    ]
  }
}
```

### 5.4 Shared memory repo example

A shared memory repo uses the same settings shape, but its storage mode describes a memory repo root, not the old shared monorepo root.

```json
{
  "version": 2,
  "onboarding": {
    "storage": {
      "mode": "memory-repo"
    },
    "pathRules": {
      "include": {
        "paths": ["README.md", "docs/**", "src/**"],
        "fileTypes": [".md", ".py", ".ts", ".tsx"]
      },
      "exclude": {
        "paths": ["vendor/**", "node_modules/**", "dist/**", "build/**"],
        "fileTypes": [".png", ".jpg", ".jpeg", ".gif", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": [
      {
        "repo": "billing-api",
        "expectedBranch": "dev",
        "includeCode": true,
        "includeMemory": true
      }
    ]
  }
}
```

Open implementation choice: `storage.mode` may stay `repo-sidecar` for both internal and shared memory roots if the resolver treats `memory_root` as the already-resolved memory location. The key point is that `shared-root` must no longer mean one shared Git repo for many repo memories.

---

## 6. Field definitions

### `version`

Required integer.

```json
"version": 2
```

The existing repo uses `version: 1`. Worktree/cross-repo branch gates should move the machine-readable settings contract to version 2.

### `onboarding.storage`

Preserved from the current settings shape.

Internal memory mode:

```json
"storage": { "mode": "repo-sidecar" }
```

Shared memory mode may use:

```json
"storage": { "mode": "memory-repo" }
```

or reuse `repo-sidecar` once the resolver has already resolved a memory root.

### `onboarding.pathRules`

Preserved from the current settings shape.

The worktree design should not replace path rules. They still answer:

```text
Which source paths and file types are eligible for onboarding?
```

### `crossRepo.allow`

Required array. Empty means disabled.

Preferred v2 item shape:

```json
{
  "repo": "repo-b",
  "expectedBranch": "dev",
  "includeCode": true,
  "includeMemory": false
}
```

String entries from the old v1 shape are not branch-safe. In alpha v2 they should be rejected with a migration error rather than guessed.

### `repo`

Required string.

Logical repo name used by the resolver and local path hints.

### `expectedBranch`

Required string.

The external code repo must be on this branch.

### `includeCode`

Optional boolean. Defaults to true.

### `includeMemory`

Optional boolean. Defaults to false.

---

## 7. Local coordinator settings

The shared `ar-coordination/` coordinator may have local path hints:

```text
ar-coordination/settings/settings.json
```

Example:

```json
{
  "version": 1,
  "repoLocations": {
    "repo-a": {
      "codePath": "/workspace/repos/repo-a",
      "memoryPath": "/workspace/ar-coordination/memory-repos/ar-repo-a"
    },
    "repo-b": {
      "codePath": "/workspace/repos/repo-b",
      "memoryPath": "/workspace/ar-coordination/memory-repos/ar-repo-b"
    }
  }
}
```

This file can answer:

```text
Where is repo-b checked out locally?
Where is ar-repo-b checked out locally?
```

It cannot answer:

```text
May repo-a use repo-b as cross-repo context?
May repo-a use ar-repo-b memory?
```

Permission must come from `repo-a`'s committed memory settings.

---

## 8. Memory ledger compatibility

Shared memory repos use:

```text
ar-coordination/memory-repos/ar-<repo-name>/memory.md
```

`memory.md` is the per-branch ledger. It must be parseable without YAML dependencies.

Recommended format:

````markdown
# Memory Branch Ledger

```json ar-memory-ledger
{
  "schema": "ar-memory-branch-ledger/v1",
  "repoName": "repo-b",
  "trackedCodeBranch": "dev",
  "memoryBranch": "dev",
  "baseCodeCommit": "8d21c91",
  "baseMemoryCommit": "a71f002",
  "lastVerifiedCodeCommit": "f4c8b12",
  "lastMemoryContentCommit": "b9e44aa",
  "sortOrder": "newest-first"
}
```

| Code commit | Memory commit |
|---|---|
| f4c8b12 | b9e44aa |
| c31a760 | d08219f |
| 8d21c91 | a71f002 |
````

For cross-repo memory inclusion, resolver checks:

```text
external memory branch == expectedBranch
memory.md trackedCodeBranch == expectedBranch
memory.md memoryBranch == expectedBranch
first ledger row Code commit == lastVerifiedCodeCommit
first ledger row Memory commit == lastMemoryContentCommit
```

---

## 9. Resolution algorithm

When the current repo requests cross-repo context:

```text
1. Resolve current repo memory root.
2. Read <current-memory-root>/settings/settings.json.
3. Read crossRepo.allow.
4. If allow is missing or empty, cross-repo mode is disabled.
5. For each allow object:
   a. Validate repo and expectedBranch.
   b. Resolve external code path from task context or local coordinator hints.
   c. Check external code branch.
   d. If external code branch != expectedBranch, exclude entire entry.
   e. If includeMemory is false, include code-only context.
   f. If includeMemory is true, resolve external memory path.
   g. Check external memory branch.
   h. Parse external memory.md ledger metadata.
   i. Validate branch and ledger top-row invariants.
   j. Include memory only if all checks pass.
6. Return included and excluded entries with reasons.
```

The resolver must not silently checkout, switch, rebase, or repair external repos.

---

## 10. Result states

### `included`

Code and memory are both included.

Conditions:

```text
includeMemory == true
external code branch == expectedBranch
external memory branch == expectedBranch
memory.md trackedCodeBranch == expectedBranch
memory.md memoryBranch == expectedBranch
```

### `included-code-only`

Only code is included.

Conditions:

```text
external code branch == expectedBranch
includeMemory == false
```

or:

```text
external code branch == expectedBranch
includeMemory == true
external memory is missing/disabled/invalid
```

The result must clearly report why memory was excluded.

### `excluded`

The entry is not used.

Common reasons:

```text
legacy string allow entry missing expectedBranch
external code path missing
external code branch mismatch
external repo is detached
external memory branch mismatch
memory.md missing
memory.md invalid JSON metadata
memory.md trackedCodeBranch mismatch
```

---

## 11. Resolver output example

```json
{
  "crossRepo": {
    "allow": [
      {
        "repo": "repo-b",
        "expectedBranch": "dev",
        "includeCode": true,
        "includeMemory": true,
        "state": "included",
        "code": {
          "path": "/workspace/repos/repo-b",
          "branch": "dev",
          "head": "abc123"
        },
        "memory": {
          "path": "/workspace/ar-coordination/memory-repos/ar-repo-b",
          "branch": "dev",
          "ledgerPath": "/workspace/ar-coordination/memory-repos/ar-repo-b/memory.md",
          "lastVerifiedCodeCommit": "abc123",
          "lastMemoryContentCommit": "def456"
        }
      },
      {
        "repo": "repo-c",
        "expectedBranch": "dev",
        "includeCode": true,
        "includeMemory": false,
        "state": "excluded",
        "reason": "external code repo is on branch main, expected dev"
      }
    ]
  }
}
```

---

## 12. Worktree interaction

Cross-repo mode must be worktree-aware.

If a task is running inside a worktree, the resolver should use the task/worktree context for the current repo and any explicitly prepared external repo paths.

`C-09-git-worktree-manager` does not own cross-repo policy. It may:

```text
request cross-repo resolution
record a snapshot of included/excluded cross-repo entries in the task contract
warn the human when configured cross-repo entries are excluded
re-run cross-repo resolution before closeout if onboarding cross-repo sections changed
```

The task contract may record what cross-repo context was used, but that snapshot is not policy.

Example snapshot:

```json
{
  "crossRepoSnapshot": {
    "resolvedAt": "2026-05-08T00:00:00Z",
    "allow": [
      {
        "repo": "repo-b",
        "state": "included",
        "expectedBranch": "dev",
        "codeCommit": "abc123",
        "memoryCommit": "def456"
      }
    ]
  }
}
```

---

## 13. Onboarding rules for cross-repo context

Onboarding may contain cross-repo context when useful, but durable claims must be provenance-backed.

Allowed:

```markdown
## Cross-Repo Context

- `billing-api@dev` commit `abc123` emits `PaymentStatus.SUSPENDED`; this repo's mapper must preserve that status.
  Memory clue used: `ar-billing-api@dev` memory commit `def456`.
```

Code-only context must say memory was not used:

```markdown
## Cross-Repo Context

- `billing-api@dev` commit `abc123` emits `PaymentStatus.SUSPENDED`; this repo's mapper must preserve that status.
  Memory clue used: none; verified from code only.
```

---

## 14. Failure behavior

### Missing current repo settings

Cross-repo disabled.

The resolver must not infer cross-repo policy from local coordinator settings.

### Invalid current repo settings JSON

Cross-repo disabled and configuration error reported.

### Legacy string allow entries

Old v1 shape:

```json
{
  "crossRepo": {
    "allow": ["repo-b"]
  }
}
```

This is not branch-safe. In alpha v2, treat it as invalid for cross-repo use and explain that `expectedBranch` is required.

### External code branch mismatch

Exclude entire entry.

### External memory branch mismatch

Include code-only if the code branch is valid and `includeCode` is true. Exclude memory and report mismatch.

### Detached external checkout

Exclude in v1. Named branches are required.

---

## 15. Implementation requirements

### 15.1 Preserve JSON-first parsing

The existing resolver already prefers `settings.json`. Keep that direction.

### 15.2 Extend `CrossRepoSettings`

Current shape:

```python
@dataclass
class CrossRepoSettings:
    allow: list[str] = field(default_factory=list)
```

New shape should model objects:

```python
@dataclass
class CrossRepoAllowEntry:
    repo: str
    expected_branch: str
    include_code: bool = True
    include_memory: bool = False

@dataclass
class CrossRepoSettings:
    allow: list[CrossRepoAllowEntry] = field(default_factory=list)
```

### 15.3 Keep validation strict

Validate:

```text
settings root is object
version is integer if present
crossRepo is object if present
crossRepo.allow is an array if present
allow item is object
allow.repo is string
allow.expectedBranch is string
allow.includeCode is boolean if present
allow.includeMemory is boolean if present
```

Malformed top-level settings should disable cross-repo mode for safety.

Malformed individual entries should be excluded and reported.

---

## 16. Tests

Minimum tests:

```text
1. cross-repo disabled when crossRepo.allow is missing or empty
2. cross-repo disabled when settings.json is invalid JSON
3. local ar-coordination settings cannot enable cross-repo by itself
4. legacy string crossRepo.allow entry rejected because expectedBranch is missing
5. external repo included code-only when branch matches and includeMemory false
6. external repo excluded when code branch mismatches expectedBranch
7. external memory included when code branch, memory branch, and memory.md metadata all match expectedBranch
8. external memory excluded when memory branch mismatches expectedBranch
9. external memory excluded when memory.md metadata trackedCodeBranch mismatches expectedBranch
10. external memory excluded when memory.md JSON metadata cannot be parsed
11. task snapshot records included/excluded entries without becoming policy
12. onboarding cross-repo sections include repo, branch, and commit provenance
```

---

## 17. Non-goals for v1

### Auto-cloning external repos

Agents Remember does not discover company remotes or auto-clone cross-repo dependencies in v1.

Humans provide local paths or clone repos manually into the shared coordinator structure.

### Auto-switching external branches

The resolver must not silently checkout another branch in an external repo.

If `repo-b` is on `main` and policy expects `dev`, `repo-b` is excluded.

### Memory-only cross-repo context

V1 assumes code is the minimum source of truth.

Memory clues can supplement code clues, but should not replace code-side branch validation.

### Cross-repo writes

Cross-repo mode is read-only toward external repos.

Updating external memory requires a separate task for that external repo.

---

## 18. Final design sentence

```text
Cross-repo mode reuses settings.json and crossRepo.allow: each allowed external repo must declare an expected branch, code must be on that branch, and memory is used only when explicitly enabled and proven by memory.md to track that same branch.
```

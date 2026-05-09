# Agent Memory Settings Example

`settings.md` and `settings.json` exist in both supported topologies.

- Internal topology uses `<target-repo>/ar-memory/system/settings.md`.
- Shared topology uses `ar-management/memory-repos/ar-<repo-name>/system/settings.md`.
- Machine-readable storage, path-rule, and cross-repo settings live in the sibling `system/settings.json` file.

Default setup is internal and local-first. Shared setup is an explicit advanced choice for teams that want one memory repo per selected code repository.

## Human Settings Markdown

Use `settings.md` for general instructions, scaffold notes, and operational context that humans and agents should read. Do not duplicate active `pathRules` there as the authoritative machine source when `settings.json` exists.

## Internal JSON Settings

Use this shape for the default repo-local scaffold:

```json
{
  "version": 2,
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

Internal `repo-sidecar` storage keeps eligible onboarding artifacts under the repository's own `ar-memory/onboarding/` folder. `crossRepo.allow` is empty by default, so internal bootstrap and discovery stay local unless the memory settings explicitly opt into branch-gated neighboring repositories.

## Shared Memory JSON Settings

Use this shape in `ar-management/memory-repos/ar-<repo-name>/system/settings.json` for an explicitly selected shared memory repo:

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
        "includeMemory": false
      }
    ]
  }
}
```

Shared memory storage keeps eligible onboarding artifacts under the selected per-repo memory root, usually below `ar-management/memory-repos/ar-<repo-name>/onboarding/`.

Shared memory repos normally use unscoped `pathRules` because the memory repo already maps to exactly one code repo. The local coordinator may still use scoped rules as path hints for compatibility and migration, but cross-repo policy belongs in the committed memory settings.

## Storage Versus Eligibility

`onboarding.storage` in `settings.json` answers where eligible onboarding artifacts live.

`onboarding.pathRules` in `settings.json` answers which source paths and file types are eligible for onboarding. In shared settings, each rule can also identify the repository or repository subtree it applies to with `path`.

Do not use `pathRules` as per-path storage switching. If a future task adds per-path storage routing, it should do so explicitly without removing include/exclude eligibility from either topology.

## Scaffold Shape

Internal durable memory uses:

```text
ar-memory/
├── onboarding/
├── docs/
└── system/
    ├── settings.md
    ├── settings.json
    ├── sources.md
    └── tools.md
```

The local coordinator uses:

```text
ar-management/
├── system/
├── memory-repos/
├── tasks/
├── notes/
└── worktrees/
```

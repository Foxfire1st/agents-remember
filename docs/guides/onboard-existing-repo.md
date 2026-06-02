# Onboard An Existing Repo

You do not need full coverage before Agents Remember becomes useful. Start small, then let onboarding grow as work touches new files.

## 1. Install The Runtime

Configure the Agents Remember MCP server, then request:

```text
runtime_install()
```

Expose skills for your harness using the relevant [install guide](../README.md#install-guides).

## 2. Initialize Memory

Ask the agent to run `c-00-initialize-memory-repo` for the target repository.

Default internal memory creates:

```text
<repo>/ar-memory/
  onboarding/
  docs/
  system/
    settings.md
    settings.json
    sources.md
    tools.md
```

Do not create onboarding content by hand before the memory root exists. The `c-00-initialize-memory-repo` skill owns the scaffold; the `c-03-repo-bootstrap` skill owns onboarding bootstrap.

## 3. Configure Path Eligibility

Review `<repo>/ar-memory/system/settings.json`.

Start with a small eligible surface:

```json
{
  "version": 1,
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
        "paths": ["node_modules/**", "vendor/**", "dist/**", "build/**", ".env", ".env.*"],
        "fileTypes": [".png", ".zip"]
      }
    }
  }
}
```

See [Path Rules](../reference/path-rules.md) for the fuller exclusion baseline.

## 4. Bootstrap A First Overview

Ask the agent to run `c-03-repo-bootstrap`.

A repo-level `overview.md` is enough to start. Larger repositories can add route-local overviews under the mirrored onboarding hierarchy when a package, module, or source slice needs durable context.

## 5. Let Coverage Grow From Work

When a task touches a file, the agent should:

1. resolve context with the `c-08-ar-coordination-context-resolver` skill
2. check drift with the `c-02-memory-quality-control` skill
3. read or create the relevant onboarding
4. implement approved work
5. refresh onboarding through the `c-05-create-or-update-onboarding-files` skill

This avoids a giant up-front documentation project. The first task on a file pays the onboarding cost; later tasks benefit.

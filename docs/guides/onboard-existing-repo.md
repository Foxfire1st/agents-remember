# Onboard An Existing Repo

You do not need full coverage before Agents Remember becomes useful. Start small, then let onboarding grow as work touches new files.

## 1. Install The Runtime

Configure the Agents Remember MCP server, then request:

```text
runtime_install()
```

Expose skills for your harness using the relevant [install guide](../README.md#install-guides).

## 2. Initialize Memory

Ask the agent to run `C-00-initialize-memory-repo` for the target repository.

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

Do not create onboarding content by hand before the memory root exists. C-00 owns the scaffold; C-03 owns onboarding bootstrap.

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

Ask the agent to run `C-03-repo-bootstrap`.

A repo-level `overview.md` is enough to start. Larger repositories can add route-local overviews under the mirrored onboarding hierarchy when a package, module, or source slice needs durable context.

## 5. Let Coverage Grow From Work

When a task touches a file, the agent should:

1. resolve context with C-08
2. check drift with C-02
3. read or create the relevant onboarding
4. implement approved work
5. refresh onboarding through C-05

This avoids a giant up-front documentation project. The first task on a file pays the onboarding cost; later tasks benefit.

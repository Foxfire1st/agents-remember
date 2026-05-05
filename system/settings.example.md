# Agent Memory Settings Example

`settings.md` exists in both supported topologies.

- Internal topology uses `<target-repo>/ar-management/system/settings.md`.
- Shared topology uses `<shared-ar-management-root>/system/settings.md`.

Default setup is internal and local-first. Shared setup is an explicit advanced choice for teams that want one management root for selected repositories.

## Internal Settings

Use this shape for the default repo-local scaffold:

```yaml
management:
  topology: internal

onboarding:
  storage:
    mode: repo-sidecar
  pathRules:
    include:
      paths:
        - README.md
        - docs/**
        - src/**
        - skills/**
        - system/**
      fileTypes:
        - .md
        - .py
        - .ts
        - .tsx
    exclude:
      paths:
        - vendor/**
        - node_modules/**
        - dist/**
        - build/**
      fileTypes:
        - .png
        - .jpg
        - .jpeg
        - .gif
        - .zip

crossRepo:
  allow: []
```

Internal `repo-sidecar` storage keeps eligible onboarding artifacts under the repository's own `ar-management/onboarding/` folder. `crossRepo.allow` is empty by default, so internal bootstrap and discovery stay local unless the developer explicitly opts into named neighboring repositories.

## Shared Settings

Use this shape only for an explicitly selected shared management root:

```yaml
management:
  topology: shared

onboarding:
  storage:
    layout: shared-root
  pathRules:
    - path: my-app
      include:
        paths:
          - README.md
          - docs/**
          - src/**
        fileTypes:
          - .md
          - .py
          - .ts
          - .tsx
      exclude:
        paths:
          - vendor/**
          - node_modules/**
          - dist/**
          - build/**
        fileTypes:
          - .png
          - .jpg
          - .jpeg
          - .gif
          - .zip

    - path: firmware-app
      include:
        paths:
          - README.md
          - docs/**
          - firmware/**
        fileTypes:
          - .md
          - .c
          - .h
      exclude:
        paths:
          - build/**
          - generated/**
        fileTypes:
          - .bin
          - .map
```

Shared storage keeps eligible onboarding artifacts under the selected shared root, usually below `<shared-ar-management-root>/onboarding/<repo-name>/`.

In shared settings, `pathRules` should normally be a list of scoped rules. A rule with `path: my-app` applies to the repository named `my-app`; a rule with `path: my-app/src` applies only to that repository's `src/` subtree. Use an unscoped include/exclude block only when you intentionally want the same eligibility default for every shared-managed repository.

## Storage Versus Eligibility

`onboarding.storage` answers where eligible onboarding artifacts live.

`onboarding.pathRules` answers which source paths and file types are eligible for onboarding. In shared settings, each rule can also identify the repository or repository subtree it applies to with `path`.

Do not use `pathRules` as per-path storage switching. If a future task adds per-path storage routing, it should do so explicitly without removing include/exclude eligibility from either topology.

## Scaffold Shape

Both topologies use the same `ar-management/` scaffold shape:

```text
ar-management/
├── onboarding/
├── tasks/
├── docs/
├── notes/
└── system/
    ├── settings.md
    ├── sources.md
    └── tools.md
```

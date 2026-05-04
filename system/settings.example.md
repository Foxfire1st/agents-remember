# Agent Memory Settings Example

Copy or rename this file to `<AR_MANAGEMENT_ROOT>/system/settings.md` when scaffolding a management root.

The management root is read from `.env` in the agents-remember checkout:

```dotenv
AR_MANAGEMENT_ROOT=../ar-management
```

Relative `AR_MANAGEMENT_ROOT` values resolve from the `.env` file location.

## Derived Paths

- Onboarding root: `<AR_MANAGEMENT_ROOT>/onboarding`
- Task root: `<AR_MANAGEMENT_ROOT>/tasks`
- Local domain docs root: `<AR_MANAGEMENT_ROOT>/docs`
- System root: `<AR_MANAGEMENT_ROOT>/system`
- Sources file: `<AR_MANAGEMENT_ROOT>/system/sources.md`
- Tools file: `<AR_MANAGEMENT_ROOT>/system/tools.md`

## Default Scaffold

```text
ar-management/
├── onboarding/
│   ├── <repo-name>-onboarding/
│   └── <another-repo>-onboarding/
├── tasks/
├── docs/
└── system/
    ├── settings.md
    ├── sources.md
    └── tools.md
```

The onboarding root may contain local onboarding folders or cloned, version-controlled onboarding repositories shared by a team.

## Onboarding Storage

If `settings.md`, `onboarding.storage`, or `pathRules` are absent, the effective behavior stays on the existing default: `external` storage with an implicit rule for the current scoped repo using `includes: ["*"]` and `excludes: []`.

Storage modes:

```yaml
# External mode: mirrored markdown onboarding files.
onboarding:
    storage:
        mode: external
        pathRules:
            - path: "my-app"
                includes:
                    - "*"
                excludes:
                    - "vendor/**"
                    - "node_modules/**"
                    - "db/generated/**"

# Inline mode: structured onboarding blocks in source files where safe.
onboarding:
    storage:
        mode: inline
        pathRules:
            - path: "my-app"
                includes:
                    - "src/**/*.py"
                    - "src/**/*.ts"
                excludes:
                    - "src/**/*.generated.*"
                    - "src/generated/**"

# Hybrid mode: resolve the storage adapter per matched path rule.
onboarding:
    storage:
        mode: hybrid
        default: external
        pathRules:
            - path: "my-app"
                storage: inline
                includes:
                    - "src/**/*.ts"
                    - "src/**/*.tsx"
                excludes:
                    - "src/**/*.generated.*"
                    - "src/generated/**"
            - path: "my-app"
                storage: disabled
                includes:
                    - "vendor/**"
                    - "node_modules/**"
                excludes: []
```

`pathRules` are shared by all storage modes. `path` identifies the repo or scoped folder, `includes` chooses eligible files within that scope, `excludes` removes matches from the eligible set, and `excludes` always wins.

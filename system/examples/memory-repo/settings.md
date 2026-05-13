# Memory Repo Settings Example

Use this file as the human-facing `system/settings.md` starter for a durable
memory layer:

- repo-local internal memory: `<repo>/ar-memory/system/settings.md`
- shared memory repo: `ar-coordination/memory-repos/ar-<repo>/system/settings.md`

Machine-readable storage, path-rule, and cross-repo policy belongs in the
sibling `system/settings.json` file. Use the sibling `settings.json` example as
the matching JSON starter.

## Scope

This memory root stores durable context for one code repository. Its settings
and instructions are valid for that repository. It owns:

- onboarding storage policy
- onboarding path eligibility
- branch-gated cross-repo allowances
- repo-specific sources, tools, coding guidance, and workflow notes

Coordinator settings can define global instructions and tools across
repositories. They can also help locate memory repos and task folders. They
should not own rules that are valid only for this selected memory layer.

## Storage

Set `onboarding.storage.mode` in `settings.json` according to topology:

- `repo-sidecar` for internal `<repo>/ar-memory`
- `memory-repo` for shared `ar-coordination/memory-repos/ar-<repo>`

## Path Eligibility

Use `onboarding.pathRules` in `settings.json` to describe which source files
should receive onboarding companions. Keep these rules in the memory layer so
agents and tools resolve eligibility from the same committed source.

## Cross-Repo Policy

Use `crossRepo.allow` in `settings.json` for explicit branch-gated neighbor
repositories. Keep this list empty unless the memory layer truly depends on
another repository's code or memory context.

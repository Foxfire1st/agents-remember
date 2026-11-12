# Memory Repo Settings Example

Use this file as the human-facing `system/settings.md` starter for a durable
memory layer:

- `ar-coordination/memory-repos/ar-<repo>/system/settings.md`

Repo-local internal memory under `<repo>/ar-memory/` was removed from the product.

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

Set `onboarding.storage.mode` in `settings.json`:

- `memory-repo` for the memory repo `ar-coordination/memory-repos/ar-<repo>`
- `repo-sidecar` to place one artifact beside its source instead of in the memory repo; it is a
  per-artifact placement, not a memory topology

## Path Eligibility

Use `onboarding.pathRules` in `settings.json` to describe which source files
should receive onboarding companions. Keep these rules in the memory layer so
agents and tools resolve eligibility from the same committed source.

## Cross-Repo Policy

Use `crossRepo.allow` in `settings.json` for explicit branch-gated neighbor
repositories. Keep this list empty unless the memory layer truly depends on
another repository's code or memory context.

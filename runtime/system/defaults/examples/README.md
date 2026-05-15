# System Examples

Examples are split by target folder so agents can copy a whole folder shape
instead of inferring ownership from file names.

## Coordinator

`examples/coordinator/` models files for `ar-coordination/system/`.

The coordinator is the place for workspace-wide instructions and tools that
apply across multiple code repositories: global approval expectations, shared
command conventions, common source registries, task/worktree roots, selected
memory repo locations, and local operator conventions.

Coordinator files should route agents through C-08 before repository-specific
work begins. They may define global defaults, but they should not encode rules
that are true for only one code repository.

## Memory Repo

`examples/memory-repo/` models files for either:

- `<repo>/ar-memory/system/`
- `ar-coordination/memory-repos/ar-<repo>/system/`

The memory layer owns rules that are valid for its respective code repository:
onboarding storage, path eligibility, cross-repo policy, domain sources,
repo-specific tools, coding guidelines, and branch/workflow notes.

When coordinator-wide guidance and memory-layer guidance both apply, prefer the
more specific memory-layer rule for work in that code repository.

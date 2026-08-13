## Coding Style

Use this file as a starter for repository-specific coding guidance inside a
memory root, for example:

- `<repo>/ar-memory/system/coding-guidelines.md`
- `ar-coordination/memory-repos/ar-<repo>/system/coding-guidelines.md`

Keep concrete project preferences in the target repository's memory layer, not
in the generic examples folder, unless they describe Agents Remember itself.

### Compatibility And Legacy Code

Do not keep old code paths, wrappers, aliases, fallbacks, or compatibility
layers by default.

Compatibility is allowed only when required by:

- an external/public API contract
- persisted data
- staged migration or rollout requirements
- explicit user request

If compatibility is required, make the reason clear in the code or final summary
and include the removal condition when applicable.

### Deletion And Cleanup

Delete obsolete code only when it is clearly unused by the updated
implementation and not part of a public contract, generated artifact, migration
history, or documented extension point.

Remove unused imports, unreachable branches, dead helpers, obsolete tests, and
stale comments created by your change.

Do not delete migration files, generated baselines, snapshots, lockfiles, or user
data unless explicitly requested.

### Linux, WSL, And Clean Quality Boundaries

Treat the subprocess environment as part of the tested contract:

- Linux and WSL jobs resolve native POSIX executables. Reject Windows command
  shims, drive paths, UNC paths, and Windows-mounted executables rather than
  handing them to a Linux child process.
- Put `TMPDIR`, `TMP`, `TEMP`, coverage data, progress, and test reports on
  native POSIX storage under the owning worktree enclosure. One current report
  replaces its predecessor; do not accumulate per-run scratch artifacts.
- Describe dry-run plans symbolically. Resolve and require an executable only
  when execution starts, so a plan does not depend on unrelated host tools.
- Use `sys.executable` and explicit environment ownership in Python tests. Do
  not assume a checkout-local `.venv` or inherit workflow-control variables that
  the test did not declare.
- Keep `pytest -n=auto` in repository configuration so parallel execution is the
  default contract rather than an agent memory requirement.

For Agents Remember acceptance proof, use only the pinned Dagger Ubuntu graph:
`mode=targeted` for a leaf/focused run and `mode=full` once at master integration.
Both modes require the recorded source commit as `diff-base`; never infer it from the
container checkout. A direct host pytest or wrapper invocation is diagnostic only and
must not be reported as acceptance. Discover the live public contract with
`dagger call quality --help` rather than relying on a remembered command.
Materialize the exact staged candidate and a separate Git ancestry bundle; do
not mount the live coordination root, credentials, or container socket. Bundle
Codex in that graph and exercise the real read-only app-server protocol without
submitting prompts. Dagger owns container construction, caching, graph progress,
and service composition; Agents Remember retains task identity, approval,
candidate selection, lifecycle recovery, and durable report projection. Do not
add a direct-Docker or local compatibility runner beside it.

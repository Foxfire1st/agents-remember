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

### Acceptance Execution Boundaries

Treat the execution environment as part of the repository's acceptance contract:

- Name the permitted executor and environment explicitly, and refuse execution
  elsewhere before any test-capable or proof-reuse path begins.
- Put temporary data, coverage or equivalent measurement data, progress, and reports
  under storage owned by the configured executor or worktree enclosure. One current
  report replaces its predecessor; do not accumulate per-run scratch artifacts.
- Describe dry-run plans symbolically. Resolve and require an executable only
  when execution starts, so a plan does not depend on unrelated host tools.
- Make subprocess, concurrency, retry, and environment inheritance rules explicit
  in this repository's concrete policy. Do not inherit another repository's runner
  assumptions.

## Repository-specific acceptance policy

Replace this section during repository onboarding with the concrete acceptance contract for this
repository. Name the exact executor or command, permitted environment, leaf scope, full scope,
required base/candidate inputs, resource policy, retry rules, durable evidence, and refusal
behavior. The lifecycle cadence is fixed: one change-set-scoped acceptance run at leaf closeout,
no rerun at leaf integration, and one full-repository acceptance run at master integration.

Do not infer an executor from another repository, add a compatibility fallback, or leave a required
gate dependent on a file that the candidate can delete to disable its own validation. Link the
operational invocation from `system/tools.md` and the landing boundaries from
`system/git-workflow.md`.

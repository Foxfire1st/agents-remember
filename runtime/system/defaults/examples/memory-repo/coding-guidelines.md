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

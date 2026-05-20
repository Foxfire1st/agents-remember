# Coordinator Tools Example

Use this file for tools and commands that are useful across all or many code
repositories attached to this coordinator.

Repo-specific checks, commands, branch workflow, and coding tools belong in the
selected memory layer's `system/tools.md`, not here.

## Global Commands

No global commands configured yet.

When `system/settings.json` enables context providers, the provider lifecycle
tooling should expose bounded `status`, `start`, `stop`, `refresh`, and
`doctor` commands per provider instance. Until that tooling is installed,
agents may use configured providers manually only after checking the configured
root and keeping provider output small.

Expected provider command shapes:

```bash
# GrepAI memory provider
cd <coordination_root>/memory-repos
grepai status --no-ui
grepai watch --status
grepai search --compact --path "<query>"

# CodeGraphContext relationship provider
<coordination_root>/providers/_venvs/codegraphcontext/bin/pip install \
  -r <coordination_root>/providers/requirements/codegraphcontext.txt
cd <runtimeRoot>
HOME=<runtimeRoot> \
CGC_RUNTIME_DB_TYPE=kuzudb \
DEFAULT_DATABASE=kuzudb \
KUZUDB_PATH=<runtimeRoot>/.codegraphcontext/db/kuzu \
CGC_RUNTIME_DB_PATH=<runtimeRoot>/.codegraphcontext/db/kuzu \
LOG_FILE_PATH=<runtimeRoot>/.codegraphcontext/logs/cgc.log \
DEBUG_LOG_PATH=<runtimeRoot>/.codegraphcontext/logs/debug.log \
<coordination_root>/providers/_venvs/codegraphcontext/bin/cgc doctor
```

The command env above is process env. For CGC v0.4.10, do not write
`CGC_RUNTIME_DB_TYPE`, `KUZUDB_PATH`, or `CGC_RUNTIME_DB_PATH` into
`<runtimeRoot>/.codegraphcontext/.env`; `cgc doctor` accepts them as process
env but reports them as invalid persisted config keys.

Patch and containment checks are part of provider health. A CGC provider should
not be used in managed mode if indexing creates `.cgcignore`,
`.codegraphcontext`, reports, databases, or logs inside the indexed source
repository.

Provider output is discovery evidence only. Source files, verified onboarding,
drift checks, branch validity, and approved memory promotion remain the proof
layer.

## Notes

Agents should resolve the target repository with C-08 before choosing task,
worktree, memory, or validation paths. Prefer memory-layer tool instructions
when a command is repository-specific.

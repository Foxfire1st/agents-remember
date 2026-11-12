# Product Context Resolution

In [context resolution](../concepts.md#context-resolution) the product finds the facts of one code
repository. It answers where the memory repository is, where the coordination root is, which
settings apply, which [task folder](../concepts.md#task-folder) to use and which
[cross-repository context](../concepts.md#cross-repository-context) is allowed.

## Asking For The Context

A harness asks with the tool `context_packet`. The call takes these arguments:

| Argument | Meaning |
| --- | --- |
| `repo_id` | Required. The id of a code repository that the settings file lists in `repositories`. |
| `task_context` | The task of the caller: `task_document_ref`, an object with `repository` (the id of the code repository) and `path` (the path of the task document in the task folder of that repository), and `contract_path`, the path of the task's [worktree contract](../concepts.md#worktree-contract). A role agent that works on a task passes it. |
| `include_drift` | `true` measures the reference anchors of the cards. The default `false` measures nothing. See [Memory Quality Control](drift-c02.md). |
| `include_freshness` | `true` fetches the remotes and compares the code and memory checkouts with their upstream branches. The default is `false`. |

The answer of the tool is short and holds these fields:

| Field | Contents |
| --- | --- |
| `ok` | `true` when the code repository is a readable Git checkout, on a branch or with a detached `HEAD`. |
| `contextPacketVersion` | `3`. |
| `roots` | `code` and `memory`: the folders the caller works in. For a task they are the task's code and memory worktrees, and `enclosure` and `taskDocument` are added. `code` is an absolute path. The other roots are relative to `code` when that form is shorter; join such a root with `code` to get its folder. |
| `task` | For a task: its `id` and its `master`. |
| `actions` | One line for each state that needs action. The field is absent when no state needs action. |
| `servingBuild` | The commit of the build that serves the tool. |
| `pair` | The two commits of the pair: `code` and `memory`. |

The whole result stays in the file that the answer names in `log`. Among its blocks are `repo`,
`paths`, `memory`, `worktree`, `drift` and `freshness`.

## Resolution

Context resolution needs the identity of the code repository: its name, its root, or both. The
code of the product that asks for a resolution can add hints: a coordination root, a settings
file, an onboarding root, or a task's worktree contract, task name or worktree name.
`context_packet` passes the coordination root of the settings file, the folder `onboarding` of
the [memory root](../concepts.md#memory-root) and, from `task_context`, the worktree contract of
the task. A user passes no hint directly: the hints are not arguments of `context_packet`.

The product selects the memory repository in this order:

1. A settings file that the caller names decides the coordination root and the memory root.
2. Otherwise the memory repository is `<coordination-root>/memory-repos/ar-<id>/`, where `<id>` is
   the id of the code repository.

When that memory location does not exist, resolution fails with a missing-memory error that names
the checked path.

When a task's contract is in scope, the memory root and the onboarding root are those of the
task's memory worktree.

## Resolved Facts

Context resolution gives these facts. The whole result of `context_packet`, in the file that its
answer names in `log`, holds them in camel case in the blocks that the last column names; for
example `settings_path` stands there as `paths.settingsPath`.

| Fact | Contents | In the whole result of `context_packet` |
| --- | --- | --- |
| `topology` | `external`: the memory lies outside the code repository; the product knows no other placement. | not held |
| `code_repository_name`, `code_repository_root` | The code repository. | `repo.id`, `repo.root` |
| `coordination_root` | The coordination root. | `paths.coordinationRoot` |
| `memory_root` | The memory repository, or the task's memory worktree. | `paths.memoryRoot` |
| `memory_mode` | `external`. | `memory.mode` |
| `onboarding_root` | The folder `onboarding` under the memory root. | `paths.onboardingRoot` |
| `settings_path` | `system/settings.md` of the memory repository, also for a task: the instruction file of the memory repository; it is none of the four settings files. When the memory repository has none and `<coordination-root>/system/settings.md` exists, that file. | `paths.settingsPath` |
| `path_settings_path` | The memory settings file, `system/settings.json` beside `settings_path`, when that file exists. When `settings_path` is the file under the coordination root, the user settings file, when it exists. | `paths.pathSettingsPath` |
| `task_root` | The task folder of the contract or of the named task, else `<coordination-root>/tasks/<id>`. | `paths.taskRoot` |
| `temp_root` | `<coordination-root>/temp`. | `paths.tempRoot` |
| `docs_root` | The folder `docs` under the memory root when it exists, else `<coordination-root>/docs`. | `paths.docsRoot` |
| `system_root` | The folder `system` of the memory repository, also for a task; `<coordination-root>/system` when the memory repository has no such folder. | `paths.systemRoot` |
| `sources_path`, `tools_path` | `sources.md` and `tools.md` under `system_root`. | `paths.sourcesPath`, `paths.toolsPath` |
| `storage` | The storage keys of the memory settings file: `mode`, `default` and `pathRules` (see [Memory settings file keys](settings-json.md#memory-settings-file-keys)). | `memory.storage` |
| `pathRules` | The [path rules](path-rules.md) of the repository. | `memory.storage.pathRules` |
| `crossRepo` | The cross-repository entries the settings allow. | `memory.crossRepo` |
| `contract_path`, `worktree_group`, `code_worktree`, `memory_worktree` | For a task: the path of its worktree contract, its enclosure and its two worktrees. | `worktree.contractPath`, `worktree.worktreeGroup`, `worktree.codeWorktree`, `worktree.memoryWorktree` |
| `ledger_path` | The path of the file `memory.md`, the [memory attribution cache](../concepts.md#memory-attribution-cache): in the task's memory worktree for a task, else in the memory repository. | `paths.ledgerPath` |

## Boundary

Context resolution does not mutate Git, create memory roots, update cards or start worktrees.
Memory-root setup follows the [memory initialization guide](../guides/onboard-existing-repo.md).
Card work follows the [Curator card procedure](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md).
Worktree mutation uses the worktree tools under their [lifecycle authority](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/operations/closeout.md).

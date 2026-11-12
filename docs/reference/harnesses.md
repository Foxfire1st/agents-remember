# Harnesses and role settings

This page says what a harness is to Agents Remember and how the launch of a role agent is set.
It covers the role settings, the files that hold them, the order in which a start takes its
choices, and the step-up of a role.

Related pages: [Settings Reference](settings-json.md) describes the four settings files,
[Roles and role agents](../roles.md) describes the roles and their starts, and
[Host](../host.md) describes the host.

## What a harness is

A harness is a coding-agent program: Claude Code, Codex, Cursor, Hermes, Pi or OpenClaw. Agents
Remember meets a harness in two places:

- **The harness you work in.** You register the tool server in it, and its chat calls the tools.
- **The harness of a role agent.** The host (Paseo) runs each role agent as a chat of a harness.
  The host's catalog names the harnesses it can start, their models and their efforts. The role
  launcher of the dashboard shows that catalog, and `agents-remember paseo status` lists its
  [host providers](../concepts.md#host-provider) under `providers`. Whether a harness of an id
  exists is the host's answer.

A row of the role settings names the harness of a role in `harness`. A start names it in `agent`.
Both take a harness id, the id under which the role settings and the host name a harness.

## What each harness needs

Each harness page has a section that lists what that harness needs to work with Agents Remember:

- [Claude Code](../install/claude-code.md#what-this-harness-needs)
- [Codex](../install/codex.md#what-this-harness-needs)
- [Cursor](../install/cursor.md#what-this-harness-needs)
- [Hermes](../install/hermes.md#what-this-harness-needs)
- [Pi](../install/pi.md#what-this-harness-needs)
- [OpenClaw](../install/openclaw.md#what-this-harness-needs)

[Host](../host.md#what-the-host-needs) lists what a harness needs when the host runs it for a
role agent.

A role agent receives the tools of Agents Remember from one tool server that its start gives it,
named `agents-remember-task`. The whole result of `role_start`, in the file that `log` names,
says `"secondCopyLoaded": false` when the start kept the tools of the tool server
`agents-remember` out of the role agent, which it does on Claude Code only, and `true` otherwise.

## The harness ids of the role settings

The role settings know four built-in ids: `claude`, `codex`, `pi` and `eve`. `claude`, `codex`
and `pi` are the ids of Claude Code, Codex and Pi. `eve` is the id of the harness Eve, for which
this documentation has no install page; a start with it needs a host provider of that id on the
host. Hermes takes the id of its provider entry, `hermes` on [its page](../install/hermes.md).
Cursor and OpenClaw have no id: the host starts no role agent on them. The family
`orchestration.harnesses` adds ids; it is keyed by id:

- An entry under a new id adds that id. The entry is an object, and it may be empty.
- An entry accepts the key `extraArgs`, for the id `claude` and in the user settings file only.
- The loader, the part of Agents Remember that reads and checks the role settings, also accepts
  the keys `name`, `command`, `argv`, `modelFlag`, `effortFlag`, `effortFlagValues`,
  `effortSessionValues` and `effortSessionCommand` in an entry and reads none of them.
- A key that the loader does not know is refused.

`orchestration.spawn.harness` names the harness of every role whose row names none. The loader
accepts a built-in id or an id that `orchestration.harnesses` adds, and refuses another value.
The `harness` of a role row is not checked against these ids. A start checks it against the
host's catalog, so a role row may name any harness for which the host has a host provider.

## The role settings

The role settings choose the harness, the model, the effort and the permission mode of each role.
A role agent takes them at its start; a change does not alter a running role agent.

### The two files

| Layer | File | Holds |
| --- | --- | --- |
| user settings file | `~/.config/agents-remember/settings.json`, or `$XDG_CONFIG_HOME/agents-remember/settings.json` when that variable is set | your choices for every code repository |
| repository settings file | `<code-repository>/system/settings.json` | choices for the tasks of one code repository |

Both files hold the same family under the top-level key `orchestration`. The product reads both
at each use and lays the repository settings file over the user settings file, key by key.
Either file may be absent.

`user_settings_init` creates the user settings file open to writing, and `server_info` names an
open file under `openUserSettings`. You can close the file to writing.
[Settings Reference](settings-json.md) gives the command that closes the file and the
command that opens it again for an edit.

### The keys of a role row

A role row stands under `orchestration.roles.<role>`. The role is `architect`, `investigator`,
`orchestrator`, `manager`, `worker`, `reviewer`, `curator` or `bootstrap`. A row accepts these
keys. It also accepts `launchArgs`, `promptKeywords` and `sessionCommands`, and reads none of
them. A key that the loader does not know is refused, and the refusal names the file and the key.

| Key | What it sets at a start | Layer |
| --- | --- | --- |
| `harness` | the harness that runs the role agent, by its harness id | both |
| `model` | the model of that harness | both |
| `effort` | the thinking option of that model | both |
| `serviceTier` | the speed selection of the model: the value of the feature `service_tier` | both |
| `featureValues` | values for features of the model. The host's catalog lists the features of each model; a feature is a toggle, which takes `true` or `false` (for example `fast_mode`), or a selection, which takes one of its option ids (for example `service_tier`) | `service_tier` and `fast_mode` in both; every other feature in the user settings file only |
| `mode` | the [permission mode](../concepts.md#permission-mode) of the agent | user settings file only |
| `notes` | your own instruction text for the role. The product adds it to the role's [role capsule](../concepts.md#role-capsule); a text whose first line is `<overwrite>` replaces the capsule. The `notes` of the [host profile](../concepts.md#host-profile) of the role, when they are not empty, take the place of this text. | user settings file only |
| `extraArgs` | extra command-line arguments for the harness; accepted for `claude` only | user settings file only |
| `stepUp` | a second set of choices for the role; see [Step-up](#step-up) | user settings file only |

A repository file that holds a key of the user settings file only is refused when the settings
load. The refusal names the file and the key.

The loader checks that `harness`, `model` and `effort` are non-empty texts. A start checks them
against the host's catalog and matches a name to the host's id or label without regard to case.

Besides the eight roles, the family accepts the row name `review-presenter`. That row sets the
launch of the explainer of the review page. It is not a role.

### A row for one level

`orchestration.rolesPerLevel.<level>.<role>` holds a row of the same shape for one level. The
level follows the role:

| Level | Roles |
| --- | --- |
| `leaf` | `worker`, `reviewer`, `curator` |
| `master` | `manager` |
| `portfolio` | `architect`, `investigator`, `orchestrator` |

A start reads the row of the role's own level only. A row of a role under another level is
accepted and read by no start. `bootstrap` has no level.

A level row lies over the role row key by key: a key that the level row does not name comes from
the role row. Across both files the order is, strongest first:

1. the level row of the repository file;
2. the level row of the user settings file;
3. the role row of the repository file;
4. the role row of the user settings file.

### The order of choices at a start

A start takes each choice from the first source that names it:

| Choice | Order, strongest first |
| --- | --- |
| harness, model, effort | the start (`agent`, `model`, `effort` of `role_start`, or the selectors "Role agent override", "Role model override" and "Role effort override" of the role launcher); the host profile of the role; the role settings; the host's default |
| permission mode | the host profile of the role; the role settings; the least permissive mode that the product knows for the harness: `default` for `claude`, `auto` for `codex` |
| feature values | the role settings; over them the host profile of the role; over that the start |
| notes | the host profile of the role, when its `notes` are not empty; else the role settings |

Four rules complete the table:

- The host profile of a role is the agent profile that the host keeps under the role id as its
  name; see [Host profiles](../host.md#host-profiles).
- A model or an effort from a lower source counts only for the harness it was written for. When
  the start names another harness, the model and the effort of the role settings do not apply.
- A start cannot set the permission mode. A start may set the features `service_tier` and
  `fast_mode` and no other feature.
- A `model` or an `effort` on `role_start` without `agent` is refused, except for `bootstrap`.

### Defaults

Without a user settings file and without a repository file, these values hold:

| Choice | Value |
| --- | --- |
| harness | `orchestration.spawn.harness`; without it, the first of the [harness ids](#the-harness-ids-of-the-role-settings) `claude`, `codex`, `pi`, `eve` and the added ids for which the host has a host provider |
| model, effort | the host's default for the harness |
| permission mode | `default` for `claude` and `auto` for `codex`, when the host offers that mode; for another harness the product names none |
| `orchestration.roleMessageLimit` | 1,000 characters |
| `orchestration.loops.defaults.maxRounds` | 5 review rounds |

A Bootstrap role agent takes the harness of the chat that started it, unless the call names
`agent`. When the host has no host provider for that harness, the call is refused. Its `detail`,
in the log file that the answer names under `log`, lists the harnesses the host knows, and
`agents-remember paseo status` shows which of them are available. Make the call again with one
of them in `agent`.

### Step-up

A step-up is a second set of choices for a role, for work that the first set did not finish.
`orchestration.roles.<role>.stepUp` accepts `harness`, `model`, `effort`, `serviceTier`, `mode`,
`featureValues` and `notes`. It accepts no `extraArgs` and no step-up of its own, and the loader
refuses a step-up whose choices equal those of the role.

A start with the step-up reads no host profile. When the step-up names another harness or another
model, the start takes no choice of the role's own row that the step-up does not name, and
without a `mode` in the step-up it takes the host's default mode for that harness.

A step-up is taken in two ways:

- **By count.** For `worker` and `curator`, the key `afterTries` names a number of tries; it is 3
  when the row does not name it. A try of a Worker is a recorded round of code review that leaves
  findings; a try of a Curator is such a round of memory review. When the count is reached, the
  answer of the review record says that the role steps up, and the next start of that role on
  the task takes the step-up. Another role refuses `afterTries`.
- **On request.** `role_start` with `step_up` set to one sentence that gives the reason starts
  the role with its step-up. The button "Step up" of the role launcher does the same and asks for
  the sentence. Without a step-up in the settings the call is refused.

### Example

This user settings file loads as it stands:

```json
{
  "orchestration": {
    "spawn": { "harness": "claude" },
    "roles": {
      "architect": { "model": "opus", "effort": "high" },
      "worker": {
        "harness": "codex",
        "effort": "high",
        "stepUp": { "harness": "claude", "model": "opus", "afterTries": 2 }
      },
      "reviewer": {
        "model": "sonnet",
        "effort": "high",
        "mode": "default",
        "notes": "Read the test output before the diff."
      }
    },
    "rolesPerLevel": {
      "leaf": { "curator": { "model": "sonnet" } }
    }
  }
}
```

It sets these launches, where neither a start nor the host profile of a role names another
choice:

- The Worker runs on `codex`. Every other role runs on `claude`, except Bootstrap, which takes the
  harness of the chat that started it.
- The Architect takes the model `opus` with the effort `high`.
- The Worker steps up to `claude` with the model `opus` after two rounds of code review that
  left findings.
- The Reviewer takes the model `sonnet`, the effort `high` and the mode `default`, and receives
  the note with its role capsule.
- The Curator takes the model `sonnet`.
- The Investigator, the Orchestrator and the Manager take the host's default model for `claude`.

The model and effort names are examples. A start refuses a name that the host's catalog does not
offer for the harness.

## Create the user settings file

`user_settings_init` with `target` set to `role-settings` creates the user settings file with a
harness for each named role. It writes no model and no effort, and it never writes a file that
exists. Call it with `dry_run` first.

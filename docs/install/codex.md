# Install for Codex

This page adds to the common install steps what holds for Codex only: how you register the tool
server, which role agents the host starts on Codex, and which settings Codex needs.

## Install steps

These are the five install steps. [Getting started](../getting-started.md) tells them in full, and
the page [Install](README.md) names them for every harness.

1. **Install the package.** Install the published package `agents-remember-mcp` with uv.
2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`. It
   names the workspace root, the coordination root and your code repositories.
3. **Register the tool server in your harness.**
   [Register the tool server in Codex](#register-the-tool-server-in-codex) shows the file
   and the form of the declaration.
4. **Start the setup.** In a chat of Codex, ask the agent to make this call:
   `role_start(role="bootstrap", request_id="<a new UUID>")`.
   [Role agents on this harness](#role-agents-on-this-harness) says what the call does from
   Codex.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`.

## Register the tool server in Codex

Step 3 of the install steps is one table in the configuration of Codex. Codex keeps the
registration in `~/.codex/config.toml`. Where the variable `CODEX_HOME` is set, the file is
`$CODEX_HOME/config.toml`.

```toml
[mcp_servers.agents-remember]
command = "agents-remember-mcp"
args = ["--config", "/home/<you>/.config/agents-remember/agents-remember-settings.json"]
```

`--config` takes an absolute path, so write your home folder in full instead of `~`.

Codex also has a command that adds an MCP server:

```bash
codex mcp add agents-remember -- agents-remember-mcp --config /home/<you>/.config/agents-remember/agents-remember-settings.json
```

The vendor describes the table and the command in [Codex: MCP](https://developers.openai.com/codex/mcp).
The file and the table were read on Codex 0.166.0.

The registration is the only change to Codex. The tool server serves its
[skill](../concepts.md#skill), the instructions of the roles, as MCP resources. In its answer to
the harness's `initialize` request it declares the MCP skills extension
`io.modelcontextprotocol/skills`, through which a harness can load a served skill as one of its
own skills. Codex 0.166.0 contains that extension. Whether it loads the served skill as a skill is
not verified. The setup needs no read of the skill: a role agent receives its part of the
instructions with its first message.

## Role agents on this harness

The host has a built-in [host provider](../concepts.md#host-provider) for Codex; its id is
`codex`. Every role can run on Codex. You choose it for a role with `"harness": "codex"` under
`orchestration.roles.<role>` in the user settings file, `~/.config/agents-remember/settings.json`.
`<role>` is the id of a role, for example `worker`;
[The keys of a role row](../reference/harnesses.md#the-keys-of-a-role-row) lists the ids. A
`role_start` call chooses it with `agent="codex"`.

The call of step 4, `role_start` with `role="bootstrap"`, starts the Bootstrap role agent on the
harness of the chat that makes the call. Made in a Codex chat, it starts the Bootstrap role agent
on Codex.

## What this harness needs

Each entry says what you see without the setting, the setting with its file and key, and who makes
it. The entries were checked on Codex 0.166.0 and 0.160.0 with the host this build installs,
unless an entry names another ground under "Checked".

The keys of Agents Remember stand in two files. The user settings file is
`~/.config/agents-remember/settings.json`. That file can be read-only; the [settings
reference](../reference/settings-json.md) shows the two commands that open and close it. The
settings file is `~/.config/agents-remember/agents-remember-settings.json`.

- **A role agent on Codex can ask before a call of a tool of Agents Remember, and waits until you
  answer.** Without a mode in the user settings file, Agents Remember starts Codex in the mode
  `auto`, which the host labels "Default Permissions". The host starts that mode with Codex's
  approval policy `on-request` and keeps a prompt open until you answer it.
  - Setting: `orchestration.roles.<role>.mode` in the user settings file, for example
    `"auto-review"`. The host offers `auto`, `auto-review` and `full-access` for Codex. In the mode
    `auto-review`, a prompt goes to Codex's automatic reviewer and not to you.
  - Who: you state the mode. Agents Remember sends it with the start. You change the mode of a
    running agent in its chat.
  - Checked: the start mode and the key, in the code of Agents Remember; the modes, the approval
    policy of each and the open prompt, in the source of the host this build installs. No prompt
    was raised and no mode was changed for it.
- **A Codex agent that the host starts uses another sign-in or another session store than your own
  Codex.** Codex finds both through the variables `CODEX_HOME` and `CODEX_SQLITE_HOME`. A running
  host gives its agents the values of the shell or the program that started it, so the same agent
  can lose its sessions after a restart of the host from a shell with other values; see
  [When a restart is owed](../host.md#when-a-restart-is-owed).
  - Setting: `paseoRuntime.providers.codex.env` in the settings file, with the variables
    `CODEX_HOME` and `CODEX_SQLITE_HOME`. The key `codex` stands under `paseoRuntime.providers`,
    for example `{"codex": {"env": {"CODEX_HOME": "/home/<you>/codex"}}}`; the
    [settings reference](../reference/settings-json.md#paseoruntime) shows the whole value.
  - Who: you write the entry. Agents Remember hands it to the host unchanged.
    `agents-remember paseo status --config <settings file>` names under `inheritedHarnessHomes` a
    variable that the running host took from the shell that started it and that no entry states.
    `agents-remember paseo provision` refuses a restart that would change such a value, with
    `harness-home-would-change`.
  - Checked: the entry, the report and the refusal, in the code of Agents Remember. No host was
    restarted for it.
- **A role agent also carries the tools of the tool server that you registered under the name
  `agents-remember`.** This holds when the configuration of Codex that the agent reads,
  `config.toml` in the folder that `CODEX_HOME` names for the agent or else `~/.codex/config.toml`,
  registers that tool server. The role agent then has the tools of Agents Remember twice, under
  `agents-remember-task` and under `agents-remember`. The whole result of `role_start`, in the
  file that `log` names, says `"secondCopyLoaded": true` for every start on Codex, also when no
  such registration exists.
  - Setting: none. The host offers no setting that keeps one tool server out of one start on
    Codex. A role agent's instructions name the tool server `agents-remember-task` for every tool
    call.
  - Who: Agents Remember.
  - Checked: in the code of Agents Remember. No role agent was started on Codex for it.
- **A reason you give when you deny a permission prompt does not reach the agent.** The host hands
  Codex only the decision.
  - Setting: none. Deny the prompt, then send the reason to the agent as a message.
  - Who: you.
  - Checked: in the source of the host this build installs. No prompt was denied for it.

## Related pages

- [Install](README.md): the common steps and the other harnesses.
- [Getting started](../getting-started.md): the install steps in full.
- [The host](../host.md): what the host itself needs.
- [Roles and role agents](../roles.md): how role agents start and exchange messages.

# Install for Claude Code

This page adds to the common install steps what holds for Claude Code only: how you register the
tool server, which role agents the host starts on Claude Code, and which settings Claude Code needs.

## Install steps

These are the five install steps. [Getting started](../getting-started.md) tells them in full, and
the page [Install](README.md) names them for every harness.

1. **Install the package.** Install the published package `agents-remember-mcp` with uv.
2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`. It
   names the workspace root, the coordination root and your code repositories.
3. **Register the tool server in your harness.**
   [Register the tool server in Claude Code](#register-the-tool-server-in-claude-code) shows the file
   and the form of the declaration.
4. **Start the setup.** In a chat of Claude Code, ask the agent to make this call:
   `role_start(role="bootstrap", request_id="<a new UUID>")`.
   [Role agents on this harness](#role-agents-on-this-harness) says what the call does from
   Claude Code.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`.

## Register the tool server in Claude Code

Step 3 of the install steps is one command in Claude Code. Run it in a shell:

```bash
claude mcp add --scope user agents-remember -- agents-remember-mcp --config /home/<you>/.config/agents-remember/agents-remember-settings.json
```

`--config` takes an absolute path, so write your home folder in full instead of `~`.

Claude Code keeps a user-level registration in `~/.claude.json`, as the entry
`mcpServers.agents-remember` with the keys `type`, `command`, `args` and `env`:

```json
{
  "mcpServers": {
    "agents-remember": {
      "type": "stdio",
      "command": "agents-remember-mcp",
      "args": ["--config", "/home/<you>/.config/agents-remember/agents-remember-settings.json"],
      "env": {}
    }
  }
}
```

Claude Code knows the scopes `local`, `user` and `project` for a registration, and `local` is its
default. Its vendor documents `user` as the scope that holds for all your projects; see
[Claude Code: MCP](https://code.claude.com/docs/en/mcp). The file, the entry and the scopes were
read on Claude Code 2.1.321; `claude mcp add --help` prints the scopes and the default.

The registration is the only change to Claude Code. The tool server serves its
[skill](../concepts.md#skill), the instructions of the roles, as MCP resources. In its answer to
the harness's `initialize` request it declares the MCP skills extension
`io.modelcontextprotocol/skills`, through which a harness can list a served skill among its own
skills. Claude Code 2.1.321 contains that extension. Whether it lists the served skill among its
own skills is not verified. The setup needs no read of the skill: a role agent receives its part
of the instructions with its first message.

## Role agents on this harness

The host has a built-in [host provider](../concepts.md#host-provider) for Claude Code; its id is
`claude`. Every role can run on Claude Code. You choose it for a role with `"harness": "claude"`
under `orchestration.roles.<role>` in the user settings file,
`~/.config/agents-remember/settings.json`. `<role>` is the id of a role, for example `worker`;
[The keys of a role row](../reference/harnesses.md#the-keys-of-a-role-row) lists the ids. A
`role_start` call chooses it with `agent="claude"`.

Agents Remember asks for no minimum version of Claude Code. The host offers some models of its
list only when Claude Code has at least the version that the list names for the model. A role
with a model that the host does not offer is refused with `model_not_offered` (see
[What this harness needs](#what-this-harness-needs)).

The call of step 4, `role_start` with `role="bootstrap"`, starts the Bootstrap role agent on the
harness of the chat that makes the call. Made in a Claude Code chat, it starts the Bootstrap role
agent on Claude Code.

## What this harness needs

Each entry says what you see without the setting, the setting with its file and key, and who makes
it. The entries were checked on Claude Code 2.1.321 with the host this build installs, unless an
entry names another ground under "Checked".

The keys of Agents Remember stand in the user settings file. That file can be read-only; the
[settings reference](../reference/settings-json.md) shows the two commands that open and close it.

- **A role agent asks before it uses a tool for the first time.** Without a
  [permission mode](../concepts.md#permission-mode) in the user settings file, Agents Remember
  starts Claude Code in the permission mode `default`, which the host labels "Always Ask". Only the
  tool `server_info` of the tool server is approved beforehand.
  - Setting: `orchestration.roles.<role>.mode` in the user settings file, for example `"auto"`. The
    host offers `plan`, `default`, `acceptEdits`, `auto` and `bypassPermissions` for Claude Code.
  - Who: you state the permission mode. Agents Remember sends it with the start. A repository's
    settings file that states one is refused, and no start call accepts one.
  - Checked: the start mode and the key, in the code of Agents Remember; the permission modes and
    their labels, in the source of the host this build installs. The prompt itself was not
    reproduced.
- **A long-running role agent compacts its conversation earlier or later than you want.** To
  compact, Claude Code continues the conversation from a summary of it. It compacts when the
  conversation reaches a size in tokens: the smaller of the setting below and the model's largest
  context window.
  - Setting: `modelSettings.<model>.autoCompactWindow` in `~/.claude/settings.json`: a number of
    tokens from 100000 to 1000000, or `"auto"`.
  - Who: you. Agents Remember does not write the settings of Claude Code.
  - Checked: the key, its range and the threshold, in the settings schema and the texts that
    Claude Code 2.1.321 carries.
- **A role agent lists the tools of Agents Remember once, under the name of its own tool server,
  `agents-remember-task`.** Your registration names your tool server `agents-remember`, and the
  start keeps the tools of that tool server out of the role agent. The whole result of
  `role_start`, in the file that `log` names, then says `"secondCopyLoaded": false`.
  - Setting: none. At each start, Agents Remember hands the host one denied-tool rule for that
    agent, `mcp__agents-remember`, together with the rules that the entry `claude` of the host's
    configuration `config.json`, in the [host's home](../host.md#where-it-lives-on-disk), already
    denies. Your registration and your own chats stay as they are.
  - Who: Agents Remember.
- **A role's model is refused with `model_not_offered`.** Agents Remember refuses a model that the
  host's list for Claude Code does not hold. The host offers the models of its own list and the
  models that `~/.claude/settings.json` names, for example under `model`.
  - Setting: `orchestration.roles.<role>.model` in the user settings file, with a model that the
    role launcher of the [dashboard](../dashboard.md) lists. The version of the host comes with the
    build of Agents Remember; you do not choose it.
  - Who: you.
  - Checked: the refusal, in the code of Agents Remember; the two sources of the list, in the
    source of the host this build installs. No refused start was run.
- **A role agent reports that a tool call was declined although nobody declined it.** A message
  that reaches an agent on Claude Code while a call runs can stop that call. Claude Code then gives
  the agent, as the result of the call, the words "The user doesn't want to proceed with this tool
  use." Claude Code 2.1.321 adds that the call was stopped while it ran, that it may have run in
  part or in full, and that no permission prompt was involved.
  - Setting: none. Advice: check the effect of the call before the call is repeated.
  - Who: nobody sets anything. Agents Remember ships no instruction for it.
  - Checked: on a sandbox with Claude Code 2.1.321. The message `/compact`, sent into a turn in
    which a shell call ran, stopped the call within four seconds, and the agent quoted these words
    as the result. A call of a tool server and a message of another agent were not tried.

## Related pages

- [Install](README.md): the common steps and the other harnesses.
- [Getting started](../getting-started.md): the install steps in full.
- [The host](../host.md): what the host itself needs.
- [Roles and role agents](../roles.md): how role agents start and exchange messages.

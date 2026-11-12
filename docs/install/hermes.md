# Install for Hermes

This page adds to the common install steps what holds for Hermes only: how you register the tool
server, the [provider entry](../concepts.md#host-provider) the host needs before it starts role
agents on Hermes, and which settings Hermes needs. The program names itself Hermes Agent; this
documentation calls it Hermes.

## Install steps

These are the five install steps. [Getting started](../getting-started.md) tells them in full, and
the page [Install](README.md) names them for every harness.

1. **Install the package.** Install the published package `agents-remember-mcp` with uv.
2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`. It
   names the workspace root, the coordination root and your code repositories.
3. **Register the tool server in your harness.**
   [Register the tool server in Hermes](#register-the-tool-server-in-hermes) shows the file
   and the form of the declaration.
4. **Start the setup.** In a chat of Hermes, ask the agent to make this call:
   `role_start(role="bootstrap", request_id="<a new UUID>")`.
   [Role agents on this harness](#role-agents-on-this-harness) says what the call does from
   Hermes.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`.

## Register the tool server in Hermes

Step 3 of the install steps is one entry in `~/.hermes/config.yaml`, under `mcp_servers`:

```yaml
mcp_servers:
  agents-remember:
    command: "agents-remember-mcp"
    args: ["--config", "/home/<you>/.config/agents-remember/agents-remember-settings.json"]
```

`--config` takes an absolute path, so write your home folder in full instead of `~`.

Start a new Hermes session after the change, or run `/reload-mcp` in a running one. Hermes
describes the entry, its other keys and the reload in
[Hermes: MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/).

The registration is the only change to Hermes that the install asks of you. The tool server serves
its [skill](../concepts.md#skill) as MCP resources and declares the MCP skills extension
`io.modelcontextprotocol/skills`, through which a harness can ask a tool server for the list of its
skills and for each skill. Hermes v0.21.7 does not contain that extension. It offers its agent the
served files as plain MCP resources, through its tools `list_resources` and `read_resource`.

## Role agents on this harness

The host has no built-in host provider for Hermes. It runs Hermes over the Agent Client Protocol
(ACP) once the settings file, `~/.config/agents-remember/agents-remember-settings.json`, names
Hermes under `paseoRuntime.providers`. Put this provider entry into the object
`paseoRuntime.providers` of that file:

```json
{
  "hermes": { "extends": "acp", "label": "Hermes", "command": ["hermes", "acp"] }
}
```

In the entry, `"extends": "acp"` has the host drive the harness over ACP, `label` is the name that
the host shows for it, and `command` is the program with its arguments that the host starts.

The block `paseoRuntime` exists after the first call of step 4. That call writes the block, with
an empty `providers`, when the settings file holds none. So make the call of step 4 first, then
add the entry and run:

```bash
agents-remember paseo provision --config /home/<you>/.config/agents-remember/agents-remember-settings.json
```

The command writes the entry into the configuration of the host, and a running host reloads it.
Hermes documents an extra install step for `hermes acp`, the installation of its extra `acp` with
the package `agent-client-protocol`, and the check `hermes acp --check` in
[Hermes: ACP](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp/).

With the entry, every role can run on Hermes. You choose it for a role with `"harness": "hermes"`
under `orchestration.roles.<role>` in the user settings file,
`~/.config/agents-remember/settings.json`. `<role>` is the id of a role, for example `worker`. A
`role_start` call chooses it with `agent="hermes"`.

Without the entry, the host starts no agent on Hermes. The call of step 4, `role_start` with
`role="bootstrap"`, starts the Bootstrap role agent on the harness of the chat that makes the call.
Made in a Hermes chat without the entry, a first call still prepares the coordination root and
installs and starts the host. The call then starts no agent and is refused. The answer says "This
host cannot be given the caller's own harness", lists the harnesses the host can run, and ends
with "Name one with `agent` on this call, or install that harness's CLI and retry." Add the entry
and repeat the call, or name a harness from that list that is installed, for example:

```text
role_start(role="bootstrap", agent="claude", request_id="<a new UUID>")
```

## What this harness needs

Each entry says what you see without the setting, the setting with its file and key, and who makes
it. The entries were checked on Hermes v0.21.7 with the host this build installs, unless an
entry names another ground under "Checked".

The keys of Agents Remember stand in the user settings file. That file can be read-only; the
[settings reference](../reference/settings-json.md) shows the two commands that open and close it.

- **A role agent on Hermes asks before every file edit.** Agents Remember sends Hermes no
  [permission mode](../concepts.md#permission-mode) unless the user settings file states one.
  - Setting: `orchestration.roles.<role>.mode` in the user settings file, for example
    `"accept_edits"`. The host offers the permission modes that Hermes names for its sessions. A
    start with a mode that the host does not offer is refused with `mode_not_offered`, and the
    refusal names the modes that the host offers.
    `orchestration.roles.<role>.featureValues.auto_accept` set to `true` lets the host approve the
    agent's permission prompts.
  - Who: you state both keys. Agents Remember sends them with the start, so the first turn already
    runs with them. A repository's settings file that states either key is refused.
  - Checked: the keys and the refusal, in the code of Agents Remember; that the modes come from
    Hermes, in the source of the host this build installs. No agent was started for it.
- **Every inline script asks for approval.** This holds for `python3 -c`, for `node -e`, and for a
  script or a shell passed as a heredoc. The check is Hermes' own; the host's automatic approval,
  `featureValues.auto_accept` of the entry above, does not answer it.
  - Setting: `command_allowlist` in `~/.hermes/config.yaml`, with the three entries shown below.
    Hermes reads the list when its process starts, so the list holds for agents started after the
    change.
  - Who: you. Agents Remember does not change the files of Hermes.
  - Checked: the three entry names, in the source of Hermes v0.21.7. The effect of the third
    entry was not tested.

  ```yaml
  command_allowlist:
    - script execution via -e/-c flag
    - script execution via heredoc
    - shell execution via heredoc
  ```

- **An approval that nobody answers is denied after five minutes.** The agent goes on without the
  command.
  - Setting: `approvals.timeout` in `~/.hermes/config.yaml`, in seconds. The default is 300.
  - Who: you.
  - Checked: the key and its default, in the source of Hermes v0.21.7. No approval was left
    unanswered for it.
- **After such a denial, the host can still show the prompt on the idle agent.** Hermes stops
  waiting without telling the host, and the host keeps a prompt until you answer it or until a
  turn of the agent ends without completing. A message to that agent is held while the prompt is
  open.
  - Setting: none. Deny the prompt in the agent's chat. Agents Remember then delivers the held
    message.
  - Who: you.
  - Checked: in the source of Hermes v0.21.7 and of the host this build installs; the hold,
    in the code of Agents Remember. No approval was left unanswered for it.
- **A reason you give when you deny a permission prompt does not reach the agent.** Hermes tells
  the agent only that the client denied the edit.
  - Setting: none. Deny the prompt, then send the reason to the agent as a message.
  - Who: you.
  - Checked: the fixed text of the denial, in the source of Hermes v0.21.7. No denial was
    made for it.
- **A message to a Hermes agent in the middle of a turn reaches the agent only when the turn
  ends.** The host cannot hand a message into a running turn of a harness that it drives over ACP,
  so the answer of `role_message` says `held`, with the reason `recipient-in-turn`
  ([Holds and refusals](../reference/mcp-tools.md#holds-and-refusals)).
  - Setting: none. Agents Remember holds the message and delivers it when the turn ends. Nothing
    has to be sent again.
  - Who: Agents Remember.
  - Checked: the hold and the answer, in the code of Agents Remember. No message was sent for it.
- **A role's model, effort or speed setting is refused.** The refusals are `model_not_offered`,
  `effort_not_offered` and `service_tier_not_offered`. Hermes names a model as
  `<its provider>:<model>`; a ChatGPT sign-in, for example, is the provider `openai-codex`. Through
  the host, Hermes offers no effort and no speed choice per agent.
  - Setting: `orchestration.roles.<role>.model` in the user settings file, written as the role
    launcher of the [dashboard](../dashboard.md) lists the model. State no `effort` and no
    `serviceTier` for a role that runs on Hermes. Hermes takes its effort from
    `agent.reasoning_effort` in `~/.hermes/config.yaml`, not from a role.
  - Who: you.
  - Checked: the refusals, in the code of Agents Remember; the model names and the missing
    choices, in the list that the host gives for Hermes v0.21.7. No refused start was run.
- **An agent that waits for its [helpers](../concepts.md#helper), the agents that it started in
  the background, stays idle after they have finished.** Hermes does not wake an agent whose turn
  has ended.
  - Setting: none. In its first message, Agents Remember tells an agent on an ACP harness to end
    such a turn with a line that starts `WAITING FOR SUBAGENTS:` and a progress report. Three
    minutes after such a turn end, the [plug-in](../concepts.md#plug-in) that Agents Remember
    installs in the host sends the agent a message that asks for the helpers' progress.
  - Who: Agents Remember.
  - Checked: the instruction and the plug-in's question, in the code of Agents Remember. The
    behaviour of Hermes was not reproduced.
- **Temporary files appear under `~/.hermes/cache/scratch`.** That folder is Hermes' default.
  - Setting: none. A role agent's instructions name the scratch folder of its task.
  - Who: nobody sets anything. Hermes chooses that folder; Agents Remember only names the role
    agent's own scratch folder.
  - Checked: the default folder, in the source of Hermes v0.21.7.
- **A role agent also carries the tools of the tool server that you registered under the name
  `agents-remember`.** This holds when `~/.hermes/config.yaml` registers that tool server, as this
  page tells you to; the role agent then has the tools of Agents Remember twice, under
  `agents-remember-task` and under `agents-remember`. The whole result of `role_start`, in the
  file that `log` names, says `"secondCopyLoaded": true` for every start on Hermes, also when no
  such registration exists.
  - Setting: none. The host offers no setting that keeps one tool server out of one start on
    Hermes. A role agent's instructions name the tool server `agents-remember-task` for every tool
    call.
  - Who: Agents Remember.
  - Checked: in the code of Agents Remember. No role agent was started on Hermes for it.
- **In Hermes, `write_file` refuses to overwrite a file that the agent has not read from its first
  line to its last.** One read returns at most 2,000 lines and about 100,000 characters. The
  refusal names a "partial view".
  - Setting: none. The agent reads the remaining pages, or makes the change with `patch` of
    Hermes, as the refusal says.
  - Who: nobody sets anything.
  - Checked: the refusal and what counts as a partial read, in the source of Hermes v0.21.7.
    No file was written for it.

## Related pages

- [Install](README.md): the common steps and the other harnesses.
- [Getting started](../getting-started.md): the install steps in full.
- [The host](../host.md): the block `paseoRuntime` and what the host itself needs.
- [Roles and role agents](../roles.md): how role agents start and exchange messages.

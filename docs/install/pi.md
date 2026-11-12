# Install for Pi

This page adds to the common install steps what holds for Pi only: how you register the tool
server, which role agents the host starts on Pi, and which settings Pi needs.

## Install steps

These are the five install steps. [Getting started](../getting-started.md) tells them in full, and
the page [Install](README.md) names them for every harness.

1. **Install the package.** Install the published package `agents-remember-mcp` with uv.
2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`. It
   names the workspace root, the coordination root and your code repositories.
3. **Register the tool server in your harness.**
   [Register the tool server in Pi](#register-the-tool-server-in-pi) shows the file
   and the form of the declaration.
4. **Start the setup.** In a chat of Pi, ask the agent to make this call:
   `role_start(role="bootstrap", request_id="<a new UUID>")`.
   [Role agents on this harness](#role-agents-on-this-harness) says what the call does from
   Pi.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`.

## Register the tool server in Pi

Step 3 of the install steps is one command in Pi. Run it in a shell, outside a trusted project, a
folder whose own Pi settings, servers and packages Pi loads because you trusted it; the second
entry under [What this harness needs](#what-this-harness-needs) says why.

```bash
pi mcp add agents-remember -- agents-remember-mcp --config /home/<you>/.config/agents-remember/agents-remember-settings.json
```

`--config` takes an absolute path, so write your home folder in full.

The command writes the entry into the user-level file `~/.pi/agent/mcp.json`, which every Pi
session reads:

```json
{
  "mcpServers": {
    "agents-remember": {
      "command": "agents-remember-mcp",
      "args": ["--config", "/home/<you>/.config/agents-remember/agents-remember-settings.json"]
    }
  }
}
```

`pi mcp add` does not connect to the tool server; `pi mcp list` connects and prints the state of
each MCP server. In a Pi session that was open during the change, run `/reload`. Pi describes the
file and the commands in [Pi: MCP servers](https://pi.dev/docs/latest/mcp).

Pi's built-in MCP support reads this file. An installed Pi extension that registers `/mcp`, such as
`pi-mcp-adapter`, replaces the built-in support: Pi then does not read `mcp.json` in a session.
Removing the extension restores the built-in support. Pi 1.3.0 says so in the documentation that
it installs. To see which case you are in, look at `pi list`: it shows the configured packages,
among them such an extension when it was installed as a package.

The registration is the only change to Pi. The tool server serves its [skill](../concepts.md#skill)
as MCP resources and declares the MCP skills extension `io.modelcontextprotocol/skills`, through
which a harness can ask a tool server for the list of its skills and for each skill. Pi 1.3.0
does not contain that extension. For an MCP server with resources it adds the tools
`list_mcp_resources`, `list_mcp_resource_templates` and `read_mcp_resource`.

## Role agents on this harness

The host has a built-in [host provider](../concepts.md#host-provider) for Pi; its id is `pi`. By
the code of Agents Remember, every role can run on Pi. You choose it for a role with
`"harness": "pi"` under `orchestration.roles.<role>` in the user settings file,
`~/.config/agents-remember/settings.json`. `<role>` is the id of a role, for example `worker`. A
`role_start` call chooses it with `agent="pi"`. The host offers no
[permission mode](../concepts.md#permission-mode) for Pi, so Agents Remember sends none.

What this section says of role agents on Pi follows from the code of Agents Remember and of the
host; it was not tried with a role agent on Pi.

The call of step 4, `role_start` with `role="bootstrap"`, starts the Bootstrap role agent on the
harness of the chat that makes the call. Agents Remember compares the name that the calling
harness sends as MCP client with the ids and the labels of the host providers: it takes the name
for Pi when the name is `pi`, in any case, or holds `pi` as a separate word. When the name does
not match, the call is refused: the answer says "This host cannot be given the caller's own
harness" and lists the harnesses the host can run. A call that names the harness with `agent` does
not depend on that name:

```text
role_start(role="bootstrap", agent="pi", request_id="<a new UUID>")
```

## What this harness needs

Each entry says what you see without the setting, the setting with its file and key or its
command, and who makes it. The entries were checked on Pi 1.3.0, in the documentation that Pi
installs, with the host this build installs, unless an entry names another ground under
"Checked".

- **A Pi run without a terminal, in Pi's print, JSON or RPC mode, ignores the tool server named in
  a project's `.pi/mcp.json`.** It uses the MCP servers of your user-level file instead. Pi reads
  project files only in a trusted project, and in those modes it cannot show its prompt that asks
  for trust.
  - Setting: start that run with `pi --approve`, or save the decision once with `/trust` in an
    interactive session. `pi mcp add` writes the user-level file unless you add `--local`, which
    writes the project file.
  - Who: you. The registration on this page uses the user-level file, which needs no trust.
- **Any `pi` command run inside a trusted project installs that project's Pi packages into
  `<project>/.pi/npm`.** This holds for `pi --help` too. A project is trusted once you allow it at
  Pi's prompt or with `/trust`, or for one run with `--approve`. Pi applies a trust decision to the
  folders below the trusted one and installs the packages that a project's `.pi/settings.json`
  names.
  - Setting: run `pi` from a folder outside every trusted project, or with `--no-approve`.
  - Who: you, and every agent that calls `pi`. Agents Remember puts no `.pi` folder into a
    repository.
- **A role agent also carries the tools of the tool server that you registered under the name
  `agents-remember`.** This holds when the configuration of Pi that the agent reads registers that
  tool server, as this page tells you to; the role agent then has the tools of Agents Remember
  twice, under `agents-remember-task` and under `agents-remember`. The whole result of
  `role_start`, in the file that `log` names, says `"secondCopyLoaded": true` for every start on
  Pi, also when no such registration exists.
  - Setting: none. The host offers no setting that keeps one tool server out of one start on Pi. A
    role agent's instructions name the tool server `agents-remember-task` for every tool call.
  - Who: Agents Remember.
  - Checked: in the code of Agents Remember. No role agent was started on Pi for it.

## Related pages

- [Install](README.md): the common steps and the other harnesses.
- [Getting started](../getting-started.md): the install steps in full.
- [The host](../host.md): what the host itself needs.
- [Roles and role agents](../roles.md): how role agents start and exchange messages.

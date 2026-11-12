# Install for Cursor

This page adds to the common install steps what holds for Cursor only: how you register the tool
server, and that the host starts no role agent on Cursor. The call of step 4 from a Cursor chat
is therefore refused until it names another harness;
[Role agents on this harness](#role-agents-on-this-harness) tells how. The registration is told as
documented by the vendor and was not tried with Cursor.

## Install steps

These are the five install steps. [Getting started](../getting-started.md) tells them in full, and
the page [Install](README.md) names them for every harness.

1. **Install the package.** Install the published package `agents-remember-mcp` with uv.
2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`. It
   names the workspace root, the coordination root and your code repositories.
3. **Register the tool server in your harness.**
   [Register the tool server in Cursor](#register-the-tool-server-in-cursor) shows the file
   and the form of the declaration.
4. **Start the setup.** In a chat of Cursor, ask the agent to make this call:
   `role_start(role="bootstrap", request_id="<a new UUID>")`.
   [Role agents on this harness](#role-agents-on-this-harness) says what the call does from
   Cursor.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`.

## Register the tool server in Cursor

Step 3 of the install steps is one entry in the MCP configuration of Cursor. As documented by the
vendor, Cursor reads MCP servers from the key `mcpServers` of a file `mcp.json`. The file is
`~/.cursor/mcp.json` for all your projects, or `.cursor/mcp.json` in one project. See
[Cursor: MCP](https://cursor.com/docs/mcp.md).

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

`--config` takes an absolute path, so write your home folder in full instead of `~`.

The registration is the only change to Cursor.

## Role agents on this harness

The host starts no role agent on Cursor: it has no [host provider](../concepts.md#host-provider)
for Cursor. Use Cursor as the chat that calls the tools. The role agents run on a harness the host
can start. Once the host runs, `agents-remember paseo status --config <settings file>` lists those
harnesses under `providers`, each with `available`.

The call of step 4, `role_start` with `role="bootstrap"`, starts the Bootstrap role agent on the
harness of the chat that makes the call. Agents Remember compares the client name that the calling
harness sends in its MCP `initialize` request with the ids and the labels of the host providers.
The host has no host provider for Cursor, so the call from a Cursor chat finds none.

A first call still prepares the coordination root and installs and starts the host. The call then
starts no agent and is refused. The answer says "This host cannot be given the caller's own
harness", names after "It can run:" the harnesses the host knows, and ends with "Name one with
`agent` on this call, or install that harness's CLI and retry." The host starts a role agent only
on a harness whose program is installed on the machine, for example Claude Code; install one first
if none is. Repeat the call and name one of the listed harnesses that is installed, for example:

```text
role_start(role="bootstrap", agent="claude", request_id="<a new UUID>")
```

The refusal is read from the code of Agents Remember. The client name that Cursor sends was not
read.

## What this harness needs

This section has no entry. No role agent runs on Cursor, and the registration above is the only
change to Cursor.

## Related pages

- [Install](README.md): the common steps and the other harnesses.
- [Getting started](../getting-started.md): the install steps in full.
- [The host](../host.md): what the host itself needs.
- [Roles and role agents](../roles.md): how role agents start and exchange messages.

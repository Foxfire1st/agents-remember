# Install for OpenClaw

This page adds to the common install steps what holds for OpenClaw only: how you register the tool
server, and that the host starts no role agent on OpenClaw. Every statement about OpenClaw itself
follows its vendor's documentation and was not tried with an installed OpenClaw.

## Install steps

These are the five install steps. [Getting started](../getting-started.md) tells them in full, and
the page [Install](README.md) names them for every harness.

1. **Install the package.** Install the published package `agents-remember-mcp` with uv.
2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`. It
   names the workspace root, the coordination root and your code repositories.
3. **Register the tool server in your harness.**
   [Register the tool server in OpenClaw](#register-the-tool-server-in-openclaw) shows the file
   and the form of the declaration.
4. **Start the setup.** In a chat of OpenClaw, ask the agent to make this call:
   `role_start(role="bootstrap", request_id="<a new UUID>")`.
   [Role agents on this harness](#role-agents-on-this-harness) says what the call does from
   OpenClaw.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`.

## Register the tool server in OpenClaw

Step 3 of the install steps is one entry in the configuration of OpenClaw. As documented by the
vendor, OpenClaw keeps MCP servers under `mcp.servers` in `~/.openclaw/openclaw.json`. See
[OpenClaw: MCP](https://docs.openclaw.ai/cli/mcp).

```json
{
  "mcp": {
    "servers": {
      "agents-remember": {
        "command": "agents-remember-mcp",
        "args": ["--config", "/home/<you>/.config/agents-remember/agents-remember-settings.json"]
      }
    }
  }
}
```

`--config` takes an absolute path, so write your home folder in full instead of `~`.

The registration is the only change to OpenClaw.

## Role agents on this harness

The host starts no role agent on OpenClaw: it has no [host provider](../concepts.md#host-provider)
for OpenClaw. Use OpenClaw as the chat that calls the tools. The role agents run on a harness the
host can start. Once the host runs, `agents-remember paseo status --config <settings file>` lists
those harnesses under `providers`, with `available` and `error`: `available` says whether the host
finds the harness ready to start, and `error` holds the host's message when that check failed.

From a chat of OpenClaw, the call of step 4 is refused. That call, `role_start` with
`role="bootstrap"`, starts the Bootstrap role agent on the harness of the chat that makes the call.
Agents Remember compares the name that the calling harness sends as MCP client with the ids and
the labels of the host providers. There is no host provider for OpenClaw, so the call from an
OpenClaw chat finds none.

A first call still prepares the coordination root and installs and starts the host. The call then
starts no agent and is refused. The answer says "This host cannot be given the caller's own
harness", lists the harnesses the host can run, and ends with "Name one with `agent` on this call,
or install that harness's CLI and retry." Repeat the call and name one of the listed harnesses
that is installed, for example:

```text
role_start(role="bootstrap", agent="claude", request_id="<a new UUID>")
```

The refusal is read from the code of Agents Remember. The name that OpenClaw sends as MCP client
was not read.

## What this harness needs

Agents Remember needs no setting for OpenClaw beyond the registration above, because the host
starts no role agent on OpenClaw. One setting of OpenClaw itself is told here as its vendor
documents it.

- **A turn ends when its model stops streaming for longer than the idle limit of OpenClaw.**
  - Setting: `models.providers.<provider-id>.timeoutSeconds` in `~/.openclaw/openclaw.json`, in
    seconds, for one [model provider](../concepts.md#model-provider), the service whose models
    OpenClaw calls. `agents.defaults.timeoutSeconds` sets the limit of a whole agent run instead.
    `openclaw gateway restart` restarts the gateway, OpenClaw's WebSocket server that holds its
    sessions, and so applies a change that OpenClaw does not apply while it runs. The vendor
    documents the keys in the
    [configuration reference](https://docs.openclaw.ai/gateway/configuration-reference) and the
    command in [OpenClaw: gateway](https://docs.openclaw.ai/cli/gateway).
  - Who: you. Agents Remember does not write the files of OpenClaw.
  - Not verified: as documented by the vendor. This page holds no date and no version of OpenClaw
    for it.

## Related pages

- [Install](README.md): the common steps and the other harnesses.
- [Getting started](../getting-started.md): the install steps in full.
- [The host](../host.md): what the host itself needs.
- [Roles and role agents](../roles.md): how role agents start and exchange messages.

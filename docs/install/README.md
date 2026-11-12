# Install

Agents Remember is installed in the same five steps for every harness. This page names the steps
and links the page of each harness. [Getting started](../getting-started.md) tells the steps in
full.

## Platforms

The package declares Linux and macOS as its platforms, and it requires Python 3.14
(`>=3.14,<3.15`). Windows is supported through WSL, which counts as Linux.

The host, which runs the role agents, supports Linux x86_64 only. On another platform, step 4
refuses at its host stage, the part of the call that installs and starts the host, and no role
agent starts. [Platforms without a host](../host.md#platforms-without-a-host) tells what the call
does there.

## Install steps

1. **Install the package.** Install the published package `agents-remember-mcp` with uv.
   [Getting started](../getting-started.md#1-install-the-package) also tells how to build and
   install the package from a checkout of the repository.
2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`.
   It names the workspace root, the coordination root and your code repositories.
3. **Register the tool server in your harness.** The harness starts the command
   `agents-remember-mcp` with the arguments `--config` and the full path of the settings file.
   The page of your harness shows the file and the form of this declaration. Restart the harness.
4. **Start the setup.** In a chat of that harness, the agent makes one call:
   `role_start(role="bootstrap", request_id="<a new UUID>")`. The call creates the coordination
   root, installs and starts the host, starts one role agent in the role Bootstrap and starts the
   dashboard.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`, and continue the setup in the chat of the Bootstrap role agent.

The tool server serves the [skill](../concepts.md#skill) `l-01-agent-lifecycles`, the
instructions of the roles, to the harness as MCP resources: files that a harness can list and read
through the tool server. The setup needs no read of the skill; a role agent receives its part of
the instructions with its first message.

## Choose your harness

Each page shows how the tool server is registered in that harness, which role agents the host can
start with it, and what the harness needs.

- [Install for Claude Code](claude-code.md)
- [Install for Codex](codex.md)
- [Install for Cursor](cursor.md)
- [Install for Hermes](hermes.md)
- [Install for Pi](pi.md)
- [Install for OpenClaw](openclaw.md)

The page of a harness names its harness id, the value that the argument `agent` of `role_start`
and the key `harness` of the role settings take: `claude` for Claude Code, `codex` for Codex,
`hermes` for Hermes with its provider entry, and `pi` for Pi. The host starts no role agent on
Cursor or OpenClaw. A refused call of step 4 can list more ids: it names the harnesses the host
knows, and `agents-remember paseo status --config <settings file>` shows under `providers` which of
them are available.

[Harnesses and role settings](../reference/harnesses.md) lists what every harness needs in one
place and describes the settings of a role. [The host](../host.md) describes the host itself.

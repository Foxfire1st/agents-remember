# Getting started

This page takes you from the install of the package to a running dashboard, a running host and
one repository that is set up for work. It is the one page that tells the install steps in full.
Every other page names the same five steps and links here.

The terms this page uses, such as tool server, host, role agent and memory repository, are defined
in [Concepts](concepts.md).

## What you need

- **A Linux x86_64 machine.** WSL 2 on Windows counts as Linux. The package declares Linux and
  macOS as its platforms. The host, which runs the role agents, supports Linux x86_64 only: on
  another platform step 4 installs no host and refuses, and no role agent starts.
  [Platforms without a host](host.md#platforms-without-a-host) tells what the call does there.
- **Python 3.14.** The package supports `>=3.14,<3.15`.
- **[uv](https://docs.astral.sh/uv/).** It installs the package.
- **Git.** Memory repositories and worktrees are Git repositories. Set `user.name` and
  `user.email`. Without them Agents Remember commits as `Agents Remember` with the address
  `agents-remember@example.invalid`.
- **Node.js with npm, only for a build from a checkout.** The published package contains the
  dashboard. The release job of the repository builds the dashboard with Node.js 22. Node.js
  22.13 or a later 22 release, and Node.js 24 or later, meet every version range that the
  packages in `dashboard/package-lock.json` declare. The host runs on a Node of its own, which
  Agents Remember downloads in step 4.
- **One harness, installed and logged in.** The tool server is registered in it, and the host
  starts role agents with it. Agents Remember asks for no minimum version of a harness. The page
  of each harness, which the page [Install](install/README.md) lists, says whether the host can
  start role agents with that harness and what the harness needs.
- **Network access in steps 1 and 4.** Step 1 downloads the package. In step 4 Agents Remember
  downloads Node from `nodejs.org` and installs the host from the npm registry.
- **Two free ports on the loopback address.** The dashboard listens on `127.0.0.1:8765` and the
  host on `127.0.0.1:8766`. For another port of the dashboard, add `"dashboard": {"port": 8770}`
  with your port to the settings file of step 2. [The Host](host.md#ports) says how to choose
  another port for the host.

Docker is not needed.

## Install steps

### 1. Install the package

Install the published package `agents-remember-mcp` with one command:

```bash
uv tool install --python 3.14 agents-remember-mcp
```

The package installs two commands: `agents-remember-mcp`, the tool server, and
`agents-remember`, which starts and stops the dashboard and the host. The package contains the
dashboard. If the folder that holds the two commands is not on your `PATH`, uv warns and names
the folder. Then run `uv tool update-shell` and open a new terminal.

To check the install, ask the tool server for its help text. It names the one option `--config`:

```bash
agents-remember-mcp --help
```

**From a checkout of the repository.** To install a build of your own instead, build the
dashboard, build a wheel that contains it, and install the wheel:

```bash
git clone https://github.com/Foxfire1st/agents-remember.git
cd agents-remember
npm --prefix dashboard ci
npm --prefix dashboard run build
python3 scripts/sync-dashboard.py
uv build --wheel mcp
uv tool install --python 3.14 mcp/dist/agents_remember_mcp-*.whl
```

The two `npm` commands build the dashboard. The script copies the built dashboard into the
package. A wheel that is built without these three commands contains no dashboard, and the
dashboard address then answers with an error that names the build commands. The pattern in the
last command matches the one wheel that the build wrote.

### 2. Write the settings file

The tool server reads one settings file. Create its folder:

```bash
mkdir -p ~/.config/agents-remember
```

Create the file `~/.config/agents-remember/agents-remember-settings.json` with this content and
your own paths:

```json
{
  "coordinationRoot": "/home/<you>/projects/ar-coordination",
  "workspaceRoot": "/home/<you>/projects",
  "repositories": { "my-repo": {} }
}
```

- `workspaceRoot` is the folder that holds your code repositories.
- `repositories` lists the code repositories Agents Remember may work on. Each key is the name of
  a folder directly under the workspace root, here `/home/<you>/projects/my-repo`.
- `coordinationRoot` is the folder in which Agents Remember keeps tasks, memory repositories,
  worktrees, notes and logs. Agents Remember creates the folder. It may lie inside the workspace
  root.

Both roots are absolute paths. The settings file itself lies outside the coordination root. The
file is plain JSON and takes no comments. When `XDG_CONFIG_HOME` is set, the folder is
`$XDG_CONFIG_HOME/agents-remember/` instead.

The tool server reads the roots and the repositories when it starts. After you change one of
them, restart the harness. The [Settings Reference](reference/settings-json.md) describes the
other sections that the file can hold, among them `dashboard`, `toolLog` and `paseoRuntime`.

### 3. Register the tool server in your harness

The harness starts the tool server. Give it this command and these two arguments:

```text
command:    agents-remember-mcp
arguments:  --config  /home/<you>/.config/agents-remember/agents-remember-settings.json
```

Write the path of the settings file in full. A harness does not expand `~` inside a list of
arguments. If the harness does not find the command, give the command's full path;
`uv tool dir --bin` prints its folder.

Each harness keeps this declaration in a file and a form of its own. The page of your harness
shows both: [Install for Claude Code](install/claude-code.md),
[Install for Codex](install/codex.md), [Install for Cursor](install/cursor.md),
[Install for Hermes](install/hermes.md), [Install for Pi](install/pi.md) and
[Install for OpenClaw](install/openclaw.md).

Restart the harness so that it starts the tool server. To check the registration, ask the agent
in a chat of the harness to call the tool `server_info`. The answer names the version, the
workspace root, the coordination root and the repositories. While the folder of the settings
file is open to writing, the answer also holds `openUserSettings` with a `closeCommand` and an
`editCommand`. Leave the folder open during the install: Bootstrap creates the user settings
file in it.
[Closing the user's file to writing](reference/settings-json.md#closing-the-users-file-to-writing)
tells what the two commands do.

The tool server serves the [skill](concepts.md#skill) `l-01-agent-lifecycles` to the harness as
MCP resources.

### 4. Start the setup

In a chat of that harness, ask the agent to make this call with a UUID that it generates. It is
the [Bootstrap call](concepts.md#bootstrap-call):

```text
role_start(role="bootstrap", request_id="<a new UUID>")
```

The call needs the network and can take several minutes. It does this, in this order:

- It creates the coordination root with its folders, unless they exist.
- It writes the block `paseoRuntime` into the settings file, unless the file has one. The block
  names the folder of the host's install, by default
  `~/.local/share/agents-remember/paseo/prefix`, the host's home, by default
  `~/.local/state/agents-remember/paseo`, and the address `127.0.0.1:8766`.
- It downloads Node, installs the host and starts it. The host is Paseo.
- It starts one role agent in the role Bootstrap, on the harness of the chat that made the call.
- It starts the dashboard, unless it runs, and reads its address.

The answer carries `ok: true`, the `agentId` of the role agent, the `dashboardAddress`, the
`chatAddress` of the role agent's chat and a `nextAction`. The call starts no task and no other
role.

The first four entries of the list are the stages of the call. When a stage fails, the answer
carries `ok: false`, `status: "refused"`, `refusal: "launch-refused"`, a `preparationStatus` and a
`nextAction`. The reason is the `detail`. It names the stage that failed, the stages that are done
and the state of the host. It calls the first entry of the list the stage `scaffold`, the second
`host-authority`, the third `host` or `host-verify`, and the fourth the role launch. A `detail`
that is too long for the answer is left out of it: the answer then holds `overflow: true` and
`log`, and the `detail` stands in the file that `log` names.
[Tool answers](reference/mcp-tools.md#tool-answers) describes this short form. The call undoes
nothing. Resolve the reason that the `detail` names, then make the call again with the same
`request_id`: stages that are done are not repeated.

The role launch also fails when the host cannot match the harness of your chat with a harness
that it knows. The `detail` then lists the [harness ids](concepts.md#harness) that the host
knows, for example `claude` for Claude Code. The list does not say which of these harnesses are
installed: `agents-remember paseo status --config <settings file>` shows under `providers` which
of them are available. Make the call again and name an available harness in the argument `agent`.
The page of your harness names its id.

### 5. Open the dashboard

Open the address that `dashboardAddress` names in a browser on the same machine. By default it is
`http://127.0.0.1:8765/`. The dashboard opens on the tab "Chats". Its
[chat frame](concepts.md#chat-frame) shows the chats of the host, among them the chat of the
Bootstrap role agent. The row above the chat frame is the
[role launcher](concepts.md#role-launcher); the setup does not use it.

On the first visit of a browser, the chat frame can show the host's page "Welcome to Paseo"
instead of the chats. After about 20 seconds the dashboard then shows the notice "embedded chat
control unavailable" with a button "Retry". Press "Retry": the dashboard loads the chat frame
again, and the frame then shows the chats. When the chats show at once, nothing is wrong and
there is nothing to press.

To check the dashboard and the host from a terminal:

```bash
agents-remember dashboard --status
```

Without `--config` the command first names the settings file it found. It then prints the state
of the dashboard in a line that begins `dashboard daemon`, and the state of the host in the last
line. For a running dashboard it prints, between these two, one line on the dashboard's build and
lines on the dashboard's background work; the check needs neither. The command exits with 0 when
both run, with 1 when the dashboard is down and with 3 when the host is down.

[The Host](host.md) describes how to start, stop and update the host and the dashboard.
[The Dashboard](dashboard.md) describes every tab.

## Set up a repository with Bootstrap

You continue in the chat of the Bootstrap role agent. Bootstrap first explains what the setup
changes. It then asks you three things:

- which branch of the code repository the memory is built on;
- whether the repository gets a new memory repository or uses an existing one;
- which harness each role uses.

With your answers Bootstrap does this:

- **It writes the user settings file.** The file is `~/.config/agents-remember/settings.json`. It
  records the harness of each role and nothing else, so each role runs with the defaults of its
  harness. Bootstrap shows a preview before it writes. A file that exists stays as it is, and
  Bootstrap reports what it holds. [Harnesses and role settings](reference/harnesses.md)
  describes what you can add to the file.
- **It creates the memory repository.** The memory repository lies under the coordination root,
  at `<coordination-root>/memory-repos/ar-my-repo`. It is a Git repository of its own. Its first
  branch has the same name as the code branch you chose.
- **It adopts the first baseline.** The baseline is the first commit of the memory repository's
  content. It records which commit of the code that content describes. Bootstrap shows a preview,
  and you decide whether a difference between the memory and the code is accepted.
- **It reports the result.** The report names the state of the memory repository and the state of
  the repository's knowledge.

Setup authors no onboarding or knowledge and runs no citation fixer. Bootstrap writes no card and
no knowledge record, creates no task and starts no other role. The citation fixer is the tool
`citation_fix`, which re-records anchors that have only moved in the code. For a repository that
already has a memory repository, read [Adopt Existing Memory](guides/adopt-existing-memory.md)
first.

## Start your first task

Work on a repository starts with the Architect. The Architect talks with you, writes the
requirement and the plan, and starts the role agents that build, review and record the change.

1. Open the dashboard on the tab "Chats".
2. In the [role launcher](dashboard.md#the-role-launcher), the first row of the tab, leave the
   role on "Architect" and the other selectors as they are, and press "Start". The role agent
   then starts on the harness that the user settings file names for the role. The selector of
   the harness lists every harness that the host knows, also harnesses that are not installed.
3. The chat of the new role agent opens below the launcher. Tell the Architect which outcome you
   want and which repository it concerns.

Advice: after a first setup, make the repository's knowledge your first request, for example
"Write the first cards and knowledge records of my-repo." Bootstrap's report says whether any is
recorded, and the Architect plans the cards and the knowledge records as a regular task.

[Roles and role agents](roles.md) describes each role and who starts whom.
[Workflows](workflows.md) describes how a request becomes a task and how a task lands.

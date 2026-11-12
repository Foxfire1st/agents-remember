<h1 align="center">Agents Remember MCP</h1>

`agents-remember-mcp` is the package of Agents Remember. It installs two commands: the tool server
`agents-remember-mcp`, which a coding harness starts, and `agents-remember`, which starts and stops
the dashboard and the host.

Agents Remember gives a code repository a memory in a Git repository of its own: a note at the
path of each file, records that tie each rule to all the code that expresses it, and role agents
that let a change land only when code and memory agree. The
[project README](https://github.com/Foxfire1st/agents-remember/blob/main/README.md) says what the
product does and where it is heading.

The documentation is in the repository:
[Agents Remember Documentation](https://github.com/Foxfire1st/agents-remember/blob/main/docs/README.md),
[Getting started](https://github.com/Foxfire1st/agents-remember/blob/main/docs/getting-started.md)
and [Install](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/README.md).
The site https://foxfire1st.github.io/agents-remember/ shows the folder `docs/` of the branch
`main`.

## What you need

- A Linux x86_64 machine. WSL 2 on Windows counts as Linux.
- Python 3.14 (`>=3.14,<3.15`), [uv](https://docs.astral.sh/uv/) and Git.
- One harness, installed and logged in: Claude Code, Codex, Cursor, Hermes, Pi or OpenClaw. The
  host starts no role agent on Cursor or OpenClaw.
- Network access during the setup, and the free ports 8765 and 8766 on the loopback address.

Docker is not needed.

## Install steps

[Getting started](https://github.com/Foxfire1st/agents-remember/blob/main/docs/getting-started.md)
tells these five steps in full.

1. **Install the package.** Install the published package with one command:

   ```bash
   uv tool install --python 3.14 agents-remember-mcp
   ```

   [Getting started](https://github.com/Foxfire1st/agents-remember/blob/main/docs/getting-started.md#1-install-the-package)
   also tells how to build and install the package from a checkout of the repository.

2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`.
   It names the workspace root, the coordination root and your code repositories:

   ```json
   {
     "coordinationRoot": "/home/<you>/projects/ar-coordination",
     "workspaceRoot": "/home/<you>/projects",
     "repositories": { "my-repo": {} }
   }
   ```

   The [Settings Reference](https://github.com/Foxfire1st/agents-remember/blob/main/docs/reference/settings-json.md)
   describes the other sections that the file can hold, among them `dashboard`, `toolLog` and
   `paseoRuntime`.
3. **Register the tool server in your harness.** The harness starts the command
   `agents-remember-mcp` with the arguments `--config` and the full path of the settings file.
   Restart the harness. Each harness has a page that shows its file and its form:
   [Install for Claude Code](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/claude-code.md),
   [Install for Codex](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/codex.md),
   [Install for Cursor](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/cursor.md),
   [Install for Hermes](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/hermes.md),
   [Install for Pi](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/pi.md) and
   [Install for OpenClaw](https://github.com/Foxfire1st/agents-remember/blob/main/docs/install/openclaw.md).
4. **Start the setup.** In a chat of that harness, ask the agent to make one call:

   ```text
   role_start(role="bootstrap", request_id="<a new UUID>")
   ```

   The call creates the coordination root, installs and starts the host, starts one role agent
   in the role Bootstrap and starts the dashboard.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`, and continue the setup in the chat of the Bootstrap role agent.

## Start and stop the dashboard and the host

The setup starts both. Afterwards these commands start, check and stop them:

```bash
agents-remember dashboard --daemon    # start the dashboard and the installed host
agents-remember dashboard --status    # state of both; exit 0 when both run
agents-remember dashboard --stop      # stop the dashboard; the host keeps running
agents-remember paseo status --config ~/.config/agents-remember/agents-remember-settings.json
agents-remember paseo stop --config ~/.config/agents-remember/agents-remember-settings.json
```

[The Host](https://github.com/Foxfire1st/agents-remember/blob/main/docs/host.md) describes the
commands, the ports and an update of the install.
[The Dashboard](https://github.com/Foxfire1st/agents-remember/blob/main/docs/dashboard.md)
describes every tab.

## Tools

The tool server registers these groups of tools:

| Group | Tools |
| --- | --- |
| Server and context | `server_info`, `context_packet`, `read_ar_files`, `runtime_install` |
| Memory | `memory_init`, `memory_baseline_status`, `memory_baseline_adopt`, `memory_carryover_plan`, `memory_carryover_apply`, `memory_quality_check`, `route_index_refresh`, `citation_fix`, `citation_note_revision` |
| Knowledge | `knowledge_read`, `knowledge_write`, `knowledge_diff`, `knowledge_integrity_check` |
| Worktrees | `worktree_start`, `worktree_enclosure_adopt`, `worktree_attach`, `worktree_status`, `worktree_sync`, `worktree_pause` |
| Closeout and landing | `worktree_closeout_preview`, `worktree_closeout_apply`, `worktree_integrate`, `worktree_checkpoint_landing`, `worktree_record_landing`, `worktree_operation_control`, `worktree_cleanup`, `worktree_abandon`, `direct_execution_grant`, `direct_landing` |
| Task documents | `task_doc`, `task_reopen`, `lifecycle_finalize_task`, `closeout_queue`, `curator_coherence` |
| Role agents | `role_start`, `role_start_set`, `role_message`, `retire_child`, `user_settings_init` |
| Review page | `explainer_control` |

The [MCP Tool Reference](https://github.com/Foxfire1st/agents-remember/blob/main/docs/reference/mcp-tools.md)
describes each tool, in sections that carry the names of these groups.
The group names use terms that the page Concepts defines:
[closeout](https://github.com/Foxfire1st/agents-remember/blob/main/docs/concepts.md#closeout),
[landing](https://github.com/Foxfire1st/agents-remember/blob/main/docs/concepts.md#landing),
[task document](https://github.com/Foxfire1st/agents-remember/blob/main/docs/concepts.md#task-document) and
[review page](https://github.com/Foxfire1st/agents-remember/blob/main/docs/concepts.md#review-page).

The tool server of an explainer, the agent that answers your requests on the review page,
registers four more tools: `render`, `define_category`, `remove_category` and `grade`. The tool
server that you register in a harness does not register them.

The tool server also serves the
[skill](https://github.com/Foxfire1st/agents-remember/blob/main/docs/concepts.md#skill)
`l-01-agent-lifecycles`, the instructions of the roles, as MCP resources.

## More

- [Project README](https://github.com/Foxfire1st/agents-remember/blob/main/README.md), with the
  status of this version
- [Getting started](https://github.com/Foxfire1st/agents-remember/blob/main/docs/getting-started.md)
- [Settings Reference](https://github.com/Foxfire1st/agents-remember/blob/main/docs/reference/settings-json.md)
- [Contributing to Agents Remember](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md)

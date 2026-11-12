# Agents Remember Documentation

Agents Remember gives a code repository a memory in a Git repository of its own, and role agents
that let a change land only when code and memory agree. These pages describe how you install the
product, how you work with it and how its parts behave.

This page links every page of the documentation. [Concepts](concepts.md) defines the terms that
the pages use. The
[front page of the repository](https://github.com/Foxfire1st/agents-remember/blob/main/README.md)
says what the product is for, and the
[package description](https://github.com/Foxfire1st/agents-remember/blob/main/mcp/README.md)
lists the groups of tools.

## Start here

- [Getting started](getting-started.md) tells the install steps in full, the setup of a
  repository and the start of a first task.
- [Install](install/README.md) names the install steps and links the page of each harness.
- [Concepts](concepts.md) defines every term: tool server, host, harness, role agent, memory
  repository, card, knowledge record, task.
- [Features](features.md) says what the product does, part by part.

## Install steps

[Getting started](getting-started.md) tells these five steps in full.

1. **Install the package.** Install the published package `agents-remember-mcp` with uv.
2. **Write the settings file.** Create `~/.config/agents-remember/agents-remember-settings.json`.
   It names the workspace root, the coordination root and your code repositories.
3. **Register the tool server in your harness.** The harness starts the command
   `agents-remember-mcp` with the arguments `--config` and the full path of the settings file.
   The page of your harness shows the file and the form of this declaration.
4. **Start the setup.** In a chat of that harness, the agent makes one call:
   `role_start(role="bootstrap", request_id="<a new UUID>")`.
5. **Open the dashboard.** Open the address that the answer names, by default
   `http://127.0.0.1:8765/`.

The page of each harness:

- [Install for Claude Code](install/claude-code.md)
- [Install for Codex](install/codex.md)
- [Install for Cursor](install/cursor.md)
- [Install for Hermes](install/hermes.md)
- [Install for Pi](install/pi.md)
- [Install for OpenClaw](install/openclaw.md)

## Work with the product

- [The Host](host.md) describes the program that runs the role agents: what installs and starts
  it, how you see its state, how you stop and start it, its ports and an update of the install.
- [The Dashboard](dashboard.md) describes every tab, the role launcher, the task list, the task
  document, the review page and the tray.
- [Roles and role agents](roles.md) describes the eight roles, who starts whom and who
  coordinates the work.
- [Workflows](workflows.md) describes how a request becomes a task and how a task lands.
- [Harnesses and role settings](reference/harnesses.md) describes how you choose the harness, the
  model, the effort and the permission mode of a role.
- [Architecture](architecture.md) describes the parts of the product and the folders they use.
- [FAQ](FAQ.md) answers common questions.

## Guides

[Guides](guides/README.md) is the index of this folder. Each guide tells how to do one thing.

- [Onboard An Existing Repository](guides/onboard-existing-repo.md)
- [Use The Memory Repository](guides/use-external-memory.md)
- [Adopt Existing Memory](guides/adopt-existing-memory.md)
- [Carry Memory From a Branch](guides/carryover-from-branch.md)
- [Refresh Stale Cards](guides/refresh-stale-onboarding.md)
- [The Cost Of A Repository's First Memory](guides/cost-aware-bootstrap.md)
- [The review page and the explainer](guides/review-page-and-explainer.md)

## Reference

[Reference](reference/README.md) is the index of this folder.

- [MCP Tool Reference](reference/mcp-tools.md) describes each tool of the tool server.
- [Tool details](tool-details.md) holds the longer explanation of each tool whose short
  description points to it.
- [Settings Reference](reference/settings-json.md) describes the four settings files.
- [Harnesses and role settings](reference/harnesses.md)
- [Path Rules](reference/path-rules.md)
- [Runtime Layout](reference/runtime-layout.md) describes the folders and files that the product
  creates.
- [Knowledge System](reference/knowledge-system.md)
- [Memory Quality Control](reference/drift-c02.md) describes the checks that show whether the
  cards can be trusted and whether the memory of a task is clean enough to commit.
- [Worktrees And Closeout](reference/worktrees-c09.md)
- [Product Context Resolution](reference/resolver-c08.md)
- [Role capsules, handovers and messages](reference/role-capsules.md)
- [Skills Reference](reference/skills.md)
- [Repository Certification Profiles](reference/repository-certification-profile.md)
- [Execution-topology authoring](reference/execution-topology.md) describes the fields
  `executionNature` and `executionGraph` of the task documents, which say how the masters of a
  sprint run and which of them wait for others.

Beside these pages, the file [llms.txt](llms.txt) in the folder of this page is a summary of this
documentation for language models.

## For contributors

[Contributing to Agents Remember](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md)
is the page for work on the product itself. These pages add to it:

- [Release Checklist](release-checklist.md)
- [Benchmark Methodology](benchmarks-methodology.md)
- [Python test policy and commands](design/python-pytest-bootstrap.md) states how the Python
  tests of the product's repository are admitted, selected and run.
- [Python Test Evidence System](design/python-evidence-system.md) states how that repository
  sorts the results of its Python tests by what a result can prove.
- [Python Test Evidence Authority](design/python-test-evidence.md) states which test result
  counts as acceptance.
- [The canvas host, and the review-presenter capability](design/canvas-host.md) states the
  contracts of the [canvas host](concepts.md#canvas-host), which serves the page of an explainer,
  and of the capability by whose text the [explainer](concepts.md#explainer) writes the page.
- [The pinned offline presentation kit](design/canvas-kit.md) describes the
  [presentation kit](concepts.md#presentation-kit), the libraries that the product ships for the
  pages of the explainer.

# Install For Antigravity

Antigravity is Google's agentic development platform. It supports workspace
context files, workspace skills, workspace MCP configuration, plugins, and
execution hooks.

Official references:

- [Antigravity CLI Plugins & Skills](https://antigravity.google/docs/cli-plugins)
- [Antigravity CLI Migration](https://antigravity.google/docs/gcli-migration)
- [Antigravity IDE MCP](https://antigravity.google/docs/ide-mcp)
- [Antigravity IDE Skills](https://antigravity.google/docs/ide-skills)
- [Antigravity IDE Rules](https://antigravity.google/docs/ide-rules)
- [Antigravity IDE Hooks](https://antigravity.google/docs/ide-hooks)

## Root Starter Package

The repository includes an Antigravity starter package at `.agents/`. Copy that
folder to your workspace root, copy `.agents/GEMINI.md` to the workspace root as
`GEMINI.md`, replace every placeholder, including
`<PATH/TO/YOUR/PROJECTS_FOLDER>` and `<YOUR_REPOSITORY_FOLDER_NAME>`, then start
or reload Antigravity from that workspace once.

The source checkout keeps the template under `.agents/` so the repository root
remains reserved for source-project files such as its own `AGENTS.md`.

The package contains:

- `.agents/GEMINI.md` - template for the installed Antigravity/Gemini workspace
  context file with the mandatory first-action directive.
- `.agents/mcp_config.json` - workspace-local Antigravity MCP server
  registration.
- `.agents/mcp/agents-remember-settings.json` - Agents Remember MCP authority
  settings.
- `.agents/skills/` - Agents Remember skills in Antigravity's workspace skill
  root.

After the restart or reload, invoke:

```text
c-13-install-and-onboard
```

That skill runs or verifies `runtime_install()` and then handles memory,
onboarding, and providers.

## Workspace Instructions Or Start Hook

Antigravity reads workspace `GEMINI.md` and `AGENTS.md` files, and also reads
global `~/.gemini/GEMINI.md`. The starter package ships `.agents/GEMINI.md` as a
template so it does not overwrite this source checkout's root `AGENTS.md`.

If you set it up by hand, place this directive in the Antigravity `GEMINI.md`
file:

```markdown
# Workspace Agent Instructions

Read and follow `<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md
```

Add both the coordination runtime and target repository to the workspace when possible. If not, point the include at an absolute readable path.

Do not overwrite a repository's root `AGENTS.md` just to wire Agents Remember;
root `AGENTS.md` is project-specific.

Antigravity documents command hooks in `.agents/hooks.json` and supports a
`PreInvocation` hook that can inject transient context before a model call. It
does not currently document a simple project-local `session_start` hook. Keep
the root starter package on the documented always-loaded context file unless
you intentionally add a hook script for your own environment.

## Runtime And Skills

After the restart or reload, the copied `c-13-install-and-onboard` skill runs or
verifies:

```text
runtime_install()
```

Antigravity discovers workspace skills in
`.agents/skills/<skill-name>/SKILL.md`. Global skills live under
`~/.gemini/antigravity/skills/<skill-name>/` for the IDE and under
`~/.gemini/antigravity-cli/skills/` for the CLI.

The copied starter package already provides one flat folder per skill under
`.agents/skills/`.

Do not run `skills_install()` for first-run setup. It remains available for
manual maintenance and non-package installs.

Antigravity can invoke skills automatically or on request once they are discovered.

## MCP

Antigravity CLI reads workspace MCP servers from `.agents/mcp_config.json`.
Antigravity IDE also exposes a raw MCP config from its MCP settings UI; current
IDE docs show that global file at `~/.gemini/config/mcp_config.json`.

The starter package uses the workspace-local CLI-compatible file:

```json
{
  "mcpServers": {
    "agents-remember": {
      "command": "uvx",
      "args": [
        "--refresh-package",
        "agents-remember-mcp",
        "agents-remember-mcp@latest",
        "--config",
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.agents/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

Open `/mcp` in Antigravity CLI, or the IDE MCP Store's raw config view, to
confirm the server is visible after setup.

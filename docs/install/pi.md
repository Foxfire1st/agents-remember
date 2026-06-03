# Install For Pi.dev

Pi supports context files, project settings, project-local extensions, Agent
Skills, and package-managed extensions. Pi core intentionally does not include
built-in MCP; the starter package uses `pi-mcp-adapter`.

Official references:

- [Pi Using Pi](https://pi.dev/docs/latest/usage)
- [Pi Settings](https://pi.dev/docs/latest/settings)
- [Pi Extensions](https://pi.dev/docs/latest/extensions)
- [Pi Skills](https://pi.dev/docs/latest/skills)
- [Pi Packages](https://pi.dev/docs/latest/packages)
- [pi-mcp-adapter](https://pi.dev/packages/pi-mcp-adapter)

## Root Starter Package

The repository includes a Pi starter package at `.pi/`. Copy that folder to
your workspace root, replace every placeholder, including
`<PATH/TO/YOUR/PROJECTS_FOLDER>` and `<YOUR_REPOSITORY_FOLDER_NAME>`, then start
or reload Pi from the workspace once.

The package contains:

- `.pi/settings.json` - project settings that load the local extension, local
  skills, and `npm:pi-mcp-adapter`.
- `.pi/extensions/agents-remember-start.ts` - project-local
  `before_agent_start` extension that injects the mandatory first-action
  directive.
- `.pi/mcp/agents-remember-settings.json` - Agents Remember MCP authority
  settings.
- `.pi/mcp.json` - Pi project override consumed by `pi-mcp-adapter`.
- `.pi/skills/` - Agents Remember skills in Pi's project skill root.

After the restart or reload, invoke:

```text
c-13-install-and-onboard
```

That skill runs or verifies `runtime_install()` and then handles memory,
onboarding, and providers.

## Workspace Instructions

The starter package uses a project-local `.pi/extensions/agents-remember-start.ts`
extension. Pi runs `before_agent_start` before an agent turn and allows the
extension to inject the mandatory first-action directive into the system prompt.

Do not overwrite a repository's root `AGENTS.md` just to wire Agents Remember;
root `AGENTS.md` is project-specific. Pi also supports global context under
`~/.pi/agent/AGENTS.md`, but the starter package does not need it for first-run
setup.

If you edit the extension while Pi is already running, use `/reload`.

## Runtime And Skills

After the restart or reload, the copied `c-13-install-and-onboard` skill runs or
verifies:

```text
runtime_install()
```

Pi loads skills from project `.pi/skills`, project `.agents/skills`, global
`~/.pi/agent/skills`, global `~/.agents/skills`, settings entries, package
entries, and repeated `--skill <path>` flags. The starter package keeps skills
in `.pi/skills` and also lists `skills` in `.pi/settings.json` to make the
project package self-contained.

Pi can discover `SKILL.md` directories recursively, but skill names should still match their parent folder for clean validation.

Do not run `skills_install()` for first-run setup. It remains available for
manual maintenance and non-package installs.

## MCP

Pi core has no built-in MCP client. The starter package loads
`npm:pi-mcp-adapter` from `.pi/settings.json`; Pi installs missing project
packages automatically on startup.

`pi-mcp-adapter` can read shared project `.mcp.json`, but this starter keeps the
Pi package self-contained with the documented Pi-owned project override
`.pi/mcp.json`:

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
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.pi/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

Use `/mcp` to inspect adapter status or `/mcp setup` when you want Pi to adopt
existing host-specific MCP configs.

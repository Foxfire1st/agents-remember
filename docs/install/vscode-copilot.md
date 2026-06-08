# Install For VS Code + GitHub Copilot

VS Code supports Agent Skills, always-on repository instructions, workspace MCP
configuration, and agent hooks for GitHub Copilot Chat.

Official references:

- [Use Agent Skills in VS Code](https://code.visualstudio.com/docs/copilot/customization/agent-skills)
- [Use custom instructions in VS Code](https://code.visualstudio.com/docs/copilot/customization/custom-instructions)
- [Agent hooks in VS Code](https://code.visualstudio.com/docs/copilot/customization/hooks)
- [Add and manage MCP servers in VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
- [Copilot settings reference](https://code.visualstudio.com/docs/copilot/reference/copilot-settings)

## Root Starter Package

The repository includes a VS Code + GitHub Copilot starter package at
`.github-vscode/` and `.vscode/`. Copy `.vscode/` to your workspace root, then
copy the contents of `.github-vscode/` into your workspace `.github/` folder.
Then render the copied package. The `.github/render-starter` script is a
convenience: with a single `--repo` list such as `--repo my-app shared-lib`, it
infers the workspace root from the copied `.github/` folder, fills path and
repository placeholders, writes the OS-specific Python hook command, and
validates that each requested repository exists before you open or restart the
workspace in VS Code once. If you prefer not to run the renderer, make those
same replacements by hand and verify that no placeholder tokens remain.

The starter package uses `.github-vscode/` only to avoid mixing harness files
with this source checkout's real `.github/workflows/`. VS Code and Copilot still
expect the installed files under `.github/`.

The package contains:

- `.github-vscode/copilot-instructions.md` - template for the installed
  `.github/copilot-instructions.md` always-on repository instruction.
- `.github-vscode/hooks/agents-remember-session-start.json` and sibling hook
  files - templates for installed `.github/hooks/` `SessionStart` startup
  context injection.
- `.github-vscode/skills/` - templates for installed `.github/skills/`, which
  is Copilot's default project skill root.
- `.vscode/mcp.json` - workspace MCP registration for VS Code.
- `.vscode/mcp/agents-remember-settings.json` - Agents Remember MCP authority
  settings.

After the restart, invoke:

```text
c-13-install-and-onboard
```

That skill runs or verifies `runtime_install()` and then handles memory,
onboarding, and providers.

## Skills

VS Code discovers project skills from `.github/skills`, `.claude/skills`, and
`.agents/skills`, and personal skills from `~/.copilot/skills`,
`~/.claude/skills`, and `~/.agents/skills`. The copied starter package already
provides Agents Remember skills in the installed `.github/skills/` folder.

Do not run `skills_install()` for first-run setup. It remains available for
manual maintenance and non-package installs.

If you prefer to point VS Code directly at the installed runtime, add the
installed skill roots in the workspace settings:

```json
{
  "settings": {
    "chat.agentSkillsLocations": {
      "ar-coordination/skills": true
    }
  }
}
```

Use `chat.useCustomizationsInParentRepositories` when a monorepo or parent workspace should provide skills to nested repositories.

## Instructions

Copilot supports repository instructions such as
`.github/copilot-instructions.md`. The starter package installs the Agents
Remember instruction there. Keep repo-specific guidance separate when the
repository already owns its `.github/copilot-instructions.md`.

## Hooks

VS Code discovers workspace hooks from `.github/hooks/*.json`. The starter
package includes a `SessionStart` hook that emits the same first-action
directive as additional context:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "python3 .github/hooks/agents-remember-session-start.py",
        "windows": "py -3 .github\\hooks\\agents-remember-session-start.py",
        "linux": "python3 .github/hooks/agents-remember-session-start.py",
        "osx": "python3 .github/hooks/agents-remember-session-start.py",
        "timeout": 60
      }
    ]
  }
}
```

## MCP

VS Code workspace MCP config lives at `.vscode/mcp.json`.

```json
{
  "servers": {
    "agents-remember": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--refresh-package",
        "agents-remember-mcp",
        "agents-remember-mcp@latest",
        "--config",
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.vscode/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

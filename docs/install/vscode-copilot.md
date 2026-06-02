# Install For VS Code + GitHub Copilot

VS Code supports Agent Skills for GitHub Copilot Chat.

Official references:

- [Use Agent Skills in VS Code](https://code.visualstudio.com/docs/copilot/customization/agent-skills)
- [Copilot settings reference](https://code.visualstudio.com/docs/copilot/reference/copilot-settings)

## Install Runtime

```text
runtime_install()
```

## Expose Skills

VS Code discovers project skills from `.github/skills`, `.claude/skills`, and `.agents/skills`, and personal skills from `~/.copilot/skills`, `~/.claude/skills`, and `~/.agents/skills`. You can also configure additional locations with `chat.agentSkillsLocations`. Place the MCP settings under the matching registration folder, such as `.agents/mcp/`, and the skill target is inferred as the sibling `skills/` folder.

For a workspace-local cross-agent install:

```text
skills_install()
```

If you prefer to point VS Code directly at the installed runtime, add the installed skill roots in the workspace settings:

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

Copilot also supports repository instructions such as `.github/copilot-instructions.md`. Put repo-specific guidance there, and keep the Agents Remember runtime instruction close to the workspace root or in the workspace file so the agent can find `ar-coordination/AGENTS.md`.

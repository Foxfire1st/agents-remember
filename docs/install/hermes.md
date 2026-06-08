# Install For Hermes.md

Hermes Agent supports project context files, MCP servers, and a skills system.

Official references:

- [Hermes Context Files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files/)
- [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/)
- [Hermes Skills](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md)
- [Hermes Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration/)

## Root Starter Package

The repository includes a Hermes starter package at `.hermes/`. Copy that
folder to your workspace root, then render the copied package. The
`.hermes/render-starter` script is a convenience: with a single `--repo` list
such as `--repo my-app shared-lib`, it infers the workspace root from the copied
`.hermes/` folder, fills path and repository placeholders, writes the installed
`HERMES.md`, and validates that each requested repository exists before you
merge `.hermes/config.yaml` into `~/.hermes/config.yaml` and start or reload
Hermes from that workspace once. If you prefer not to run the renderer, make
those same replacements by hand, copy or merge the directive into `HERMES.md`,
and verify that no placeholder tokens remain.

If the workspace root already has a different `HERMES.md`, merge the rendered
Agents Remember directive into that file before rerunning the renderer.

The source checkout keeps the template under `.hermes/` so the repository root
remains reserved for source-project files such as its own `AGENTS.md`.

The package contains:

- `.hermes/HERMES.md` - template for the installed highest-priority Hermes
  project context file.
- `.hermes/config.yaml` - merge template for Hermes' user-level config.
- `.hermes/mcp/agents-remember-settings.json` - Agents Remember MCP authority
  settings.
- `.hermes/skills/` - Agents Remember skills exposed through
  `skills.external_dirs`.

After the restart or reload, invoke:

```text
c-13-install-and-onboard
```

That skill runs or verifies `runtime_install()` and then handles memory,
onboarding, and providers.

## Workspace Instructions

Hermes uses `.hermes.md` or `HERMES.md` as highest-priority project context files, and also supports `AGENTS.md` and `CLAUDE.md`.

Use the packaged `HERMES.md` template for Hermes-specific priority. Do not
overwrite a repository's root `AGENTS.md` just to wire Agents Remember; root
`AGENTS.md` is project-specific.

The starter package ships `.hermes/HERMES.md` as a template because Hermes loads
installed `.hermes.md` / `HERMES.md` before `AGENTS.md` when building project
context.

## MCP

Hermes reads MCP server configuration from `~/.hermes/config.yaml` under
`mcp_servers`. Merge this starter snippet into that file:

```yaml
mcp_servers:
  agents-remember:
    command: "uvx"
    args:
      - "--refresh-package"
      - "agents-remember-mcp"
      - "agents-remember-mcp@latest"
      - "--config"
      - "<PATH/TO/YOUR/PROJECTS_FOLDER>/.hermes/mcp/agents-remember-settings.json"
```

If you edit MCP configuration while Hermes is running, use `/reload-mcp`.

## Runtime And Skills

After the restart or reload, the copied `c-13-install-and-onboard` skill runs or
verifies:

```text
runtime_install()
```

Hermes local skills commonly live under `~/.hermes/skills/`. Place the MCP
settings under `~/.hermes/mcp/` to infer `~/.hermes/skills/`, or set
`harnessSkillRoot` to a category folder when you want one. The starter package
already provides one flat folder per skill under `.hermes/skills/` and exposes
that folder through `skills.external_dirs`.

Do not run `skills_install()` for first-run setup. It remains available for
manual maintenance and non-package installs.

You can also use `harnessSkillRoot` for a shared skills directory and add it to
`~/.hermes/config.yaml` when it does not follow the sibling-folder convention:

```yaml
skills:
  external_dirs:
    - "<PATH/TO/YOUR/PROJECTS_FOLDER>/.hermes/skills"
```

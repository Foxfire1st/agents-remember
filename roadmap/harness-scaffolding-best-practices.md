# Agents Remember — Cross-Harness Scaffolding Best Practices

## Purpose

This document assumes the repository has already been cleaned up:

- Skill package names are syntactically valid.
- Skill `name` frontmatter matches parent directories.
- `AGENTS.md` is concise and professional.
- The canonical skill tree is trusted as the portable workflow layer.
- Git worktrees are part of the intended execution model.

The goal here is not to fix malformed repo contents. The goal is to help Agents Remember offer clean scaffolding for several agent harnesses:

- Codex
- Claude Code
- VS Code / GitHub Copilot agents
- Cursor
- OpenCode
- Hermes
- Pi.dev

The core recommendation is to keep one portable operating model and provide thin, explicit harness adapters.

```text
Portable repo contract
  AGENTS.md
  skills/
  scripts/agent/
  schemas/

Harness adapters
  harnesses/codex/
  harnesses/claude-code/
  harnesses/vscode/
  harnesses/cursor/
  harnesses/opencode/
  harnesses/hermes/
  harnesses/pi/

User-facing examples
  examples/codex/
  examples/claude-code/
  examples/vscode/
  examples/cursor/
  examples/opencode/
  examples/hermes/
  examples/pi/
```

---

## Design Philosophy

### Core mental model

Agents Remember should be a **portable agent-operating system for repositories**, not a pile of tool-specific prompt files.

The repo should separate five concerns:

| Concern | Portable or harness-specific? | Best home |
|---|---:|---|
| Project facts and standing repo rules | Portable | `AGENTS.md` |
| Repeatable workflows | Portable | `skills/` |
| Machine-checkable repo policy | Portable | `schemas/`, `scripts/agent/`, `policy/` |
| Harness discovery and UI affordances | Harness-specific | `harnesses/<harness>/` and `examples/<harness>/` |
| Hard execution controls | Harness-specific, generated from portable policy | hooks, permissions, sandbox config, extensions |

The important rule is:

```text
Do not duplicate meaning across harnesses.
Export the same meaning into harness-native surfaces.
```

That means a Claude Code adapter, a VS Code adapter, and a Pi adapter may look different on disk, but they should all enforce the same conceptual contract:

```text
Resolve active repo → resolve active memory/onboarding root → verify drift → plan → implement in worktree → review → close out.
```

### Major buckets

#### 1. Portable semantic layer

This is the part that should work across tools.

```text
AGENTS.md
skills/
scripts/agent/
schemas/
policy/
```

Use this layer for:

- repo overview;
- build, test, lint, and verification commands;
- coding conventions;
- onboarding and drift rules;
- Agents Remember workflow skills;
- task execution contract schema;
- validation scripts.

#### 2. Harness adapter layer

This is the part that lets each tool consume the portable layer cleanly.

```text
harnesses/<harness>/README.md
harnesses/<harness>/config.example.*
harnesses/<harness>/hooks/
harnesses/<harness>/agents/
harnesses/<harness>/instructions/
harnesses/<harness>/extensions/
```

Use this layer for:

- tool-native configuration;
- permission defaults;
- hook registration;
- role-specific agents or subagents;
- skill-location mapping;
- installation instructions;
- known limitations.

#### 3. Execution contract layer

This is the layer that turns “the agent should stay inside the task boundary” into something tools can enforce.

```text
ar-management/tasks/<task-id>/execution-context.json
```

Example:

```json
{
  "taskId": "tas-link-expand",
  "targetRepo": "device-management",
  "codeWorktree": "/home/user/worktrees/device-management/tas-link-expand",
  "memoryRoot": "/home/user/ar-management",
  "onboardingRoot": "/home/user/ar-management/onboarding/device-management",
  "coordinationRoot": "/home/user/ar-management/tasks/tas-link-expand",
  "allowedWriteRoots": [
    "/home/user/worktrees/device-management/tas-link-expand",
    "/home/user/ar-management/onboarding/device-management",
    "/home/user/ar-management/tasks/tas-link-expand"
  ],
  "commitAllowed": false,
  "pushAllowed": false,
  "networkAllowed": false
}
```

The execution contract is the bridge between portable policy and harness-specific enforcement.

#### 4. Deterministic enforcement layer

This is where “professional” starts to matter.

Prompts and skills are advisory. Permissions, sandboxes, hooks, policy scripts, and CI checks are enforceable.

The rule is:

```text
If violating a rule would be dangerous, do not rely on prose.
Use a technical control.
```

Examples:

| Risk | Best control |
|---|---|
| Editing `.env` | permission deny / hook deny |
| Running `git push` | permission deny / hook deny |
| Running migrations | ask gate |
| Writing outside active worktree | execution-contract guard |
| Finishing without drift check | stop hook |
| Malformed skills | CI validator |
| Shipping secrets in a zip | packaging safety check |

---

## Highest-leverage repo structure

### Recommended top-level structure

```text
AGENTS.md
README.md

skills/
  p-00-creation/
  p-01-research/
  p-02-synthesis/
  p-03-design/
  p-04-planning/
  p-05-implementation/
  p-99-review/
  u-01-core-skills/
  w-01-heavy-task-workflow/
  w-02-light-task-workflow/

policy/
  harness-policy.schema.json
  default-harness-policy.json
  protected-paths.json
  dangerous-commands.json

schemas/
  execution-context.schema.json
  skill-manifest.schema.json

scripts/
  agent/
    ar-preflight.py
    ar-resolve-json.py
    ar-stop-check.py
    validate-skills.py
    validate-harness-config.py
    export-skills.py
    package-safety-check.py

harnesses/
  codex/
  claude-code/
  vscode/
  cursor/
  opencode/
  hermes/
  pi/

examples/
  codex/
  claude-code/
  vscode/
  cursor/
  opencode/
  hermes/
  pi/

docs/
  harnesses.md
  execution-contract.md
  skill-authoring.md
```

### Why this structure is cleaner than tool-specific copies

This avoids three bad outcomes:

1. **Duplicate skill text** — one skill gets fixed while another harness copy goes stale.
2. **Hidden harness assumptions** — a user has to reverse-engineer why the repo works in VS Code but not OpenCode.
3. **Prompt-only safety** — the agent is told not to do something but is still technically allowed to do it.

The clean model is:

```text
skills/ contains canonical workflows.
policy/ contains canonical enforcement intent.
harnesses/<harness>/ turns policy into tool-native controls.
examples/<harness>/ shows users how to install the adapter.
```

---

## Skill distribution strategy

### The problem

Agent Skills are portable as a format, but not every harness discovers them from the same directory.

Some harnesses can be configured to read arbitrary skill locations. Others expect tool-native folders such as:

```text
.agents/skills/
.claude/skills/
.opencode/skills/
.pi/skills/
.github/skills/
```

Your canonical skill tree is phase-organized:

```text
skills/p-03-design/d-04-output-documentation/SKILL.md
```

A harness may expect a flatter discovery surface:

```text
.agents/skills/d-04-output-documentation/SKILL.md
```

### Recommendation: canonical source plus generated exports

Keep `skills/` as the canonical source. Generate tool-native export surfaces from it.

```text
skills/
  p-03-design/d-04-output-documentation/SKILL.md

.generated/harness-skills/
  agents/d-04-output-documentation -> ../../../skills/p-03-design/d-04-output-documentation
  claude/d-04-output-documentation -> ../../../skills/p-03-design/d-04-output-documentation
  opencode/d-04-output-documentation -> ../../../skills/p-03-design/d-04-output-documentation
  pi/d-04-output-documentation -> ../../../skills/p-03-design/d-04-output-documentation
```

Then each harness adapter can choose its best install mode:

| Mode | When to use | Tradeoff |
|---|---|---|
| Symlink export | Unix/macOS dev machines, low duplication | Windows can be awkward without symlink privileges |
| Copy export | Windows or packaged release archives | Must be regenerated after canonical skill edits |
| Workspace/config location | VS Code or Pi-style configurable discovery | Requires users to use the provided workspace/config |
| Package distribution | Pi packages, Codex plugins, Claude plugins | More setup, best for mature sharing |

This is not a compatibility shim for malformed skills. It is a professional distribution adapter for different harness discovery conventions.

### Add a skill manifest

Add a generated manifest so adapters do not need to crawl the tree differently.

```text
skills.manifest.json
```

Example:

```json
{
  "version": 1,
  "skills": [
    {
      "name": "c-08-ar-management-resolver",
      "path": "skills/u-01-core-skills/c-08-ar-management-resolver",
      "phase": "u-01-core-skills",
      "description": "Resolve the active Agents Remember management root, onboarding root, and task context before planning or editing."
    }
  ]
}
```

Then `scripts/agent/export-skills.py` can create harness-native exports from the manifest.

Suggested commands:

```bash
python scripts/agent/validate-skills.py
python scripts/agent/export-skills.py --target .agents/skills --mode symlink
python scripts/agent/export-skills.py --target .claude/skills --mode symlink
python scripts/agent/export-skills.py --target .opencode/skills --mode symlink
python scripts/agent/export-skills.py --target .pi/skills --mode symlink
```

For release archives:

```bash
python scripts/agent/export-skills.py --target dist/agents-skills --mode copy
python scripts/agent/package-safety-check.py dist/agents-remember-md.zip
```

### Validation rule

Generated exports should not be edited by hand.

Add a marker file:

```text
.generated/harness-skills/README.md
```

```md
# Generated skill exports

Do not edit these files by hand.

Canonical source: `skills/`.
Regenerate with:

```bash
python scripts/agent/export-skills.py --all
```
```

---

## Cross-harness invariants

These should hold regardless of the harness.

### Always-on context invariants

```text
- AGENTS.md is the portable repo contract.
- Nested AGENTS.md files are only used when a subproject truly differs.
- Long procedures live in skills, not in AGENTS.md.
- User-level preferences should not be committed as project policy.
- Harness-specific adapters must not override canonical repo policy silently.
```

### Execution invariants

```text
- Source edits happen inside the active code worktree.
- Durable memory/onboarding edits happen only inside the resolved memory root.
- Task coordination edits happen only inside the active task root.
- Protected files are not edited by default.
- Commits, pushes, deploys, migrations, and destructive commands require explicit approval or are denied.
- The agent cannot claim completion unless verification and drift checks were addressed.
```

### Safety invariants

```text
- `.env`, credentials, private keys, `.git/`, generated build output, and vendored dependencies are protected.
- Network access is off or ask-gated unless a task explicitly needs it.
- Tool hooks and permission scripts are treated as privileged code.
- CI validates skill metadata, harness config, policy schemas, and package safety.
```

---

## Portable policy model

Add a single policy file that harness adapters can compile into native controls.

```text
policy/default-harness-policy.json
```

Example:

```json
{
  "protectedPaths": [
    ".git/**",
    ".env",
    ".env.*",
    "**/*.pem",
    "**/*.key",
    "node_modules/**",
    "dist/**",
    "build/**",
    "coverage/**",
    "vendor/**"
  ],
  "denyCommands": [
    "git push",
    "git reset --hard",
    "git clean",
    "git worktree remove",
    "git worktree prune",
    "rm -rf"
  ],
  "askCommands": [
    "git commit",
    "git rebase",
    "npm install",
    "pnpm add",
    "yarn add",
    "bun add",
    "pip install",
    "uv add",
    "cargo add",
    "curl",
    "wget",
    "deploy",
    "migrate"
  ],
  "completionRequirements": {
    "preflightRequired": true,
    "driftCheckRequired": true,
    "verificationRequired": true,
    "diffReviewRequired": true
  }
}
```

Each harness adapter should document how it maps this policy into native mechanisms.

```text
Codex        config.toml, sandboxing, rules, hooks
Claude Code  settings.json permissions, hooks, subagents
VS Code      workspace settings, hooks, custom agents
Cursor       rules, hooks/plugins, skills, subagents
OpenCode     opencode.json permissions, agents
Hermes       config.yaml approvals, hooks, toolsets, containers
Pi           extensions, settings, containers, skills
```

---

## Harness capability matrix

| Harness | Best at | Portable context | Skills | Role split | Hard controls | Main caveat |
|---|---|---|---|---|---|---|
| Codex | Strong sandboxed coding loop | `AGENTS.md` | `.agents/skills` | agents/subagents/config | sandbox, approvals, hooks, rules | Requires configuring approval/sandbox profiles deliberately |
| Claude Code | Rich memory, skills, subagents, hooks | `CLAUDE.md`, imports, `AGENTS.md` bridge | `.claude/skills` | subagents | permissions, hooks | `CLAUDE.md` is context, not enforcement |
| VS Code / Copilot | IDE-native workflows and custom agents | `AGENTS.md`, `.github/instructions` | configured skill locations | `.agent.md` custom agents | hooks, settings, permissions | Parent discovery and ordering can surprise users |
| Cursor | Agent-first IDE, rules, cloud/local agents | `AGENTS.md`, `.cursor/rules` | Cursor skills/plugins | subagents / parallel agents | hooks/plugins/rules | Rapidly evolving surface; keep adapter thin |
| OpenCode | Open terminal/IDE agent with explicit permissions | `AGENTS.md` | `.agents`, `.opencode`, `.claude` skill paths | Build/Plan agents and subagents | `permission` config | Defaults can be permissive unless configured |
| Hermes | Orchestration, memory, remote workers, messaging | context files including `AGENTS.md` | `~/.hermes/skills`, external dirs | delegation/workers/kanban | approvals, containers, hooks, allowlists | Treat as orchestrator; isolate workers |
| Pi.dev | Minimal, customizable harness primitives | `AGENTS.md`, `SYSTEM.md` | `.pi`, `.agents`, settings | extension-built or package-installed | extensions, containers, custom gates | Permissions/plan/subagents are intentionally extension/package territory |

---

# Harness-specific scaffolding

## 1. Codex

### Best-practice framing

Use Codex when you want a strong local or cloud coding loop with explicit sandboxing, approvals, hooks, `AGENTS.md`, and skills.

Codex reads `AGENTS.md` before work, building an instruction chain from global and project scopes. It also supports repo skills through `.agents/skills`, and its security model combines sandbox mode with approval policy.

### Recommended scaffold

```text
harnesses/codex/
  README.md
  config.toml.example
  hooks/
    pre-tool-guard.py
    stop-check.py
  rules/
    default.rules.example
  skills-export.md

examples/codex/
  install-codex-adapter.sh
  config.toml.example
```

### Skill exposure

Preferred:

```bash
python scripts/agent/export-skills.py --target .agents/skills --mode symlink
```

For Windows or archives:

```bash
python scripts/agent/export-skills.py --target .agents/skills --mode copy
```

### Config guidance

Example:

```toml
# .codex/config.toml.example

sandbox_mode = "workspace-write"
approval_policy = "on-request"
allow_login_shell = false

[sandbox_workspace_write]
network_access = false

[shell_environment_policy]
inherit = "core"
ignore_default_excludes = false
exclude = ["AWS_*", "AZURE_*", "GCP_*", "*_TOKEN", "*_SECRET", "*_KEY"]

[features]
codex_hooks = true
```

For a hardened profile, define explicit permission roots and keep network off unless the task contract allows it.

```toml
# Example only. Adapt per machine.
default_permissions = "agents-remember-workspace"

[permissions.agents-remember-workspace.filesystem]
":project_roots" = { "." = "write", "**/*.env" = "none", ".git/**" = "none" }
glob_scan_max_depth = 4

[permissions.agents-remember-workspace.network]
enabled = false
```

### What to document for users

```text
- Run from the intended worktree root.
- Keep `.agents/skills` generated from canonical `skills/`.
- Keep `approval_policy` interactive unless running inside an already-isolated CI/container.
- Do not enable danger/no-sandbox profiles for normal repo work.
- Use hooks for protected paths and stop checks.
```

### Clean adapter checklist

```text
[ ] `AGENTS.md` is loaded.
[ ] `.agents/skills` export exists.
[ ] Sandbox mode is not full access by default.
[ ] Network is off or ask-gated by default.
[ ] Protected paths are blocked.
[ ] Stop hook checks preflight, drift, diff, and verification.
```

---

## 2. Claude Code

### Best-practice framing

Use Claude Code when users want strong CLI workflows with `CLAUDE.md`, skills, subagents, permissions, and hooks.

Claude Code treats `CLAUDE.md` and auto memory as context, not enforcement. Keep `CLAUDE.md` concise and move procedures into skills. Use permissions and hooks for hard rules.

### Recommended scaffold

```text
harnesses/claude-code/
  README.md
  CLAUDE.md.example
  settings.json.example
  agents/
    ar-planner.md
    ar-builder.md
    ar-reviewer.md
  hooks/
    pre-tool-guard.py
    stop-check.py
  skills-export.md

examples/claude-code/
  install-claude-adapter.sh
```

### `CLAUDE.md` bridge

Keep it small.

```md
# Claude Code entrypoint

Follow the repository contract in `AGENTS.md`.

Claude-specific rules:

- Use planning mode before editing auth, billing, database, infra, or durable memory files.
- Use the Agents Remember skills for workflow execution.
- Treat hook denials and permission denials as hard policy.
- Do not retry a blocked action with alternate shell syntax.
```

If Claude Code supports imports in your environment, use an import rather than copy-pasting the entire `AGENTS.md` content.

### Skill exposure

Preferred project export:

```bash
python scripts/agent/export-skills.py --target .claude/skills --mode symlink
```

Fallback for Windows or archives:

```bash
python scripts/agent/export-skills.py --target .claude/skills --mode copy
```

### Permissions guidance

Example:

```json
{
  "permissions": {
    "deny": [
      "Bash(git push*)",
      "Bash(git reset --hard*)",
      "Bash(git clean*)",
      "Bash(rm -rf*)",
      "Edit(.env*)",
      "Edit(.git/**)",
      "Edit(node_modules/**)",
      "Edit(dist/**)",
      "Edit(build/**)",
      "Edit(coverage/**)"
    ],
    "ask": [
      "Bash(git commit*)",
      "Bash(git rebase*)",
      "Bash(*install*)",
      "Bash(*migrate*)",
      "Bash(curl*)",
      "Bash(wget*)"
    ]
  }
}
```

Exact matcher syntax should be verified against the installed Claude Code version, but the policy intent should stay stable.

### Subagent pattern

```text
AR Planner
  Read-only. Resolves repo, checks drift, produces plan.

AR Builder
  Edits only after approved plan. Uses active execution context.

AR Reviewer
  Reviews diff, onboarding consistency, protected paths, and verification.
```

### Clean adapter checklist

```text
[ ] `CLAUDE.md` is a bridge, not a duplicate constitution.
[ ] `.claude/skills` is generated from canonical `skills/`.
[ ] Project permissions deny destructive operations.
[ ] Hooks enforce pre-tool and stop checks.
[ ] Subagents have least-privilege tool access.
[ ] Auto memory does not compete with Agents Remember durable memory.
```

---

## 3. VS Code / GitHub Copilot agents

### Best-practice framing

Use VS Code when users want IDE-native agent behavior, custom agents, workspace settings, hooks, and direct integration with editor state.

VS Code can consume `AGENTS.md`, custom instructions, prompt files, custom agents, Agent Skills, MCP, and hooks. It also supports configurable skill locations, which is useful for your phase-organized canonical skill tree.

### Recommended scaffold

```text
harnesses/vscode/
  README.md
  agents/
    ar-planner.agent.md
    ar-builder.agent.md
    ar-reviewer.agent.md
  instructions/
    ar-memory.instructions.md
    ar-worktree.instructions.md
  hooks/
    ar-guard.json
  scripts/
    ar_vscode_session_context.py
    ar_vscode_pretool_guard.py
    ar_vscode_stop_check.py
  settings.example.json

examples/vscode/
  agents-remember.code-workspace.example
```

### Skill exposure

For VS Code, keep using workspace-configured canonical locations if that already works well.

```jsonc
"chat.agentSkillsLocations": {
  "agents-remember-md/skills": true,
  "agents-remember-md/skills/p-00-creation": true,
  "agents-remember-md/skills/p-01-research": true,
  "agents-remember-md/skills/p-02-synthesis": true,
  "agents-remember-md/skills/p-03-design": true,
  "agents-remember-md/skills/p-04-planning": true,
  "agents-remember-md/skills/p-05-implementation": true,
  "agents-remember-md/skills/p-99-review": true,
  "agents-remember-md/skills/u-01-core-skills": true
}
```

For users who do not want a workspace file, provide an export option:

```bash
python scripts/agent/export-skills.py --target .github/skills --mode symlink
```

### Custom agents

Use custom agents for role separation, not for duplicating skills.

```text
AR Planner
  read/search only; no edits.

AR Builder
  edit/execute after approved plan; no commit/push/deploy.

AR Reviewer
  read/execute; review and verify; no broadening scope.
```

### Hook guidance

Use hooks for deterministic policy:

```text
SessionStart
  Resolve active repo/worktree/memory/task roots.

PreToolUse
  Deny protected paths and commands.
  Ask for migrations, dependency changes, commits, network, deploys.

Stop
  Block completion if preflight, drift, diff review, or verification is missing.
```

### Workspace guidance

The workspace example should not contain user-specific absolute paths. Prefer placeholders.

```jsonc
{
  "folders": [
    { "path": "../<active-code-worktree>" },
    { "path": "../agents-remember-md" }
  ],
  "settings": {
    "chat.useAgentsMdFile": true,
    "chat.useNestedAgentsMdFiles": true,
    "chat.agentSkillsLocations": {
      "../agents-remember-md/skills": true,
      "../agents-remember-md/skills/p-00-creation": true,
      "../agents-remember-md/skills/p-01-research": true,
      "../agents-remember-md/skills/p-02-synthesis": true,
      "../agents-remember-md/skills/p-03-design": true,
      "../agents-remember-md/skills/p-04-planning": true,
      "../agents-remember-md/skills/p-05-implementation": true,
      "../agents-remember-md/skills/p-99-review": true,
      "../agents-remember-md/skills/u-01-core-skills": true
    },
    "chat.hookFilesLocations": {
      "../agents-remember-md/harnesses/vscode/hooks": true
    },
    "chat.agentFilesLocations": {
      "../agents-remember-md/harnesses/vscode/agents": true
    },
    "chat.instructionsFilesLocations": {
      "../agents-remember-md/harnesses/vscode/instructions": true
    }
  }
}
```

### Clean adapter checklist

```text
[ ] Workspace file uses relative paths or placeholders.
[ ] Canonical skills are loaded without duplicate wrappers.
[ ] Custom agents are role-specific and least-privilege.
[ ] Hooks are enabled from `harnesses/vscode/hooks`.
[ ] Terminal auto-approval is conservative.
[ ] Copilot memory is disabled if Agents Remember is the memory authority.
```

---

## 4. Cursor

### Best-practice framing

Use Cursor when users want an agent-first IDE with project/team/user rules, skills, hooks/plugins, cloud/local agents, and parallel work.

Cursor is evolving quickly. The adapter should be intentionally thin: expose the portable contract, add minimal Cursor-native rules, and avoid encoding Agents Remember as a pile of Cursor-only behavior.

### Recommended scaffold

```text
harnesses/cursor/
  README.md
  rules/
    00-agents-remember.mdc
    protected-paths.mdc
    worktree-boundaries.mdc
  hooks/
    ar-pretool-guard.example
    ar-stop-check.example
  skills-export.md
  plugin-notes.md

examples/cursor/
  cursor-rules.example.md
  cursor-setup.md
```

### Rule strategy

Cursor rules should be short and activation-scoped.

```text
Always-on rule:
  follow AGENTS.md, use Agents Remember skills, respect execution contract.

Path-scoped rule:
  memory/onboarding files are durable state; do not edit speculatively.

Manual or intelligent rule:
  use resolver before planning or implementation.
```

Avoid putting the full workflow doctrine into `.cursor/rules`. Use skills for procedures.

### Skill exposure

Use the same export idea. Depending on the Cursor version and team setup, users may choose Cursor-native skills/plugins or a shared `.agents/skills` surface.

```bash
python scripts/agent/export-skills.py --target .agents/skills --mode symlink
```

If Cursor-specific skill locations are preferred in a given installation, add that target to `export-skills.py` rather than copying skills by hand.

### Cursor-specific guidance

```text
- Keep Cursor rules narrow and non-conflicting.
- Use skills for multi-step Agents Remember workflows.
- Use hooks/plugins for protected paths and command gating where available.
- Use parallel agents only after the task execution contract has been split into independent slices.
- Require a reviewer pass before accepting multi-agent changes.
```

### Clean adapter checklist

```text
[ ] `.cursor/rules` is minimal and scoped.
[ ] Skills are generated from canonical `skills/`.
[ ] Hooks/plugins enforce dangerous commands where available.
[ ] Parallel agent use requires independent execution contexts.
[ ] Local/cloud handoff preserves worktree and task-contract boundaries.
```

---

## 5. OpenCode

### Best-practice framing

Use OpenCode when users want an open terminal, desktop, or IDE-extension agent with explicit `AGENTS.md`, skills, permissions, and configurable agents.

OpenCode supports `AGENTS.md` and multiple skill locations, including `.agents/skills`. It also exposes a `permission` config with `allow`, `ask`, and `deny` decisions. Configure it deliberately because broad tool access can otherwise be permissive.

### Recommended scaffold

```text
harnesses/opencode/
  README.md
  opencode.json.example
  agents/
    ar-planner.md
    ar-builder.md
    ar-reviewer.md
  skills-export.md

examples/opencode/
  install-opencode-adapter.sh
  opencode.json.example
```

### Skill exposure

Preferred:

```bash
python scripts/agent/export-skills.py --target .agents/skills --mode symlink
```

OpenCode also supports `.opencode/skills`, so users who want OpenCode-local scaffolding can use:

```bash
python scripts/agent/export-skills.py --target .opencode/skills --mode symlink
```

### Permission baseline

Example:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "*": "ask",
    "read": "allow",
    "grep": "allow",
    "glob": "allow",
    "edit": "ask",
    "bash": "ask",
    "webfetch": "ask",
    "websearch": "ask",
    "external_directory": "ask"
  }
}
```

Then specialize edits and commands where OpenCode supports specific matching.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "edit": {
      "*": "ask",
      ".env*": "deny",
      ".git/**": "deny",
      "node_modules/**": "deny",
      "dist/**": "deny",
      "build/**": "deny",
      "coverage/**": "deny"
    },
    "bash": "ask"
  }
}
```

### Agent strategy

OpenCode has built-in Build and Plan concepts. Use them cleanly:

```text
Plan
  read-only analysis and review; no edits.

Build
  implementation after approved plan.

Reviewer subagent
  diff review, protected paths, onboarding consistency, verification.
```

### Clean adapter checklist

```text
[ ] `AGENTS.md` is committed and concise.
[ ] `.agents/skills` or `.opencode/skills` is generated.
[ ] `permission` defaults to ask, not broad allow.
[ ] Plan agent is used for read-only planning.
[ ] External directories and network access are ask-gated.
[ ] Execution context limits write roots.
```

---

## 6. Hermes

### Best-practice framing

Use Hermes when users want orchestration, memory, messaging-platform access, remote execution, long-running jobs, or delegation to other coding agents.

Hermes should usually be treated as an **orchestrator**, not just another editor-bound coding harness.

The clean model is:

```text
Hermes plans, schedules, delegates, remembers, and coordinates.
Worker harnesses implement inside isolated worktrees.
```

### Recommended scaffold

```text
harnesses/hermes/
  README.md
  AGENTS.md.example
  config.yaml.example
  hooks/
    ar-pretool-guard.py
    ar-stop-check.py
  skills-export.md
  worker-profiles.md

examples/hermes/
  hermes-profile.example.yaml
  hermes-worker-codex.md
  hermes-worker-opencode.md
```

### Context strategy

Hermes can load project context files such as `AGENTS.md`, Hermes-native context files, Claude files, and Cursor rules depending on its prompt assembly path. Keep project-specific rules in `AGENTS.md`. Keep persona/style in `SOUL.md`, not in project policy.

For Agents Remember users:

```text
AGENTS.md
  repo policy and workflow boundaries

SOUL.md
  optional personal Hermes voice/personality

skills/
  canonical Agents Remember workflows

Hermes profile/config
  toolsets, approvals, memory behavior, terminal backend, containers
```

### Skill exposure

Hermes uses its own skill directory but can also work with external skill directories. Recommended install options:

```bash
python scripts/agent/export-skills.py --target ~/.hermes/skills --mode copy
```

or, for repo-local development:

```bash
# Configure Hermes external skill directories if preferred.
# Keep canonical source in `skills/`; do not hand-copy individual skills.
```

### Worker strategy

Hermes already has skills for delegating to coding agents such as Codex, Claude Code, and OpenCode. Use that as a pattern:

```text
Hermes root session
  - owns user conversation, task decomposition, progress tracking
  - creates task execution contract
  - assigns work to worker harness

Worker harness
  - Codex/OpenCode/Claude Code in task worktree
  - receives explicit task contract
  - runs implementation and verification

Hermes reviewer
  - reviews worker output
  - coordinates final closeout
```

### Security guidance

```text
- Do not use `--yolo` for normal coding work.
- Keep dangerous-command approvals enabled unless the worker is already inside a locked-down container.
- Use explicit allowlists for messaging bots.
- Prefer container, Docker, Modal, Daytona, or isolated worktree backends for long-running or remote work.
- Treat Hermes hooks as privileged scripts.
```

### Clean adapter checklist

```text
[ ] Hermes has a project-specific context file, not a giant global persona file.
[ ] Skills are installed/generated from canonical `skills/`.
[ ] Dangerous-command approval is enabled.
[ ] Messaging users are allowlisted.
[ ] Worker tasks run in isolated worktrees or containers.
[ ] Delegated worker harnesses receive the execution context.
```

---

## 7. Pi.dev

### Best-practice framing

Use Pi when users want a minimal terminal harness they can shape through skills, prompt templates, and TypeScript extensions.

Pi is especially interesting for Agents Remember because it encourages building the harness behavior you want rather than working around sealed product defaults.

### Recommended scaffold

```text
harnesses/pi/
  README.md
  settings.json.example
  extensions/
    ar-permission-gate.ts
    ar-path-protection.ts
    ar-session-context.ts
    ar-stop-check.ts
  prompts/
    ar-plan.md
    ar-review.md
  skills-export.md
  package/
    package.json.example

examples/pi/
  install-pi-adapter.sh
  pi-settings.example.json
```

### Context strategy

Pi can load project instructions such as `AGENTS.md`, supports `SYSTEM.md` for system-prompt changes, loads skills on demand, and lets extensions inject dynamic context.

Use the split carefully:

```text
AGENTS.md
  project policy and workflow contract

SYSTEM.md
  only if a user intentionally wants to replace or append system-level behavior

skills/
  portable workflows

.pi/extensions/
  deterministic controls and workflow primitives

.pi/prompts/
  optional reusable slash prompts
```

Avoid putting project-specific repo rules into `SYSTEM.md`. That would make the adapter feel magical and harder to audit.

### Skill exposure

Pi supports `.pi/skills`, `.agents/skills`, settings-based skill paths, and packages. Good options:

```bash
python scripts/agent/export-skills.py --target .pi/skills --mode symlink
```

or:

```bash
python scripts/agent/export-skills.py --target .agents/skills --mode symlink
```

For a distributable Pi package, bundle generated skills and extensions together.

```text
pi-agents-remember-package/
  package.json
  skills/
  extensions/
  prompts/
```

### Extension strategy

Pi’s main professional advantage is that hardening can be implemented as extensions.

Create extensions for:

```text
ar-session-context.ts
  On session start, resolve execution context and inject active roots.

ar-permission-gate.ts
  Intercept tool calls and ask/deny based on policy/default-harness-policy.json.

ar-path-protection.ts
  Block writes to protected paths and outside allowed roots.

ar-stop-check.ts
  Prevent false completion if drift/verification/review are missing.
```

Example policy behavior:

```text
Tool call: bash "git push"
Decision: block
Reason: pushes are not allowed from agent sessions.

Tool call: write ".env"
Decision: block
Reason: `.env` is protected; edit `.env.example` only.

Tool call: bash "pnpm add zod"
Decision: ask
Reason: dependency changes require explicit approval.
```

### Container guidance

Pi’s product philosophy leaves many features as primitives/extensions rather than forcing one built-in model. For normal users, provide two tracks:

```text
Pi standard
  Extensions for permissions and path protection.
  Worktree execution.
  Manual approval for risky operations.

Pi hardened
  Containerized shell execution.
  Permission extension.
  Path protection extension.
  Stop-check extension.
  No broad host filesystem access.
```

### Clean adapter checklist

```text
[ ] `AGENTS.md` remains the project policy source.
[ ] `SYSTEM.md` is optional and not used for repo-specific rules.
[ ] Skills are generated from canonical `skills/`.
[ ] Permission/path/stop behavior is implemented as extensions.
[ ] Extensions are reviewed as privileged code.
[ ] Hardened users get a containerized option.
[ ] Pi package distribution is available for users who want one-command install.
```

---

# User scaffolding profiles

Not every user needs the same hardening level. Provide explicit profiles.

## Profile 1: Minimal portable

For users who only want the core Agents Remember workflow.

```text
Includes:
- AGENTS.md
- canonical skills/
- skill validator
- basic setup docs

Excludes:
- hooks
- sandbox config
- custom agents/subagents
- worktree enforcement
```

Good for:

```text
- evaluating Agents Remember
- using arbitrary agent harnesses
- low-risk personal repos
```

## Profile 2: Standard professional

For most developers.

```text
Includes:
- AGENTS.md
- skills/
- generated skill export for chosen harness
- planner/builder/reviewer role guidance
- default protected path policy
- basic permissions/hook config
- package-safety check
```

Good for:

```text
- serious personal repos
- small teams
- users switching between VS Code, Codex, Claude Code, Cursor, or OpenCode
```

## Profile 3: Hardened execution

For high-trust or production-adjacent work.

```text
Includes:
- worktree execution contracts
- permission deny/ask policy
- pre-tool hooks
- stop hooks
- container or sandbox configuration
- no ambient network by default
- CI validation
- release/package safety checks
```

Good for:

```text
- company repos
- agents that can run for long periods
- remote workers
- messaging bots with terminal access
- parallel agent workflows
```

---

# Suggested installer UX

Add a single setup command that asks the user which harnesses they want to scaffold.

```bash
python scripts/agent/setup-harness.py
```

Prompt:

```text
Which harness do you want to configure?

[ ] Codex
[ ] Claude Code
[ ] VS Code / Copilot
[ ] Cursor
[ ] OpenCode
[ ] Hermes
[ ] Pi.dev

Hardening level?

[ ] Minimal portable
[ ] Standard professional
[ ] Hardened execution

Skill export mode?

[ ] Symlink
[ ] Copy
[ ] Config-only
```

Output examples:

```text
Configured Codex:
- generated .agents/skills
- wrote .codex/config.toml from example
- enabled hooks feature flag
- wrote .codex/hooks/pre-tool-guard.py

Configured OpenCode:
- generated .agents/skills
- wrote opencode.json
- set default permission to ask
```

Keep installer output explicit and reversible.

```bash
python scripts/agent/setup-harness.py --harness codex --profile standard --mode symlink
python scripts/agent/setup-harness.py --harness pi --profile hardened --mode copy
python scripts/agent/setup-harness.py --undo codex
```

---

# Shared validation gates

## Skill validation

```bash
python scripts/agent/validate-skills.py
```

Checks:

```text
- every SKILL.md has valid YAML frontmatter
- name matches parent directory
- name matches ^[a-z0-9]+(-[a-z0-9]+)*$
- description exists and is <= 1024 characters
- no duplicate skill names
- links resolve
- scripts referenced by skills exist
- no WIP/TODO/placeholder skill descriptions
```

## Harness config validation

```bash
python scripts/agent/validate-harness-config.py
```

Checks:

```text
- each harness README exists
- each example config parses
- each harness policy maps all protected paths
- each hook script is executable where required
- generated skill exports match skills.manifest.json
- no generated export is stale
```

## Package safety

```bash
python scripts/agent/package-safety-check.py agents-remember-md.zip
```

Reject:

```text
.git/
.env
.env.*
*.pem
*.key
id_rsa
id_ed25519
__pycache__/
*.pyc
node_modules/
dist/
build/
coverage/
```

## Execution contract validation

```bash
python scripts/agent/validate-execution-context.py ar-management/tasks/<task-id>/execution-context.json
```

Checks:

```text
- all declared roots exist
- allowedWriteRoots are absolute or normalized
- allowedWriteRoots do not include home directory wholesale
- memory root and code worktree are distinct when required
- task ID matches path
- commit/push/network flags are explicit
```

---

# Best-practice documentation pages to add

## `docs/harnesses.md`

Purpose: help users choose the right adapter.

Sections:

```text
- What is portable across all harnesses?
- Which harness should I use?
- What does each hardening level include?
- How do I install a harness adapter?
- How do I remove a harness adapter?
- How do I verify the adapter works?
```

## `docs/skill-distribution.md`

Purpose: explain canonical skills vs generated exports.

Sections:

```text
- Why `skills/` is canonical
- Why harnesses need export surfaces
- Symlink vs copy mode
- How to regenerate exports
- How CI detects stale exports
```

## `docs/execution-contract.md`

Purpose: make worktree/hardening behavior legible.

Sections:

```text
- What is an execution context?
- How allowed write roots are calculated
- How hooks consume execution context
- What happens when no execution context exists
- How to review an execution context before approving work
```

## `docs/security-model.md`

Purpose: explain what is advisory vs enforced.

Sections:

```text
- Prompt rules vs hard controls
- Protected paths
- Dangerous commands
- Network policy
- Secrets handling
- Hook/extension trust model
- CI/package safety
```

---

# Review standard for the scaffolding work

A reviewer should inspect in this order:

1. **Canonical layer** — `AGENTS.md`, `skills/`, `policy/`, `schemas/`.
2. **Export behavior** — generated skill surfaces are derived, not hand-maintained.
3. **Harness adapters** — each adapter is thin and maps to the same policy.
4. **Hard controls** — protected paths and dangerous commands are enforced technically.
5. **Examples** — examples are user-neutral, path-neutral, and do not include secrets.
6. **Validation** — CI catches malformed skills, stale exports, bad configs, and unsafe packages.

A strong result looks like this:

```text
A user can pick Codex, Claude Code, VS Code, Cursor, OpenCode, Hermes, or Pi.dev,
run one setup command, and get the same Agents Remember workflow semantics expressed
through that harness's native controls.
```

A weak result looks like this:

```text
Each harness has manually copied prompts and skills that drift independently.
Dangerous operations are discouraged only in prose.
Users need to reverse-engineer how VS Code was configured to make other tools work.
```

---

# Patch plan

## Patch 1 — Add portable policy and schemas

```text
policy/default-harness-policy.json
policy/harness-policy.schema.json
schemas/execution-context.schema.json
schemas/skill-manifest.schema.json
```

Outcome:

```text
There is a canonical machine-readable policy that harness adapters can compile from.
```

## Patch 2 — Add skill manifest and export script

```text
scripts/agent/validate-skills.py
scripts/agent/export-skills.py
skills.manifest.json
```

Outcome:

```text
Skills remain canonical under `skills/`, but harness-native exports can be generated.
```

## Patch 3 — Add common guard scripts

```text
scripts/agent/ar-preflight.py
scripts/agent/ar-resolve-json.py
scripts/agent/ar-stop-check.py
scripts/agent/validate-execution-context.py
scripts/agent/package-safety-check.py
```

Outcome:

```text
Harnesses call shared scripts instead of each reimplementing Agents Remember logic.
```

## Patch 4 — Add standard harness adapters

```text
harnesses/codex/
harnesses/claude-code/
harnesses/vscode/
harnesses/cursor/
harnesses/opencode/
harnesses/hermes/
harnesses/pi/
```

Outcome:

```text
Every supported harness has a documented adapter with setup, policy mapping, and limitations.
```

## Patch 5 — Add examples and installer

```text
examples/<harness>/
scripts/agent/setup-harness.py
```

Outcome:

```text
Users can scaffold their harness without hand-copying files.
```

## Patch 6 — Add CI

```text
.github/workflows/validate.yml
```

Checks:

```text
python scripts/agent/validate-skills.py
python scripts/agent/validate-harness-config.py
python scripts/agent/package-safety-check.py --self-test
python -m py_compile $(find scripts -name '*.py')
```

Outcome:

```text
Repo professionalism is enforced continuously.
```

---

# Recommended first implementation slice

Start with the portable mechanics, not with all harness adapters at once.

```text
1. Add `policy/default-harness-policy.json`.
2. Add `skills.manifest.json` generation.
3. Add `scripts/agent/export-skills.py`.
4. Add Codex + OpenCode adapters first because `.agents/skills` gives high reuse.
5. Add Claude Code adapter next because it needs `.claude/skills` and permissions.
6. Keep the existing VS Code workspace approach, then add VS Code hooks/agents as the IDE adapter.
7. Add Pi adapter once permission/path extensions are designed.
8. Add Hermes adapter as orchestration guidance and worker-profile examples.
```

Why this order:

```text
Codex/OpenCode/Pi can benefit from `.agents/skills` quickly.
Claude Code needs a `.claude` export and permission setup.
VS Code already has working workspace skill loading.
Hermes is higher-level orchestration and should consume the mature policy model.
```

---

# Source basis

The recommendations above are based on the current public documentation for the relevant harnesses and standards:

- AGENTS.md open format: https://agents.md/
- Agent Skills open format: https://agentskills.io/
- Codex `AGENTS.md`: https://developers.openai.com/codex/guides/agents-md
- Codex skills: https://developers.openai.com/codex/skills
- Codex approvals and sandboxing: https://developers.openai.com/codex/agent-approvals-security
- Codex hooks: https://developers.openai.com/codex/hooks
- Codex configuration: https://developers.openai.com/codex/config-reference
- Claude Code memory and `CLAUDE.md`: https://code.claude.com/docs/en/memory
- Claude Code skills: https://code.claude.com/docs/en/skills
- Claude Code permissions: https://code.claude.com/docs/en/permissions
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- Claude Code subagents: https://code.claude.com/docs/en/sub-agents
- VS Code AI customization overview: https://code.visualstudio.com/docs/copilot/customization/overview
- VS Code custom instructions: https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- VS Code Agent Skills: https://code.visualstudio.com/docs/copilot/customization/agent-skills
- VS Code custom agents: https://code.visualstudio.com/docs/copilot/customization/custom-agents
- VS Code hooks: https://code.visualstudio.com/docs/copilot/customization/hooks
- Cursor rules: https://cursor.com/docs/rules
- Cursor skills: https://cursor.com/docs/skills
- Cursor hooks: https://cursor.com/docs/hooks
- Cursor agent best practices: https://cursor.com/blog/agent-best-practices
- Cursor plugins/marketplace: https://cursor.com/blog/marketplace
- OpenCode rules: https://opencode.ai/docs/rules/
- OpenCode skills: https://opencode.ai/docs/skills/
- OpenCode permissions: https://opencode.ai/docs/permissions/
- OpenCode agents: https://opencode.ai/docs/agents/
- OpenCode tools: https://opencode.ai/docs/tools/
- Hermes documentation: https://hermes-agent.nousresearch.com/docs/
- Hermes context files: https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files
- Hermes skills system: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- Hermes tools and toolsets: https://hermes-agent.nousresearch.com/docs/user-guide/features/tools
- Hermes security: https://hermes-agent.nousresearch.com/docs/user-guide/security
- Hermes hooks: https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks
- Pi.dev homepage and concepts: https://pi.dev/
- Pi skills docs: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md
- Pi extensions docs: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md

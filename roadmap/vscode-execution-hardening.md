# Agents Remember — Clean Skill Base and VS Code Execution Hardening

## Purpose

This document reframes the VS Code hardening work around a cleaner base:

- No compatibility wrappers for malformed skill names.
- No duplicate VS Code-specific skill copies.
- Fix the canonical skill tree so the repo itself is syntactically correct.
- Then add VS Code hardening as a first-class harness layer.

The goal is a professional repo whose portable pieces work across harnesses and whose VS Code-specific execution controls are explicit, isolated, and maintainable.

---

## 1. Clean base fixes first

### 1.1 Updated assumption

The repo already uses a VS Code workspace file to load the skill tree through `chat.agentSkillsLocations`.

That means the issue is **not** that VS Code cannot discover the skills. The issue is that the canonical skill packages should conform to the documented skill contract rather than relying on observed slash-command behavior.

VS Code skill packages should satisfy these constraints:

- The skill `name` should contain only lowercase letters, numbers, and hyphens.
- The skill `name` should match the parent directory name.
- Invalid names can fail to load or behave inconsistently.
- Non-standard skill locations can be loaded through workspace settings such as `chat.agentSkillsLocations`.

### 1.2 Design Philosophy

The clean repo principle is:

```text
Portable semantic layer first.
Harness-specific execution layer second.
No compatibility shims for avoidable syntax defects.
```

That means:

```text
AGENTS.md
  Portable operating policy.

runtime/skills/
  Canonical portable skills.
  These should be valid as real skill packages.

harnesses/vscode/
  VS Code-specific agents, hooks, scripts, and policy.
  These should not paper over malformed canonical files.

examples/vscode/
  Reusable workspace examples.
```

The repo should not depend on accidental tolerance from a specific harness. If a skill is malformed, fix the skill. If a harness needs extra enforcement, add a harness module.

---

## 2. Normalize the canonical skill tree

### 2.1 Nest phase skill packages under the heavy workflow

The `P-*` skill packages are not standalone workflow entrypoints. They are the phase-local implementation packages for `W-01-heavy-task-workflow`, so their source location should sit under that workflow:

```text
runtime/skills/P-00-creation/C-01-task-folder-creation
-> runtime/skills/W-01-heavy-task-workflow/skills/P-00-creation/C-01-task-folder-creation
   name: c-01-task-folder-creation

runtime/skills/P-01-research/R-01-requirements-normalization
-> runtime/skills/W-01-heavy-task-workflow/skills/P-01-research/R-01-requirements-normalization
   name: r-01-requirements-normalization

runtime/skills/P-01-research/R-02-input-documentation
-> runtime/skills/W-01-heavy-task-workflow/skills/P-01-research/R-02-input-documentation
   name: r-02-input-documentation

runtime/skills/P-02-synthesis/S-01-requirement-question-framing
-> runtime/skills/W-01-heavy-task-workflow/skills/P-02-synthesis/S-01-requirement-question-framing
   name: s-01-requirement-question-framing

runtime/skills/P-02-synthesis/S-02-architecture-question-framing
-> runtime/skills/W-01-heavy-task-workflow/skills/P-02-synthesis/S-02-architecture-question-framing
   name: s-02-architecture-question-framing

runtime/skills/P-03-design/D-01-requirement-clarification
-> runtime/skills/W-01-heavy-task-workflow/skills/P-03-design/D-01-requirement-clarification
   name: d-01-requirement-clarification

runtime/skills/P-03-design/D-02-architecture-deliberation
-> runtime/skills/W-01-heavy-task-workflow/skills/P-03-design/D-02-architecture-deliberation
   name: d-02-architecture-deliberation

runtime/skills/P-03-design/D-03-output-dry-run-planning
-> runtime/skills/W-01-heavy-task-workflow/skills/P-03-design/D-03-output-dry-run-planning
   name: d-03-output-dry-run-planning

runtime/skills/P-03-design/D-04-output-documentation
-> runtime/skills/W-01-heavy-task-workflow/skills/P-03-design/D-04-output-documentation
   name: d-04-output-documentation

runtime/skills/P-04-planning/P-01-implementation-planning
-> runtime/skills/W-01-heavy-task-workflow/skills/P-04-planning/P-01-implementation-planning
   name: p-01-implementation-planning

runtime/skills/P-05-implementation/I-01-implementation
-> runtime/skills/W-01-heavy-task-workflow/skills/P-05-implementation/I-01-implementation
   name: i-01-implementation

runtime/skills/P-99-review/R-01-adversarial-review
-> runtime/skills/W-01-heavy-task-workflow/skills/P-99-review/R-01-adversarial-review
   name: r-01-adversarial-review
```

### 2.2 Lowercase all skill frontmatter names

Every `SKILL.md` frontmatter `name` should be lowercase and should use the package identifier, even when the package folder keeps the uppercase taxonomy prefix for readability:

```text
runtime/skills/U-01-core-skills/C-00-initialize-memory-repo/SKILL.md
  name: c-00-initialize-memory-repo

runtime/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/SKILL.md
  name: c-05-create-or-update-onboarding-files

runtime/skills/W-01-heavy-task-workflow/SKILL.md
  name: w-01-heavy-task-workflow

runtime/skills/W-02-light-task-workflow/SKILL.md
  name: w-02-light-task-workflow
```

This also fixes the two direct stale-name mismatches:

```text
runtime/skills/W-01-heavy-task-workflow/skills/P-01-research/R-02-input-documentation/SKILL.md
  name: r-02-input-documentation

runtime/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/SKILL.md
  name: c-05-create-or-update-onboarding-files
```

### 2.3 Do not create empty phase skill folders

Do not create a source skill package for `P-06-closing` until it contains a real skill. Empty phase folders in a public or shared repo look accidental.

### 2.4 Keep source package paths separate from task artifact paths

There is an important distinction between source skill package paths and generated task artifact paths.

```text
Source skill package path:
runtime/skills/W-01-heavy-task-workflow/skills/P-03-design/D-04-output-documentation

Task artifact path:
<task-folder>/P-03-design/D-04-output-documentation/
```

The frontmatter skill identifier must be lowercase because agent skill loaders can require lowercase names. Task artifact phase folders keep the workflow taxonomy because those are generated task outputs rather than skill identifiers.

---

## 3. Replace the WIP `r-02-input-documentation` skill frontmatter/body

The current `r-02-input-documentation` skill should not present as WIP if the folder already contains the workflow and templates.

Suggested replacement:

````md
---
name: r-02-input-documentation
description: Create or refresh task-local current-state input documentation for the approved project slice. Use when Research needs mapped existing-file documentation before synthesis, design, or planning.
---

# Input Documentation

Use this skill to create or refresh task-local input documentation under:

```text
<task-folder>/input-project-documentation/
```
````

This skill documents existing current-state behavior only. It does not update durable onboarding.

Follow the canonical workflow:

- [input-documentation-workflow.md](./input-documentation-workflow.md)

Use these templates:

- [input-documentation-template.md](./input-documentation-template.md)
- [input-documentation-overview-template.md](./input-documentation-overview-template.md)

Required modes:

1. `file-mapping` — create or update mirrored file-level current-state docs.
2. `overview-generation` — synthesize area overview docs only after mapped file docs are current.

Return the files created or updated, the evidence used, and any unresolved uncertainty.

````

---

## 4. Update the VS Code workspace skill locations

After nesting the heavy workflow phase skills, update the workspace file to point at their current source paths.

Example:

```jsonc
"chat.agentSkillsLocations": {
  "agents-remember-md/runtime/skills": true,
  "agents-remember-md/runtime/skills/U-01-core-skills": true,
  "agents-remember-md/runtime/skills/W-01-heavy-task-workflow": true,
  "agents-remember-md/runtime/skills/W-01-heavy-task-workflow/skills": true,
  "agents-remember-md/runtime/skills/W-02-light-task-workflow": true,
  "agents-remember-md/runtime/skills/W-03-chat-task-workflow": true
}
````

Do not include a `P-06-closing` source skill package until it contains a real skill.

---

## 5. Add a skill validator

Skill validity should be a repo quality gate, not a convention remembered by humans.

Add:

```text
scripts/validate_skills.py
```

The validator should check:

```text
- every SKILL.md has YAML frontmatter
- name exists
- name matches ^[a-z0-9-]{1,64}$
- name matches the parent directory
- description exists
- description <= 1024 characters
- description does not contain WIP/TODO/placeholder language
- relative markdown links resolve
- no duplicate skill names
- no empty skill phase directories
```

Suggested CI:

```yaml
name: validate

on:
  pull_request:
  push:

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python scripts/validate_skills.py
      - run: python -m py_compile $(find skills -name '*.py')
```

This makes the professional baseline enforceable.

---

## 6. Repo presentation cleanup

Before adding more execution machinery, fix obvious polish issues.

Examples found in `AGENTS.md`:

```text
sollution → solution
says says → says
onboardings → onboarding files / onboarding units
repos overview.md → repository overview.md
```

Also avoid distributing archives that contain private or noisy files.

Reject packaged archives containing:

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

Prefer packaging with:

```bash
git archive --format=zip --output agents-remember-md.zip HEAD
```

Add a packaging check script such as:

```text
scripts/package_safety_check.py
```

The point is not only security. It also improves the professional impression of the repo.

---

## 7. After syntax is clean: add a first-class VS Code harness layer

Once the canonical repo is valid, add VS Code-specific hardening under a clear harness module.

Suggested structure:

```text
harnesses/
  vscode/
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
    policy/
      default-policy.json

examples/
  vscode/
    agents-remember.code-workspace.example
```

This keeps responsibilities clean:

```text
runtime/skills/
  Portable canonical skills.

AGENTS.md
  Portable repo policy.

harnesses/vscode/
  VS Code-specific execution hardening.

examples/vscode/
  Reusable workspace configuration examples.
```

The goal is not to duplicate the portable system. The goal is to make VS Code enforce it.

---

## 8. Add VS Code custom agents for role separation

Add three VS Code custom agents:

```text
harnesses/vscode/agents/ar-planner.agent.md
harnesses/vscode/agents/ar-builder.agent.md
harnesses/vscode/agents/ar-reviewer.agent.md
```

### 8.1 AR Planner

Purpose:

```text
- Resolve active repo.
- Run drift/preflight.
- Read onboarding and source.
- Produce a plan.
- Do not edit files.
```

Suggested file:

```md
---
name: AR Planner
description: Read-only Agents Remember planner. Resolves onboarding, checks drift, and proposes a plan without editing files.
tools: ["search/codebase", "search/usages", "read/problems", "web/fetch"]
handoffs:
  - label: Start approved implementation
    agent: AR Builder
    prompt: Implement only the approved plan. Do not broaden scope. Update onboarding in the same pass as source changes.
    send: false
---

# AR Planner

Follow `AGENTS.md`.

Before planning:

1. Resolve the active target repo.
2. Resolve the active memory/onboarding root.
3. Run onboarding drift detection.
4. Read verified onboarding beside source files.
5. If onboarding is missing, stale, orphaned, or unverifiable, report that first.

You must not edit files.

Output:

- resolved target repo
- resolved memory/onboarding root
- drift status
- files inspected
- proposed plan
- explicit approval request before implementation
```

### 8.2 AR Builder

Purpose:

```text
- Start only from an approved plan.
- Edit source.
- Update onboarding in the same pass when relevant.
- Run checks.
- Do not commit, push, deploy, or broaden scope.
```

Suggested file:

```md
---
name: AR Builder
description: Approved implementation agent. Edits source and corresponding onboarding only after explicit approval.
tools: ["search/codebase", "search/usages", "read/problems", "edit", "execute"]
handoffs:
  - label: Review completed work
    agent: AR Reviewer
    prompt: Review the implementation, verify onboarding consistency, and run applicable checks. Do not broaden the task.
    send: false
---

# AR Builder

You implement only an approved plan.

Rules:

- Do not begin from a vague request; require an approved plan or explicit implementation instruction.
- Re-run the resolver if paths are unclear.
- Update source and onboarding in the same pass when onboarding is affected.
- Do not commit, push, deploy, delete branches, prune worktrees, or edit `.env`.
- Do not write durable memory outside the active resolved memory root.
- After changes, run applicable verification commands.
```

### 8.3 AR Reviewer

Purpose:

```text
- Inspect diff.
- Check onboarding consistency.
- Check protected paths.
- Verify checks.
- Do not broaden scope.
```

Suggested file:

```md
---
name: AR Reviewer
description: Read-mostly verifier for Agents Remember work. Checks diff, onboarding consistency, drift, and policy violations.
tools: ["search/codebase", "search/usages", "read/problems", "execute"]
---

# AR Reviewer

Review the completed work.

Check:

- source diff
- onboarding diff
- resolver output
- drift detector output
- protected path changes
- unexpected `.env`, `.git`, generated, vendored, or external repo edits
- verification commands attempted and results

Do not commit or push.
```

Keep these files compact. They should encode role boundaries, not restate the entire repo doctrine.

---

## 9. Add VS Code hooks as executable policy

This is the real hardening layer.

Prompt instructions express what the agent should do. Hooks enforce what the agent may do.

Add:

```text
harnesses/vscode/hooks/ar-guard.json
harnesses/vscode/scripts/ar_vscode_session_context.py
harnesses/vscode/scripts/ar_vscode_pretool_guard.py
harnesses/vscode/scripts/ar_vscode_stop_check.py
harnesses/vscode/policy/default-policy.json
```

### 9.1 Hook configuration

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "python3 agents-remember-md/harnesses/vscode/scripts/ar_vscode_session_context.py",
        "windows": "py -3 agents-remember-md\\harnesses\\vscode\\scripts\\ar_vscode_session_context.py",
        "timeoutSec": 5
      }
    ],
    "PreToolUse": [
      {
        "type": "command",
        "command": "python3 agents-remember-md/harnesses/vscode/scripts/ar_vscode_pretool_guard.py",
        "windows": "py -3 agents-remember-md\\harnesses\\vscode\\scripts\\ar_vscode_pretool_guard.py",
        "timeoutSec": 5
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "python3 agents-remember-md/harnesses/vscode/scripts/ar_vscode_stop_check.py",
        "windows": "py -3 agents-remember-md\\harnesses\\vscode\\scripts\\ar_vscode_stop_check.py",
        "timeoutSec": 10
      }
    ]
  }
}
```

### 9.2 `PreToolUse` deny policy

Deny operations that should never depend on model obedience:

```text
- git push
- git reset --hard
- git clean
- git worktree remove
- git worktree prune
- rm -rf
- edits to .git/
- edits to .env except .env.example
- edits to node_modules/
- edits to dist/
- edits to build/
- edits to coverage/
- edits to vendor/
- edits outside active code, memory, or task roots
```

### 9.3 `PreToolUse` ask policy

Ask before operations that may be valid but require explicit approval:

```text
- git commit
- git rebase
- dependency installs/removals
- migrations
- network fetches
- deployment-like commands
- writes to durable onboarding/memory unless the active task contract allows them
```

### 9.4 Minimal `PreToolUse` guard skeleton

```python
#!/usr/bin/env python3
import json
import re
import sys

payload = json.load(sys.stdin)

tool_name = (payload.get("tool_name") or "").lower()
tool_input = payload.get("tool_input") or {}

DENY_COMMANDS = [
    (r"\brm\s+-rf\b", "Blocked destructive recursive delete."),
    (r"\bgit\s+push\b", "git push is blocked for agents."),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard is blocked."),
    (r"\bgit\s+clean\b", "git clean is blocked."),
    (r"\bgit\s+worktree\s+(remove|prune)\b", "git worktree remove/prune is blocked."),
]

ASK_COMMANDS = [
    (r"\bgit\s+commit\b", "Committing requires explicit closeout approval."),
    (r"\bgit\s+rebase\b", "Rebase requires explicit approval."),
    (r"\b(npm|pnpm|yarn|bun|pip|uv|cargo)\s+(install|add|remove)\b", "Dependency changes require approval."),
    (r"\b(migrate|migration)\b", "Migrations require approval."),
    (r"\b(curl|wget)\b", "Network fetches require approval."),
]

PROTECTED_PATH_PATTERNS = [
    r"(^|/)\.git(/|$)",
    r"(^|/)\.env($|\.)",
    r"(^|/)node_modules(/|$)",
    r"(^|/)dist(/|$)",
    r"(^|/)build(/|$)",
    r"(^|/)coverage(/|$)",
    r"(^|/)vendor(/|$)",
]


def respond(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def extract_paths(value):
    paths = []
    if isinstance(value, dict):
        for key, inner in value.items():
            if key.lower() in {"path", "filepath", "file", "filename", "target", "uri"} and isinstance(inner, str):
                paths.append(inner)
            else:
                paths.extend(extract_paths(inner))
    elif isinstance(value, list):
        for item in value:
            paths.extend(extract_paths(item))
    return paths


command = tool_input.get("command") or tool_input.get("cmd") or ""

if "terminal" in tool_name or "execute" in tool_name or command:
    for pattern, reason in DENY_COMMANDS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            respond("deny", reason)

    for pattern, reason in ASK_COMMANDS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            respond("ask", reason)

for raw_path in extract_paths(tool_input):
    normalized = raw_path.replace("\\", "/")

    if normalized.endswith(".env.example"):
        continue

    for pattern in PROTECTED_PATH_PATTERNS:
        if re.search(pattern, normalized):
            respond("deny", f"Protected path blocked: {raw_path}")

print(json.dumps({"continue": True}))
```

### 9.5 `Stop` hook policy

The `Stop` hook should block false completion when:

```text
- resolver/preflight did not run
- drift check did not run
- source changed but onboarding consistency was not checked
- protected files changed
- verification was skipped without explanation
- writes happened outside the active worktree/memory/task roots
```

The `Stop` hook should avoid loops by checking whether a stop hook is already active.

---

## 10. Protect hook and policy files themselves

Hook scripts execute with the same authority as the editor process, so they must be treated as protected infrastructure.

Require explicit approval for edits to:

```text
harnesses/vscode/hooks/**
harnesses/vscode/scripts/**
harnesses/vscode/policy/**
scripts/validate_*.py
scripts/package_safety_check.py
```

The guard should not let an agent silently weaken the guard.

---

## 11. Make the C-08 resolver execution-ready

The `c-08-ar-coordination-context-resolver` skill should become the source of truth for execution-layer preflight.

Hooks need structured status, not prose.

Example resolver output:

```json
{
  "status": "uninitialized",
  "topology": "internal",
  "code_repository_root": "/path/to/repo",
  "coordination_root": "/path/to/repo/ar-coordination",
  "coordination_root_exists": false,
  "onboarding_root": "/path/to/repo/ar-coordination/onboarding",
  "onboarding_root_exists": false,
  "settings_path": "/path/to/repo/ar-coordination/system/settings.md",
  "settings_path_exists": false,
  "recommended_next_skill": "c-00-initialize-memory-repo"
}
```

Then the VS Code `SessionStart` hook can inject a clear state:

```text
Resolved repo: /path/to/repo
Coordination status: uninitialized
Recommended action: run c-00-initialize-memory-repo
```

That is cleaner than letting downstream scripts fail with missing-directory errors.

---

## 12. Make worktree hardening contract-based

Once the worktree layer lands, the hook should not infer boundaries from chat. It should read a task execution contract.

Suggested file:

```text
ar-coordination/tasks/<task-id>/execution-context.json
```

Example:

```json
{
  "taskId": "tas-link-expand",
  "targetRepo": "device-management",
  "codeWorktree": "/home/example/worktrees/device-management/tas-link-expand",
  "memoryRoot": "/home/example/ar-coordination",
  "onboardingRoot": "/home/example/ar-coordination/onboarding/device-management",
  "coordinationRoot": "/home/example/ar-coordination/tasks/tas-link-expand",
  "allowedWriteRoots": [
    "/home/example/worktrees/device-management/tas-link-expand",
    "/home/example/ar-coordination/onboarding/device-management",
    "/home/example/ar-coordination/tasks/tas-link-expand"
  ],
  "commitAllowed": false,
  "pushAllowed": false
}
```

Then the execution model becomes mechanical:

```text
Prompt:
  Explains what the agent should do.

Skill:
  Explains how the workflow proceeds.

Execution contract:
  States what roots are active.

Hook:
  Enforces what the agent may touch.
```

That is the professional version of the current escape-hatch approach.

---

## 13. Suggested patch order

### Patch 1: Normalize skill packages

```text
chore(skills): normalize skill names and package paths
```

Scope:

```text
- lowercase skill directories
- fix frontmatter names
- fix r-02 and c-05 mismatches
- remove empty p-06-closing
- document skill-package path casing vs task-artifact path casing
```

### Patch 2: Add skill validation

```text
chore(skills): add skill validation
```

Scope:

```text
- scripts/validate_skills.py
- CI validation
- Python compile checks for bundled scripts
```

### Patch 3: Add clean VS Code workspace example

```text
docs(vscode): add clean workspace example
```

Scope:

```text
- examples/vscode/agents-remember.code-workspace.example
- no absolute local paths
- no user-specific auto-approval
- normalized skill paths
```

### Patch 4: Add first-class VS Code harness module

```text
feat(vscode): add first-class VS Code harness module
```

Scope:

```text
- harnesses/vscode/agents
- harnesses/vscode/instructions
- harnesses/vscode/hooks
- harnesses/vscode/scripts
- harnesses/vscode/policy
```

### Patch 5: Add resolver/preflight status and worktree-aware execution context

```text
feat(execution): add resolver preflight and worktree-aware execution context
```

Scope:

```text
- c-08 emits structured status/existence fields
- hooks read execution-context.json
- protected-root policy becomes machine-checkable
```

---

## 14. Target end state

```text
AGENTS.md
  Portable operating policy.

runtime/skills/
  Canonical lowercase portable skills.

scripts/validate_skills.py
  Repo quality gate.

harnesses/vscode/
  First-class VS Code execution layer.

examples/vscode/
  Reusable workspace configuration.

ar-coordination/tasks/<task-id>/execution-context.json
  Active worktree/memory/write-boundary contract.

hooks
  Deterministic enforcement.
```

---

## 15. Core rule

Use the right layer for each kind of control.

| Need                                       | Best layer                            |
| ------------------------------------------ | ------------------------------------- |
| The agent should prefer something          | Prompt / `AGENTS.md`                  |
| The agent needs repo context               | `AGENTS.md` / onboarding              |
| The agent needs a repeatable process       | Skill                                 |
| The agent must not do something            | Hook / permission / sandbox           |
| The agent must prove completion            | Stop hook / validation script / CI    |
| The repo should look professional          | Validator / CI / packaging discipline |
| The active write boundary must be enforced | Execution contract + hook             |

Final architecture:

```text
AGENTS.md        = portable policy
runtime/skills/          = portable procedures
harnesses/vscode = VS Code execution layer
examples/vscode  = reusable workspace examples
scripts/         = validation and packaging gates
worktree contract = active authority boundary
hooks            = deterministic enforcement
```

---

## Source notes

Relevant VS Code documentation areas to verify against when implementing:

- VS Code Agent Skills: https://code.visualstudio.com/docs/copilot/customization/agent-skills
- VS Code Custom Instructions: https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- VS Code Custom Agents: https://code.visualstudio.com/docs/copilot/customization/custom-agents
- VS Code Hooks: https://code.visualstudio.com/docs/copilot/customization/hooks
- VS Code Copilot Settings: https://code.visualstudio.com/docs/copilot/reference/copilot-settings

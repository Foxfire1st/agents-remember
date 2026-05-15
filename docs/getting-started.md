# Getting Started

This guide sets up Agents Remember in a workspace that contains one or more code repositories.

The short version is:

1. install the runtime into `ar-coordination`
2. expose installed skills to your agent harness
3. point workspace instructions at `ar-coordination/AGENTS.md`
4. initialize memory for a target repository
5. bootstrap initial onboarding

## Example Workspace

```text
projects/
  AGENTS.md
  agents-remember-md/
  my-app/
  ar-coordination/
```

`agents-remember-md` is the source checkout. `ar-coordination` is the installed runtime and local coordination area. `my-app` is the repository you want agents to work on.

## Install The Runtime

From the workspace root:

```bash
python3 agents-remember-md/installer/install-runtime.py ./ar-coordination
```

The installer reconciles package-owned runtime files into `ar-coordination`: installed `AGENTS.md` templates, skills, and scripts. It may create missing runtime folders, but it does not create memory repos, run onboarding bootstrap, overwrite live settings, or modify tasks, notes, worktrees, memory content, or temporary artifacts.

To preview an install:

```bash
python3 agents-remember-md/installer/install-runtime.py ./ar-coordination --dry-run
```

Benchmark fixtures are optional and are not installed by default. Install or refresh them with:

```bash
python3 agents-remember-md/installer/install-runtime.py ./ar-coordination --include-benchmarks
```

The benchmark package is idempotent. Reinstalling refreshes package-owned benchmark cases, templates, prompts, and author results while preserving local outputs under `ar-coordination/benchmarks/user-runs/`. Benchmark preparation generates resettable case workspaces and clones pinned code and memory repositories into them.

## Expose Skills To Your Harness

Some agent tools can read skills from a repository in the workspace. Others require skills to live in a specific folder. Use the installed adapter instead of copying skill folders by hand.

For recursive skill scanners such as Codex and Claude Code:

```bash
./ar-coordination/scripts/install-skills.sh \
  --install-root ./.agents/skills
```

This creates one namespace symlink:

```text
.agents/skills/agents-remember-md -> ar-coordination/skills
```

For direct skill-folder scanners such as Cursor or Windsurf:

```bash
./ar-coordination/scripts/install-skills.sh \
  --install-root ./.windsurf/skills \
  --layout flat
```

This creates one symlink per skill using the lowercase `name` from each `SKILL.md`.

See the harness-specific pages under [install](install/) for exact locations.

## Add Workspace Instructions

At the root of the shared projects folder, add the instruction file your harness reads. For Codex, Pi.dev, Windsurf, and many compatible tools, use `AGENTS.md`:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Claude Code uses the same pattern in `CLAUDE.md`. Cursor can use a project rule. OpenClaw usually uses the `AGENTS.md` file in its dedicated agent workspace.

## Initialize Memory

Ask the agent to run `C-00-initialize-memory-repo` for the target code repository.

By default this creates repo-local internal memory:

```text
my-app/
  ar-memory/
    onboarding/
    docs/
    system/
      settings.md
      settings.json
      sources.md
      tools.md
```

Use external memory only when you intentionally want a separate memory repo under `ar-coordination/memory-repos/ar-<repo>/`.

## Bootstrap Onboarding

Ask the agent to run `C-03-repo-bootstrap` for the target repository. A thin `overview.md` is enough to start. Larger repositories can grow route-local overviews and file-level onboarding as work touches new areas.

## Start Working

Normal tasks start in chat mode. The agent should:

1. resolve the repository context with `C-08-ar-coordination-context-resolver`
2. run `C-02-onboarding-drift-detection` before planning against onboarding
3. read the relevant onboarding beside the code
4. propose changes and wait for approval
5. implement approved work
6. update onboarding through `C-05-create-or-update-onboarding-files`

Escalate to [light or heavy workflows](workflows.md) when the task needs a durable plan or stronger review gates.

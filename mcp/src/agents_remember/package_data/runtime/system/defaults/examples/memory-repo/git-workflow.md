# Git Workflow Example

Copy or rename this file to the memory layer's `system/git-workflow.md` when a repo lands changes
through a **gated branch** (e.g. a PR-gated `main`). The coordinator routes "read `git-workflow.md`
when present," so this file is where a repo states *how its changes reach the protected branch*.
PR-gating and the spear branch are per-repo — fill in the `<placeholders>` for yours and delete what
doesn't apply.

---

## Spine

- **Spear branch = `<your spear, e.g. main>`.** Is it protected / gated? `<yes — never push directly,
  land via PR>` or `<no — direct pushes allowed>`. State it explicitly so agents don't guess.
- Work branches are cut from the spear: **`feat/<slug>`** (features) and **`fix/<slug>`** (fixes).
- **Worktree-backed?** `<yes — chat and task both>` keeps external memory consistent: memory parks on
  the worktree memory branch and lands on the spear via the **`c-11-memory-carryover-from-branch` skill** *after* the code lands.
  Repos with internal/disabled memory can relax this.

---

## When you need an issue + PR

| Change kind | Issue? | PR to spear? |
| --- | --- | --- |
| `feat` / `fix` / `chore` | `<gh issue create, if your repo tracks issues>` | `<yes, if gated>` |
| pure research (read-only, no source/memory change) | no | no |

---

## The landing flow

Adapt to your repo; a gated-`main` repo typically does:

1. `<gh issue create>` for feat/fix/chore (skip for pure research).
2. Cut `feat/<slug>` | `fix/<slug>` from the spear.
3. The `c-09-git-worktree-manager` skill creates a worktree on that branch (task adds `task.md`; chat doesn't).
4. Work in the worktree; memory parks on the worktree memory branch.
5. **Commit gate (human)** — nothing is committed before explicit developer approval (the `c-12-closeout` skill
   worktree closeout preview first).
6. **Push gate (human — one question)** — after commit approval, a single "push?" approval hands the
   tail to the agent.
7. Agent owns the tail: **push branch → `gh pr create` (target spear) → checks green →
   `gh pr merge --delete-branch`.**
8. The `c-09-git-worktree-manager` skill handles closeout + worktree/provider cleanup.
9. The `c-11-memory-carryover-from-branch` skill carries parked memory to spear-memory, run against the merged spear. Carryover maps the
   ledger to the actual spear HEAD, **including a PR merge commit** even when nothing else needs
   carrying, so the next worktree bases off the merged spear without a manual reconciliation.

### Gates, in one line

`commit approval (human)` → `push approval (human, one question)` → agent owns `push → PR → checks →
merge → cleanup → carryover`. Merge is not its own gate — only timing.

---

## PR merge: prefer a merge commit over squash

- **Default: merge commit** — preserves a branch's distinct commits on the spear, so a PR that
  bundles several self-contained changes (each with its own onboarding + ledger mapping) stays
  bisectable and traceable.
- **Squash** is for messy WIP branches full of "fix typo" commits where the individual history has no
  value. Don't squash a bundle of distinct features just to get one line.
- Don't rebase-merge in a way that rewrites already-pushed history.

---

## Commit and push quality gates

If the repo configures an integrated acceptance adapter and hooks:

- **Pull-request CI** — `<your CI workflow>` always runs deterministic non-test validation for the
  PR; ordinary branch pushes do not launch a duplicate acceptance run.
- **Local pre-commit** — `<.githooks/pre-commit>` runs deterministic fast checks before an ordinary
  commit.
- **Leaf closeout** — the closeout apply path runs change-set-scoped acceptance exactly once before creating
  the leaf code commit, even when local hooks are not configured.
- **Leaf integration** — lands the already-certified leaf commit and does not rerun acceptance.
- **Master integration** — runs full-repository acceptance exactly once before integrating the master into its
  parent or super branch.
- **Local pre-push** — `<.githooks/pre-push>` may repeat deterministic non-test diagnostics, never
  acceptance. Enable it once per clone with `<./setup-hooks.sh, if present>`;
  `git push --no-verify` bypasses those local diagnostics intentionally.

Point at the acceptance adapter itself in [`tools.md`](tools.md); keep every gate calling the
project-owned implementation, not a hand-picked subset. Any repository-defined risk threshold must
be enforced by the default accepted invocation, not by an optional strictness flag.

---

## Release And Changelog Convention

Fill in only if the repo publishes releases. State:

- **Where release notes live** (`<GitHub Releases — no CHANGELOG.md>` is one good convention).
- **Tag scheme** (`<e.g. mcp-vX.Y.Z>`) and what it triggers (`<publish workflow → registry>`).
- **Version-bump locations that must stay in sync** — list the exact files (e.g. `<pyproject
  version>`, `<SERVER_VERSION fallback>`, `<README status line>`). If a test asserts the version
  **dynamically**, note it is not a bump location and must stay dynamic.
- **Release commit subject** convention.
- **End-to-end release flow** — for a gated spear: land the version bump via PR, then **tag the
  merged commit**, confirm the publish workflow, then create the release notes.

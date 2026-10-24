# Task Publication Example

Copy this file to the memory layer's `system/task-publication.md`. It is where a repo states *how its
changes land and get published*: the landing workflow it runs, its branch policy and parent-branch
edge, the pull-request publication steps, and the release/tag flow. The coordinator routes "read
`task-publication.md` when present," so a repo that fills this in is answered from here rather than
guessed at.

## Landing Workflow Setting

The workflow itself is a machine-read setting, not prose. `system/settings.json` carries

```json
"landing": { "workflow": "pull-request" }
```

at the root, and `local` is what a missing key means. The value is read by `parse_landing_settings`,
which refuses any spelling other than those two by name, so a typo fails loudly instead of landing
the repo the wrong way. `local` is the behavior every repository had before the setting existed: the
integration moves the master line. `pull-request` publishes each leaf landing as a merged pull
request and lets the master chain proof admit that one merge — the merge whose parents are the
recorded base and the integrated landing, and whose tree is the landing's.

The rest of this file is the prose half: the branch policy an agent must discover before it starts,
and the publication steps it runs at the end. PR-gating and the spear branch are per-repo — fill in
the `<placeholders>` for yours and delete what doesn't apply.

---

## Spine

- **Spear branch = `<your spear, e.g. main>`.** Is it protected / gated? `<yes — never push directly,
  land via PR>` or `<no — direct pushes allowed>`. State it explicitly so agents don't guess.
- Work branches are cut from the spear: **`feat/<slug>`** (features) and **`fix/<slug>`** (fixes).
- **Worktree-backed?** `<yes — chat and task both>` keeps external memory consistent: memory parks on
  the worktree memory branch and lands on the spear via the **`c-10-adopt-memory-baseline` skill** *after* the code lands.
  Repos with `disabled` memory can relax this.

---

## When you need an issue + PR

| Change kind | Issue? | PR to spear? |
| --- | --- | --- |
| `feat` / `fix` / `chore` | `<gh issue create, if your repo tracks issues>` | `<yes, if gated>` |
| pure research (read-only, no source/memory change) | no | no |

---

## The landing flow (the `local` workflow)

This is the recipe for a repository that lands locally: the merge *is* the landing. A repository set
to `pull-request` lands through the AR integration and then publishes - see the next section; the
steps below do not apply to it, and its squash preference is wrong there.

Adapt to your repo; a gated-`main` repo typically does:

1. `<gh issue create>` for feat/fix/chore (skip for pure research).
2. Cut `feat/<slug>` | `fix/<slug>` from the spear.
3. The `w-02-light-task-workflow` skill creates a worktree on that branch (task adds `task.md`; chat doesn't).
4. Work in the worktree; memory parks on the worktree memory branch.
5. **Commit gate (human)** — nothing is committed before explicit developer approval (the `w-02-light-task-workflow` skill
   worktree closeout preview first).
6. **Push gate (human — one question)** — after commit approval, a single "push?" approval hands the
   tail to the agent.
7. Agent owns the tail: **push branch → `gh pr create` (target spear) → checks green →
   `gh pr merge --delete-branch`.**
8. The `w-02-light-task-workflow` skill handles closeout + worktree/provider cleanup.
9. The `c-10-adopt-memory-baseline` skill carries parked memory into an ordinary recovery
   leaf against the merged spear. Each actual memory-content commit attributes the landed code
   HEAD. If no content needs carrying, it creates no commit. The consumer ledger is computed
   from history and never authorizes the next worktree.

### Gates, in one line

`commit approval (human)` → `push approval (human, one question)` → agent owns `push → PR → checks →
merge → cleanup → carryover`. Merge is not its own gate — only timing.

---

## Publishing a landing (the `pull-request` workflow)

Under `pull-request`, landing and publication are two events, in that order. The AR integration moves
this side's master line first; the landing is done. Publishing then makes that landing visible on the
forge without adding anything to it. Three names carry the whole rule:

| Name | What it is | Where it comes from |
| --- | --- | --- |
| **B** | the master-line position this side held immediately *before* that integration | the accepted integration authority - never a branch name, a receipt or a local memory |
| **H** | the integrated landing commit that integration produced | the same authority |
| **M** | the merge that publishes H - the forge's publication merge | the forge, read back as a commit |

M has exactly two parents, in this order: **B**, then **H**. M's tree equals H's tree, because M adds
nothing of its own. Read every step against them - the base pinned to B, the head to H, the readback
to M. B and H are facts of the integration, not of this file; if that pair cannot be read, the
repository does not publish and the leaf stays open.

1. Confirm the side is integrated and that its H differs from its B. A side whose H equals its B has
   nothing to publish.
2. Publish H onto the master line, whose position is B, and let the forge open the pull request.
3. Let the checks go green, then merge with a merge commit - never squash, never rebase-merge.
4. Read M back: two parents, B then H, and a tree equal to H's. If either disagrees, that merge is not
   this landing's publication and must not be recorded as one.
5. Pull the master line, confirm it now holds M, and close the leaf's publication only after both
   sides are published. A side already published stays as it is; an unchanged side is decided by the
   rule above, not by convenience.

---

## PR merge: prefer a merge commit over squash (the `local` workflow)

The preference below holds where the merge *is* the landing. In `pull-request` mode it does not apply:
M must keep H as its second parent with H's tree, so squash and rebase-merge are refused there.

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

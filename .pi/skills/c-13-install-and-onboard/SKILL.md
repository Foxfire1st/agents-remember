---
name: c-13-install-and-onboard
description: "After the harness starter package and MCP server are wired and the harness has restarted, run/verify runtime_install, set up the memory repo, bootstrap onboarding when scaffolding new, reach the repository's knowledge foundation through the curator's c-14-knowledge-bootstrap procedure, and configure providers to index the code and memory."
---

# c-13-install-and-onboard Install And Onboard

Take over setup **after** the developer has copied the harness starter package,
wired the Agents Remember MCP server, and restarted the harness once.

The harness package owns harness-native files: skills, hooks, rules,
instructions, MCP registration templates, and settings templates. This skill does
not install or repair those files during first-run setup. If they are missing,
stop and tell the developer to copy the correct starter package from this repo,
render the copied package with its `render-starter` script or by hand, restart
the harness, and invoke this skill again.

The MCP layer still exposes deterministic maintenance tools such as
`runtime_install()`. In the normal first-run path, no skills installation step
is used because the starter package already placed the
skills where the harness can discover them. `runtime_install()` remains the first
runtime action this skill performs because it creates or refreshes the
coordinator scaffold under `ar-coordination/`.

## When To Use

Use this skill after these prerequisites are in place:

1. The developer copied the harness-native starter package for the active
   harness into the workspace and ran that package's `render-starter` script
   with an explicit `--repo` list, or manually replaced every path, repository,
   and hook-command placeholder. The renderer infers the workspace root from the
   copied harness folder.
2. The Agents Remember MCP server is registered with the harness, typically with
   a command like:

   ```text
   uvx agents-remember-mcp@latest --config <absolute path to agents-remember-settings.json>
   ```

3. The harness was restarted once so it can load the MCP server, native skills,
   hooks, rules, and instruction files from the copied package.

If any prerequisite is missing, do not improvise a harness installer. Point the
developer at the appropriate `docs/install/<harness>.md` page, have them copy the
starter package, and resume after the restart.

## What This Skill Owns

Run this sequence in order:

0. Preflight: verify the copied package and MCP setup are usable enough to
   continue.
1. Runtime scaffold: run or verify `runtime_install()`.
2. Agentic settings: walk the developer through the orchestration defaults in
   the global settings file with built-in defaults for absent knobs.
3. Repository certification profile: author, validate, and register one repository-owned Gate 1-4
   profile for the requested repository when the developer asks for repository certification or the
   full code-quality operation. This profile is not curation, so the curator's complete
   memory-quality result neither waits on it nor substitutes for it: curation is always complete,
   while a certification profile is authored only as that requested operation and routine closeout
   and master integration do not enter this stage.
4. Memory repo: ask scaffold-new vs use-existing, unless memory already exists.
5. Bootstrap: when a new memory repo was scaffolded, hand off to
   `c-03-repo-bootstrap` (running the fixer on the cards before the baseline),
   adopt the first baseline through `c-10-adopt-memory-baseline`, then reach the
   repository's knowledge foundation through `c-14-knowledge-bootstrap`. That procedure is the curator's work, and the session
   that carries it — the taskless bootstrap seat or an instructed session before a
   task exists, or a curator opened on a task document — is stated there.
6. Providers: when providers are enabled, start/refresh indexing and verify
   readiness.

This skill orchestrates and delegates. It does not reimplement
`c-00-initialize-memory-repo`, `c-03-repo-bootstrap`,
`c-14-knowledge-bootstrap`, `c-10-adopt-memory-baseline`, context resolution, or
provider lifecycle tools.

## Stage 0 - Preflight

Before runtime or memory work, report a short pass/fail checklist. For each
failed check, give the exact fix and stop when the developer must act.

Check, in order:

1. **MCP reachable.** Confirm the MCP responds (`server_info`). If not, the harness did not load
   the server; tell
   the developer to verify the MCP registration path and restart the harness.
2. **Harness package present.** Confirm this skill is running from a
   harness-discoverable skill root and that the active harness package includes
   its native settings/instruction files. Do not create missing harness files
   here; ask the developer to recopy the package and restart.
3. **Settings sane.** Confirm `server_info` reports the expected
   `coordinationRoot`, `workspaceRoot`, allowed repositories, and providers.
   Surface the resolved absolute paths because wrong paths are the common cause
   of resolver failures. Also report whether the global agentic settings file
   (`~/.config/agents-remember/settings.json`, under `XDG_CONFIG_HOME` when set) exists and carries `paseoRuntime`.
   When `server_info` carries `openUserSettings`, that file or its folder is open to writing: tell the
   developer so and give both of its commands as they stand; change no mode yourself.
   Starter setup supplies one block; runtime_install never writes this authority.
   If missing, calculate the complete five-key block below using the installation's
   actual absolute data/state folders. Empty or unset XDG values use the home
   defaults; refuse relative overrides. Show the resolved block and its destination
   before asking to add it. Preserve other families and any explicit existing
   home/installPrefix/listen. For a legacy per-harness block, offer to move that
   complete block to this one shared file and remove its old copy after approval.
   Do not duplicate it or use a fallback. After the approved setup, retry
   runtime_install; configured=false is an incomplete host setup, not a failed
   scaffold. Stage 2 configures the agentic posture.

   The fresh block is the following, with <data> and <state> replaced by the
   actual absolute XDG_DATA_HOME/XDG_STATE_HOME or $HOME/.local/share and
   $HOME/.local/state paths. Existing explicit ports and paths take precedence.

   ```json
   {
     "paseoRuntime": {
       "installPrefix": "<data>/agents-remember/paseo/prefix",
       "home": "<state>/agents-remember/paseo",
       "listen": "127.0.0.1:8766",
       "providers": {},
       "embed": [
         {"dashboardOrigin": "http://127.0.0.1:8765", "frameBaseUrl": "http://127.0.0.1:8766"},
         {"dashboardOrigin": "http://localhost:8765", "frameBaseUrl": "http://127.0.0.1:8766"}
       ]
     }
   }
   ```
4. **Runtime state.** Check whether the coordinator scaffold already exists under
   `coordinationRoot` (`AGENTS.md`, `skills/`, `tasks/`, `memory-repos/`,
   `system/` as applicable). If it is missing or stale, Stage 1 will run
   `runtime_install()`.
5. **Provider prerequisites, only when providers are enabled.** Providers run in
   Docker. If provider diagnostics report Docker/Ollama/image problems, explain
   the gap. A developer may explicitly defer providers and continue with core
   memory setup.
6. **Topology consistency.** `external` is the only supported memory topology. Never
   let memory setup create a repo-local `ar-memory/` root: it was removed from the
   product, and a request for it is refused by name rather than substituted.
7. **Certification authority, when explicitly requested.** For a developer-requested certification
   operation, report the exact `repositories.<repo-id>.certificationProfile` value and whether that
   candidate file exists inside the repository. Routine code closeout/integration is a Git
   transaction and does not require discovering or running a certification profile.

Do not check for legacy hook prerequisites such as `jq`. Hook and instruction
files are part of the copied, rendered harness package; this skill no longer
installs them.

## Stage 1 - Runtime Scaffold

Run or verify:

```text
runtime_install()
```

Use `runtime_install(dry_run=true)` only when the developer asks for a preview or
when local workflow policy requires one. Otherwise apply the runtime scaffold so
setup can proceed.

The result's `host` part reports the build-pinned host, whole dependency lock and
checked product Node. Report `ok`, `changed`, version/home/listen, Node and errors.
A failed host part sets the whole result's ok=false while earlier scaffold
results stand. Report its code/step/message/detail and repair that host step.
`restartRequired` means the running host was preserved before a required
transition; the developer chooses a terminal outside the host for explicit
`paseo provision`. It can end sessions, running turns and permission prompts.
A dry run downloads nothing, runs no npm, writes no host settings and starts
nothing. No separate host provision is an install step.

After it runs, verify the coordinator root exists and contains the expected
package-owned runtime files. `runtime_install()` may also prepare provider
runtime assets when providers are enabled, but it does not create memory repos,
bootstrap onboarding, or start provider indexing.

Do not install skills as part of first-run setup. The harness starter
package already carries the skills.

## Stage 2 - Agentic Settings: Interview The Developer

An existing `<coordinationRoot>/system/settings.json` requires the explicit product
upgrade before the server can load settings. Preview `agents-remember upgrade-user-settings
--coordination-root <absolute root> --dry-run`, then run it without `--dry-run` to move
the entire file once. An old-only or both-file state refuses and names both paths;
there is no old-location reader. Never migrate the MCP `--config` file or a repository
or memory-repository settings file. In a sandbox, use its own user configuration directory.

Starter/setup supplies the GLOBAL settings file at
`~/.config/agents-remember/settings.json` (under `XDG_CONFIG_HOME` when set);
`runtime_install()` never writes it. Absent agentic knobs retain their documented
defaults: all-human gate delegation, the standard three-party-loop
defaults, no concurrency caps, no spawn harness preference. This stage turns
those defaults into the developer's actual posture so the intended workflows
run on ANY harness. The schema reference is `docs/reference/settings-json.md`
(Agentic Settings); unknown `orchestration.*` keys fail loud, so write only
documented keys.

Walk the developer through the four knob families and edit the global file with
their answers (leave a family at its built-in default when they have no
preference):

1. **Gate delegation posture** (`orchestration.gateDelegation`; GLOBAL file only - the
   loader refuses it repo-locally and the boot snapshot reads the user file) - keep every
   gate human (`all-human`, the default), or delegate leaf gates with the
   `manager-decides-leaf-gates` policy, optional per-kind `kinds` overrides,
   and `requireReviewerVerdictAtSeams`. Say explicitly that this one key is
   boot-snapshot: a change needs an MCP/harness restart.
2. **Loop defaults** (`orchestration.loops`) - the review-round cap
   (`defaults.maxRounds`), reviewer reuse (`defaults.reviewerReuse`),
   complexity thresholds (`defaults.complexity`), and per-level loop postures
   (`perLevel`). Read per-use; edits apply on the next use, no restart.
3. **Concurrency caps** (`orchestration.concurrency`) - `maxParallelMasters`,
   `maxParallelLeaves`, `maxSubAgents`. Default: uncapped.
4. **Agent kind + role knobs** (`orchestration.spawn.harness`,
   per-role `orchestration.roles.<role>`, per-level
   `orchestration.rolesPerLevel.<level>.<role>`) - which agent kind a role start
   uses when no role/level knob supplies one, per-role
   harness/model/effort settings, and per-LEVEL settings
   (leaf|master|portfolio). Do not teach a static builtin model or effort
   vocabulary: the host's catalog names the agent kinds, their models and the
   efforts of each model, and a start checks the selection against it.
   `launchArgs`, `sessionCommands` and `promptKeywords` stay accepted but no
   code reads them in this build. The reference is
   `docs/reference/settings-json.md`.

Verify the installed instructions tell a chat without a role to answer a question inline and,
for work on a managed repository, to write the request into the sprint document and to start an
Architect with `role_start` (the call for an Architect names no document), and that they name no
removed tool.

If the developer wants to skip the interview, confirm the built-in defaults
apply and continue; tell them the file can be edited any time (picked up
per-use, except the gateDelegation restart note above).

For every role offered by the Paseo launcher, guide a new user through permission mode choice. The accepted designer, strategist and bootstrap role entries have no Paseo start route in this build; name that limit instead of inventing an options request. Read the running host's modes
and descriptions once per agent kind from the existing public `POST /api/role-launch/options`
route with `{"role":"architect","refreshCatalog":true}`. This taskless catalog read needs no
sprint, master or leaf; use the returned agent kinds to interview each launcher role. A running host/dashboard is a named setup
prerequisite: if unavailable, report it and defer this interview, without a guessed or second
catalog. Write `orchestration.roles.<role>.mode` in the user's configuration settings only after
the user chooses it. No shipped settings contain a mode. With no choice, new Claude starts state
`default` (host label Always Ask), new Codex starts state `auto` (the host's workspace-write,
on-request preset). This explicitly changes Codex starts that previously inherited a different
user configuration; existing agents retain their settings. Pi has no selectable permission mode;
task-document changes can proceed without a permission request. Describe any extension's prompts
separately, without promising that Pi asks before a change. For unknown providers or a missing named mode, no mode is
stated; describe the observed behavior, and mark unwitnessed claims as not established.

Modes, `notes`, and permission-bearing features (`plan_mode`, `auto_accept`, and unknown features)
are user/profile authority only; never put them in a repository's settings. `service_tier` and
`fast_mode` are speed features and may come from either settings layer. A role's host profile wins
field by field; user instruction text is plain text, with `<overwrite>` alone on the first nonblank
line replacing the baseline. No skill or file-path loading is performed.

Verify the workspace's `.agents-remember/role-executions/` directory exists and is ignored by Git.
Each taskless or controller role receives a per-execution scratch folder there; leaf roles receive
one under their group's real `scratch`. The handover names the exact folder.

Per-repo overrides: a code repo may carry its own `<repo>/system/settings.json`
with the same `orchestration.*` shape; repo-local leaf values override the
global file (arrays replace). Offer this only when the developer asks for
repo-specific behavior; do not create the file unprompted.

## Stage 3 - Repository Certification Profile (explicit operation only)

Run this stage only when the developer explicitly requests a repository certification or full
quality operation. Routine closeout and master integration do not enter this stage. The normative
field and authoring procedure are documented in
`docs/reference/repository-certification-profile.md`.

1. Read the repository's documented build, lint/type/format/structural checks, ordinary suite,
   suite-dependent quality, integration/E2E scenarios, runtime locks, and existing ownership
   manifests. Inspect source only to confirm those repository-owned contracts. Do not derive
   authority from a filename, executable being present, or historical success.
2. If `repositories.<repo-id>.certificationProfile` is absent, select and record one explicit
   repository-relative path consistent with that repository's configuration layout. This is not a
   conventional filename or built-in default. Add the exact path to the MCP authority settings.
3. Author the versioned profile at that path from the repository's own contract. Include explicit
   targeted local/closeout and full closeout selections over one rail catalog; declare all four
   gates, including typed not-applicable gates; and declare the exact sandbox adapter, decoder,
   artifacts, selectors, runtimes, secret policy, resource bounds, and Gate-4 teardown.
4. Compute the canonical profile digest and compile every required selection through the installed
   repository-profile loader. Repair every schema, authority, classification, graph, selection,
   artifact, adapter, or decoder finding before continuing. A missing or invalid profile is
   `certification-profile-invalid`; no Gate-1 command may start.
5. Run the smallest focused repository verification that proves its declared adapter and decoder
   implement the exact profile execution and terminal-artifact contract. Do not launch the full
   suite or clean-room scenarios merely as install diagnostics.
6. If the authority settings changed, tell the developer that the MCP/harness must restart before
   the new boot-time repository authority is live. This affects the requested certification
   operation only; it is not a curation step, and routine closeout and integration do not enter
   this stage.

Make progress autonomously from durable repository contracts. Ask the developer only when the
repository's intended rail, posture, or clean-room boundary is genuinely ambiguous. Never copy the
Agents Remember reference profile into another repository, create a default profile, install a
host executor, or add a compatibility/fallback route.

An older configured repository with no profile remains valid for reading, task recovery, onboarding,
and routine code/memory/ledger closeout. A developer-requested certification operation must fail
closed until this stage is completed.

## Stage 4 - Memory Repo: Ask Scaffold Vs Existing

Do not assume the developer wants a fresh memory repo. Ask which case applies,
unless a memory repo is already present and resolvable:

1. **Scaffold a new memory repo** - they have no existing memory for this code
   repo. Ask the foundation question before creating anything: **which code branch
   should be the foundation (spear) of this repository's memory?** Default it to the
   branch the code repository currently has checked out, say plainly why it matters
   (memory is written against that branch's state, and changing it later is a
   carryover step rather than an edit), and hand the answer to
   `c-00-initialize-memory-repo` as `initial_branch`. That skill owns the procedure,
   including the short plain-language account for a developer who skipped the
   README. It creates the external memory repo at
   `<coordination-root>/memory-repos/ar-<code-repository-name>`. Continue to
   Stage 5.
2. **Use an existing memory repo** - they already have one. Clone or checkout it
   to the resolved memory location, then adopt it as the Git-attributed baseline with
   `c-10-adopt-memory-baseline`. Its onboarding already exists, so skip Stage 5's
   onboarding half — but **do not skip Stage 5's knowledge half**: an existing
   memory repo is exactly the case that can have onboarding and no
   knowledge records, so read the knowledge state and run the foundation step
   when the location is `not-recorded`. An `unusable` location is reported with
   its code and path and repaired first: nothing is written over it.

Removed-layout note: repo-local internal memory lived inside the code repo at
`<repo>/ar-memory/`. That layout was removed from the product and is refused, not
migrated. If a checkout still carries one, report the exact path and the route out
(re-point to the external memory root and record `memory_mode: external` on the
worktree contracts, or re-initialize with `c-00-initialize-memory-repo`); never
rewrite or delete it, and never treat it as an already-present memory layer.

## First Conversion Of Unconverted Memory (MIK-R46 rule 6)

When the memory is still in the legacy format (no `knowledge/layout.json`), every
ordinary memory operation refuses it with the code `legacy-format` and names this
procedure. Reading still works: `read_ar_files` returns old-format cards as they are,
marked `legacy-format`. Convert before anything else in this skill: no baseline is
adopted and no knowledge foundation is authored on unconverted memory. Pick exactly one
case; both end on converted memory, after which the same operation is repeated and runs
the ordinary converted route.

1. **Memory with no attributed baseline** (it has no commit that a leaf could start
   from): run `agents-remember knowledge-convert MEMORY_ROOT --code CODE_REPOSITORY`
   on the memory root itself, paired with the code repository it documents, then adopt
   the baseline, which validates the tree (MIK-R44 rule 7). Before the conversion,
   `memory_baseline_status` answers the state `legacy-format` with this step.
   `memory_init` on an existing unconverted root still writes no marker; its result
   names this procedure.
2. **Memory with an official line** (a branch leaves start from): start one new leaf
   from that line — `worktree_start` works on unconverted memory and its result says
   `memoryFormat: "legacy-format"` with this procedure. Run `knowledge-convert` on the
   leaf's memory worktree with the leaf's code worktree, as the leaf's first memory
   change, naming the paired code commit explicitly (`--code-commit`; the default is
   the code worktree's HEAD). Then close the leaf out and land it like any leaf: its candidate is
   converted, so the validator and the invariant gate run against the converted base
   (MIK-R24 rule 7) and the conversion itself counts as no change. The landing moves
   the leaf's source branches (the series line), not the base; read back the landed
   memory commit (message plus `Code-Commit` trailer) and the marker at its tip. Every other open
   line of that repository crosses at its next `worktree_sync` with `merge-memory`
   (MIK-R24 rule 8).

A conversion that fails validation writes nothing and names the failing files. After a
conversion or a crossing, the refused operation is repeated and runs the ordinary
converted route. The full procedure also lives in `docs/guides/first-conversion.md`;
`knowledge-convert --help` states the same entry points.

Authored migration prose uses the
[shared STE-inspired writing profile](../l-01-agent-lifecycles/reference/shared-writing-profile.md).
The converter is not a cosmetic rewrite. Before assessment waves, the migration procedure links
that profile; later authored statements, family guarantees and history reasons preserve scope,
source/evidence identities and frozen history, and proposed rewrites receive independent meaning
review before the normal writer publishes. A shorter sentence does not prove equivalent meaning.

The converter stays: the conversion command, its read-only reader of the legacy
database, and conversion-format version 1 are permanent parts of the product, because
unconverted lines live in other clones and on other machines (MIK-R46 rule 7). Never
propose retiring them as part of this master's work.

## Stage 5 - Bootstrap, The First Baseline, Then The Knowledge Foundation

Run this stage's onboarding and baseline halves only when Stage 4 scaffolded a new memory repo; the
knowledge half applies to **both** memory-repo answers, because a `recorded` knowledge foundation is not
something an existing memory repo is guaranteed to have. For a new memory repo the order is
`memory_init`, then the first onboarding through `c-03-repo-bootstrap` with the fixer, then
`memory_baseline_adopt`, and only then the knowledge foundation, which needs the commit the
baseline made. Stage 5 has three parts:

1. **Scaffold onboarding** - hand off to `c-03-repo-bootstrap` to generate initial
   onboarding. A thin `overview.md` is enough to start; deeper route-local overviews and
   file-level onboarding should grow as work touches new areas. Run the fixer
   (`citation_fix` / `memory-citations --fix`) on the cards before the baseline: without a
   contract it is admitted on the configured memory root while that root has no attributed
   baseline. Whether the developer wants
   onboarding at all is their call: `system/` alone is enough to adopt.
2. **Adopt the first attributed baseline** - hand off to `c-10-adopt-memory-baseline`,
   which owns the procedure. Read `memory_baseline_status` **before** adoption, put the
   drift-acceptance decision to the developer rather than assuming it, adopt through
   `memory_baseline_adopt`, then read `memory_baseline_status` **again** and report both.
   Baseline adoption is valid only before attributed memory exists, so an
   `already-adopted` report means this half is already done.
3. **Reach the repository's knowledge foundation** - hand off to
   `c-14-knowledge-bootstrap`. That procedure is the curator's work and states its own
   carriers; before a task exists, an instructed session that holds that procedure
   authors it under the curator's rules and the taskless bootstrap seat reaches
   the read-and-report step, as that procedure states. Read the state first:
   `memory_init` returns a `knowledge` block naming where the repository's
   foundation lives and what a read of that location finds now (`not-recorded`,
   `recorded` at an exact identity, `unusable`, or a refused admission with its
   own `nextAction`). Say which state it is, and run
   the foundation step when the location is `not-recorded`. An `unusable` location
   is reported with its code and path and repaired before anything is written to
   it. A memory tree in the legacy format is refused by the writer as
   `legacy-format`, naming the conversion command.

   On a new memory repo this part runs after the baseline: the foundation writer needs
   the memory commit the baseline made, and before the first baseline no leaf can exist.
   Only a repository whose memory already has a commit may reach its foundation before
   the onboarding or the baseline. It authors
   knowledge records through the curator's own writer, so this skill never
   writes knowledge records itself, never invents records to make setup look
   finished, and never creates a development leaf, worktree or enclosure to give
   the writer an argument list it does not need — and it never names a seat that
   cannot be started: a curator seat opens only on a leaf's task document, so before
   a task exists the authoring is handed on as `c-14-knowledge-bootstrap`
   states rather than worked around. The knowledge write needs the developer's
   commit word, exactly as the other effectful stages do.

The `onboarding/bootstrap/` working files ship with the first baseline commit as the run's audit trail. Say so before the first adoption, because the developer is the one who decides whether they are removed beforehand. Only a memory-root-level `bootstrap/` staging directory is transient and excluded from memory commits.

Skip the onboarding and baseline halves when an existing memory repo was adopted. The knowledge
half is not skipped with them: see Stage 4 case 2.

## Stage 6 - Retire Providers

MIK-R112 removed every product install/start/watch path for the two providers. Do not start watchers and do not verify with the removed provider tools:

1. Confirm the install stopped leftover watcher processes: the install result's watcher report holds the single `stop` phase.
2. Confirm old provider keys are reported once as unused: the install result's `unusedProviderKeys` and `server_info` carry them. Enabled keys drive nothing.
3. Remove the keys from settings when the older installation is retired.

Providers are accelerators. Core by-path memory works without them; a provider gap is reported from the install result and `server_info`, never polled.

## Report Result

Summarize:

1. harness package: detected and left untouched, or missing with the exact copy
   step the developer must perform;
2. runtime scaffold: `runtime_install()` run or already current, with the
   resolved coordination root;
3. agentic settings: interviewed and written, or left at the built-in defaults,
   with the global file path;
4. repository certification: exact profile path and digest for each explicitly requested code
   repository, validation result, and whether an authority-settings restart remains; routine
   closeout/integration needs no profile;
5. memory repo: scaffolded, existing-adopted, or already present, with the
   resolved memory root;
6. bootstrap: run via `c-03-repo-bootstrap` or skipped, with the fixer run on the
   cards before the baseline;
7. first baseline: `memory_baseline_status` before and after adoption, the adoption
   result at its memory-content commit, or the exact reason it was not run;
8. knowledge foundation: the state read from `memory_init`'s `knowledge` block,
   and then either the foundation run carried as `c-14-knowledge-bootstrap`
   states, with the records it wrote, or the exact reason it was not run;
9. providers: indexing status and any deferred/degraded state;
10. host: shared settings path, pinned host/product Node, provision result,
    no-change repeat or deferred restart, and any incomplete migration.

End with one executable start using the actual active MCP --config path and
exact package/source installation used by the registered tool server. For a uvx
registration, use `uvx --from "agents-remember-mcp==<served-version>" agents-remember
dashboard --daemon --config "<absolute active MCP settings file>"`, replacing both
values in the reported command; a local-wheel registration uses that exact wheel
as --from instead. A uv tool-installed command can use `agents-remember dashboard
--daemon --config "<absolute active MCP settings file>"`. A source registration
uses its registered interpreter and environment with `-m agents_remember.cli`.
Do not assume uvx placed a bare executable on PATH or discovery found a
non-Claude starter. The one start ensures the installed host and dashboard. `dashboard --status` reports
both (host query limited to 5 seconds): exit0 both up or host unconfigured, exit1
dashboard down, exit3 configured host down. A start never installs/upgrades;
dashboard stop/restart never stops the host. A host start drops the caller's
session/seat identities by exact name and preserves harness logins.

End by telling the developer whether the project is ready for normal work — and say the
knowledge foundation's state in that same sentence. A repository whose knowledge
location reads `not-recorded` is set up for ordinary work and has **no recorded
intent** for a planner to read; `recorded` names the identity that is there, and
`unusable` is a state to report with its path rather than a ready repository. Do
not tell them to restart for hooks installed by this skill, because this skill no
longer installs hooks.

## Boundaries

1. This skill is guidance for the model; it adds no MCP tool and no hardcoded
   per-harness installer.
2. It must not install, overwrite, or invent harness hooks, rules, instruction
   files, skills, or MCP registration files during first-run setup.
3. It must not install skills as part of the package-based first-run
   path.
4. It must not scaffold a memory repo without asking unless memory already
   exists and resolves cleanly.
5. It delegates memory init to `c-00-initialize-memory-repo`, bootstrap to
   `c-03-repo-bootstrap`, baseline adoption to
   `c-10-adopt-memory-baseline`, and context resolution to
   `c-08-ar-coordination-context-resolver`. The fresh-scaffold path reaches baseline
   adoption in Stage 5; it does not stop at the scaffold.
6. It delegates the repository's knowledge foundation to `c-14-knowledge-bootstrap`,
   and its own stages never author knowledge records. It does not report a repository's
   knowledge as ready without that skill's outcome, and the absence of a knowledge
   foundation is a named state of the report rather than a silent omission.
7. It must not invent a certification profile, discover one by convention, or copy another
   repository's commands. A missing/invalid profile blocks only the explicitly requested
   certification operation; routine code/memory/ledger closeout remains a Git transaction.

# Tool details — short tool descriptions (MIK-R112)

The longer explanations behind the short tool descriptions that point here.
Every moved argument, operation and refusal is retained. This file ships
with the product: it lives at `docs/tool-details.md` in the checkout, ships
inside the package at `runtime/docs/tool-details.md`, and the installer copies
that packaged tree to `<coordinationRoot>/docs/`, so the short descriptions
name this one place — `docs/tool-details.md` — in both layouts.

## `server_info`

Report the resolved configuration of this server: build, roots, allowed
repos, provider keys left unused, and, on a role-launched server, the tool
server name and agent binding. It does not list the tools.

Arguments: none.

Answer fields: `ok`, `server` (this package's own name), `version`,
`transport`, `configPath`, `coordinationRoot`, `workspaceRoot`,
`transcriptRoot`, `harnessSkillRoot` (absent when unconfigured),
`allowedRepoIds` (the configured repositories), `unusedProviderKeys` (old
provider keys, reported once as unused — MIK-R112 removed the provider
tools, so enabled keys drive nothing), `unusedSettingsKeys` (settings keys
the files hold that only removed parts read, named once as no longer used),
`openUserSettings` (only while the user's settings file or its folder is
open to writing: `userPath`, `fileExists`, `fileOpen`, `folderOpen`, and the
user's two commands for that exact path, `closeCommand` and `editCommand`),
`servingBuild` (the serving build record), and, on a role-launched server
only, `toolServer` and `agentBinding`.

Operations: one (report). The call is read-only; it reflects the settings
and runtime loaded at startup, and the modes of the user's settings file and
folder as they are at the call. It changes no mode and no byte of them.

Refusals: none in the ordinary path — the call cannot fail on arguments
(it takes none). A server that cannot read its own configuration refuses at
startup, before any tool is served.

## `runtime_install`

Install or refresh the packaged coordinator runtime into the coordination
root; safe to re-run. Preserves user data (`memory-repos/`,
`providers/data/`, `providers/runners/`); replaces the managed scaffold
(`skills/`, `docs/`) and prunes legacy binaries.

Arguments: `dry_run=false` (preview: changes nothing), `include_benchmarks=false`
(copy benchmark fixtures), `install_provider_deps=true` (retained knob: no longer
drives installation — MIK-R112 retired the two providers, so enabled keys drive
nothing), `no_cache=false` (retained knob: carried as the provider-dependency
step's cache-reuse flag — true would forbid reuse — but no product path reads it,
and provider installation is retired, so it currently changes nothing observable),
`experiment=null` (a per-call input, never a
setting, selecting an experimental cutover for one run; its source is named in the
record).

`experiment=role-capsules` is the one supported experiment. `capsule` mode
withholds the legacy AR startup chain in the installation (the coordinator
`AGENTS.md` targets) while still installing the `skills/` authored copy — read
by humans, the dashboard and curators, never compiled; a per-run record
carries experiment, source, selection source and mode. Selection precedence:
the request field, then the `AR_EXPERIMENT` environment fallback; empty at
both means unselected (the legacy default). An unknown id is refused by name
before anything is written, never silently run as legacy. Omission after a
capsule run undoes it: re-running the installer with no experiment selected
copies the withheld `AGENTS.md` targets back (the `rollback` plan in the
run's `experiment` part names this step).

Operations: one install run. It stops leftover provider watcher processes as its
only watcher action; a failed stop is recorded in the install result and the
install continues. It reports enabled provider keys once as unused
(`summary.unusedProviderKeys`, mirrored by `server_info`). Provider setup for
the retired providers performs nothing from any product path: retired ids never count as enabled,
so no install/start/watch runs — no install, prepare, seed, watcher start or status — only the explicit `providers-remove` command deletes provider runtime folders,
and only the old-watcher stop remains. Benchmark commands run providerless with
the same empty setup. The final `host` part provisions the build-pinned
host/plugin from the per-use shared `system/settings.json` block; shared
authority is never written and needed restarts are deferred. Preview
(`dry_run`) observes only and reports what would change (`wouldInstallHost`,
`wouldStart`); an absent host reports `configured: false` with the setup
message. A needed restart reports `restartRequired` with the remedy: run the
provision from a terminal outside the host, which ends agent sessions and
running turns.

Answer fields (delivered): `ok`, `coordinationRoot`, `dryRun`,
`experiment` (selection, delivery, record), `host` (configured, changed,
message), `includeBenchmarks`, `installProviderDeps`, `log` (the detail file
holding the whole result), `messages` (first 5 inline, the rest in the
report), `reportPath`, `summary` (`createdDirs`, `copiedFiles`,
`unchangedFiles`, `replacedLinks`, `removedPaths`, `dependencyRuns`,
`unusedProviderKeys`), `providerWatcherRebind` (attempted, ok, `phases[]`
with phase/action/ok per run — the single stop phase; the untruncated runs
live in the report file), `recoveryActions` (when the run lists any),
`removedPartStores` (when stores of removed parts remain: `count`, `bytes`,
`notice` naming `agents-remember removed-stores`, and `storedGates` when a
gate log is among them; the report file adds `stores`, each with its path,
size and files. The install deletes none of them).

Refusals: an experimental cutover refused before anything is written (nothing
under the coordination root is created, replaced or removed, and the selected
mode is preserved); a failed watcher stop refuses nothing — the install
continues and reports; a removal failure after the stop reports the failed
paths. `providers-remove` (not this tool) refuses when its watcher stop
fails, when a live process holds a folder open (naming pid and command), or
when the provider root or a deletion target escapes the owned tree.

## Drift check: the carried-anchor exception (MIK-R53 rule 11)

`context_packet` with `include_drift` measures the reference anchors of
onboarding cards and the validity of the tree. A stale reference is reported
and never blocks by itself — with one exception: a carried anchor at a path
the leaf deleted refuses the leaf's closeout or landing as
`R22.6-carried-stale`, and is only reported at a sync merge. MIK-R53 rule 11
replaces the MIK-R22 rule 6 clause that exception rested on, and its
Supersedes names MIK-R44 v2 for that one kind (the "`violationCount` stays 0"
boundary and the never-blocks sentence for a carried anchor at a gone path).
Every other stale reference keeps the never-blocks sentence.


## `citation_fix`

Regenerate anchored citation ranges inside one leaf memory worktree.

Two admissions: a leaf enclosure contract (the ordinary write route; the
application guard refuses the official memory repo), or no contract on the
configured memory root while that root has no attributed baseline (the
pre-baseline bootstrap admission, which answers with `admission: "pre-baseline"`).
In every other case a call without a contract refuses and names the leaf route;
once the baseline exists, the refusal says so. A
pure move is repaired; renamed, deleted, or ambiguous anchors remain a curator worklist.
Preview with dry_run=true. Check the cards of a change in one call and repair them in
one call: pass `documents` with every onboarding-relative file, or `document` for one
file (a list of one). A call that names both is refused, and so is an empty list; a
call that names more documents than one leaf may hold changed files with cards is
refused before anything is read. A named document that does not exist is a refusal
of that document and does not stop the others. Old-format memory is refused:
convert it with knowledge-convert or the crossing sync before repair.
The answer is a status -- whether the step went well, with the counts (rewritten,
stale, unreadable, refused) -- and the whole result with every list is in the log's
detail file. On converted memory it authors the cards' citation rows into sidecar
references and re-records moved anchors; document selection scopes the run to those
cards.


## `context_packet`

Where the caller works and what needs action (MIK-R109).

Slim answer: the roots (code/memory worktrees, enclosure, task
document), task ID/master, action lines when needed, serving build
and pair commits. `include_drift` measures the reference anchors of onboarding cards
and the validity of the tree -- a stale reference is reported and never blocks by itself;
whether an invariant is still current is answered by `knowledge_read` and
`knowledge_integrity_check`, not here; a leaf that changes a source
file must change that file's card or record why not (MIK-R30); and
nothing is repaired (`citation_fix` re-records anchors that only
moved, and a curator re-authors a stale one); omit it and nothing is measured. `include_freshness` fetches remotes and writes FETCH_HEAD,
so the tool is marked as a write. Leaf calls pass task_context.
Longer explanation: docs/reference/mcp-tools.md.


## `curator_coherence`

Author, publish, inspect, or validate one leaf's curator-coherence authority.
The structured stable manifest selects exactly one live content-addressed record;
Markdown is a generated digest-bound projection and is never parsed as authority.
`prepare` writes the exact candidate set to the enclosure's
`reports/curator-coherence-candidates.json` (with a `.md` reading rendering beside
it) and answers with the count of candidates by kind, the set's digest and the
locations; the curator reads the candidates from the file. `status` and `validate`
answer with the state, the counts and what blocks. `publish` requires the unchanged
identities plus one curator/architect-authored disposition, rationale, and evidenceRef
for every candidate, and answers the outcome with the count of published judgments;
on a refusal it names which expectation no longer holds. `publish` takes `judgments`
and `review_assessments` inline, as before, or from one JSON file (`judgments_file`,
an absolute path) holding the two lists; the file is read once and its SHA-256 is
answered, and a call that supplies a list both ways is refused.
`publish` also requires every one of `semantic_requirement_revision`, `delivery_attempt`,
`expected_predecessor_digest`, `expected_code_candidate_tree`,
`expected_memory_candidate_tree`, `expected_task_topology_fingerprint`,
`expected_task_intent`, `expected_attestation_sha256` and `caller` to be supplied and
non-null: the request carries one action's input shape at a time, so `status`, `prepare`
and `validate` forbid all nine. It rejects missing, extra, duplicate, malformed, or stale
judgments, publishes atomically, and may freeze an immutable delivery-attempt snapshot.
Evidence references use one explicit authority namespace—`code:`, `memory:`, or `task:`—and
the lifecycle records and later revalidates the referenced bytes' digest. A semantic
requirement revision, delivery attempt, and content digest are separate fields. `validate`
is the same validator used by memory preflight and closeout admission. Historical files
are never searched as fallbacks.


## `memory_baseline_adopt`

Create the first attributed memory baseline for an external memory repo. Mutating: commits
memory content and refreshes its disposable ledger cache. Gated on clean drift unless accept_drift=true.
`accept_drift` accepts stale anchors only: it never lets a tree with a validator
violation through, and the first baseline of a converted tree is validated
before it is committed. Preview with
dry_run=true. Usually driven by the c-10-adopt-memory-baseline skill.


## `memory_carryover_plan`

Plan (non-mutating) carrying richer onboarding from a source branch into the exact open
external-memory leaf. Review it, then apply before normal leaf closeout/integration.
Files no report.


## `memory_quality_check`

Prepare memory before certification admission using drift-integrity and style checks.
It never changes code or memory. A full contract-scoped call atomically replaces the one
operational checklist at `<worktree enclosure>/reports/curator-memory-quality.md` and its
structured, report-digest-bound `.json` attestation; subset and unscoped calls write
operation. Every contract-scoped execution also publishes the complete structural
census to reports/memory-census.json; the answer carries the counts and the
checklist's location instead of the census, the sample and the catalogue.
This worklist requires no code-gate results and does not certify semantic decisions.
ok=false means findings exist (e.g. dirty-source
drift), not that the tool failed. `curatorActionableCount` and
`qualityChecklistStatus` are the onboarding-repair loop gate. Once the raw checklist is
`ready-for-closeout`, combined `checklistStatus=coherence-required` means the caller must
prepare, publish, and validate the exact structured `curator_coherence` authority;
`closeoutReady=true` is impossible until that same validator accepts it. Checklist and
attestation rendering are deterministic, so a same-input rerun preserves the published
authority; changed inputs stale it and require republishing. Final commit stamps remain
closeout-owned. The request is exactly one discriminated mode:
`sync` and `start` carry repository plus optional scope/check/detail fields, while
`poll` carries repository, run id, and the same contract path for a candidate run.
Repository-only calls are explicitly official diagnostics and cannot become candidate
acceptance. A saturated unique start returns `capacity-reached` without launching work.
A poll returns the identical pair-bound result; `run-not-found` means the run was evicted,
belongs to another repository, or the server restarted, so submit a new start request
with the original contract path for a candidate run.


## `retire_child`

Retire the receipt-tracked agent in one authorized direct child seat.

MIK-R100 rule 8: the agent that started the seat retires the agent in it
without giving it a turn, and the launch record names the caller and the
reason. An agent in a turn is not retired unless interrupt_turn says the
turn may be interrupted. Any other role agent is refused with the starter
named; an unknown or ambiguous agent id is refused by name and nothing
is archived; a repeated call for an already retired agent changes nothing
and says so.


## `role_start_set`

Start a leaf's Worker, Reviewer and Curator in one call (MIK-R100 rule 1).

Only a role agent that AR launched can call this. Only the leaf's owning Manager can call this: any other role is refused
before anything is prepared. Each seat is then checked as a start of its
role is. The three have the calling Manager as their parent and the same
leaf as their selection, and each takes its agent, model, effort and
feature values as any start of its role does: pass them
per seat in worker, reviewer and curator (agent, model, effort, featureValues).

The Worker opens at once. The Reviewer and the Curator are held: each exists on
the host, is reached by its role and the leaf's task reference, and has taken no
turn, with its handover written when the seat is prepared. reviewer_message is
the Reviewer's first work: carried, the Reviewer opens at once with its handover
and that message. replaces names, per seat role, the holder a replacing start
retires first, by the rule of role_start; replaces_interrupt_turn is the word,
for the whole set, that a replaced holder's turn may be interrupted.

The answer names the three agent ids and says for each whether it has taken a
turn. A repeat with the same request id reconciles the same three agents: it
creates no fourth and opens no held seat. If one of the three cannot be created,
the call says which, leaves the others as they are, and a repeat completes the
set.


## `route_index_refresh`

Regenerate the overview.index.json route indexes so they match the current onboarding
tree. Apply requires a leaf enclosure contract and writes only that leaf's memory
worktree, or no contract on the configured memory root while that root has no
attributed baseline (the pre-baseline bootstrap admission, which answers with
`admission: "pre-baseline"`). Any other unscoped call is read-only and therefore
requires dry_run=true.

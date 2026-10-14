# Tool details — `server_info` and `runtime_install` (MIK-R112)

The complete longer explanation behind the two short tool descriptions this
leaf owns. Every argument, every operation, every refusal. This file ships
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
tools, so enabled keys drive nothing), `servingBuild` (the serving build
record), and, on a role-launched server only, `toolServer` and
`agentBinding`.

Operations: one (report). The call is read-only; it reflects the settings
and runtime loaded at startup.

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
by humans, the dashboard and curators, never compiled — and the pinned eve
application; a per-run record carries experiment, source, selection source and
mode. Selection precedence: the request field, then the `AR_EXPERIMENT`
environment fallback; empty at both means unselected (the legacy default). An
unknown id is refused by name before anything is written, never silently run
as legacy. Omission after a capsule run undoes it: re-running the installer
with no experiment selected copies the withheld `AGENTS.md` targets back (the
rollback plan also removes the installed application dir and runtime state).

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
live in the report file), `recoveryActions` (when the run lists any).

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

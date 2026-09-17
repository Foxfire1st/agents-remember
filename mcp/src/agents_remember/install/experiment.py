"""The experimental instruction cutover: one option, selected per run, recorded per run.

This module owns the three things CAPS-R09 behaviour 2 asks of an experimental installation, and
nothing else:

**Selection.** One narrowly owned option, ``AR_EXPERIMENT=role-capsules`` (or the same value passed
as the install request's ``experiment`` field), resolved by :func:`resolve_experiment_selection`.
The value is read from the run's own input, never from a settings file, a user configuration and
never written back anywhere: the experiment has **no persistent global switch**, so there is none to
leave on at the master's exit. An unrecognised value is refused by name rather than treated as "not
selected", because silently running the unmodified path for a typo is the silent switch this
behaviour forbids.

**The delivery decision.** :func:`decide_instruction_delivery` answers exactly one of ``capsule``,
``legacy`` or ``refused``. Not selected is ``legacy`` — the existing installation, unchanged.
Selected and able is ``capsule``. Selected and unable is ``refused``, naming the failure: it is
never ``legacy``. A runtime failure therefore reports itself and preserves the selected mode; it
never quietly runs the old startup chain.

**The record.** :class:`ExperimentRunRecord` is the packet's example run record (``experiment``,
``source``, ``harness``, ``instructionSource``, ``eveVersion``) plus the delivery mode and the
capability detail that produced it. It is one record for one run, returned to the caller that ran
the install and written beside it — an example-shaped record, not a new configuration framework.

**What the cutover is.** ``capsule`` mode replaces the legacy AR startup chain *in the installation*
by not installing it: the coordinator ``AGENTS.md`` targets (``AGENTS.md``, ``system/AGENTS.md``,
``skills/AGENTS.md``, ``tasks/AGENTS.md``) are withheld. The coordinator ``skills/`` tree is still
installed, and its role is worth stating exactly: it is the **authored copy the coordination root
carries** — what a human, the dashboard or a curator reads — and it is **not** what the compiler
reads. In production the capsule compiler resolves its corpus from the *packaged* tree
(``application/skill_resources`` → ``packaged_source_root()/runtime/skills``), so the installed copy
is never injected and never compiled; it is not a second delivery of the corpus.
:data:`WITHHELD_STARTUP_TARGETS` names the withheld paths so the install can report them and a reader
can see exactly what changed.

**Returning to the unmodified configuration.** :func:`experiment_rollback_plan` renders the exact
undo rows: the experiment's own machine-local artifacts, and the one command that restores the
legacy startup chain (run the installer again with no experiment selected). The adopt / revise /
discard decision itself belongs to the developer and the owning seat; this module only makes the
route back executable and checkable.

**What this module is not.** It does not compile a capsule, does not decide a launch's instruction
mode for a seat (that is the serving tier's ``launch_capsule`` gate, which records ``instructionMode``
per launch), and does not touch a user-level harness configuration. It decides what an *installation*
is, and it is the only place that decides it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

EXPERIMENT_ENV = "AR_EXPERIMENT"
"""The per-run selection input. An environment variable of the run, never a settings key."""

ROLE_CAPSULES = "role-capsules"
"""The one experiment this installation supports."""

EXPERIMENTS = (ROLE_CAPSULES,)
"""The closed vocabulary. Adding an experiment is a deliberate edit here, not a settings value."""

PACKAGED_APPLICATION_PATH = Path("runtime") / "eve-runtime"
"""Where the pinned eve application source ships inside the package data root.

Deliberately **not** ``runtime/eve-agent``: that path is the adapter's own packaged-application
probe, and populating it would silently repoint every source-checkout launch at a copy with no
installed dependencies. The install copies this tree to :data:`INSTALLED_APPLICATION_PATH` instead,
which the launch path reaches through its documented ``AR_EVE_RUNTIME_ROOT`` root override.
"""

INSTALLED_APPLICATION_PATH = Path("runtime") / "eve-agent"
"""Where one experiment installs the pinned application inside a coordination root."""

APPLICATION_SOURCE_NAMES = ("package.json", "package-lock.json", "agent")
"""The authored application surface. Dependencies are installed into it, never shipped."""

PINNED_DEPENDENCIES: Mapping[str, str] = {
    "@ai-sdk/openai-compatible": "3.0.49",
    "ai": "7.0.102",
    "eve": "0.56.0",
    "zod": "4.6.5",
}
"""The four exact pins ``eve_runtime/package.json`` declares. No ranges, by the design's rule."""

PINNED_DEPENDENCY_PACKAGES = tuple(sorted(PINNED_DEPENDENCIES))

MINIMUM_NODE_MAJOR = 24
"""The oldest Node major the pinned eve release starts under (``engines.node`` and eve's own check)."""

CORPUS_ANCHOR = Path("skills") / "l-01-agent-lifecycles" / "composition-manifest.json"
"""The canonical corpus anchor this installation digests: routing metadata for the authored tree."""

NODE_EXECUTABLE_ENV = "AR_EVE_NODE"
RUNTIME_ROOT_ENV = "AR_EVE_RUNTIME_ROOT"
STATE_ROOT_ENV = "AR_EVE_STATE_ROOT"

WITHHELD_STARTUP_TARGETS = (
    "AGENTS.md",
    "system/AGENTS.md",
    "skills/AGENTS.md",
    "tasks/AGENTS.md",
)
"""The legacy AR startup chain's installation surface, withheld in ``capsule`` mode.

These are the coordinator instruction targets ``install/runtime.py::AGENTS_MD_TARGETS`` writes. The
harness-level session-start hook is a *developer-workspace* surface, generated by
``scripts/sync-harness.py`` into each starter package's own directory; it is not install-managed and
is not removed here. That residual is named in the leaf's report and owned by the leaves that own the
adapter-side prompt construction.
"""


class ExperimentError(RuntimeError):
    """A refused experiment selection or a failed capability probe.

    Raised *before* an install writes anything, so a refusal leaves no partial installation behind —
    the refusal a retry can undo is the fail-open this repository has already paid for once (D20).
    """


@dataclass(frozen=True)
class ExperimentSelection:
    """Which experiment one run selected, and where the value came from."""

    requested: str
    experiment: str | None
    selected: bool
    source: str
    reason: str

    def payload(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "experiment": self.experiment,
            "selected": self.selected,
            "source": self.source,
            "reason": self.reason,
        }


def resolve_experiment_selection(
    *,
    requested: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ExperimentSelection:
    """Resolve this run's experiment, refusing an unknown id by name.

    Precedence: the explicit request field, then :data:`EXPERIMENT_ENV`. An empty or blank value at
    both is "not selected" — the unmodified installation, which is the default an operator who never
    opted in gets. An id outside :data:`EXPERIMENTS` raises :class:`ExperimentError`: a typo must not
    silently become "run the old path".
    """

    environ = os.environ if environ is None else environ
    explicit = (requested or "").strip()
    from_environment = str(environ.get(EXPERIMENT_ENV, "")).strip()
    value = explicit or from_environment
    source = "request" if explicit else ("environment" if from_environment else "unselected")
    if not value:
        return ExperimentSelection(
            requested="",
            experiment=None,
            selected=False,
            source=source,
            reason=(
                "no experiment was selected for this run; the installation is the unmodified one"
            ),
        )
    if value not in EXPERIMENTS:
        raise ExperimentError(
            f"unknown experiment {value!r}: this installation supports "
            f"{', '.join(repr(name) for name in EXPERIMENTS)}. Nothing was installed and the "
            "selected mode was preserved; correct the value (it is never silently ignored)."
        )
    return ExperimentSelection(
        requested=value,
        experiment=value,
        selected=True,
        source=source,
        reason=f"experiment {value!r} selected through {source}",
    )


def unselected_experiment() -> ExperimentSelection:
    """The canonical "this run selected nothing" value.

    One producer, so no caller has to write ``None`` and mean "look at the environment": the
    unmodified installation passes this explicitly, and only :func:`resolve_experiment_selection`
    called from the configuration entry point ever reads an ambient value.
    """

    return resolve_experiment_selection(requested="", environ={})


@dataclass(frozen=True)
class InstructionDelivery:
    """One run's instruction-delivery mode and the reason it answered that way."""

    mode: str
    reason: str

    def payload(self) -> dict[str, str]:
        return {"mode": self.mode, "reason": self.reason}


def decide_instruction_delivery(
    selection: ExperimentSelection,
    *,
    capsule_available: bool,
    capsule_failure: str = "",
) -> InstructionDelivery:
    """The one decision: ``capsule``, ``legacy`` or ``refused`` — never a silent switch.

    ``legacy`` is reachable only when the run did not select the experiment. A selected run whose
    capsule path is unavailable is ``refused`` and carries the failure text, so the run reports the
    failure and preserves the selected mode instead of quietly installing the old startup chain.
    """

    if not selection.selected:
        return InstructionDelivery(mode="legacy", reason=selection.reason)
    if capsule_available:
        return InstructionDelivery(
            mode="capsule",
            reason=f"{selection.reason}; the capsule path is available for this installation",
        )
    return InstructionDelivery(
        mode="refused",
        reason=(
            f"{selection.reason}, but its capsule path is unavailable: "
            f"{capsule_failure or 'no capability detail was reported'}. The old startup chain was "
            "not installed in its place"
        ),
    )


@dataclass(frozen=True)
class Capability:
    """One probed capability of the experimental installation."""

    name: str
    ok: bool
    blocking: bool
    detail: str
    remedy: str = ""

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "blocking": self.blocking,
            "detail": self.detail,
            "remedy": self.remedy,
        }


@dataclass(frozen=True)
class CapabilityReport:
    """Every capability the experiment needs, and whether any blocking one failed."""

    capabilities: tuple[Capability, ...]

    @property
    def ok(self) -> bool:
        return not self.blocking_failures

    @property
    def blocking_failures(self) -> tuple[Capability, ...]:
        return tuple(item for item in self.capabilities if item.blocking and not item.ok)

    @property
    def corpus_anchor_digest(self) -> str:
        for item in self.capabilities:
            if item.name == "canonical-corpus-anchor" and item.ok:
                return item.detail.removeprefix("sha256:")
        return ""

    def remedy_lines(self) -> tuple[str, ...]:
        return tuple(
            f"{item.name}: {item.remedy or item.detail}" for item in self.blocking_failures
        )

    def payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "capabilities": [item.payload() for item in self.capabilities],
        }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_digest(path: Path) -> str:
    """The sha256 of one file, or ``""`` when it does not exist."""

    try:
        return _digest(path)
    except OSError:
        return ""


def application_source_root(source_root: Path) -> Path:
    """The packaged pinned-application tree inside one install source root."""

    return source_root / PACKAGED_APPLICATION_PATH


def corpus_anchor_path(source_root: Path) -> Path:
    """The canonical corpus anchor inside one install source root."""

    return source_root / "runtime" / CORPUS_ANCHOR


def dependency_install_command(application_root: Path) -> str:
    """The exact one-line install a clean machine runs for the pinned application.

    ``npm ci`` rather than ``npm install``: the committed lockfile is the transitive pin, and ``ci``
    installs exactly it instead of resolving ranges. ``--no-audit --no-fund`` keeps the command's
    output about the install. ``<node 24 bin>`` is the caller's Node >= 24 directory, exactly as
    ``eve_runtime/README.md`` documents it for a checkout.
    """

    return (
        f"cd {application_root.as_posix()} && PATH=<node 24 bin>:$PATH npm ci --no-audit --no-fund"
    )


def resolve_path_node(environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    """The PATH Node the install would run the dependency install with, and its version.

    A diagnostic, not the launch authority: the serving tier's own resolver prefers
    ``AR_EVE_NODE``, then the newest nvm runtime, then ``PATH``, and it is the one a launch obeys.
    This reports what a bare ``node`` on this machine is so a clean-environment install can say the
    requirement out loud instead of failing later at spawn.
    """

    environ = os.environ if environ is None else environ
    executable = str(environ.get(NODE_EXECUTABLE_ENV, "")).strip() or (shutil.which("node") or "")
    if not executable:
        return "", ""
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return executable, ""
    return executable, completed.stdout.strip() or completed.stderr.strip()


def node_major(version: str) -> int | None:
    """The major of an ``v24.19.0``-shaped version string, or ``None`` when unreadable."""

    text = version.strip().lstrip("vV")
    head = text.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def _package_json_capability(application_root: Path) -> Capability:
    package_json = application_root / "package.json"
    if not package_json.is_file():
        return Capability(
            name="pinned-eve-dependencies",
            ok=False,
            blocking=True,
            detail=f"the pinned application declares no package.json at {package_json}",
            remedy=f"restore the packaged application source at {application_root}",
        )
    try:
        declared = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return Capability(
            name="pinned-eve-dependencies",
            ok=False,
            blocking=True,
            detail=f"{package_json} is unreadable: {error}",
            remedy=f"restore the packaged application source at {application_root}",
        )
    dependencies = declared.get("dependencies")
    if not isinstance(dependencies, dict):
        return Capability(
            name="pinned-eve-dependencies",
            ok=False,
            blocking=True,
            detail=f"{package_json} declares no dependencies object",
            remedy="restore the pinned application's package.json",
        )
    mismatched = {
        name: {"declared": dependencies.get(name), "pinned": version}
        for name, version in PINNED_DEPENDENCIES.items()
        if dependencies.get(name) != version
    }
    extra = sorted(set(dependencies) - set(PINNED_DEPENDENCIES))
    detail = json.dumps(
        {
            "declared": {name: dependencies.get(name) for name in PINNED_DEPENDENCY_PACKAGES},
            "mismatched": mismatched,
            "unpinnedExtras": extra,
        },
        sort_keys=True,
    )
    return Capability(
        name="pinned-eve-dependencies",
        ok=not mismatched and not extra,
        blocking=True,
        detail=detail,
        remedy=(
            "the packaged application is not the pinned one; re-run scripts/sync-runtime.py "
            "against the canonical eve_runtime/ tree"
        ),
    )


def _lockfile_capability(application_root: Path) -> Capability:
    lock_path = application_root / "package-lock.json"
    if not lock_path.is_file():
        return Capability(
            name="eve-lockfile-integrity",
            ok=False,
            blocking=True,
            detail=f"the pinned application ships no lockfile at {lock_path}",
            remedy="restore package-lock.json from the canonical eve_runtime/ tree",
        )
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return Capability(
            name="eve-lockfile-integrity",
            ok=False,
            blocking=True,
            detail=f"{lock_path} is unreadable: {error}",
            remedy="restore package-lock.json from the canonical eve_runtime/ tree",
        )
    packages = lock.get("packages")
    resolved: dict[str, dict[str, object]] = {}
    if isinstance(packages, dict):
        for name in PINNED_DEPENDENCY_PACKAGES:
            entry = packages.get(f"node_modules/{name}")
            if isinstance(entry, dict):
                resolved[name] = {
                    "version": entry.get("version"),
                    "integrity": bool(entry.get("integrity")),
                }
    wrong = {
        name: detail
        for name, detail in resolved.items()
        if detail.get("version") != PINNED_DEPENDENCIES[name] or not detail.get("integrity")
    }
    missing = sorted(set(PINNED_DEPENDENCY_PACKAGES) - set(resolved))
    eve_entry = packages.get("node_modules/eve") if isinstance(packages, dict) else None
    eve_integrity = str(eve_entry.get("integrity", "")) if isinstance(eve_entry, dict) else ""
    return Capability(
        name="eve-lockfile-integrity",
        ok=not wrong and not missing,
        blocking=True,
        detail=json.dumps(
            {
                "lockfileVersion": lock.get("lockfileVersion"),
                "resolved": resolved,
                "wrong": wrong,
                "missing": missing,
                "eveIntegrity": eve_integrity,
            },
            sort_keys=True,
        ),
        remedy="re-run scripts/sync-runtime.py against the canonical eve_runtime/ tree",
    )


def probe_capabilities(
    *,
    source_root: Path,
    coordination_root: Path,
    environ: Mapping[str, str] | None = None,
) -> CapabilityReport:
    """Probe every capability the experimental installation needs, before anything is written.

    ``source_root`` is the install source (the package data root for a packaged run, an explicit
    tree for a checkpoint run). ``coordination_root`` is where the experiment would be installed.
    Blocking capabilities decide the delivery mode: any failure is a refusal, never a fallback.
    """

    application_root = application_source_root(source_root)
    anchor = corpus_anchor_path(source_root)
    capabilities: list[Capability] = []

    anchor_digest = file_digest(anchor)
    capabilities.append(
        Capability(
            name="canonical-corpus-anchor",
            ok=bool(anchor_digest),
            blocking=True,
            detail=f"sha256:{anchor_digest}" if anchor_digest else f"missing: {anchor}",
            remedy=(
                "the canonical corpus anchor is not installed with the runtime assets; run "
                "scripts/sync-skills.py and scripts/sync-runtime.py"
            ),
        )
    )

    agent_definition = application_root / "agent" / "agent.ts"
    application_present = (application_root / "package.json").is_file() and (
        agent_definition.is_file()
    )
    capabilities.append(
        Capability(
            name="packaged-eve-application",
            ok=application_present,
            blocking=True,
            detail=(
                f"source {application_root} -> install {coordination_root / INSTALLED_APPLICATION_PATH}"
                if application_present
                else f"missing: {application_root / 'package.json'} or {agent_definition}"
            ),
            remedy="re-run scripts/sync-runtime.py to refresh the packaged application source",
        )
    )

    capabilities.append(_package_json_capability(application_root))
    capabilities.append(_lockfile_capability(application_root))

    executable, version = resolve_path_node(environ)
    major = node_major(version)
    capabilities.append(
        Capability(
            name="node-runtime",
            ok=major is not None and major >= MINIMUM_NODE_MAJOR,
            blocking=False,
            detail=json.dumps(
                {"executable": executable, "version": version, "minimumMajor": MINIMUM_NODE_MAJOR},
                sort_keys=True,
            ),
            remedy=(
                f"the pinned eve release needs Node.js >= {MINIMUM_NODE_MAJOR}; name one with "
                f"{NODE_EXECUTABLE_ENV} (the launch path also searches $HOME/.nvm)"
            ),
        )
    )

    installed_modules = application_root / "node_modules"
    capabilities.append(
        Capability(
            name="eve-dependencies-installed",
            ok=installed_modules.is_dir(),
            blocking=False,
            detail=f"{installed_modules} present"
            if installed_modules.is_dir()
            else (
                f"{installed_modules} is absent; the runtime refuses to stage a launch without it"
            ),
            remedy=dependency_install_command(coordination_root / INSTALLED_APPLICATION_PATH),
        )
    )

    return CapabilityReport(capabilities=tuple(capabilities))


@dataclass(frozen=True)
class ExperimentRunRecord:
    """One run's experiment record, in the packet's shape plus its delivery detail.

    ``source`` is the installation source's own identity, ``instructionSource`` is the canonical
    corpus digest, ``eveVersion`` is the pinned release — the three values a second run compares to
    prove it reproduced the same dependency and corpus versions.
    """

    experiment: str
    source: str
    harness: str
    instruction_source: str
    eve_version: str
    mode: str
    reason: str
    selection_source: str
    corpus_anchor: str
    application_root: str
    installed_application_root: str
    dependency_install_command: str
    dependencies_installed: bool
    capabilities: tuple[Capability, ...]

    def payload(self) -> dict[str, object]:
        return {
            "experiment": self.experiment,
            "source": self.source,
            "harness": self.harness,
            "instructionSource": self.instruction_source,
            "eveVersion": self.eve_version,
            "mode": self.mode,
            "reason": self.reason,
            "selectionSource": self.selection_source,
            "corpusAnchor": self.corpus_anchor,
            "applicationRoot": self.application_root,
            "installedApplicationRoot": self.installed_application_root,
            "dependencyInstallCommand": self.dependency_install_command,
            "dependenciesInstalled": self.dependencies_installed,
            "capabilities": [item.payload() for item in self.capabilities],
        }


@dataclass(frozen=True)
class InstallTargets:
    """Where one install reads from and writes to, plus the identity of that source.

    One value so the record builder takes four arguments rather than seven, and so a
    caller cannot pass a source identity that belongs to a different root than the one
    the record's digests were taken from.
    """

    source_root: Path
    coordination_root: Path
    source: str
    harness: str = "eve"

    @property
    def application_root(self) -> Path:
        return application_source_root(self.source_root)

    @property
    def installed_application_root(self) -> Path:
        return self.coordination_root / INSTALLED_APPLICATION_PATH


def build_run_record(
    selection: ExperimentSelection,
    delivery: InstructionDelivery,
    report: CapabilityReport,
    targets: InstallTargets,
) -> ExperimentRunRecord:
    """One run's record, built from the values the run itself resolved."""

    application_root = targets.application_root
    installed_root = targets.installed_application_root
    dependencies_installed = all(
        item.ok for item in report.capabilities if item.name == "eve-dependencies-installed"
    )
    return ExperimentRunRecord(
        experiment=selection.experiment or "",
        source=targets.source,
        harness=targets.harness,
        instruction_source=(
            f"sha256:{report.corpus_anchor_digest}" if report.corpus_anchor_digest else ""
        ),
        eve_version=PINNED_DEPENDENCIES["eve"],
        mode=delivery.mode,
        reason=delivery.reason,
        selection_source=selection.source,
        corpus_anchor=str(corpus_anchor_path(targets.source_root)),
        application_root=str(application_root),
        installed_application_root=str(installed_root),
        dependency_install_command=dependency_install_command(installed_root),
        dependencies_installed=dependencies_installed,
        capabilities=report.capabilities,
    )


def source_identity(source_root: Path, environ: Mapping[str, str] | None = None) -> str:
    """The installation source's own identity: ``<branch>@<commit>``, else its path.

    Read with ``git`` when the source root is inside a checkout, because the packet's record asks
    which source line the experiment was installed from. A tree that is not a checkout — a packaged
    install, a checkpoint copy under ``/tmp`` — reports its own path instead of inventing a commit.
    """

    environ = os.environ if environ is None else environ
    root = source_root if source_root.is_dir() else source_root.parent
    for relative in ("", "mcp"):
        candidate = root / relative if relative else root
        try:
            branch = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=dict(environ),
            )
            commit = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=dict(environ),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        branch_name = branch.stdout.strip()
        commit_id = commit.stdout.strip()
        if branch.returncode == 0 and commit.returncode == 0 and branch_name and commit_id:
            return f"{branch_name}@{commit_id}"
    return source_root.as_posix()


@dataclass(frozen=True)
class UndoRow:
    """One row of the machine-change inventory: what the experiment did and how to undo it."""

    path: str
    action: str
    undo_command: str


def experiment_rollback_plan(
    *,
    coordination_root: Path,
    install_roots: Sequence[Path] = (),
    state_root: Path | None = None,
) -> tuple[UndoRow, ...]:
    """The executable route back to the unmodified installation.

    The experiment's own installed application, its runtime state epochs, and finally the one command
    that restores the legacy startup chain — the same installer, run with no experiment selected,
    which copies the withheld ``AGENTS.md`` targets back from the packaged runtime.

    ``install_roots`` is for a **separate** ``skills_install`` run and is never populated by the
    runtime install: ``install_runtime`` copies runtime assets and nothing else, so a row for a
    harness skill root is rendered only when the caller actually ran that other tool, and its action
    and undo text say so.
    """

    rows = [
        UndoRow(
            path=(coordination_root / INSTALLED_APPLICATION_PATH).as_posix(),
            action="created",
            undo_command=f"rm -rf {(coordination_root / INSTALLED_APPLICATION_PATH).as_posix()}",
        )
    ]
    if state_root is not None:
        rows.append(
            UndoRow(
                path=state_root.as_posix(),
                action="created",
                undo_command=f"rm -rf {state_root.as_posix()}",
            )
        )
    for install_root in install_roots:
        rows.append(
            UndoRow(
                path=install_root.as_posix(),
                action="created-by-skills_install",
                undo_command=(
                    f"rm -rf {install_root.as_posix()}   # only if skills_install populated it; "
                    "runtime_install does not write this root"
                ),
            )
        )
    rows.append(
        UndoRow(
            path=(coordination_root / "AGENTS.md").as_posix(),
            action="restored",
            undo_command=(
                "re-run the installer with no experiment selected "
                "(RuntimeInstallRequest(experiment=None)) to copy the legacy startup chain back"
            ),
        )
    )
    return tuple(rows)


def rollback_payload(rows: Sequence[UndoRow]) -> list[dict[str, str]]:
    return [
        {"path": row.path, "action": row.action, "undoCommand": row.undo_command} for row in rows
    ]

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
``legacy`` or ``refused``. Not selected is ``legacy``: the run keeps the old startup chain's
unmodified install path, but the cutover described below still retires the ``AGENTS.md`` copies.
Selected and able is ``capsule``. Selected and unable is ``refused``, naming the failure: it is
never ``legacy``. A runtime failure therefore reports itself and preserves the selected mode; it
never quietly runs the old startup chain.

**The record.** :class:`ExperimentRunRecord` is the packet's example run record (``experiment``,
``source``, ``instructionSource``) plus the delivery mode and the capability detail that produced
it. It is one record for one run, returned to the caller that ran the install and written beside
it — an example-shaped record, not a new configuration framework.

**What the cutover is.** Every accepted install removes the retired coordinator
``AGENTS.md``, ``tasks/AGENTS.md`` and ``system/AGENTS.md`` files through the existing
capsule cutover. No delivery mode installs or restores these files. No skill tree is
installed beside the runtime: canonical skills are authored in the source tree and
served from the packaged copy by the tool server's resource surface.
:data:`WITHHELD_STARTUP_TARGETS` names the removed paths in the experiment report.
The per-run experiment selection still controls its capability probe and record;
removing the experiment selection does not restore retired instruction copies.

**What this module is not.** It does not compile a capsule, does not decide what a role launch
carries as instructions (that is the value ``serving/launch_capsule.py`` holds for each launch), and
does not touch a user-level harness configuration. It decides what an *installation* is, and it is
the only place that decides it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

EXPERIMENT_ENV = "AR_EXPERIMENT"
"""The per-run selection input. An environment variable of the run, never a settings key."""

ROLE_CAPSULES = "role-capsules"
"""The one experiment this installation supports."""

EXPERIMENTS = (ROLE_CAPSULES,)
"""The closed vocabulary. Adding an experiment is a deliberate edit here, not a settings value."""

CORPUS_ANCHOR = Path("skills") / "l-01-agent-lifecycles" / "composition-manifest.json"
"""The canonical corpus anchor this installation digests: routing metadata for the authored tree."""

RETIRED_STARTUP_TARGETS = ("AGENTS.md", "system/AGENTS.md", "tasks/AGENTS.md")
"""Earlier-install startup targets removed by every accepted installation."""

WITHHELD_STARTUP_TARGETS = RETIRED_STARTUP_TARGETS
"""Retired startup targets reported as withheld by an experimental install.

This cutover removes only the three coordinator files named above.
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


def corpus_anchor_path(source_root: Path) -> Path:
    """The canonical corpus anchor inside one install source root."""

    return source_root / "runtime" / CORPUS_ANCHOR


def probe_capabilities(*, source_root: Path) -> CapabilityReport:
    """Probe every capability the experimental installation needs, before anything is written.

    ``source_root`` is the install source (the package data root for a packaged run, an explicit
    tree for a checkpoint run). Blocking capabilities decide the delivery mode: any failure is a
    refusal, never a fallback.
    """

    anchor = corpus_anchor_path(source_root)
    anchor_digest = file_digest(anchor)
    return CapabilityReport(
        capabilities=(
            Capability(
                name="canonical-corpus-anchor",
                ok=bool(anchor_digest),
                blocking=True,
                detail=f"sha256:{anchor_digest}" if anchor_digest else f"missing: {anchor}",
                remedy=(
                    "the canonical corpus anchor is not installed with the runtime assets; run "
                    "scripts/sync-skills.py and scripts/sync-runtime.py"
                ),
            ),
        )
    )


@dataclass(frozen=True)
class ExperimentRunRecord:
    """One run's experiment record, in the packet's shape plus its delivery detail.

    ``source`` is the installation source's own identity and ``instructionSource`` is the canonical
    corpus digest — the two values a second run compares to prove it reproduced the same source and
    corpus versions.
    """

    experiment: str
    source: str
    instruction_source: str
    mode: str
    reason: str
    selection_source: str
    corpus_anchor: str
    capabilities: tuple[Capability, ...]

    def payload(self) -> dict[str, object]:
        return {
            "experiment": self.experiment,
            "source": self.source,
            "instructionSource": self.instruction_source,
            "mode": self.mode,
            "reason": self.reason,
            "selectionSource": self.selection_source,
            "corpusAnchor": self.corpus_anchor,
            "capabilities": [item.payload() for item in self.capabilities],
        }


@dataclass(frozen=True)
class InstallTargets:
    """Where one install reads from, plus the identity of that source.

    One value so a caller cannot pass a source identity that belongs to a different root
    than the one the record's digests were taken from.
    """

    source_root: Path
    source: str


def build_run_record(
    selection: ExperimentSelection,
    delivery: InstructionDelivery,
    report: CapabilityReport,
    targets: InstallTargets,
) -> ExperimentRunRecord:
    """One run's record, built from the values the run itself resolved."""

    return ExperimentRunRecord(
        experiment=selection.experiment or "",
        source=targets.source,
        instruction_source=(
            f"sha256:{report.corpus_anchor_digest}" if report.corpus_anchor_digest else ""
        ),
        mode=delivery.mode,
        reason=delivery.reason,
        selection_source=selection.source,
        corpus_anchor=str(corpus_anchor_path(targets.source_root)),
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

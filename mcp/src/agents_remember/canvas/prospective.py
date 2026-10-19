"""The model-free *load* of a prospective revision, before its ready pointer moves (MIK-R104 rule 14).

:mod:`agents_remember.canvas.readiness` reads the exact bytes about to be published and refuses a
page that names something it does not carry. It never executes anything: it is a static check and
says so in its notes. Rule 14 asks for more than that -- the revision is *loaded* once, without a
model, before the frame's ready pointer moves -- so this module defines the port the presenter runs
at that point and the three honest answers it can give:

``loaded``
    a browser opened the exact prospective assets from a loopback server that serves only them,
    under the served policy headers, with the reader's reduced-motion preference in force, and
    observed no console error, no failed request, no request outside the revision, no unresolved
    evidence reference and no motion the preference could not stop. Only then may publication
    proceed.
``failed``
    something *was* observed: the diagnostics name the console errors, the foreign requests, the
    unresolved references and the reduced-motion result. A failed load refuses publication and
    preserves the last ready revision.
``unavailable``
    nothing could be observed, so nothing is claimed. A missing browser, a missing script, a load
    that did not finish, or an answer this module cannot read all land here. This is deliberately
    not a pass: a check that did not run proves nothing (rule 10's arrangement, applied to rule 14).

:class:`StaticProspectiveLoad` is the bridge for an environment with no browser wired: it runs the
static check and answers ``failed`` when that check fails and ``unavailable`` when it passes,
because a static check has not loaded the page. It exists so a caller can *name* that limitation
instead of silently replacing the load with a byte scan.

The browser run is the packaged ``canvas/canvas-prospective-load.mjs``: it writes the assets to a temporary
directory, serves that directory (and nothing else) from a random loopback port with the same
headers :mod:`agents_remember.canvas.policy` produces, opens it in Chromium under
``prefers-reduced-motion: reduce``, and prints one JSON object. It runs no model.
Both evidence attributes are retained through a bounded two-second post-load observation.
Paint suppression checks that visible text, controls or graphics reach the reduced-motion viewport;
it judges no explanation quality and makes no claim about arbitrary future script execution.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from agents_remember.canvas.readiness import check_page, evidence_reference_state
from agents_remember.models.knowledge.review_evidence import EvidencePacket

__all__ = [
    "DEFAULT_LOAD_SCRIPT",
    "BrowserProspectiveLoad",
    "ProspectiveLoad",
    "ProspectiveResult",
    "StaticProspectiveLoad",
    "default_load_script",
]

#: The browser runner ships beside this module in both source and installed builds.
DEFAULT_LOAD_SCRIPT = "canvas-prospective-load.mjs"
_SCRIPT_ENV = "AR_CANVAS_PROSPECTIVE_LOAD"

#: How long one browser load may take before it is reported as not having finished.
DEFAULT_TIMEOUT_SECONDS = 180.0

#: How much of a child's stderr a diagnostic keeps.
_STDERR_TAIL = 400


def default_load_script() -> Path:
    """The package-owned runner; an explicit override is for an instrumented load."""

    override = os.environ.get(_SCRIPT_ENV)
    if override:
        return Path(override)
    return Path(__file__).with_name(DEFAULT_LOAD_SCRIPT)


@dataclass(frozen=True)
class ProspectiveResult:
    """What one model-free load of one prospective revision established.

    ``kind`` names how it was established (``browser`` or ``static``) so a reader is never told a
    byte scan was a load. Every observed fault has its own tuple, and :meth:`diagnostics` renders
    all of them into the sentence a refusal carries -- the presenter never reduces a failed load to
    "not ready".
    """

    state: Literal["loaded", "failed", "unavailable"]
    kind: Literal["browser", "static"]
    errors: tuple[str, ...] = ()
    foreign_requests: tuple[str, ...] = ()
    reduced_motion_ok: bool = True
    unresolved_references: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    detail: str = ""

    @property
    def loaded(self) -> bool:
        return self.state == "loaded"

    def diagnostics(self) -> str:
        """Every observation this result carries, as the detail of a refusal."""

        parts = [f"the model-free prospective load ({self.kind}) is {self.state}"]
        if self.detail:
            parts.append(self.detail)
        if self.errors:
            parts.append(f"errors: {_joined(self.errors)}")
        if self.foreign_requests:
            parts.append(f"requests outside the revision: {_joined(self.foreign_requests)}")
        if self.unresolved_references:
            parts.append(f"unresolved evidence references: {_joined(self.unresolved_references)}")
        if not self.reduced_motion_ok:
            parts.append("the page is not usable with prefers-reduced-motion: reduce")
        return "; ".join(parts)


def _joined(values: tuple[str, ...], limit: int = 6) -> str:
    shown = "; ".join(values[:limit])
    return shown if len(values) <= limit else f"{shown}; … (+{len(values) - limit} more)"


class ProspectiveLoad(Protocol):
    """The load port the presenter runs on the exact prospective revision before publication.

    A conforming load answers ``loaded`` only when it *observed* the page working; anything it did
    not observe, and anything it could not observe, is ``failed`` or ``unavailable`` respectively.
    """

    def load(
        self, *, assets: dict[str, bytes], entrypoint: str, packet: EvidencePacket
    ) -> ProspectiveResult: ...


def _strings(value: object, limit: int = 40) -> tuple[str, ...]:
    """A tolerant reader for a child's list of strings: anything else contributes nothing."""

    if not isinstance(value, (list, tuple)):
        return ()
    found = [item for item in value if isinstance(item, str) and item.strip()]
    return tuple(found[:limit])


def _flag(value: object, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _declared_state(value: object) -> Literal["loaded", "failed", "unavailable"] | None:
    """The state a child named, when it named one this port knows."""

    if value == "loaded":
        return "loaded"
    if value == "failed":
        return "failed"
    if value == "unavailable":
        return "unavailable"
    return None


class _UnsafeAsset(ValueError):
    """An asset path that would leave the staging directory."""


@dataclass(frozen=True)
class _Invocation:
    """One child run, or the reason no child ran at all."""

    ran: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    problem: str = ""


class BrowserProspectiveLoad:
    """Load the prospective revision in Chromium through the package-owned browser runner.

    The assets are staged into a fresh temporary directory and served from a random loopback port;
    the child prints one JSON object. A missing ``node``, a missing script, a run that did not
    finish, or an answer this module cannot read are all ``unavailable``: the load did not happen,
    and publication must not treat that as success.
    """

    def __init__(
        self,
        *,
        script: Path | None = None,
        node: str = "node",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        screenshot: Path | None = None,
    ) -> None:
        self._script = script if script is not None else default_load_script()
        self._node = node
        self._timeout = timeout_seconds
        self._screenshot = screenshot

    def load(
        self, *, assets: dict[str, bytes], entrypoint: str, packet: EvidencePacket
    ) -> ProspectiveResult:
        """Stage the exact assets, load them once without a model, and report what was observed."""

        if entrypoint not in assets:
            return ProspectiveResult(
                state="failed",
                kind="browser",
                errors=(f"the revision holds no {entrypoint!r}",),
                detail="nothing was staged: the entrypoint is not among the admitted assets",
            )
        if not self._script.is_file():
            return ProspectiveResult(
                state="unavailable",
                kind="browser",
                errors=(f"the browser load script is absent at {self._script}",),
                detail=(
                    "no browser ran, so this build cannot show the prospective revision loaded; "
                    "publication is refused rather than passed on the static check alone"
                ),
            )
        directory = Path(tempfile.mkdtemp(prefix="ar-prospective-load-"))
        try:
            try:
                _stage(assets, directory)
            except _UnsafeAsset as refusal:
                return ProspectiveResult(
                    state="failed",
                    kind="browser",
                    errors=(str(refusal),),
                    detail="the revision names an asset path that leaves its own staging directory",
                )
            invocation = self._invoke(directory, entrypoint)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if not invocation.ran:
            return ProspectiveResult(
                state="unavailable",
                kind="browser",
                errors=(invocation.problem,),
                detail="the load did not run, so nothing about the page was observed",
            )
        result = _read_result(invocation)
        unknown = tuple(
            reference
            for reference in result.evidence_references
            if evidence_reference_state(packet, reference) is None
        )
        if unknown:
            result = replace(
                result,
                state="failed",
                unresolved_references=(*result.unresolved_references, *unknown),
                detail="the rendered DOM cites references absent from the bound evidence packet",
            )
        return result

    def _invoke(self, directory: Path, entrypoint: str) -> _Invocation:
        """Run the child once, answering ``ran=False`` with a named reason when it did not."""

        command = [self._node, str(self._script), "--dir", str(directory), "--entry", entrypoint]
        if self._screenshot is not None:
            command.extend(["--screenshot", str(self._screenshot)])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError:
            return _Invocation(
                ran=False,
                problem=(
                    f"no node executable at {self._node!r}, so the browser load could not be "
                    "started"
                ),
            )
        except subprocess.TimeoutExpired:
            return _Invocation(
                ran=False,
                problem=f"the browser load did not finish within {self._timeout:g}s",
            )
        except OSError as error:
            return _Invocation(ran=False, problem=f"the browser load could not start: {error}")
        return _Invocation(
            ran=True,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )


def _stage(assets: dict[str, bytes], directory: Path) -> None:
    """Write the exact prospective bytes below ``directory``, refusing a path that leaves it."""

    for relative, payload in sorted(assets.items()):
        parts = relative.split("/")
        if (
            not relative
            or relative.startswith("/")
            or "\\" in relative
            or ".." in parts
            or "" in parts
        ):
            raise _UnsafeAsset(f"the asset path {relative!r} is not a plain relative path")
        target = directory.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _unreadable(invocation: _Invocation, extra: str = "") -> ProspectiveResult:
    """The child ran but its answer cannot be read: that is an unavailable load, never a pass."""

    tail = invocation.stderr.strip()[-_STDERR_TAIL:]
    parts = [f"the browser load answered with no readable result (exit {invocation.returncode})"]
    if extra:
        parts.append(extra)
    if tail:
        parts.append(f"stderr: {tail}")
    stdout = invocation.stdout.strip()
    if stdout and not extra:
        parts.append(f"stdout: {stdout[:200]}")
    detail = "; ".join(parts)
    return ProspectiveResult(
        state="unavailable",
        kind="browser",
        errors=(detail,),
        detail="an unreadable answer is not evidence that the page loaded",
    )


def _read_result(invocation: _Invocation) -> ProspectiveResult:
    """Parse the child's one JSON object into a result, refusing to trust a self-contradiction."""

    try:
        payload = json.loads(invocation.stdout.strip())
    except json.JSONDecodeError:
        return _unreadable(invocation)
    if not isinstance(payload, dict):
        return _unreadable(invocation)
    declared = _declared_state(payload.get("state"))
    if declared is None:
        return _unreadable(invocation, extra=f"it named the state {payload.get('state')!r}")
    if declared == "unavailable":
        return ProspectiveResult(
            state="unavailable",
            kind="browser",
            errors=_strings(payload.get("errors")),
            detail=str(payload.get("detail", "no browser observed the page")),
        )
    references = payload.get("evidenceReferences")
    if (
        not isinstance(references, list)
        or len(references) > 400
        or any(not isinstance(reference, str) or len(reference) > 512 for reference in references)
    ):
        return _unreadable(invocation, extra="no bounded rendered evidence-reference inventory")
    detail = payload.get("detail")
    result = ProspectiveResult(
        state=declared,
        kind="browser",
        errors=_strings(payload.get("errors")),
        foreign_requests=_strings(payload.get("foreignRequests")),
        reduced_motion_ok=_flag(payload.get("reducedMotionOk"), default=declared != "loaded"),
        unresolved_references=_strings(payload.get("unresolvedReferences")),
        evidence_references=tuple(references),
        detail=detail if isinstance(detail, str) else "",
    )
    return _consistent(result)


def _observed_faults(result: ProspectiveResult) -> tuple[str, ...]:
    """Every observation a ``loaded`` answer cannot survive, named."""

    faults: list[str] = []
    if result.errors:
        faults.append(f"{len(result.errors)} console or load error(s)")
    if result.foreign_requests:
        faults.append(f"{len(result.foreign_requests)} request(s) outside the revision")
    if result.unresolved_references:
        faults.append(f"{len(result.unresolved_references)} unresolved evidence reference(s)")
    if not result.reduced_motion_ok:
        faults.append("the page is not usable with reduced motion")
    return tuple(faults)


def _consistent(result: ProspectiveResult) -> ProspectiveResult:
    """Downgrade a ``loaded`` answer that names its own faults: a pass may not contradict itself."""

    if result.state != "loaded":
        return result
    faults = _observed_faults(result)
    if not faults:
        return result
    detail = result.detail or "the load reported no detail"
    return ProspectiveResult(
        state="failed",
        kind=result.kind,
        errors=result.errors,
        foreign_requests=result.foreign_requests,
        reduced_motion_ok=result.reduced_motion_ok,
        unresolved_references=result.unresolved_references,
        detail=f"the load called itself loaded while naming {', '.join(faults)}; {detail}",
    )


@dataclass
class StaticProspectiveLoad:
    """The static check standing in a load's place, and saying so.

    It is useful exactly because it never claims a pass: a page the static check refuses is
    ``failed`` with that check's diagnostics, and a page it accepts is ``unavailable`` -- the bytes
    resolve, but no page was loaded.
    """

    kind: str = "static"

    def load(
        self, *, assets: dict[str, bytes], entrypoint: str, packet: EvidencePacket
    ) -> ProspectiveResult:
        report = check_page(assets, entrypoint=entrypoint, packet=packet)
        if report.failures:
            return ProspectiveResult(
                state="failed",
                kind="static",
                errors=tuple(f"{item.name}: {item.detail}" for item in report.failures),
                detail="the static readiness check refused these bytes",
            )
        return ProspectiveResult(
            state="unavailable",
            kind="static",
            errors=(),
            detail=(
                "the static readiness check passed, but a static check has not loaded the page: "
                "no browser opened these bytes, so no ready pointer may move on this answer"
            ),
        )

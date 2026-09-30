"""Resolving a requirement endpoint of the text knowledge format (MIK-R13 rule 4, MIK-R21 rule 6).

A record's link or reference may name a requirement packet by ``{ task, packet, id, version }``
(:class:`…knowledge_files.shapes.RequirementReference`). ``task`` is ``{ repository, path }``, with
``path`` relative to ``tasks/<repository>/`` of the coordination root, so the owning task's root is
``<coordination root>/tasks/<repository>/<path>``.

The **requirement owner remains the resolver**: this module only locates that task root and hands
``{ packet, id, version }`` to :func:`.requirement_owner.consume_owner_resolution`, which carries
the owner's own answer back verbatim. Two answers are this module's own, and both are about the
root, never the packet:

* ``requirement-task-plane-unavailable`` -- the caller has no coordination root (for example a
  standalone run);
* ``requirement-task-outside-tasks`` -- ``repository`` is not one plain directory name, so the
  task root would leave ``tasks/``.

An endpoint that does not resolve is **reported as unresolved, never refused**: the record keeps
it exactly as written. The resolved task root is returned so a later reader -- MIK-R14's lookup of a
newer approved version in that task's ``requirements/manifest.json`` -- starts from the same place.

**Newer approved version (MIK-R14 rule 2).** :func:`latest_approved_requirement_version` reads the
owning task's ``requirements/manifest.json`` (format ``approved-requirement-corpus``) and returns the
highest version among its ``packets`` entries with that stable ID and ``state: approved``, comparing
the integer after ``v``. :func:`requirement_approval` is the same lookup with its reason: a task
without a manifest -- or with one that is not an approved-requirement corpus this lookup can read --
has approval state ``unknown`` and never triggers a reconsideration.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from agents_remember.memory.knowledge.requirement_owner import consume_owner_resolution
from agents_remember.models.knowledge.requirement import RequirementOwnerRef
from agents_remember.models.knowledge_files.shapes import RequirementReference

TASK_PLANE_UNAVAILABLE: Final = "requirement-task-plane-unavailable"
TASK_OUTSIDE_TASKS: Final = "requirement-task-outside-tasks"

EndpointState = Literal["resolved", "unresolved"]
ApprovalState = Literal["approved", "not_approved", "unknown"]

MANIFEST_PATH: Final = "requirements/manifest.json"
MANIFEST_FORMAT: Final = "approved-requirement-corpus"
_VERSION: Final = re.compile(r"^v(?P<number>[1-9][0-9]*)$")


@dataclass(frozen=True)
class RequirementEndpoint:
    """One requirement endpoint and the owner's answer about it.

    ``task_root`` is the owning task's directory whenever it could be located, resolved or not.
    ``code`` and ``detail`` are the owner's refusal, verbatim, or one of this module's two root
    answers; both are empty when the endpoint resolved.
    """

    reference: RequirementReference
    state: EndpointState
    task_root: Path | None
    code: str = ""
    detail: str = ""

    @property
    def key(self) -> str:
        """``<repository>/<task path>#<id>@<version>``, the index's spelling of the endpoint."""

        task = self.reference.task
        return f"{task.repository}/{task.path}#{self.reference.id}@{self.reference.version}"


def requirement_task_root(coordination_root: Path, reference: RequirementReference) -> Path | None:
    """The owning task's root, or ``None`` when ``repository`` is not one plain directory name."""

    repository = reference.task.repository
    if "/" in repository or "\\" in repository or repository in {".", ".."}:
        return None
    return coordination_root / "tasks" / repository / reference.task.path


def resolve_requirement_endpoint(
    coordination_root: Path | None, reference: RequirementReference
) -> RequirementEndpoint:
    """Ask the requirement owner whether ``reference`` names an existing packet of that version."""

    if coordination_root is None:
        return RequirementEndpoint(
            reference,
            "unresolved",
            None,
            TASK_PLANE_UNAVAILABLE,
            "no coordination root was given, so the owning task cannot be read",
        )
    task_root = requirement_task_root(coordination_root, reference)
    if task_root is None:
        return RequirementEndpoint(
            reference,
            "unresolved",
            None,
            TASK_OUTSIDE_TASKS,
            f"task repository {reference.task.repository!r} is not one directory under tasks/",
        )
    resolution = consume_owner_resolution(
        task_root,
        RequirementOwnerRef(
            path=reference.packet, stableId=reference.id, version=reference.version
        ),
    )
    if resolution.state == "resolved":
        return RequirementEndpoint(reference, "resolved", task_root)
    return RequirementEndpoint(
        reference,
        "unresolved",
        task_root,
        resolution.refusal_code or "",
        resolution.refusal_detail or "",
    )


def version_number(version: str) -> int | None:
    """The integer after ``v`` in a packet version (``v12`` -> 12), or ``None``."""

    matched = _VERSION.match(version)
    return None if matched is None else int(matched["number"])


@dataclass(frozen=True)
class RequirementApproval:
    """What the owning task's manifest says about one stable ID.

    ``latest`` is the highest approved version (``approved``) and ``packet`` its packet path relative
    to the task root (``requirements/<file>``, from the entry's ``file``; ``None`` when the entry
    names none); ``not_approved`` means the manifest lists no approved entry for the ID; ``unknown``
    means there is no manifest this lookup can read, and ``detail`` says why.
    """

    state: ApprovalState
    latest: str | None = None
    detail: str = ""
    packet: str | None = None

    def newer_than(self, version: str) -> bool:
        """Whether the latest approved version is newer than ``version``."""

        latest = None if self.latest is None else version_number(self.latest)
        current = version_number(version)
        return latest is not None and current is not None and latest > current


def _manifest(task_root: Path) -> dict[str, Any] | str:
    path = task_root / MANIFEST_PATH
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"the task has no {MANIFEST_PATH}"
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return f"{MANIFEST_PATH} cannot be read: {error}"
    if not isinstance(document, dict) or document.get("format") != MANIFEST_FORMAT:
        return f"{MANIFEST_PATH} is not an {MANIFEST_FORMAT!r} manifest"
    if not isinstance(document.get("packets"), list):
        return f"{MANIFEST_PATH} has no 'packets' list"
    return document


def _approved_entries(packets: list[Any], stable_id: str) -> list[tuple[int, str, Any]]:
    """``(version number, version, file)`` of each approved manifest entry of ``stable_id``."""

    found = []
    for entry in packets:
        if not isinstance(entry, dict) or entry.get("id") != stable_id:
            continue
        version = entry.get("version")
        number = version_number(version) if isinstance(version, str) else None
        if entry.get("state") == "approved" and number is not None:
            found.append((number, str(version), entry.get("file")))
    return found


def requirement_approval(task_root: Path, stable_id: str) -> RequirementApproval:
    """The owning task's approval of ``stable_id``: its highest approved version, or why unknown."""

    manifest = _manifest(task_root)
    if isinstance(manifest, str):
        return RequirementApproval("unknown", None, manifest)
    approved = _approved_entries(manifest["packets"], stable_id)
    if not approved:
        return RequirementApproval("not_approved", None, f"no approved {stable_id} entry")
    _number, latest, file = max(approved, key=lambda one: one[0])
    packet = f"requirements/{file}" if isinstance(file, str) and file else None
    return RequirementApproval("approved", latest, packet=packet)


def latest_approved_requirement_version(task_root: Path, stable_id: str) -> str | None:
    """The highest approved version of ``stable_id`` in the task's manifest (MIK-R14 rule 2).

    ``None`` when the task has no readable ``approved-requirement-corpus`` manifest or the manifest
    approves no version of the ID.
    """

    return requirement_approval(task_root, stable_id).latest

"""Private converted carryover units and official-tree anchors (MIK-R56 rules 6-8).

These helpers read only. The planner owns evidence/merge-base/selection policy; apply owns
transactions and validation. ``fresh_document_payloads`` returns both files, never history.
Pass an opened *official* code tree, not source HEAD or a working-tree fallback.
"""

from __future__ import annotations

import stat
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from agents_remember.application.knowledge_currentness.observe import (
    OpenedCodeTree,
    observe_entry,
)
from agents_remember.application.knowledge_writer.carry import mapped_anchor
from agents_remember.memory.knowledge_index import Entry
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    file_sidecar_path,
    parse_document,
    parse_document_text,
    route_sidecar_path,
)
from agents_remember.models.knowledge_files.shapes import AnchorTarget, require_repository_path
from agents_remember.models.knowledge_files.sidecars import FileSidecar, LayoutMarker, RouteSidecar
from agents_remember.worktrees.cutover_lock import cutover_lock_refusal, legacy_format_refusal


def safe_memory_path(root: Path, relative: str) -> Path:
    """Admit a regular file or missing member, never a symlink or special file."""
    require_repository_path(relative)
    if root.is_symlink():
        raise ValueError(f"memory root is a symlink: {root}")
    boundary = root.resolve(strict=True)
    path = boundary
    for part in PurePosixPath(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"memory path is a symlink: {relative}")
    if not path.resolve().is_relative_to(boundary):
        raise ValueError(f"memory path escapes its root: {relative}")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return path
    if not stat.S_ISREG(mode):
        raise ValueError(f"memory member is not a regular file: {relative}")
    return path


def require_matching_layout(source: Path, target: Path) -> str:
    """Read only the two markers before any other memory; return their conversion version."""
    try:
        markers = [
            parse_document_text(safe_memory_path(root, LAYOUT_MARKER_PATH).read_text())
            for root in (source, target)
        ]
        if not all(isinstance(marker, LayoutMarker) for marker in markers):
            raise ValueError("not a converted layout marker")
        versions = [marker.conversion for marker in markers if isinstance(marker, LayoutMarker)]
        if versions[0] != versions[1]:
            raise ValueError(f"conversion versions differ: {versions}")
    except (OSError, ValueError) as error:
        raise ValueError(
            f"carryover requires matching converted memory: {error}; "
            "run knowledge-convert and the crossing sync"
        ) from error
    return versions[0]


def document_paths(markdown: str) -> tuple[str, str]:
    """The indivisible Markdown/JSON unit; indexes, records and history are not documents."""
    require_repository_path(markdown)
    if not markdown.endswith(".md") or not (
        markdown.startswith("onboarding/") or markdown == "entities.md"
    ):
        raise ValueError(f"not a carryover document: {markdown}")
    return markdown, str(PurePosixPath(markdown).with_suffix(".json"))


def _sidecar(document: Mapping[str, Any], relative: str) -> FileSidecar | RouteSidecar:
    model = parse_document(document)
    if not isinstance(model, FileSidecar | RouteSidecar):
        raise ValueError(f"not an onboarding sidecar: {relative}")
    expected = (
        file_sidecar_path(model.path)
        if isinstance(model, FileSidecar)
        else route_sidecar_path(model.path)
    )
    if relative not in {"entities.json", expected}:
        raise ValueError(f"sidecar path mismatch: {relative}, expected {expected}")
    return model


@dataclass(frozen=True)
class DocumentUnit:
    """Source bytes, paired paths and the schema-validated JSON (not reformatted on read)."""

    paths: tuple[str, str]
    markdown: bytes
    sidecar: bytes
    document: dict[str, Any]


def load_document_unit(root: Path, markdown: str) -> DocumentUnit:
    paths = document_paths(markdown)
    prose, facts = [safe_memory_path(root, path).read_bytes() for path in paths]
    document = parse_json(facts.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"sidecar must be a JSON object: {paths[1]}")
    _sidecar(document, paths[1])
    return DocumentUnit(paths, prose, facts, document)


@dataclass(frozen=True)
class PairComparison:
    """JSONable pair-level facts; missing source components are never silently selected."""

    paths: tuple[str, str]
    source_missing: tuple[str, ...]
    target_missing: tuple[str, ...]
    differing_paths: tuple[str, ...]
    identical: bool
    target_exists: bool

    def to_document(self) -> dict[str, Any]:
        return asdict(self)


def compare_document_pair(source: Path, target: Path, markdown: str) -> PairComparison:
    paths = document_paths(markdown)
    sides = [
        {
            path: file.read_bytes() if file.is_file() else None
            for path in paths
            for file in (safe_memory_path(root, path),)
        }
        for root in (source, target)
    ]
    missing = [tuple(path for path in paths if side[path] is None) for side in sides]
    differing = tuple(path for path in paths if sides[0][path] != sides[1][path])
    return PairComparison(
        paths,
        missing[0],
        missing[1],
        differing,
        not missing[0] and not differing,
        len(missing[1]) != len(paths),
    )


@dataclass(frozen=True)
class _AnchorSlot:
    location: tuple[str | int, ...]
    entry: Entry

    @property
    def label(self) -> str:
        return "/".join(map(str, self.location))


def _anchor_slots(document: dict[str, Any], relative: str) -> Iterator[_AnchorSlot]:
    model = _sidecar(document, relative)
    if isinstance(model, FileSidecar):
        for field, kind in (("realizes", "realization"), ("proves", "proof")):
            for number, entry in enumerate(document.get(field) or ()):
                typed_kind: Literal["realization", "proof"] = (
                    "proof" if kind == "proof" else "realization"
                )
                yield _AnchorSlot(
                    (field, number, "anchor"),
                    Entry(entry["id"], typed_kind, entry["invariant"], model.path, relative, entry),
                )
    for reference, value in model.references.items():
        for number, target in enumerate(value.targets):
            if isinstance(target, AnchorTarget):
                anchor = document["references"][reference]["targets"][number]["anchor"]
                path = target.anchor.path or model.path
                yield _AnchorSlot(
                    ("references", reference, "targets", number, "anchor"),
                    Entry(
                        f"reference[{reference}].target[{number}]",
                        "realization",
                        "",
                        path,
                        relative,
                        {"anchor": anchor},
                    ),
                )


def observe_document_anchors(
    document: dict[str, Any], sidecar_path: str, code: OpenedCodeTree
) -> dict[str, Any]:
    """Observe *every* realization, proof and code/test reference; zero anchors is not proof.

    ``all_current`` is suitable for the memory-only evidence prerequisite, never sufficient
    without the planner's unchanged-target-since-base check. ``problems`` names entry/reference
    locations and failed reads. A current moved anchor is recorded as ``needsReanchor``.
    """
    observations = []
    for slot in _anchor_slots(document, sidecar_path):
        observed = observe_entry(slot.entry, code)
        observations.append(
            {
                **observed.to_document(),
                "location": slot.label,
                "readFailed": observed.read_failed,
                "needsReanchor": observed.state == "current"
                and observed.observed_blob != observed.recorded_blob,
            }
        )
    problems = [
        f"{item['location']} ({item['id']}): {item['state']}: {item.get('reason', '')}"
        for item in observations
        if item["state"] != "current"
    ]
    return {
        "anchor_count": len(observations),
        "observations": observations,
        "all_current": bool(observations) and not problems,
        "problems": problems,
        "reanchor_required": any(item["needsReanchor"] for item in observations),
    }


def fresh_document_payloads(
    root: Path, markdown: str, code: OpenedCodeTree, *, reviewed: bool = False
) -> dict[str, bytes]:
    """Reread/reobserve at apply, then map current moved anchors using the writer API.

    Noncurrent anchors refuse by default. Explicitly reviewed candidates may retain them for
    the curator/worklist/validator; they are never mechanically reanchored. An anchorless
    document needs caller review policy. Source bytes stay unchanged when no anchor moves;
    moved sidecars use the existing canonical serializer. No file or history row is written.
    """
    unit = load_document_unit(root, markdown)
    observation = observe_document_anchors(unit.document, unit.paths[1], code)
    if observation["problems"] and not reviewed:
        raise ValueError("; ".join(observation["problems"]))
    movable = {item["location"] for item in observation["observations"] if item["needsReanchor"]}
    document = deepcopy(unit.document)
    moved = False
    for slot in _anchor_slots(document, unit.paths[1]):
        if slot.label not in movable:
            continue
        anchor = slot.entry.document["anchor"]
        blob = code.files.get(slot.entry.path)
        if code.trees is None:
            raise ValueError(code.problem or "official code tree is unavailable")
        fresh = mapped_anchor(code.trees, slot.entry.path, anchor, blob)
        if fresh is None or fresh["content"] != anchor["content"]:
            raise ValueError(f"cannot reanchor {slot.label} ({slot.entry.id}) at official code")
        owner: Any = document
        for part in slot.location[:-1]:
            owner = owner[part]
        owner[slot.location[-1]] = fresh
        moved = True
    _sidecar(document, unit.paths[1])
    facts = canonical_text(document).encode("utf-8") if moved else unit.sidecar
    return {unit.paths[0]: unit.markdown, unit.paths[1]: facts}


def require_converted_formats(source: Path, target: Path, operation: str) -> str:
    for root in (target, source):
        if not root.is_dir():
            raise RuntimeError(f"carryover memory root does not exist or is unreadable: {root}")
        if not (root / LAYOUT_MARKER_PATH).exists():
            refusal = cutover_lock_refusal(root, operation=operation, line=str(root))
            raise RuntimeError(
                refusal
                or legacy_format_refusal(
                    root, operation=operation, subject=f"the unconverted memory at {root}"
                )
            )
    try:
        return require_matching_layout(source, target)
    except (ValueError, OSError) as error:
        raise RuntimeError(f"carryover conversion-format/layout refusal: {error}") from error


def memory_only_evidence(
    target: Path, relative: str, anchors: dict[str, Any], base: Mapping[str, bytes] | None
):
    if not anchors["all_current"]:
        return "unverifiable", "review-required", "document has no anchors or noncurrent anchors"
    paths = (relative, relative[:-3] + ".json")
    unchanged = base is not None and all(
        base.get(path) == ((target / path).read_bytes() if (target / path).is_file() else None)
        for path in paths
    )
    if not unchanged:
        return (
            "target-memory-moved",
            "review-required",
            "target pair changed since memory merge-base or no base resolves",
        )
    return (
        "memory-only-anchors-current",
        "auto-carry",
        "all anchors current; target pair unchanged since memory merge-base",
    )


def document_selection(source: Path, target: Path, relative: str, landed: set[str]):
    """Stable key/kind of a memory document; malformed pairs remain review candidates."""
    key = relative.removeprefix("onboarding/").removesuffix(".md")
    kind = "memory-only-doc"
    try:
        unit = load_document_unit(source, relative)
    except (OSError, ValueError):
        pass  # the candidate names the exact unreadable pair; no unit is written
    else:
        key = str(unit.document["path"])
        route = unit.document.get("schema") == "ar-onboarding-route/v1"
        if route and any(key == "." or path.startswith(key + "/") for path in landed):
            kind = "route-overview"
    if relative == "onboarding/entities.md":
        key, kind = "entity-catalog", "entity-catalog"
    return key, kind, compare_document_pair(source, target, relative)

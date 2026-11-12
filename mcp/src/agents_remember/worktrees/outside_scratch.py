"""One enclosure generation's scratch root outside ``~/projects`` (MIK-R207).

Some work cannot live in an enclosure's own ``scratch/``: a sandbox must leave the marker
ancestry of the projects home (a package install below it would be captured), and a tool may
refuse a long scratch path (the L195 ruling). Agents used to pick such folders by hand and
nothing removed them, which is how hundreds of gigabytes of landed leaves collected in
``/var/tmp``.

This module owns that place: a product-owned parent under ``/var/tmp/agents-remember/``, one
short root per enclosure generation, a marker inside the root that names the exact repository,
enclosure, generation and path, and the removal of a root whose record says it belongs to a
generation that has ended. Nobody has to remember a deletion.

The identity is derived, never guessed:

* the generation is ``sha256`` over the repository, the exact enclosure contract path and the
  predecessor generation, so the same generation always computes the same root and a crash
  between creating the folder and recording it leaves a root the reconcile can still attribute;
* the marker's bytes are a function of that identity alone, so recreating a root that
  ``systemd-tmpfiles`` aged writes the same digest the enclosure already records;
* a root is deleted only when its marker matches a retained record and that record's generation
  is proven ended. A name is never evidence of ownership (rule 5).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, TypedDict

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.worktrees.worktree_contract import load_contract

OUTSIDE_SCRATCH_SCHEMA = "ar-outside-scratch-root/v1"
"""The marker schema. A root carrying anything else is not this product's root."""

OUTSIDE_SCRATCH_PARENT = Path("/var/tmp/agents-remember")
"""The product-owned parent of every root. Never the shared sticky ``/var/tmp`` itself."""

MARKER_NAME = ".agents-remember-root.json"
"""The marker's name inside a root, and the only file that proves who owns it."""

LIFECYCLE_DIRECTORY = ".lifecycle"
ENCLOSURE_MANIFEST = "enclosure-manifest.json"
"""The immutable enclosure manifest the publication writes before its contract."""

MANAGED_MODE = 0o700
"""The mode of the parent and of every root: private to the user that owns the installation."""

SOCKET_PATH_LIMIT = 108
"""Linux admits one unix domain socket path of 108 bytes including its terminator."""

SANDBOX_SOCKET_SUFFIX = "/run/tmux/tmux-{uid}/default"
"""The longest path the sandbox builds below its root (``pnt_sandbox``'s tmux server socket)."""

LONGEST_UID = 4_294_967_295
"""``uid_t``'s maximum, so the budget holds for any account the platform can hand out."""

PARTIAL_PREFIX = ".partial-"
"""A root being built. Never addressable, so a leftover of it is always ours to remove."""

RECONCILE_CURSOR = ".reconcile-cursor"
"""Where a bounded sweep resumes, so one pass never re-reads the same prefix."""

MAX_ROOTS = 512
"""How many roots one reconcile pass inspects. Beyond this the pass reports and stops."""


class OutsideScratchError(Exception):
    """A root that cannot be allocated, read or removed without guessing."""


class ContractRootCarrier(Protocol):
    """The contract facts an outside root is derived from (avoiding an import cycle)."""

    @property
    def repo_name(self) -> str: ...

    @property
    def coordination_root(self) -> Path: ...

    @property
    def contract_path(self) -> Path: ...


class ReconcileReport(TypedDict):
    """What one reconcile pass removed, and what it left with its reason."""

    removed: list[dict[str, Any]]
    reported: list[dict[str, Any]]


@dataclass(frozen=True)
class OutsideScratchRoot:
    """One enclosure generation's outside scratch root and the identity that proves it."""

    root: Path
    generation: str
    marker_sha256: str
    repository: str
    coordination_root: Path
    contract_path: Path

    @property
    def marker_path(self) -> Path:
        return self.root / MARKER_NAME

    def manifest_payload(self) -> dict[str, str]:
        """The record as the immutable enclosure manifest keeps it."""

        return {
            "path": self.root.as_posix(),
            "generation": self.generation,
            "markerSha256": self.marker_sha256,
        }

    def removal_entry(self, *, dry_run: bool) -> dict[str, object]:
        """The cleanup shape: what happened to this root, and why when nothing did."""

        return {
            "path": self.root.as_posix(),
            "would_remove" if dry_run else "removed": True,
        }


def generation_id(repository: str, contract_path: Path, predecessor_generation: str = "") -> str:
    """The generation identity of one enclosure publication.

    A successor carries its predecessor's generation in the hash, so the successor's root is
    never the terminal generation's root; a first generation passes nothing. The value is
    computable before the contract text that records it exists, which is what keeps the
    enclosure's own digests out of a cycle.
    """

    payload = "\n".join(
        (
            OUTSIDE_SCRATCH_SCHEMA,
            repository,
            contract_path.resolve(strict=False).as_posix(),
            predecessor_generation,
            "",
        )
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def root_path(generation: str, parent: Path | None = None) -> Path:
    """The short root of one generation: the parent plus twelve hex characters."""

    return managed_parent(parent) / f"ar-{generation[:12]}"


def marker_text(record: OutsideScratchRoot) -> str:
    """The marker's exact bytes: identity only, so recreation is byte-identical."""

    payload = {
        "schema": OUTSIDE_SCRATCH_SCHEMA,
        "repository": record.repository,
        "coordinationRoot": record.coordination_root.as_posix(),
        "contractPath": record.contract_path.as_posix(),
        "generation": record.generation,
        "path": record.root.as_posix(),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def record_for(
    contract: ContractRootCarrier,
    *,
    predecessor_generation: str = "",
    parent: Path | None = None,
) -> OutsideScratchRoot:
    """The root one publication of ``contract`` owns, computed without touching the disk."""

    generation = generation_id(
        contract.repo_name,
        contract.contract_path,
        predecessor_generation,
    )
    draft = OutsideScratchRoot(
        root=root_path(generation, parent),
        generation=generation,
        marker_sha256="",
        repository=contract.repo_name,
        coordination_root=Path(contract.coordination_root).resolve(strict=False),
        contract_path=Path(contract.contract_path).resolve(strict=False),
    )
    return replace(draft, marker_sha256=sha256_text(marker_text(draft)))


def recorded_root(contract: Any) -> OutsideScratchRoot | None:
    """The root a contract records, or ``None`` for an enclosure from before this change."""

    root = str(getattr(contract, "outside_scratch_root", "") or "")
    generation = str(getattr(contract, "outside_scratch_generation", "") or "")
    marker_sha256 = str(getattr(contract, "outside_scratch_marker_sha256", "") or "")
    if not (root and generation and marker_sha256):
        return None
    return OutsideScratchRoot(
        root=Path(root),
        generation=generation,
        marker_sha256=marker_sha256,
        repository=str(getattr(contract, "repo_name", "")),
        coordination_root=Path(contract.coordination_root).resolve(strict=False),
        contract_path=Path(contract.contract_path).resolve(strict=False),
    )


def recorded_generation(contract_path: Path) -> str:
    """The generation the contract file on disk records, or ``""`` when it records none.

    A successor is published over the contract file its terminal predecessor left behind, so
    the predecessor's generation is readable before the successor's text is written.
    """

    try:
        contract = load_contract(contract_path)
    except Exception:
        return ""
    recorded = recorded_root(contract)
    return recorded.generation if recorded is not None else ""


def published_root_record(contract: Any) -> OutsideScratchRoot | None:
    """The root the enclosure's own immutable manifest records for this generation.

    The manifest is written before the contract during publication, so it survives a crash
    between the two and is the exact record a retry must reuse rather than compute again.
    """

    manifest_path = Path(contract.worktree_group) / LIFECYCLE_DIRECTORY / ENCLOSURE_MANIFEST
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    entry = payload.get("outsideScratch")
    if not isinstance(entry, dict):
        return None
    path = str(entry.get("path") or "")
    generation = str(entry.get("generation") or "")
    marker_sha256 = str(entry.get("markerSha256") or "")
    if not (path and generation and marker_sha256):
        return None
    return OutsideScratchRoot(
        root=Path(path),
        generation=generation,
        marker_sha256=marker_sha256,
        repository=str(getattr(contract, "repo_name", "")),
        coordination_root=Path(contract.coordination_root).resolve(strict=False),
        contract_path=Path(contract.contract_path).resolve(strict=False),
    )


def with_root_record(
    contract: Any,
    *,
    predecessor_generation: str = "",
    new_generation: bool = False,
) -> Any:
    """The contract carrying this generation's root record, computed before any text exists.

    A publication that already recorded a root keeps it: a retry of the same generation must
    not chase a second identity, whose root would be a second root. ``new_generation`` says
    this publication opens a successor generation over a terminal one, so the predecessor's
    generation is folded in and the record is replaced.
    """

    if not new_generation:
        published = published_root_record(contract)
        if published is not None:
            return replace(
                contract,
                outside_scratch_root=published.root.as_posix(),
                outside_scratch_generation=published.generation,
                outside_scratch_marker_sha256=published.marker_sha256,
            )
        if recorded_root(contract) is not None:
            return contract
    record = record_for(contract, predecessor_generation=predecessor_generation)
    return replace(
        contract,
        outside_scratch_root=record.root.as_posix(),
        outside_scratch_generation=record.generation,
        outside_scratch_marker_sha256=record.marker_sha256,
    )


def ensure_recorded_root(contract: Any) -> dict[str, Any] | None:
    """Create the root a contract records, or ``None`` for an enclosure that records none."""

    record = recorded_root(contract)
    if record is None:
        return None
    return ensure_root(record)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def socket_path_bytes(root: Path) -> int:
    """The longest sandbox socket path built under ``root``, in bytes."""

    suffix = SANDBOX_SOCKET_SUFFIX.format(uid=LONGEST_UID)
    return len(root.as_posix().encode("utf-8")) + len(suffix.encode("utf-8"))


def require_socket_budget(record: OutsideScratchRoot) -> None:
    """Refuse a root whose sandbox socket path the platform would not admit."""

    observed = socket_path_bytes(record.root)
    if observed >= SOCKET_PATH_LIMIT:
        raise OutsideScratchError(
            f"the outside scratch root {record.root} builds a {observed}-byte sandbox socket "
            f"path; the platform admits {SOCKET_PATH_LIMIT - 1} bytes"
        )


def managed_parent(parent: Path | None = None) -> Path:
    """The parent a call acts on: the product's own unless a caller names another."""

    return OUTSIDE_SCRATCH_PARENT if parent is None else parent


def require_managed_parent(parent: Path | None = None) -> Path:
    """Return the parent when it is a real, private directory of ours, or refuse it.

    Both allocation and deletion validate it: a parent that is a link, somebody else's, or
    group-readable can be redirected or replaced between two calls, and a removal that trusted
    it would reach outside it.
    """

    resolved = managed_parent(parent)
    problem = _parent_problem(resolved)
    if problem is not None:
        raise OutsideScratchError(f"the outside scratch parent {resolved}: {problem}")
    return resolved


def _parent_problem(parent: Path) -> str | None:
    """Why the managed parent itself may not be used, or ``None`` when it may."""

    if parent.is_symlink():
        return "is a symlink"
    if not parent.is_dir():
        return "is not a directory"
    status = parent.stat()
    if status.st_uid != os.getuid():
        return f"belongs to uid {status.st_uid}, not {os.getuid()}"
    mode = status.st_mode & 0o777
    if mode != MANAGED_MODE:
        return f"has mode {mode:o}; it must be {MANAGED_MODE:o}"
    return None


def prepare_parent(parent: Path | None = None) -> Path:
    """Create the product-owned parent private when absent, then require it as it must be."""

    resolved = managed_parent(parent)
    if resolved.is_symlink():
        raise OutsideScratchError(f"the outside scratch parent {resolved} is a symlink")
    if not resolved.exists():
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.mkdir(mode=MANAGED_MODE)
        os.chmod(resolved, MANAGED_MODE)
        return resolved
    return require_managed_parent(resolved)


def ensure_root(record: OutsideScratchRoot, *, parent: Path | None = None) -> dict[str, Any]:
    """Create the root and its marker, or recreate what ageing removed. Idempotent.

    The target is confined first: the record's own path, its parent, and the parent it claims
    must agree with the managed parent, which is required to be a real private directory of
    ours. A tree that carries a complete marker of *another* generation is refused and left
    exactly as it is -- the path is derived from the generation, so a differing marker means a
    collision or a foreign tree, and neither is proof to delete somebody's data. A tree with no
    marker is an aged or interrupted remnant of this path, and is recreated empty.
    """

    resolved_parent = (
        require_managed_parent(parent)
        if managed_parent(parent).exists()
        else prepare_parent(parent)
    )
    require_socket_budget(record)
    require_confinement(record.root, resolved_parent)
    state = "created"
    if record.root.is_symlink():
        raise OutsideScratchError(f"the outside scratch root {record.root} is a symlink")
    if record.root.exists():
        if marker_identity(record.root) == _identity(record):
            return {"path": record.root.as_posix(), "state": "present"}
        conflicting = read_marker(record.root)
        if conflicting is not None:
            raise OutsideScratchError(
                f"the outside scratch root {record.root} carries the marker of generation "
                f"{conflicting.get('generation')}, not {record.generation}; inspect and remove "
                f"that tree yourself, or start the enclosure that owns it"
            )
        _remove_tree(record.root)
        state = "recreated"
    _build_root(record, resolved_parent)
    return {"path": record.root.as_posix(), "state": state}


def _build_root(record: OutsideScratchRoot, parent: Path) -> None:
    """Create the root in one visible step: the final path never names a root without a marker."""

    staging = parent / f"{PARTIAL_PREFIX}{record.generation[:12]}-{os.getpid()}"
    if staging.exists():
        _remove_tree(staging)
    staging.mkdir(mode=MANAGED_MODE)
    os.chmod(staging, MANAGED_MODE)
    (staging / MARKER_NAME).write_text(marker_text(record), encoding="utf-8")
    os.rename(staging, record.root)


def _entries(parent: Path) -> Iterator[tuple[str, Path]]:
    """The parent's entries as the filesystem yields them: names only, no entry inspection."""

    with os.scandir(parent) as scan:
        for entry in scan:
            yield entry.name, Path(entry.path)


def read_marker(root: Path) -> dict[str, Any] | None:
    """The marker of one root, or ``None`` when it is absent, unreadable or another schema."""

    try:
        payload = json.loads((root / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != OUTSIDE_SCRATCH_SCHEMA:
        return None
    return payload


def record_from_marker(marker: dict[str, Any]) -> OutsideScratchRoot | None:
    """The root one marker names, or ``None`` when the marker does not name a complete root.

    A reader that finds a marker by scanning the parent -- the reconcile, or a test sweep --
    needs the record the marker was written from, not a guess from the folder name.
    """

    if marker.get("schema") != OUTSIDE_SCRATCH_SCHEMA:
        return None
    root = str(marker.get("path") or "")
    generation = str(marker.get("generation") or "")
    repository = str(marker.get("repository") or "")
    coordination_root = str(marker.get("coordinationRoot") or "")
    contract_path = str(marker.get("contractPath") or "")
    if not (root and generation and coordination_root and contract_path):
        return None
    return OutsideScratchRoot(
        root=Path(root),
        generation=generation,
        marker_sha256=sha256_text(json.dumps(marker, indent=2, sort_keys=True) + "\n"),
        repository=repository,
        coordination_root=Path(coordination_root),
        contract_path=Path(contract_path),
    )


def recorded_root_in_group(group: Path) -> str:
    """The outside root path an enclosure group's published manifest records, or ``""``.

    A hand-over is built from the group's published facts, and the manifest is written before the
    contract, so this answers without loading the contract a caller may not want to read twice.
    """

    manifest = group / LIFECYCLE_DIRECTORY / ENCLOSURE_MANIFEST
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    recorded = document.get("outsideScratch")
    if not isinstance(recorded, dict):
        return ""
    path = str(recorded.get("path") or "")
    generation = str(recorded.get("generation") or "")
    marker_sha256 = str(recorded.get("markerSha256") or "")
    if not (path and generation and marker_sha256):
        return ""
    return path


def marker_identity(root: Path) -> str:
    """The digest of the marker bytes inside ``root``, or ``""`` when there are none."""

    try:
        return sha256_text((root / MARKER_NAME).read_text(encoding="utf-8"))
    except OSError:
        return ""


def remove_root(
    record: OutsideScratchRoot,
    *,
    dry_run: bool = False,
    parent: Path | None = None,
) -> dict[str, object]:
    """Remove one recorded root with its enclosure, or report what stayed and why.

    Only a real directory directly under the product-owned parent is ever removed. The caller
    owns the record: this is the enclosure's own cleanup, not the reconcile's proof question.
    """

    entry: dict[str, object] = {"path": record.root.as_posix()}
    problem = _removal_problem(record.root, managed_parent(parent))
    if problem is not None:
        entry.update({"removed": False, "reason": problem})
        return entry
    if not record.root.exists():
        entry.update({"removed": False, "reason": "already-absent"})
        return entry
    marker_problem = _marker_problem(record)
    if marker_problem is not None:
        # A tree at the recorded path is not ours because of where it stands. Removal deletes
        # bytes, so the record and the marker inside the tree must name the same generation first:
        # a complete tree of another generation is reported and left alone, never deleted.
        entry.update({"removed": False, "reason": marker_problem})
        return entry
    if dry_run:
        entry.update({"would_remove": True, "removed": False})
        return entry
    try:
        _remove_tree(record.root)
    except FileNotFoundError:
        # Another remover -- the reconcile of a parallel run, or the system's own ageing -- took
        # it between the check and the removal. The postcondition holds, so this is not a failure.
        entry.update({"removed": False, "reason": "already-absent"})
        return entry
    entry.update({"removed": True})
    return entry


def _marker_problem(record: OutsideScratchRoot) -> str | None:
    """Why the marker inside ``record.root`` does not prove this record owns that tree.

    Removal deletes bytes, so the record and the tree's own marker must name one generation
    before the branch is taken -- the same agreement the reconcile demands. A tree whose marker
    is missing, names another path, another generation or another enclosure is reported, not
    removed.
    """

    marker = read_marker(record.root)
    if marker is None:
        return "marker-missing-or-unreadable"
    disagreement = {
        "path": ("marker-names-another-path", record.root.as_posix()),
        "generation": ("marker-names-another-generation", record.generation),
        "contractPath": ("marker-names-another-enclosure", record.contract_path.as_posix()),
        "coordinationRoot": (
            "marker-names-another-enclosure",
            record.coordination_root.as_posix(),
        ),
    }
    for field, (reason, expected) in disagreement.items():
        if str(marker.get(field) or "") != expected:
            return reason
    if marker_identity(record.root) != record.marker_sha256:
        return "marker-differs-from-the-record"
    return None


def terminal_blocker(
    contract: Any, *, dry_run: bool, parent: Path | None = None
) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    """Remove a still-registered root on a completed retry; report one that stays.

    A completed cleanup or abandon that answers again must not leave a registered root behind:
    the retry removes it, and only a root it could not remove becomes a blocker. Nothing is
    blocked for an enclosure that records no root, or whose root is already gone.
    """

    entry = removal_entry_for(contract, dry_run=dry_run, parent=parent).get("outside_scratch")
    if entry is None:
        return None, []
    if entry.get("removed") or entry.get("would_remove"):
        return entry, []
    if entry.get("reason") == "already-absent":
        return entry, []
    return entry, [
        {"outsideScratch": entry.get("path"), "reason": entry.get("reason") or "not-removed"}
    ]


def removal_entry_for(
    contract: Any, *, dry_run: bool, parent: Path | None = None
) -> dict[str, dict[str, object]]:
    """The cleanup inventory entry for this enclosure's root, or ``{}`` when it has none.

    One call site per verb: cleanup and abandon both list the root they removed under
    ``outside_scratch``, so a successful answer is never given while a registered root remains.
    """

    record = recorded_root(contract)
    if record is None:
        return {}
    return {"outside_scratch": remove_root(record, dry_run=dry_run, parent=parent)}


class RootPage(TypedDict):
    """One bounded pass over the managed parent."""

    roots: list[dict[str, Any]]
    after: str
    complete: bool


def scan_roots(
    parent: Path | None = None,
    *,
    after: str = "",
    limit: int | None = None,
) -> RootPage:
    """Inspect at most ``limit`` roots above ``after`` in name order, and say where to resume.

    The page is a forward-only walk over names in lexical order. ``after`` is the last name a
    previous pass took, each pass takes the next names above it, and a pass that reaches the end
    reports ``complete`` so the caller clears the cursor and the next walk sees names that
    appeared behind it. Names are the only thing read for the ordering -- entries themselves are
    inspected for the page alone -- because a cursor taken from the filesystem's stream order is
    not a high-water mark: a name the stream yields before the last one collected would then sit
    behind the cursor forever and starve.

    Partial roots belong to the walk. A root being built carries its marker under a ``.partial-``
    name, so a sweep that skipped every dot name could never reclaim the work an interrupted
    publication left behind.
    """

    resolved = managed_parent(parent)
    if not resolved.is_dir() or resolved.is_symlink():
        return {"roots": [], "after": "", "complete": True}
    limit = MAX_ROOTS if limit is None else limit
    names = sorted(
        name
        for name, _path in _entries(resolved)
        if name.startswith(PARTIAL_PREFIX) or not name.startswith(".")
    )
    behind = [name for name in names if name > after]
    page = behind[:limit]
    roots: list[dict[str, Any]] = []
    for name in page:
        path = resolved / name
        roots.append(
            {
                "path": path.as_posix(),
                "marker": read_marker(path),
                "symlink": path.is_symlink(),
                "directory": path.is_dir() and not path.is_symlink(),
                "partial": name.startswith(PARTIAL_PREFIX),
            }
        )
    complete = len(behind) <= len(page)
    return {"roots": roots, "after": "" if complete else page[-1], "complete": complete}


def list_roots(parent: Path | None = None) -> list[dict[str, Any]]:
    """Every root one bounded pass of the parent yields, with the marker it carries.

    A root without a readable marker is reported with ``marker: None``; the reconcile never
    infers an owner from a name.
    """

    return scan_roots(parent)["roots"]


def reconcile(
    *,
    ended: Callable[[dict[str, Any]], bool],
    parent: Path | None = None,
) -> ReconcileReport:
    """Delete every root ``ended`` proves is finished, and report every root it does not.

    Deletion needs the three proofs of rule 5 at once: an exact retained path and generation
    (``ended`` reads the retained record and answers for both), a generation proven ended, and a
    real directory under the parent that is neither a link nor an escaped path. Anything else is
    reported with one reason and left untouched -- an unproved root is never deleted. The pass is
    bounded and resumes from a cursor, so a parent with more roots than one pass may inspect
    still reaches the ones behind the prefix.

    The managed parent gates every entry, the partial ones included: a parent that is a link,
    foreign-owned or not 0700 is refused before anything under it is mutated, and a partial root
    is only ever this product's own interrupted publication inside a parent that passed that
    gate. The cursor is bookkeeping inside that parent, so a refused parent leaves it untouched
    as well -- the pass reads it and writes nothing back.
    """

    resolved = managed_parent(parent)
    refused_parent = _parent_problem(resolved) is not None
    removed: list[dict[str, object]] = []
    reported: list[dict[str, object]] = []
    page = scan_roots(resolved, after=_read_cursor(resolved))
    for item in page["roots"]:
        path = Path(str(item["path"]))
        marker = item.get("marker")
        problem = _removal_problem(path, resolved)
        if problem is not None:
            reported.append({"path": path.as_posix(), "reason": problem})
            continue
        if item.get("partial") and item.get("directory") and isinstance(marker, dict):
            # A root being built carries its marker before it is visible under its final name,
            # so a real directory of that shape is this product's own interrupted work. Anything
            # else that merely carries the prefix falls through and is reported, never removed.
            # Its marker names the final path by construction, so the marker-identity checks
            # below do not apply to it -- only the parent gate above, which it has now passed.
            _remove_tree(path)
            removed.append({"path": path.as_posix(), "generation": "", "partial": True})
            continue
        reason = _marker_refusal(path, marker)
        if reason is None:
            assert isinstance(marker, dict)
            if ended(marker):
                _remove_tree(path)
                removed.append({"path": path.as_posix(), "generation": marker.get("generation")})
            else:
                reported.append({"path": path.as_posix(), "reason": "generation-not-ended"})
        else:
            reported.append({"path": path.as_posix(), "reason": reason})
    if not refused_parent:
        _write_cursor(resolved, page)
    return {"removed": removed, "reported": reported}


def _read_cursor(parent: Path) -> str:
    try:
        return (parent / RECONCILE_CURSOR).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _write_cursor(parent: Path, page: RootPage) -> None:
    cursor = parent / RECONCILE_CURSOR
    try:
        if page["complete"] or not page["roots"]:
            cursor.unlink(missing_ok=True)
            return
        cursor.write_text(page["after"], encoding="utf-8")
    except OSError:
        return


def _marker_refusal(path: Path, marker: Any) -> str | None:
    """Why the marker cannot prove ``path`` is ours to delete, or ``None`` when it can."""

    if not isinstance(marker, dict):
        return "marker-missing-or-unreadable"
    if str(marker.get("path") or "") != path.as_posix():
        return "marker-names-another-path"
    if not marker.get("generation") or not marker.get("contractPath"):
        return "marker-incomplete"
    return None


def _identity(record: OutsideScratchRoot) -> str:
    return sha256_text(marker_text(record))


def require_confinement(root: Path, parent: Path | None = None) -> None:
    """Refuse a target or parent that is not exactly a child of our own managed parent."""

    problem = confinement_problem(root, parent)
    if problem is not None:
        raise OutsideScratchError(f"the outside scratch root {root} may not be touched: {problem}")


def confinement_problem(root: Path, parent: Path | None = None) -> str | None:
    """Why ``root`` is not a child of the managed parent, or ``None`` when it is.

    The parent is validated as well as the target: a redirected parent makes
    ``root.parent == parent`` true for a path outside it, so a parent that is a link, foreign or
    group-readable is a refusal on its own.
    """

    resolved = managed_parent(parent)
    if _parent_problem(resolved) is not None:
        return "parent-unusable"
    if root == resolved:
        return "is-the-parent"
    if root.parent.resolve(strict=False) != resolved.resolve(strict=False):
        return "outside-the-managed-parent"
    return None


def _removal_problem(root: Path, parent: Path) -> str | None:
    """Why ``root`` may not be removed here, or ``None`` when it is a real child directory."""

    if root.is_symlink():
        return "symlink"
    return confinement_problem(root, parent)


def _remove_tree(path: Path) -> None:
    """Remove one tree that was already proven to be a real directory of ours."""

    if path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path, ignore_errors=False)


def _write_marker(record: OutsideScratchRoot) -> None:
    atomic_write_text(record.marker_path, marker_text(record))

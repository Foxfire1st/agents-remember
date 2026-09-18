"""Admission: read an explicit, root-confined source set from a canonical tree.

This is the one place in the role-capsule path that touches the filesystem. It is
deliberately explicit rather than eager: the caller names every path it wants
admitted, and this module resolves each one inside the root, proves it is a regular
file, reads its bytes and records their digest. It does not consult the manifest to
decide what to read — the compiler needs to be able to discover that a required
block was *not* admitted, and a loader that only ever fetched what the manifest
asked for could not express that.

Containment is proven before any byte is read, so a traversal attempt fails without
the root ever being probed outside itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agents_remember.errors import CapsuleSourceError
from agents_remember.models.role_capsules.sources import CapsuleSource
from agents_remember.models.role_capsules.types import (
    CapsuleCompositionRoot,
    CapsuleSourceSelection,
    compute_content_digest,
)

_ROOTS: tuple[CapsuleCompositionRoot, ...] = (
    "core",
    "role",
    "operation",
    "specialization",
    "skill",
)


@dataclass(frozen=True, slots=True)
class CapsuleAdmissionRequest:
    """The exact source set to admit, as root-relative POSIX paths.

    The manifest path is separate from the instruction paths because the manifest
    is metadata, not an instruction block: it is composed into no capsule, and it
    must still be read so the selections can be validated against what it declares.
    """

    root: Path
    manifest: str
    core: tuple[str, ...] = ()
    role: tuple[str, ...] = ()
    operation: tuple[str, ...] = ()
    specialization: tuple[str, ...] = ()
    skill: tuple[str, ...] = ()

    def paths(self) -> tuple[str, ...]:
        return (
            self.manifest,
            *self.core,
            *self.role,
            *self.operation,
            *self.specialization,
            *self.skill,
        )

    def as_selection(self) -> CapsuleSourceSelection:
        """The value-typed selection the pure compiler consumes."""

        return CapsuleSourceSelection(
            root=str(self.root),
            manifest=self.manifest,
            core=self.core,
            role=self.role,
            operation=self.operation,
            specialization=self.specialization,
            skill=self.skill,
        )


def admit_capsule_sources(request: CapsuleAdmissionRequest) -> tuple[CapsuleSource, ...]:
    """Read every requested path, refusing traversal, missing files and duplicates.

    Returns the sources in a fixed order — manifest, then the four composition roots
    in contract order, each alphabetically — so the admitted set itself is
    reproducible and two admissions of one tree are comparable directly.
    """

    root = _require_root(request.root)
    seen: set[str] = set()
    admitted: list[CapsuleSource] = [_read(root, request.manifest, "core", seen, is_manifest=True)]
    for composition_root in _ROOTS:
        for relative in sorted(_paths_for(request, composition_root)):
            admitted.append(_read(root, relative, composition_root, seen))
    return tuple(admitted)


def _paths_for(
    request: CapsuleAdmissionRequest, composition_root: CapsuleCompositionRoot
) -> tuple[str, ...]:
    return {
        "core": request.core,
        "role": request.role,
        "operation": request.operation,
        "specialization": request.specialization,
        "skill": request.skill,
    }[composition_root]


def _require_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise CapsuleSourceError(
            status="source-root-invalid",
            detail="the admitted source root must be a filesystem path",
            next_action="pass the canonical skills tree this capsule composes from",
        )
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise CapsuleSourceError(
            status="source-root-missing",
            detail=f"admitted source root {str(resolved)!r} is not an existing directory",
            next_action="name the canonical skills tree that carries the composition manifest",
        )
    return resolved


def _read(
    root: Path,
    relative: str,
    composition_root: CapsuleCompositionRoot,
    seen: set[str],
    *,
    is_manifest: bool = False,
) -> CapsuleSource:
    canonical = _require_confined_relative(relative)
    if canonical in seen:
        raise CapsuleSourceError(
            status="duplicate-identity",
            detail=f"source path {canonical!r} was requested twice in one admission",
            next_action="name each source path exactly once",
        )
    seen.add(canonical)
    target = root.joinpath(*PurePosixPath(canonical).parts)
    if not target.is_file():
        raise CapsuleSourceError(
            status="source-missing",
            detail=(
                f"declared source {canonical!r} does not exist as a file under the admitted "
                f"source root"
            ),
            next_action="repair the manifest or the source tree; a required block is never skipped",
        )
    content = target.read_bytes()
    return CapsuleSource(
        identity=_identity_for(canonical, composition_root, is_manifest=is_manifest),
        composition_root=composition_root,
        path=canonical,
        content=content,
        revision=compute_content_digest(content),
    )


def _identity_for(path: str, composition_root: CapsuleCompositionRoot, *, is_manifest: bool) -> str:
    if is_manifest:
        # The manifest is metadata, not an instruction block: it is composed into no
        # capsule, so its identity is the reserved metadata identity rather than a
        # block identity that could collide with a real one.
        return "meta:composition-manifest"
    return f"{composition_root}:{path}"


def _require_confined_relative(path: str) -> str:
    """Prove ``path`` is a root-relative POSIX path that cannot escape its root."""

    if not isinstance(path, str) or not path.strip():
        raise CapsuleSourceError(
            status="source-path-invalid",
            detail="admitted source paths must be non-blank strings",
            next_action="name root-relative paths inside the admitted source tree",
        )
    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise CapsuleSourceError(
            status="source-path-escapes-root",
            detail=(
                f"admitted source path {path!r} is absolute or contains a traversal segment, so it "
                "cannot be confined to the source root"
            ),
            next_action=(
                "name a root-relative path; source admission never reads outside the admitted tree"
            ),
        )
    return candidate.as_posix()


__all__ = [
    "CapsuleAdmissionRequest",
    "admit_capsule_sources",
]

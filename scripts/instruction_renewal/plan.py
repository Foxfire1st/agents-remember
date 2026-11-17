"""Plan and publish instruction facts together with their declared package copies."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from instruction_renewal import budgets, digest, manifest, pointers
from instruction_renewal.documents import replace_derived

LIFECYCLE = "skills/l-01-agent-lifecycles/"


@dataclass(frozen=True)
class RenewalRequest:
    root: Path
    unit_bindings: tuple[str, ...] = ()
    pointer_bindings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Replacement:
    path: Path
    before: bytes | None
    after: bytes | None


@dataclass(frozen=True)
class RenewalPlan:
    replacements: tuple[Replacement, ...]
    notices: tuple[str, ...]


def _skill_owner(root: Path):
    name = "_instruction_skill_copy_owner"
    spec = importlib.util.spec_from_file_location(name, root / "scripts/sync-skills.py")
    if spec is None or spec.loader is None:
        raise ValueError("cannot load the declared skill-copy owner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _derived(root: Path, relative: str, value: dict) -> bytes:
    return replace_derived((root / relative).read_bytes(), value)


def targets(root: Path) -> tuple[str, ...]:
    """Describe generated carriers through their existing declaration owner."""
    owner = _skill_owner(root)
    return (
        *(
            f"{target.label}: {target.path.relative_to(root).as_posix()}"
            for target in owner.skill_targets(root)
        ),
        "profile: mcp/certification-profile-v1.json",
        "tool details: mcp/src/agents_remember/package_data/runtime/docs/tool-details.md",
    )


def _copy_plan(root: Path, output: dict[str, bytes | None]) -> None:
    owner = _skill_owner(root)
    projection = owner._skill_projection(root)
    source = root / projection.source
    source_files = owner.file_digests(source)
    canonical = {relative: (source / relative).read_bytes() for relative in source_files}
    for relative in canonical:
        path = (Path(projection.source) / relative).as_posix()
        if path in output:
            canonical[relative] = output[path]
    for target in owner.skill_targets(root):
        for relative, content in canonical.items():
            output[(target.path / relative).relative_to(root).as_posix()] = content
        for extra in owner.file_digests(target.path).keys() - canonical.keys():
            output[(target.path / extra).relative_to(root).as_posix()] = None
    output["mcp/certification-profile-v1.json"] = owner.projected_profile_bytes(root)
    output["mcp/src/agents_remember/package_data/runtime/docs/tool-details.md"] = (
        root / "docs/tool-details.md"
    ).read_bytes()


def plan(request: RenewalRequest) -> RenewalPlan:
    """Compute final source facts and copies without writing the repository."""
    root = request.root
    derived_manifest = manifest.plan(root)
    derived_map, notices = digest.plan(root, request.unit_bindings)
    derived_pointers, pointer_notices = pointers.plan(root, request.pointer_bindings)
    output = {
        LIFECYCLE + "composition-manifest.json": _derived(
            root, LIFECYCLE + "composition-manifest.json", derived_manifest
        ),
        LIFECYCLE + "core/digest-map.json": _derived(
            root, LIFECYCLE + "core/digest-map.json", derived_map
        ),
        LIFECYCLE + "pointer-actions-derived.json": (
            json.dumps(derived_pointers, indent=2, ensure_ascii=False) + "\n"
        ).encode(),
    }
    measurements, size_notices = budgets.plan(root, output)
    output[LIFECYCLE + "capsule-budgets-current.json"] = _derived(
        root, LIFECYCLE + "capsule-budgets-current.json", measurements
    )
    carriers: dict[str, bytes | None] = dict(output)
    _copy_plan(root, carriers)
    replacements = []
    for relative, after in sorted(carriers.items()):
        path = root / relative
        before = path.read_bytes() if path.is_file() else None
        if before != after:
            replacements.append(Replacement(path, before, after))
    return RenewalPlan(tuple(replacements), tuple(notices + pointer_notices + size_notices))


def _write(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def apply(planned: RenewalPlan) -> None:
    """Publish a validated plan, restoring original file bytes after a write failure."""
    written = []
    try:
        for replacement in planned.replacements:
            written.append(replacement)
            _write(replacement.path, replacement.after)
    except OSError:
        for replacement in reversed(written):
            _write(replacement.path, replacement.before)
        raise

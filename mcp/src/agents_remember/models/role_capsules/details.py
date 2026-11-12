"""Manifest-declared, separately delivered step detail for a capsule."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.errors import CapsuleCompilationError, CapsuleManifestError
from agents_remember.models.role_capsules.sources import CapsuleSource
from agents_remember.models.role_capsules.types import (
    CapsuleDetailReference,
    CapsuleInstructionBlock,
)

_DETAIL = re.compile(r"\{\{detail:([^{}\n]+)\}\}")


@dataclass(frozen=True, slots=True)
class CapsuleDetailDeclaration:
    source: str
    roles: tuple[str, ...]
    step: str


def parse_detail_declarations(
    document: Mapping[str, object],
) -> tuple[CapsuleDetailDeclaration, ...]:
    """References without step_detail stay ordinary metadata, never launch attachments."""
    references = document.get("references", {})
    if not isinstance(references, dict):
        raise CapsuleManifestError(status="manifest-invalid", detail="references must be an object")
    declarations: list[CapsuleDetailDeclaration] = []
    for name, entry in references.items():
        if not isinstance(entry, dict) or entry.get("step_detail") is not True:
            continue
        source, roles, step = entry.get("source"), entry.get("roles"), entry.get("step")
        if (
            not isinstance(source, str)
            or not source.strip()
            or not isinstance(step, str)
            or not step.strip()
            or not isinstance(roles, list)
            or not roles
            or any(not isinstance(role, str) for role in roles)
        ):
            raise CapsuleManifestError(
                status="manifest-invalid", detail=f"references.{name} needs source, roles and step"
            )
        declarations.append(CapsuleDetailDeclaration(source, tuple(roles), step))
    return tuple(declarations)


def compile_detail_references(
    blocks: tuple[CapsuleInstructionBlock, ...],
    declarations: tuple[CapsuleDetailDeclaration, ...],
    by_path: Mapping[str, CapsuleSource],
    role: str | None,
) -> tuple[CapsuleDetailReference, ...]:
    """Keep the exact admitted source bytes, outside the instruction stream."""
    named = dict.fromkeys(path for block in blocks for path in _DETAIL.findall(block.content))
    available = {entry.source: entry for entry in declarations if role in entry.roles}
    references: list[CapsuleDetailReference] = []
    for path in named:
        entry = available.get(path)
        source = by_path.get(path)
        if entry is None or source is None:
            raise CapsuleCompilationError(
                status="detail-reference-missing",
                detail=f"capsule names {path!r} but its role declaration or admitted file is missing",
                next_action="Declare the file under references with step_detail, roles and step, and admit its bytes.",
            )
        references.append(CapsuleDetailReference(path, source.revision, source.content, entry.step))
    return tuple(references)

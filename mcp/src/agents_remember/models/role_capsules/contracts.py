"""Source-addressed producer/receiver contracts, validated before audience selection."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal, NoReturn

from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.types import CapsuleInstructionBlock
from agents_remember.models.role_capsules.vocabulary import CapsuleRole, capsule_role_or_none

_KEY = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_MARKER = re.compile(
    rf"<!-- ar:(needs|gives) ({_KEY}) (from|to) ({_KEY}): ({_KEY}(?:, {_KEY})*) -->"
)
_SHAPED = re.compile(r"<!--\s*ar:(?:needs|gives)\b", re.IGNORECASE)
_AUDIENCE = re.compile(r"<!-- ar:roles ([a-z,\-]+) -->\s*$")


@dataclass(frozen=True, slots=True)
class CapsuleContractMarker:
    """One ordered contract endpoint and its location in the original source."""

    kind: Literal["needs", "gives"]
    key: str
    peer_role: CapsuleRole
    fields: tuple[str, ...]
    source_path: str
    line: int


def read_contract_markers(
    block: CapsuleInstructionBlock,
    roles: tuple[CapsuleRole, ...],
    role: CapsuleRole | None,
    *,
    source_text: str,
) -> tuple[CapsuleContractMarker, ...]:
    """Validate every marker, including passages another role will not receive."""

    selected: list[CapsuleContractMarker] = []
    recipients: tuple[str, ...] = ()
    offset = source_text.count("\n") - block.content.count("\n")
    for number, line in enumerate(block.content.splitlines(keepends=True), offset + 1):
        audience = _AUDIENCE.fullmatch(line)
        if audience is not None:
            recipients = tuple(audience[1].split(","))
        if _SHAPED.search(line) is None:
            continue
        marker = _MARKER.fullmatch(line.rstrip("\r\n"))
        if marker is None:
            _refuse(block.source_path, number, "malformed handoff contract marker")
        kind, key, direction, peer, names = marker.groups()
        if (kind, direction) not in (("needs", "from"), ("gives", "to")):
            _refuse(block.source_path, number, "needs pairs with from; gives pairs with to")
        peer_role = capsule_role_or_none(peer)
        if peer_role is None or peer_role not in roles:
            _refuse(block.source_path, number, f"unknown contract role {peer!r}")
        fields = tuple(names.split(", "))
        if len(set(fields)) != len(fields):
            _refuse(block.source_path, number, "a contract field is repeated")
        if block.composition_root == "operation" and not recipients:
            _refuse(block.source_path, number, "operation contract has no marked role audience")
        if block.composition_root != "operation" or "all" in recipients or role in recipients:
            selected.append(
                CapsuleContractMarker(
                    "needs" if kind == "needs" else "gives",
                    key,
                    peer_role,
                    fields,
                    block.source_path,
                    number,
                )
            )
    return tuple(selected)


def strip_contract_markers(block: CapsuleInstructionBlock) -> CapsuleInstructionBlock:
    """Strip already-validated lines before detail parsing, digesting and rendering."""

    return replace(
        block,
        content="".join(
            line
            for line in block.content.splitlines(keepends=True)
            if _MARKER.fullmatch(line.rstrip("\r\n")) is None
        ),
    )


def _refuse(path: str, line: int, reason: str) -> NoReturn:
    raise CapsuleCompilationError(
        status="contract-marker-invalid",
        detail=f"{path}:{line}: {reason}",
        next_action="Correct the source contract marker, preserving its role and ordered fields.",
    )

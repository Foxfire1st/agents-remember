"""Bind an instruction-only marker to case absence and native received fields.

The production CapsuleDelivery carries its trusted instruction stream verbatim
in the first message (cli.role_brief_message.brief_prompt). Fixture actors have
no AR role binding; their existing compiled stream is carried without inventing
a role handover. Outbound configuration is never evidence of provider receipt.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.errors import AuthorityError
from agents_remember.kernel.sidecar_pairing import confine_non_symlink_rel
from agents_remember.models.role_capsules.types import CapsuleDetailReference
from agents_remember.serving.capsule_delivery import CapsuleBindingIdentity, CapsuleDelivery

from ..fixtures import CaseBundle
from ..trial_isolation import CREDENTIAL_NAMES
from .models import NativeCapture, raw_export_digest

_SENTENCE = re.compile(r"[^.!?\n]{39,359}[.!?](?=[*_]*(?:\s|$))")
_DETAIL = re.compile(r"\{\{detail:([^{}]+)\}\}")


@dataclass(frozen=True)
class DetailFile:
    source_path: str
    path: Path
    revision: str
    step: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "path": str(self.path)}


@dataclass(frozen=True)
class PointerDelivery:
    instructions: str
    details: tuple[DetailFile, ...]
    composition: dict[str, Any]
    source_instruction_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ar-eval-pointer-delivery/v1",
            "instructionSha256": hashlib.sha256(self.instructions.encode()).hexdigest(),
            "sourceInstructionSha256": self.source_instruction_sha256,
            "instructionCharacters": len(self.instructions),
            "details": [detail.to_dict() for detail in self.details],
            "composition": self.composition,
        }


def _frozen_entry(artifact: dict[str, Any]) -> tuple[str, str, tuple[CapsuleDetailReference, ...]]:
    try:
        entry = artifact["entries"]["curation"]
        instructions, digest, rows = (
            entry["instructions"],
            entry["semanticDigest"],
            entry["details"],
        )
    except (KeyError, TypeError):
        raise AuthorityError(
            "text-delivery-detail-fault: captured curation entry is incomplete"
        ) from None
    if (
        not isinstance(instructions, str)
        or not instructions.strip()
        or not isinstance(digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
        or not isinstance(rows, list)
    ):
        raise AuthorityError("text-delivery-detail-fault: invalid captured curation fields")
    references = []
    for row in rows:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) or not row[key]
            for key in ("path", "revision", "step", "content")
        ):
            raise AuthorityError("text-delivery-detail-fault: invalid frozen detail reference")
        body = row["content"].encode("utf-8")
        if row["revision"] != "sha256:" + hashlib.sha256(body).hexdigest():
            raise AuthorityError(
                f"text-delivery-detail-fault: frozen revision mismatch: {row['path']}"
            )
        references.append(CapsuleDetailReference(row["path"], row["revision"], body, row["step"]))
    sources = [reference.source_path for reference in references]
    if (
        len(set(sources)) != len(sources)
        or set(_DETAIL.findall(instructions)) != set(sources)
        or "{{detail:" in _DETAIL.sub("", instructions)
    ):
        raise AuthorityError(
            "text-delivery-detail-fault: captured pointers and detail inventory differ"
        )
    return instructions, digest, tuple(references)


def bind_text_details(artifact: dict[str, Any], report_path: Path) -> PointerDelivery:
    """Bind frozen captured references through the production launch composer."""
    if (
        not report_path.is_absolute()
        or ".." in report_path.parts
        or any(path.is_symlink() for path in (report_path, *report_path.parents))
    ):
        raise AuthorityError(
            "text-delivery-detail-fault: report path must be absolute and nonlinked"
        )
    instructions, digest, references = _frozen_entry(artifact)
    # The capture compiler excludes this fixed evaluation binding from rendered text.
    delivery = CapsuleDelivery(
        CapsuleBindingIdentity(
            "agents-remember", "agents-remember/eval-fixture/task.json", "curator", "curation"
        ),
        instructions,
        digest,
        references,
    )
    try:
        bound, composition = compose_launch_instructions(delivery, str(report_path), {})
    except (OSError, ValueError) as error:
        raise AuthorityError(
            f"text-delivery-detail-fault: production detail binding refused: {error}"
        ) from error
    paths = {
        row["sourcePath"]: Path(row["path"]) for row in composition.get("detailReferences", [])
    }
    files = tuple(
        DetailFile(
            reference.source_path,
            paths[reference.source_path],
            reference.revision,
            reference.step,
            reference.revision.removeprefix("sha256:"),
            len(reference.content),
        )
        for reference in references
    )
    result = PointerDelivery(
        bound, files, composition, hashlib.sha256(instructions.encode()).hexdigest()
    )
    validation = validate_detail_links(result)
    if validation["unknowns"]:
        raise AuthorityError("; ".join(validation["unknowns"]))
    return result


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _detail_file_state(detail: DetailFile) -> dict[str, Any]:
    row = detail.to_dict()
    try:
        if any(path.is_symlink() for path in (detail.path, *detail.path.parents)):
            return {**row, "state": "linked"}
        descriptor = os.open(detail.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                return {**row, "state": "not-regular"}
            if before.st_size != detail.size_bytes:
                return {**row, "state": "size-mismatch", "observedSizeBytes": before.st_size}
            body = stream.read(detail.size_bytes + 1)
            after = os.fstat(stream.fileno())
            named = detail.path.stat(follow_symlinks=False)
        if _file_identity(before) != _file_identity(after) or _file_identity(
            after
        ) != _file_identity(named):
            return {**row, "state": "changed-during-read"}
        digest = hashlib.sha256(body).hexdigest()
        return {
            **row,
            "state": "ready" if digest == detail.sha256 else "digest-mismatch",
            "observedSha256": digest,
        }
    except OSError as error:
        return {**row, "state": "unavailable", "errorType": type(error).__name__}


def validate_detail_links(delivery: PointerDelivery) -> dict[str, Any]:
    """Check the exact emitted files; a dead/changed pointer is a delivery fault."""
    files = [_detail_file_state(detail) for detail in delivery.details]
    return {
        "files": files,
        "unknowns": [
            f"text-delivery-detail-fault: {row['source_path']} at {row['path']}: {row['state']}"
            for row in files
            if row["state"] != "ready"
        ],
    }


@dataclass(frozen=True)
class TextDeliveryMarker:
    sentence: str
    instruction_sha256: str
    instruction_character_offset: int
    bundle_files: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "ar-eval-text-delivery-marker/v1", **asdict(self)}


def _sentences(instructions: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            match.group().strip().lstrip("*-> ")
            for match in _SENTENCE.finditer(instructions)
            if len(match.group().split()) >= 6
        )
    )


def _scan_input(path: Path, candidates: tuple[bytes, ...]) -> tuple[dict[str, Any], set[bytes]]:
    if path.name in CREDENTIAL_NAMES or any(part.is_symlink() for part in (path, *path.parents)):
        raise AuthorityError(
            f"text-delivery-marker-unavailable: private/symlink input refused: {path}"
        )
    before = path.stat()
    digest = hashlib.sha256()
    found: set[bytes] = set()
    overlap = b""
    longest = max(map(len, candidates))
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            material = overlap + chunk
            found.update(sentence for sentence in candidates if sentence in material)
            overlap = material[-(longest - 1) :]
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise AuthorityError(f"text-delivery-marker-unavailable: input changed during scan: {path}")
    return {"path": str(path), "sha256": digest.hexdigest(), "sizeBytes": size}, found


def _case_inputs(case: CaseBundle) -> tuple[Path, ...]:
    """Check the entire finite declared bundle without walking an unknown directory."""
    names = {"case.json", *(item.path for item in case.files)}
    directories: dict[Path, set[str]] = {}
    paths = []
    for name in sorted(names):
        relative = confine_non_symlink_rel(case.path, name)
        path = case.path / relative
        paths.append(path)
        parts = Path(relative).parts
        for index, child in enumerate(parts):
            directories.setdefault(case.path.joinpath(*parts[:index]), set()).add(child)
    for directory, expected in directories.items():
        actual = set()
        for child in islice(directory.iterdir(), len(expected) + 1):
            if child.name not in expected or child.is_symlink():
                raise AuthorityError(
                    f"text-delivery-marker-unavailable: unbound case input: {child}"
                )
            actual.add(child.name)
        if actual != expected:
            raise AuthorityError(
                f"text-delivery-marker-unavailable: incomplete case directory: {directory}"
            )
    return tuple(paths)


def prepare_text_delivery(
    instructions: str,
    case: CaseBundle,
    actor_inputs: tuple[Path, ...],
    *,
    details: tuple[Path, ...] = (),
) -> TextDeliveryMarker:
    """Scan explicit immutable case and actor-given inputs, never a runtime/home tree.

    ``instructions`` is the pointer-bound first part, excluding detail bodies.
    The caller supplies every bound detail and verified actor input explicitly.
    Secret-named or symlink inputs refuse proof before reading or hashing bytes.
    """
    sentences = _sentences(instructions)
    if not sentences:
        raise AuthorityError(
            "text-delivery-marker-unavailable: bound instructions have no marker sentence"
        )
    encoded = tuple(sentence.encode("utf-8") for sentence in sentences)
    case_paths = _case_inputs(case)
    paths = sorted(set((*case_paths, *actor_inputs, *details)))
    seen: set[bytes] = set()
    inventory = []
    for path in paths:
        row, matches = _scan_input(path, encoded)
        inventory.append(row)
        seen.update(matches)
    sentence = next((text for text in sentences if text.encode("utf-8") not in seen), None)
    if sentence is None:
        raise AuthorityError(
            "text-delivery-marker-unavailable: every instruction sentence occurs in the case bundle or delivered details"
        )
    return TextDeliveryMarker(
        sentence,
        hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        instructions.index(sentence),
        tuple(inventory),
    )


def _content_fields(content: object, prefix: str) -> list[tuple[str, str]]:
    if isinstance(content, str):
        return [(prefix, content)]
    if not isinstance(content, list):
        return []
    return [
        (f"{prefix}[{index}].text", block["text"])
        for index, block in enumerate(content)
        if isinstance(block, dict)
        and block.get("type") in {"text", "input_text"}
        and isinstance(block.get("text"), str)
    ]


def _received_events(capture: NativeCapture, sentence: str) -> list[dict[str, Any]]:
    locations = []
    for index, event in enumerate(capture.events):
        if event.session_id != capture.session_id:
            continue
        fields = []
        if capture.harness == "codex" and event.kind == "session":
            base = event.payload.get("base_instructions")
            if isinstance(base, dict) and isinstance(base.get("text"), str):
                fields.append(("base_instructions.text", base["text"]))
        if event.kind == "message" and event.payload.get("role") in {"user", "developer", "system"}:
            fields.extend(_content_fields(event.payload.get("content"), "content"))
        for field, text in fields:
            if text.startswith("[CONTEXT COMPACTION") or sentence not in text:
                continue
            locations.append(
                {
                    "path": f"$.events[{index}].payload.{field}",
                    "sequence": event.sequence,
                    "source": event.source,
                    "sourceSha256": event.source_sha256,
                    "characterOffset": text.index(sentence),
                    "fieldSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
    return locations


def observe_text_delivery(marker: TextDeliveryMarker, capture: NativeCapture) -> dict[str, Any]:
    """Only native received instructions/messages prove arrival, including inactive originals."""
    locations = _received_events(capture, marker.sentence)
    if capture.harness == "hermes":
        for index, record in enumerate(capture.raw_records):
            row = record.get("row")
            if record.get("table") != "messages" or not isinstance(row, dict):
                continue
            text = row.get("content")
            if (
                row.get("session_id") != capture.session_id
                or row.get("role") != "user"
                or not isinstance(text, str)
                or text.startswith("[CONTEXT COMPACTION")
                or marker.sentence not in text
            ):
                continue
            locations.append(
                {
                    "path": f"$.raw_records[{index}].row.content",
                    "nativeMessageId": row.get("id"),
                    "active": row.get("active"),
                    "characterOffset": text.index(marker.sentence),
                    "fieldSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "sourceFiles": [asdict(source) for source in capture.source_files],
                }
            )
    return {
        **marker.to_dict(),
        "received": bool(locations),
        "receivedLocations": locations,
        "nativeSessionId": capture.session_id,
        "rawExportSha256": raw_export_digest(
            capture.harness, capture.session_id, capture.raw_records
        ),
        "unknowns": []
        if locations
        else [
            "text-delivery-marker-missing: bound instruction sentence absent from native received instructions/messages"
        ],
    }

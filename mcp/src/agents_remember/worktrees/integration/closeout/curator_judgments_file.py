"""MIK-R110 rule 3: the judgments-file read, parse, and entry validation.

One bounded read of the judgments file (regular files only, capped at the
largest legal payload), one JSON parse, and per-list entry validation by the
same models as the inline lists. Extracted from curator_coherence_publication
so each module stays under the file-size rail; the behavior is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.errors import CuratorCoherenceError
from agents_remember.models.lifecycles.curator_coherence import (
    MAX_CURATOR_REVIEW_ASSESSMENTS,
    MAX_CURATOR_SOURCE_CANDIDATES,
)

# MIK-R110 rule 3: the judgments-file byte budget, derived from the entry
# models' own field limits -- characters, encoding, and counts -- rather than
# a second number. A validated judgment entry admits 33,024 characters
# (four 8192-char paths and prose, the 256-char classification); a validated
# assessment entry admits 43,072 bounded characters (two 20000-char prose
# fields, three 1024-char references) beside its keys, subject, disposition,
# and evidence outlines. A legal file serialization expands every
# non-ASCII character: UTF-8 writers use up to 4 bytes per character,
# ASCII-escaped writers up to 6 per BMP character (``\uXXXX``) and up to 12
# per supplementary character (surrogate-pair escapes), so each ceiling
# counts 12 bytes per character plus key overhead. The budget covers the
# largest legal payload -- MAX_CURATOR_SOURCE_CANDIDATES judgments and
# MAX_CURATOR_REVIEW_ASSESSMENTS assessments -- with a 1MiB envelope.
# Unbounded trailing collections (assumption strings, evidence outlines)
# ride within this same overall cap, which the over-budget refusal names;
# narrowing the file input further happens only through the common
# input-contract models both inline and file lists validate against.
_JUDGMENT_ENTRY_CEILING_BYTES = 400 * 1024
_ASSESSMENT_ENTRY_CEILING_BYTES = 520 * 1024
_JUDGMENTS_FILE_ENVELOPE_BYTES = 1024 * 1024
JUDGMENTS_FILE_MAX_BYTES = (
    MAX_CURATOR_SOURCE_CANDIDATES * _JUDGMENT_ENTRY_CEILING_BYTES
    + MAX_CURATOR_REVIEW_ASSESSMENTS * _ASSESSMENT_ENTRY_CEILING_BYTES
    + _JUDGMENTS_FILE_ENVELOPE_BYTES
)
_JUDGMENTS_FILE_READ_CHUNK = 65536


def _read_judgments_bytes(raw: str) -> tuple[bytes, str]:
    """Read the judgments file once; the SHA-256 of those bytes is answered.

    MIK-R110 rule 3: the read is resource-bounded by construction. A judgment
    entry's validated fields admit 33,024 characters and an assessment entry
    43,072 bounded characters, each counting 12 bytes per character for legal
    UTF-8 or ASCII-escaped serializations (supplementary characters escape
    as surrogate pairs) plus key overhead, so the largest legal payload --
    2048 judgments and 256 assessments -- fits under
    ``JUDGMENTS_FILE_MAX_BYTES`` with a 1MiB envelope. Special files are
    refused without waiting for a writer: the descriptor opens nonblocking,
    so a FIFO with no writer is refused by its file type instead of blocking
    on the open. Regular files over the budget are refused from their size
    before any allocation; the file is otherwise read through one descriptor
    capped at the budget plus one byte, so growth between the size check and
    the read cannot escape the bound.
    """

    path = Path(raw)
    if not path.is_absolute():
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-unreadable",
            f"judgments_file names no absolute path: {raw}",
            next_action="publish",
        )
    try:
        descriptor = os.open(os.fspath(path), os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-unreadable",
            f"judgments_file could not be read: {exc}",
            next_action="publish",
        ) from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise CuratorCoherenceError(
                "curator-coherence-judgments-file-not-regular",
                f"judgments_file names no regular file: {raw}",
                next_action="publish",
            )
        if file_stat.st_size > JUDGMENTS_FILE_MAX_BYTES:
            raise CuratorCoherenceError(
                "curator-coherence-judgments-file-over-budget",
                f"judgments_file holds more than the largest legal payload "
                f"({JUDGMENTS_FILE_MAX_BYTES} bytes): {raw}",
                next_action="publish",
            )
        chunks: list[bytes] = []
        remaining = JUDGMENTS_FILE_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(_JUDGMENTS_FILE_READ_CHUNK, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
    except OSError as exc:
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-unreadable",
            f"judgments_file could not be read: {exc}",
            next_action="publish",
        ) from exc
    finally:
        os.close(descriptor)
    if len(content) > JUDGMENTS_FILE_MAX_BYTES:
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-over-budget",
            f"judgments_file holds more than the largest legal payload "
            f"({JUDGMENTS_FILE_MAX_BYTES} bytes): {raw}",
            next_action="publish",
        )
    return content, hashlib.sha256(content).hexdigest()


def _parse_judgments_payload(content: bytes) -> tuple[list[object], list[object]]:
    """Parse the file's two lists; shape failures name the file, never an entry."""

    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-invalid",
            f"judgments_file is no JSON object: {exc}",
            next_action="publish",
        ) from exc
    if not isinstance(payload, dict):
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-invalid",
            "judgments_file must hold a JSON object with judgments and review_assessments lists",
            next_action="publish",
        )
    unknown = sorted(set(payload) - {"judgments", "review_assessments"})
    if unknown:
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-invalid",
            f"judgments_file holds unexpected keys: {', '.join(unknown)}",
            next_action="publish",
        )
    judgments_raw = payload.get("judgments", [])
    assessments_raw = payload.get("review_assessments", [])
    if not isinstance(judgments_raw, list) or not isinstance(assessments_raw, list):
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-invalid",
            "judgments_file must hold judgments and review_assessments as lists",
            next_action="publish",
        )
    return judgments_raw, assessments_raw


def _validate_file_entries(
    raw_entries: list[object], model: Any, name: str, bound: int
) -> list[Any]:
    """Validate one file list by the same model as its inline list.

    A file with one entry too many is refused as the inline list is: the inline request
    model bounds the same collection at the same number.
    """

    if len(raw_entries) > bound:
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-invalid",
            f"{name} holds {len(raw_entries)} entries; at most {bound} may be published",
            next_action="publish",
        )
    try:
        return [model.model_validate(item) for item in raw_entries]
    except ValidationError as exc:
        raise CuratorCoherenceError(
            "curator-coherence-judgments-file-invalid",
            f"a {name} entry is invalid: {_first_validation_error(exc)}",
            next_action="publish",
        ) from exc


def _first_validation_error(exc: ValidationError) -> str:
    """Name the first entry a file's list failed on, within one short phrase."""

    errors = exc.errors()
    if not errors:
        return "invalid entry"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid entry"))
    text = f"{location}: {message}" if location else message
    return text if len(text) <= 300 else text[:300]

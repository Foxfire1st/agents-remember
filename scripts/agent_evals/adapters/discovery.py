"""Resolve original native session identity from fresh project metadata alone.

Candidates are explicit native files supplied by the measurement owner. Never
assume a host-agent id is a provider session id, or walk another harness home.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..artifacts import sha256_file
from .capture import capture_claude_jsonl, capture_codex_jsonl
from .hermes import capture_hermes_db
from .models import Harness, NativeCapture, SourceFile


def discover_capture(
    harness: Harness,
    paths: Iterable[Path],
    *,
    project: Path,
    not_before: float,
    session_id: str | None = None,
) -> NativeCapture:
    candidates = tuple(paths)
    matches: dict[str, list[Path]] = {}
    unknowns: list[str] = []
    for path in candidates:
        try:
            identities = (
                _hermes_ids(path, project, not_before)
                if harness == "hermes"
                else _jsonl_ids(path, harness, project, not_before)
            )
            for identity in identities:
                if session_id is None or identity == session_id:
                    matches.setdefault(identity, []).append(path)
        except (OSError, ValueError, sqlite3.Error) as error:
            unknowns.append(f"native session discovery incomplete for {path}: {error}")
    if len(matches) != 1:
        sources = tuple(
            SourceFile(str(path), sha256_file(path))
            for matched in matches.values()
            for path in matched
            if path.is_file()
        )
        return NativeCapture(
            harness,
            "",
            (),
            sources,
            (),
            tuple(
                [
                    *unknowns,
                    f"native identity requires exactly one fresh project match; observed {len(matches)}",
                ]
            ),
        )
    identity, matched_paths = next(iter(matches.items()))
    if harness == "hermes":
        if len(matched_paths) != 1:
            return NativeCapture(
                harness, identity, (), (), (), ("native Hermes identity has multiple databases",)
            )
        captured = capture_hermes_db(matched_paths[0], identity)
    elif harness == "codex":
        captured = capture_codex_jsonl(matched_paths, identity)
    else:
        captured = capture_claude_jsonl(matched_paths, identity)
    return replace(captured, unknowns=(*captured.unknowns, *unknowns))


def _timestamp(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError("native creation timestamp absent")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("native creation timestamp has no timezone")
    return parsed.timestamp()


def _jsonl_ids(path: Path, harness: Harness, project: Path, not_before: float) -> set[str]:
    observed: dict[str, tuple[str, object]] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("native metadata record is not an object")
            metadata = _metadata(row, harness)
            identity, cwd, stamp = metadata
            if isinstance(identity, str) and isinstance(cwd, str):
                observed.setdefault(identity, (cwd, stamp))
                if harness == "codex":
                    break  # Native first metadata row suffices; do not read another project payload.
    return {
        identity
        for identity, (cwd, stamp) in observed.items()
        if Path(cwd).resolve() == project.resolve() and _timestamp(stamp) >= not_before
    }


def _metadata(row: dict[str, Any], harness: Harness) -> tuple[object, object, object]:
    if harness == "codex":
        payload = row.get("payload")
        if row.get("type") != "session_meta" or not isinstance(payload, dict):
            return None, None, None
        return payload.get("id"), payload.get("cwd"), payload.get("timestamp", row.get("timestamp"))
    return row.get("sessionId"), row.get("cwd"), row.get("timestamp")


def _hermes_ids(path: Path, project: Path, not_before: float) -> set[str]:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        return {
            row[0]
            for row in db.execute(
                "SELECT id FROM sessions WHERE cwd = ? AND started_at >= ?",
                (str(project.resolve()), not_before),
            )
        }

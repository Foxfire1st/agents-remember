"""Observe reads of delivered detail files without inferring log completeness."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from ..artifacts import json_digest
from .detail_read_intents import extract_execute_code_reads, read_intents
from .models import NativeCapture, NativeEvent, raw_export_digest

if TYPE_CHECKING:
    from pathlib import Path

    from .text_delivery import PointerDelivery

_ABSENCE = "not seen in kept log"
_REASON = "the kept log is not known to be complete on this harness"
_NAMES = {"read_file", "terminal", "CommandExecution", "exec_command", "execute_code"}


@dataclass(frozen=True)
class _Read:
    paths: tuple[str, ...]
    cwd: str | None
    state: str
    whole: bool
    failed: bool
    output_sha256: str


def _object(value: object) -> dict[str, Any]:
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict):
        raise ValueError("native arguments/result are not an object")
    return result


def _cwd(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
        value = unquote(parsed.path)
    elif parsed.scheme:
        return None
    return posixpath.normpath(value) if value.startswith("/") else None


def _path(value: str, cwd: str | None) -> str:
    if not value or any(char in value for char in "$`*?[]{}\n") or value.startswith("~"):
        raise ValueError("native file target is not a literal path")
    if not value.startswith("/"):
        if cwd is None:
            raise ValueError("relative native file target has no explicit per-call cwd")
        value = posixpath.join(cwd, value)
    return posixpath.normpath(value)


def _file_read(arguments: dict[str, Any], output: dict[str, Any]) -> _Read:
    path = arguments.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Hermes read_file target is not absolute; per-call cwd unavailable")
    if output.get("is_binary") is True or output.get("is_image") is True:
        raise ValueError("native read_file result is not a text detail read")
    failed = bool(output.get("error")) or output.get("not_found") is True
    if not failed and (
        output.get("not_found") is not False or not isinstance(output.get("content"), str)
    ):
        raise ValueError("Hermes read_file has no explicit successful text receipt")
    whole = not ({"offset", "limit"} & arguments.keys()) and output.get("truncated") is False
    whole = whole and not output.get("truncated_lines") and not output.get("truncated_by")
    return _Read((path,), None, "opened", bool(whole), failed, json_digest(output))


def _native_output(
    event: NativeEvent, result: NativeEvent, arguments: dict[str, Any]
) -> tuple[object, str | None, str, dict[str, Any]]:
    name = event.payload["name"]
    output = _object(result.payload.get("output"))
    cwd = _cwd(arguments.get("workdir"))
    if name == "CommandExecution":
        item = event.payload.get("native_item")
        if (
            not isinstance(item, dict)
            or item != result.payload.get("native_item")
            or output != item
        ):
            raise ValueError("Codex completion differs from its captured native command")
        command, cwd = item.get("command"), _cwd(item.get("cwd"))
        if (
            isinstance(command, list)
            and len(command) == 3
            and command[0] in {"/bin/bash", "/usr/bin/bash", "/bin/sh"}
            and command[1] in {"-c", "-lc"}
        ):
            command = command[2]
        completed = item.get("status") in {"completed", "failed"}
        text = item.get("aggregated_output")
    else:
        command = arguments.get("command" if name == "terminal" else "cmd")
        text, completed = output.get("output"), output.get("exit_code") is not None
    if not completed or type(output.get("exit_code")) is not int or not isinstance(text, str):
        raise ValueError("native shell has no completed text result/exit code")
    return command, cwd, text, output


def _execute_intents(
    arguments: dict[str, Any], count: int
) -> tuple[list[dict[str, Any]], list[str]]:
    requests, diagnostics = extract_execute_code_reads(arguments.get("code"), max_targets=count)
    intents = []
    for request in requests:
        if request["tool"] == "terminal":
            found, notes = read_intents(request["command"], _cwd(request["workdir"]))
            intents.extend(found)
            diagnostics.extend(notes)
        else:
            try:
                operand = request["path"]
                relative = not operand.startswith("/") and request["cwd"] is None
                if relative and ".." in operand.split("/"):
                    raise ValueError("relative content binding cannot contain parent traversal")
                path = _path(operand, "/" if relative else request["cwd"])
                path = path.lstrip("/") if relative else path
                intents.append(
                    {
                        "path": path,
                        "state": "opened",
                        "whole": request["whole"],
                        "relativeContentBinding": relative,
                    }
                )
            except ValueError as error:
                diagnostics.append(str(error))
    return intents, diagnostics


def _body_witness(text: str, body: bytes) -> dict[str, Any] | None:
    if len(text.encode("utf-8")) > 32 * 1024 * 1024:
        raise ValueError("detail-read observation budget: native output exceeds 32 MiB")
    # Only native read_file's actual line-number presentation is normalized.
    for basis, value in (
        ("decoded UTF-8 output", text),
        (
            "decoded UTF-8 output with native decimal line prefixes removed",
            re.sub(r"(?m)^[ \t]*\d+\|", "", text),
        ),
    ):
        encoded = value.encode("utf-8")
        offset = encoded.find(body)
        complete = offset >= 0
        returned = body
        if not complete and body.endswith(b"\n") and encoded.endswith(body[:-1]):
            returned = body[:-1]
            offset = len(encoded) - len(returned)
        if offset >= 0:
            return {
                "basis": basis,
                "utf8ByteRange": [offset, offset + len(returned)],
                "bodySha256": hashlib.sha256(body).hexdigest(),
                "bodyBytes": len(body),
                "returnedSha256": hashlib.sha256(returned).hexdigest(),
                "returnedBytes": len(returned),
                "completeBody": complete,
                "coverage": "complete frozen body"
                if complete
                else "all frozen bytes except final LF",
            }
    return None


def _bound_path(intent: dict[str, Any], bodies: dict[str, bytes]) -> str | None:
    path = intent["path"]
    if not intent.get("relativeContentBinding"):
        return path if path in bodies else None
    if ".." in path.split("/"):
        raise ValueError("relative content binding cannot contain parent traversal")
    matches = [candidate for candidate in bodies if candidate.endswith("/" + path)]
    if len(matches) != 1:
        raise ValueError(
            "relative content binding has absent or ambiguous delivered suffix: " + path
        )
    return matches[0]


def _intent_reads(
    event: NativeEvent, result: NativeEvent, arguments: dict[str, Any], bodies: dict[str, bytes]
) -> tuple[list[tuple[_Read, dict[str, Any] | None]], list[str]]:
    command, cwd, text, output = _native_output(event, result, arguments)
    if len(text.encode("utf-8")) > 32 * 1024 * 1024:
        raise ValueError("detail-read observation budget: native output exceeds 32 MiB")
    if event.payload["name"] == "execute_code":
        intents, diagnostics = _execute_intents(arguments, max(1, len(bodies)))
    else:
        intents, diagnostics = read_intents(command, cwd)
    failed = output["exit_code"] != 0 or bool(output.get("error"))
    truncated = any(
        output.get(key)
        for key in ("truncated", "output_truncated", "stdout_truncated", "stdout_bytes_omitted")
    )
    found: dict[str, dict[str, Any] | None] = {}
    reads = []
    output_digest = json_digest(output)
    for intent in intents:
        try:
            path = _bound_path(intent, bodies)
        except ValueError as error:
            diagnostics.append(str(error))
            continue
        if path is None:
            continue
        if path not in found:
            found[path] = _body_witness(text, bodies[path])
        proof = found[path]
        if not proof and not failed:
            diagnostics.append("literal read intent lacks complete frozen body: " + path)
            continue
        if proof and intent.get("relativeContentBinding"):
            proof = {
                **proof,
                "pathBinding": "relative literal suffix and unique frozen body; cwd unavailable",
                "literalOperand": intent["path"],
                "cwd": None,
                "nativeAbsolutePath": None,
            }
        reads.append(
            (
                _Read(
                    (path,),
                    None,
                    intent["state"],
                    bool(proof and proof["completeBody"] and intent["whole"]) and not truncated,
                    failed,
                    output_digest,
                ),
                proof,
            )
        )
    return reads, diagnostics


def _groups(capture: NativeCapture, kind: str) -> dict[str, list[NativeEvent]]:
    result: dict[str, list[NativeEvent]] = {}
    seen: dict[str, set[str]] = {}
    for event in capture.events:
        identity = event.payload.get("call_id")
        if event.kind != kind or not isinstance(identity, str) or not identity:
            continue
        marker = json_digest(
            {
                "session_id": event.session_id,
                "source": event.source,
                "source_sha256": event.source_sha256,
                "payload": event.payload,
                "native_timestamp": event.native_timestamp,
            }
        )
        identities = seen.setdefault(identity, set())
        if marker not in identities:
            identities.add(marker)
            result.setdefault(identity, []).append(event)
    return result


def _pair(
    capture: NativeCapture,
    event: NativeEvent,
    calls: list[NativeEvent],
    results: list[NativeEvent],
    sources: set[tuple[str, str]],
) -> NativeEvent:
    if len(calls) != 1 or len(results) != 1:
        raise ValueError("native call/result is missing or ambiguous")
    result = results[0]
    if (
        not capture.session_id
        or event.session_id != capture.session_id
        or (event.session_id, event.source, event.source_sha256)
        != (result.session_id, result.source, result.source_sha256)
    ):
        raise ValueError("native call/result session or source does not match")
    if (event.source, event.source_sha256) not in sources:
        raise ValueError("native event source/hash is absent from capture sources")
    if (
        result.sequence <= event.sequence
        or result.payload.get("name", event.payload["name"]) != event.payload["name"]
    ):
        raise ValueError("native result order or tool name does not match")
    return result


def _witness(event: NativeEvent, index: int) -> dict[str, Any]:
    return {
        "sourceId": "sha256:" + json_digest({"path": event.source, "sha256": event.source_sha256}),
        "source": event.source,
        "source_sha256": event.source_sha256,
        "sequence": event.sequence,
        "eventIndex": index,
        "JSONPath": f"$.events[{index}].payload",
        "payloadSha256": json_digest(event.payload),
    }


def _direct_read(
    arguments: dict[str, Any], result: NativeEvent, bodies: dict[str, bytes]
) -> tuple[_Read, dict[str, Any] | None]:
    output = _object(result.payload.get("output"))
    read = _file_read(arguments, output)
    body = bodies.get(read.paths[0])
    proof = _body_witness(output["content"], body) if body and not read.failed else None
    return replace(read, whole=bool(read.whole and proof and proof["completeBody"])), proof


def _record_reads(
    reads: list[tuple[_Read, dict[str, Any] | None]],
    rows: dict[str, dict[str, Any]],
    witnesses: dict[str, Any],
) -> list[dict[str, Any]]:
    failed, seen = [], set()
    for read, proof in reads:
        for value in read.paths:
            path = _path(value, read.cwd)
            if path not in rows:
                continue
            witness = {
                **witnesses,
                "path": path,
                "state": read.state,
                "wholeRead": read.whole,
                "resultCanonicalJsonSha256": read.output_sha256,
                "bodyCorroboration": proof,
                "pathBinding": proof.get("pathBinding", "explicit native path/cwd")
                if proof
                else "explicit native path/cwd",
            }
            if read.failed:
                failed.append({**witness, "state": "error", "wholeRead": False})
            elif path not in seen:
                seen.add(path)
                rows[path]["observations"].append(witness)
    return failed


def _observe(
    capture: NativeCapture, rows: dict[str, dict[str, Any]], bodies: dict[str, bytes]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calls, results = _groups(capture, "tool_call"), _groups(capture, "tool_result")
    failed, diagnostics = [], []
    sources = {(source.path, source.sha256) for source in capture.source_files}
    indices = {id(event): index for index, event in reversed(list(enumerate(capture.events)))}
    for identity, values in calls.items():
        event = values[0]
        if event.payload.get("name") not in _NAMES:
            continue
        call_witness = _witness(event, indices[id(event)])
        try:
            result = _pair(capture, event, values, results.get(identity, []), sources)
            if event.payload.get("opaque"):
                raise ValueError("opaque native dispatch has no inferred read")
            arguments = _object(event.payload.get("arguments"))
            if event.payload["name"] == "read_file":
                reads, notes = [_direct_read(arguments, result, bodies)], []
            else:
                reads, notes = _intent_reads(event, result, arguments, bodies)
            failed.extend(
                _record_reads(
                    reads,
                    rows,
                    {
                        "call_id": identity,
                        "tool": event.payload["name"],
                        "call": call_witness,
                        "result": _witness(result, indices[id(result)]),
                    },
                )
            )
            diagnostics.extend(
                {"call_id": identity, "basis": note, **call_witness} for note in notes
            )
        except (ValueError, TypeError) as error:
            diagnostics.append(
                {
                    "call_id": identity,
                    "kind": event.kind,
                    "keys": sorted(event.payload),
                    "basis": str(error),
                    **call_witness,
                }
            )
    return failed, diagnostics


def _frozen_bodies(
    rows: dict[str, dict[str, Any]], contents: dict[str, str]
) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    bodies, diagnostics = {}, []
    if len(rows) > 64:
        return {}, [{"basis": "detail-read observation budget: more than 64 delivered details"}]
    for path, row in rows.items():
        text = contents.get(row["source_path"])
        body = text.encode("utf-8") if isinstance(text, str) else b""
        if (
            not body
            or len(body) > 2 * 1024 * 1024
            or len(body) != row["size_bytes"]
            or hashlib.sha256(body).hexdigest() != row["sha256"]
        ):
            diagnostics.append(
                {
                    "path": path,
                    "basis": "frozen detail body absent, over 2 MiB, or differs from delivery hash/size",
                }
            )
        else:
            bodies[path] = body
    return bodies, diagnostics


def observe_detail_reads(
    delivery: PointerDelivery,
    capture: NativeCapture,
    project_root: Path,
    *,
    frozen_contents: dict[str, str],
) -> dict[str, Any]:
    """Pure kept-log observations; never reopen files or infer a grade/completeness."""
    rows = {
        str(detail.path): {
            "source_path": str(detail.source_path),
            "path": str(detail.path),
            "revision": detail.revision,
            "step": detail.step,
            "sha256": detail.sha256,
            "size_bytes": detail.size_bytes,
            "observations": [],
        }
        for detail in delivery.details
    }
    bodies, body_diagnostics = _frozen_bodies(rows, frozen_contents)
    failed, diagnostics = _observe(capture, rows, bodies)
    diagnostics = body_diagnostics + diagnostics
    for row in rows.values():
        observations = row["observations"]
        opened = [item for item in observations if item["state"] == "opened"]
        selected = next((item for item in opened if item["wholeRead"]), None)
        selected = selected or (opened[0] if opened else observations[0] if observations else None)
        row.update(
            state=selected["state"] if selected else _ABSENCE,
            basis="source-bound successful literal native read" if selected else _REASON,
            call_id=selected["call_id"] if selected else None,
            wholeRead=selected["wholeRead"] if selected else None,
            nativeEvent=selected["call"] if selected else None,
        )
    return {
        "schema": "ar-eval-detail-read-observations/v1",
        "harness": capture.harness,
        "session_id": capture.session_id,
        "project_root": str(project_root),
        "captureRawExportSha256": raw_export_digest(
            capture.harness, capture.session_id, capture.raw_records
        ),
        "sequenceBasis": "input NativeCapture.events; not original native line ordinals",
        "sourceIdBasis": "derived from captured source path and declared SHA-256",
        "details": list(rows.values()),
        "failedAttempts": failed,
        "diagnostics": diagnostics,
        "observationOnly": True,
        "absenceReason": _REASON,
    }

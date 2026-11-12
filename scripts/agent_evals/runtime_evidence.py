"""Observed native runtime facts; command derivation uses only a frozen trial snapshot."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from .adapters.models import NativeCapture, NativeEvent
from .artifacts import json_digest

PROOF_LIMITS = [
    "An explicit argv[0] is the actor's requested path, not proof that execution succeeded.",
    "A derived bare name uses only the recorded trial PATH snapshot; later shell or filesystem changes are not observed.",
    "Unresolved executable identity is not evidence of a stale build or absent conduct under C6(e).",
    "A tool_describe response is a queried schema subset, not a complete actor tool inventory.",
    "The complete MCP inventory covers the bound tool server, not the harness's native terminal or skill tools.",
]


def _decoded(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _object(value: Any) -> dict[str, Any] | None:
    """Only native result envelopes, never recursive prose/blob discovery."""
    value = _decoded(value)
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("structuredContent"), dict):
        return value["structuredContent"]
    if "output" in value:
        inner = _decoded(value["output"])
        if isinstance(inner, dict):
            return inner
    content = value.get("content")
    if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
        inner = _decoded(content[0].get("text"))
        if isinstance(inner, dict):
            return inner
    return value


def _reference(event: NativeEvent) -> dict[str, Any]:
    return {
        "observationId": f"event-{event.sequence}",
        "source": {"path": event.source, "sha256": event.source_sha256},
        "payloadSha256": json_digest(event.payload),
    }


def _command_argv(command: Any) -> tuple[list[str], str | None]:
    if isinstance(command, str):
        try:
            argv = shlex.split(command)
        except ValueError as error:
            return [], f"command cannot be tokenized: {error}"
        reason = (
            "shell syntax or environment expansion changes command resolution"
            if re.search(r"[|;&><\n`$]", command)
            else None
        )
        return argv, reason
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        return command, None
    return [], "native command has no supported string or argv shape"


def _snapshot_resolution(first: str, snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if snapshot is None:
        return {"state": "unresolved", "reason": "no recorded trial PATH snapshot"}
    digest = snapshot.get("sha256")
    body = {key: value for key, value in snapshot.items() if key != "sha256"}
    if digest != json_digest(body):
        return {
            "state": "unresolved",
            "reason": "recorded trial PATH snapshot digest differs or is absent",
        }
    executables = snapshot.get("executables")
    entry = executables.get(first) if isinstance(executables, dict) else None
    path = entry.get("path") if isinstance(entry, dict) else None
    if not isinstance(path, str) or not path.startswith("/"):
        return {
            "state": "unresolved",
            "reason": "bare name absent from recorded trial PATH resolutions",
            "pathSnapshotSha256": digest,
        }
    return {"state": "derived", "path": path, "pathSnapshotSha256": digest}


def _argv_resolution(argv: list[str], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    first = argv[0]
    executable = first.rsplit("/", 1)[-1]
    reason = None
    if "=" in first or executable == "env":
        reason = "environment-modifying invocation"
    elif executable in {"sh", "bash", "dash", "zsh", "fish"} and any(
        flag.startswith("-") and "c" in flag for flag in argv[1:]
    ):
        reason = "shell -c dispatch; inner resolution is not observed"
    elif "/" in first and not first.startswith("/"):
        reason = "relative executable needs an observed command working directory"
    if reason:
        return {"state": "unresolved", "reason": reason}
    if first.startswith("/"):
        return {"state": "explicit", "path": first}
    return _snapshot_resolution(first, snapshot)


def _resolution(command: Any, snapshot: dict[str, Any] | None) -> dict[str, Any]:
    argv, reason = _command_argv(command)
    if reason or not argv:
        return {
            "argv0": argv[0] if argv else None,
            "state": "unresolved",
            "reason": reason or "empty command",
        }
    return {"argv0": argv[0], **_argv_resolution(argv, snapshot)}


def _append_command(result, event, matches, reference, path_snapshot):
    payload = event.payload
    if payload.get("name") not in {
        "terminal",
        "exec_command",
        "functions.exec_command",
        "CommandExecution",
        "shell",
    }:
        return
    arguments = _object(payload.get("arguments", payload.get("input"))) or {}
    command = arguments.get("command", arguments.get("cmd"))
    resolution = _resolution(command, path_snapshot)
    result["commands"].append(
        {
            **reference,
            "command": command,
            "resolution": resolution,
            "results": [_reference(one) for one in matches],
        }
    )
    if resolution["state"] == "unresolved":
        result["unresolved"].append(
            {"observationId": reference["observationId"], "reason": resolution["reason"]}
        )
    text = command if isinstance(command, str) else json.dumps(command)
    if re.search(r"\bwhich\s|\bcommand\s+-v\b", text):
        for one in matches:
            result["resolutionObservations"].append(
                {
                    **reference,
                    "result": _reference(one),
                    "output": one.payload.get("output", one.payload.get("content")),
                    "proof": "Observed lookup output; not reassigned to later invocations.",
                }
            )


def _append_result(result, reference, name, one):
    observed = _object(one.payload.get("output", one.payload.get("content")))
    if observed is None:
        return
    if name.endswith("server_info") and isinstance(observed.get("servingBuild"), dict):
        result["servingBuildObservations"].append(
            {**reference, "result": _reference(one), "servingBuild": observed["servingBuild"]}
        )
    if name in {"tool_describe", "list_tools", "tools/list"} and isinstance(
        observed.get("tools"), (dict, list)
    ):
        result["actorToolSchemas"].append(
            {
                **reference,
                "result": _reference(one),
                "tools": observed["tools"],
                "completeInventory": False,
            }
        )


def collect_runtime_evidence(
    capture: NativeCapture,
    *,
    server_info: dict[str, Any] | None = None,
    path_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return source-bound runtime observations without reading the current environment."""
    result: dict[str, Any] = {
        "schema": "ar-eval-runtime-evidence/v1",
        "servingBuildObservations": [],
        "mcpToolInventory": None,
        "actorToolSchemas": [],
        "commands": [],
        "resolutionObservations": [],
        "unresolved": [],
        "proofLimits": PROOF_LIMITS,
    }
    if server_info is not None:
        result["servingBuildObservations"].append(
            {
                "provenance": "trial same-definition MCP probe",
                "receiptSha256": json_digest(server_info),
                "servingBuild": server_info.get("servingBuild"),
            }
        )
        result["mcpToolInventory"] = server_info.get("evaluationToolInventory")
    if path_snapshot is not None:
        result["pathSnapshot"] = {
            "receiptSha256": json_digest(path_snapshot),
            "snapshotSha256": path_snapshot.get("sha256"),
            "recordedPATH": path_snapshot.get("path"),
        }
    paired: dict[str, list[NativeEvent]] = {}
    for event in capture.events:
        identity = event.payload.get("call_id", event.payload.get("tool_use_id"))
        if event.kind == "tool_result" and isinstance(identity, str):
            paired.setdefault(identity, []).append(event)
    for event in capture.events:
        if event.kind != "tool_call":
            continue
        payload = event.payload
        name = payload.get("name", "")
        identity = payload.get("call_id", payload.get("id"))
        matches = paired.get(identity, []) if isinstance(identity, str) else []
        reference = {**_reference(event), "callId": identity, "tool": name}
        _append_command(result, event, matches, reference, path_snapshot)
        for one in matches:
            _append_result(result, reference, name, one)
    return result

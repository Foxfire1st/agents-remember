"""Concrete current-trial native capture and opposite-family rubric execution.

Native capture is bound to the fresh trial session. Judge environment and
capture roots remain those supplied by the trial sources.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agents_remember.errors import AuthorityError

from ..artifacts import read_object, sha256_file
from ..judge_evidence import enforce_judge_limit
from ..package_budget import JudgeDiskEvidence, load_judge_disk_evidence
from .capture import capture_claude_cli_jsonl
from .discovery import discover_capture
from .execution import LaunchProfile, NativeBinding, NativeInvocation
from .host_events import HostCaptureWindow, merge_host_evidence
from .judge_failures import finalize_judge_context
from .judges import (
    JudgeRequest,
    answered_read_metrics,
    audit_judge_reads,
    codex_judge_argv,
    execute_judge_native,
    judge_profile,
    opus_judge_argv,
    validate_judge_project,
)
from .models import Harness, NativeCapture, SourceFile, raw_export_digest
from .paseo_session import paseo_session
from .server_calls import FixtureServerWindow, recover_server_calls
from .usage import native_usage


class TrialSources(Protocol):
    @property
    def project_root(self) -> Path: ...

    @property
    def paseo_home(self) -> Path: ...

    @property
    def settings_path(self) -> Path: ...

    @property
    def tool_build(self) -> str: ...

    @property
    def native_capture_roots(self) -> Mapping[str, Path]: ...

    @property
    def host_event_paths(self) -> tuple[Path, ...]: ...

    @property
    def turn_ends_path(self) -> Path | None: ...

    def environment(self) -> dict[str, str]: ...


def native_candidates(harness: Harness, root: Path, not_before: float) -> tuple[Path, ...]:
    """Only native transcript storage; fresh file selection precedes metadata reads."""
    if harness == "hermes":
        return (root / "state.db",)
    if harness != "codex":
        raise ValueError("project transcript discovery supports Codex and Hermes actors")
    return tuple(
        path
        for path in sorted((root / "sessions").rglob("*.jsonl"))
        if path.is_file() and path.stat().st_mtime >= not_before
    )


def capture_trial_actor(
    provisioned: TrialSources,
    harness: Harness,
    host_agent_id: str,
    not_before: float,
) -> NativeCapture:
    """The host UUID is provenance only, never a native session identity."""
    root = provisioned.native_capture_roots.get(harness)
    if root is None:
        return NativeCapture(harness, "", (), (), (), ("native capture home not bound",))
    if harness == "hermes" and (
        root.is_symlink()
        or not root.resolve().is_relative_to(provisioned.project_root.parent.resolve())
        or (root / "state.db").is_symlink()
    ):
        return NativeCapture(harness, "", (), (), (), ("Hermes capture home is not trial-owned",))
    try:
        binding = paseo_session(
            provisioned.paseo_home, provisioned.project_root, host_agent_id, harness
        )
        native = discover_capture(
            harness,
            native_candidates(harness, root, not_before),
            project=provisioned.project_root,
            not_before=not_before,
            session_id=binding.session_id,
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        return NativeCapture(
            harness, "", (), (), (), (f"native actor session binding failed: {error}",)
        )
    records = (*native.raw_records, {"paseoActorIdentity": binding.projection})
    native = replace(
        native,
        source_files=(*native.source_files, binding.source),
        raw_records=records,
        raw_export_sha256=raw_export_digest(native.harness, native.session_id, records),
    )
    window = host_window(provisioned, host_agent_id)
    native = _fixture_server_gate(provisioned, native, host_agent_id, window)
    if not provisioned.host_event_paths:
        return native
    logs = provisioned.turn_ends_path
    merged = merge_host_evidence(
        native,
        owner_id=host_agent_id,
        host_event_paths=provisioned.host_event_paths,
        turn_ends_path=logs if logs is not None and logs.is_file() else None,
        window=window,
    ).capture
    if window is None or not window.complete:
        return replace(
            merged,
            unknowns=(*merged.unknowns, "capture-absent: native host recorder window incomplete"),
        )
    return merged


def _fixture_server_gate(
    provisioned: TrialSources,
    capture: NativeCapture,
    owner: str,
    window: HostCaptureWindow | None,
) -> NativeCapture:
    """Bind server evidence to this trial and its awaited pre-turn receipt."""
    settings = provisioned.settings_path
    try:
        root_value = read_object(settings).get("transcriptRoot")
        if not isinstance(root_value, str) or not Path(root_value).is_absolute():
            raise ValueError(f"fixture log root is not explicitly bound in {settings}")
        root = Path(root_value)
        fixture = provisioned.project_root.absolute().parent
        relative = root.absolute().relative_to(fixture)
        if any(
            (fixture / Path(*relative.parts[:index])).is_symlink()
            for index in range(1, len(relative.parts) + 1)
        ):
            raise ValueError(f"fixture log root traverses a symlink: {root}")
        checkpoint = provisioned.paseo_home / "agent-eval" / f"{owner}.window.json"
        if window is None:
            raise ValueError(f"pre-actor server-log checkpoint unavailable: {checkpoint}")
        if not window.complete:
            return replace(
                capture,
                unknowns=(
                    *capture.unknowns,
                    f"capture-absent: no complete final stop in {checkpoint}",
                ),
            )
        source = SourceFile(str(checkpoint.resolve()), sha256_file(checkpoint))
        binding = FixtureServerWindow(
            root,
            window.started_at,
            window.ended_at,
            source,
            owner_id=owner,
            project_root=provisioned.project_root,
        )
    except (AuthorityError, OSError, ValueError) as error:
        return replace(capture, unknowns=(*capture.unknowns, f"binding-unavailable: {error}"))
    return recover_server_calls(capture, binding)


def host_window(provisioned: TrialSources, host_agent_id: str) -> HostCaptureWindow | None:
    """Proof from the native plugin's awaited subscription and actual end hook."""
    if Path(host_agent_id).name != host_agent_id:
        raise AuthorityError("native recorder owner is not a single path component")
    marker = provisioned.paseo_home / "agent-eval" / f"{host_agent_id}.window.json"
    if not marker.is_file():
        return None
    value = read_object(marker)
    ready = value.get("readyAt")
    ended = value.get("lastTurnEndedAt")
    try:
        start_at = datetime.fromisoformat(str(ready))
        end_at = datetime.fromisoformat(str(ended))
        dated = (
            start_at.tzinfo is not None
            and end_at.tzinfo is not None
            and start_at <= end_at <= datetime.now(UTC)
        )
    except (ValueError, TypeError):
        dated = False
    valid = (
        dated
        and value.get("agentId") == host_agent_id
        and value.get("projectRoot") == str(provisioned.project_root)
        and isinstance(ready, str)
        and bool(value.get("lastTurnEndedAt"))
        and value.get("errors") == []
    )
    return HostCaptureWindow(str(ready), datetime.now(UTC).isoformat(), valid)


@dataclass(frozen=True)
class NativeJudgeOptions:
    timeout_seconds: float
    executable: Path | None = None


def _token_total(usage: object, fields: tuple[str, ...]) -> int | None:
    if not isinstance(usage, dict) or not all(
        type(usage.get(field)) is int and usage[field] >= 0 for field in fields
    ):
        return None
    return sum(usage[field] for field in fields)


def _native_call_block(block: object) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "tool_use"
        and isinstance(block.get("id"), str)
    )


def _native_result_block(block: object) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "tool_result"
        and isinstance(block.get("tool_use_id"), str)
    )


def _message_blocks(raw: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    message = raw.get("message")
    if not isinstance(message, dict):
        return {}, []
    blocks = message.get("content")
    return message, blocks if isinstance(blocks, list) else []


def _claude_tool_metrics(capture: NativeCapture) -> tuple[list[dict[str, Any]], int, list[str]]:
    parents: dict[str, tuple[str, object, int]] = {}
    groups: dict[str, int] = {}
    results: dict[str, tuple[str, dict[str, Any]]] = {}
    unknowns = []
    for index, raw in enumerate(capture.raw_records):
        message, blocks = _message_blocks(raw)
        if raw.get("type") == "assistant" and isinstance(message.get("id"), str):
            identity = message["id"]
            groups.setdefault(identity, len(groups) + 1)
            for block in blocks:
                if _native_call_block(block):
                    parents[block["id"]] = (identity, raw.get("request_id"), groups[identity])
        elif raw.get("type") == "user":
            for block in blocks:
                if not _native_result_block(block):
                    continue
                identity, content = block["tool_use_id"], block.get("content")
                encoded = json.dumps(content)
                if identity in results:
                    if results[identity][0] != encoded:
                        unknowns.append(f"conflicting native tool result: {identity}")
                    continue
                parent = parents.get(identity, (None, None, None))
                results[identity] = (
                    encoded,
                    {
                        "toolUseId": identity,
                        "nativeRawOrdinal": index,
                        "parentMessageId": parent[0],
                        "requestId": parent[1],
                        "messageGroupOrdinal": parent[2],
                        "contentUtf8Bytes": len(content.encode())
                        if isinstance(content, str)
                        else None,
                        "serializedContentUtf8Bytes": len(encoded.encode()),
                        "turnIndex": None,
                        "remainingTurns": None,
                        "byteTurnExposure": None,
                        "turnIndexUnknown": "native-turn-index-unavailable",
                    },
                )
    return [entry[1] for entry in results.values()], len(groups), unknowns


def _claude_session_metrics(capture: NativeCapture) -> dict[str, Any]:
    finals = [
        raw
        for raw in capture.raw_records
        if raw.get("type") == "result" and raw.get("session_id") == capture.session_id
    ]
    final = finals[0] if len(finals) == 1 else {}
    model_usage = final.get("modelUsage")
    fields = ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "outputTokens")
    usages = list(model_usage.values()) if isinstance(model_usage, dict) and model_usage else []
    valid = bool(usages) and all(_token_total(usage, fields) is not None for usage in usages)
    components = {
        field: sum(usage[field] for usage in usages) if valid else None for field in fields
    }
    components["totalTokens"] = _token_total(components, fields) if valid else None
    final_usage = final.get("usage")
    turns = final.get("num_turns")
    return {
        "turns": turns if type(turns) is int and turns >= 0 else None,
        "tokenComponents": components,
        "modelUsage": model_usage,
        "finalResultUsage": final_usage,
        "tokenBasis": "native result.modelUsage session aggregate; no nested counters added",
        "finalResultUsageScope": "final call, not the session",
        "finalResultUsageTotal": _token_total(
            final_usage,
            (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "output_tokens",
            ),
        ),
        "tokenUnknown": None if valid else "native modelUsage session counters absent/invalid",
    }


def judge_run_metrics(capture: NativeCapture, inline_prompt: str) -> dict[str, Any]:
    """Account only native counters and byte representations; never infer turns."""
    report: dict[str, Any] = {
        "schema": "ar-native-judge-run-metrics/v1",
        "inlinePackageCharacters": len(inline_prompt),
        "inlinePackageUtf8Bytes": len(inline_prompt.encode()),
        "nativeSessionId": capture.session_id,
        "rawExportSha256": capture.to_dict()["raw_export_sha256"],
        "sourceFiles": [
            {"path": source.path, "sha256": source.sha256} for source in capture.source_files
        ],
    }
    usage = native_usage(capture)
    report["nativeUsage"] = usage
    if capture.harness == "claude":
        report.update(_claude_session_metrics(capture))
        # The admitted usage parser also verifies same-result model costs.
        # Preserve raw four components, but never claim an unchecked total.
        report["tokenComponents"]["totalTokens"] = usage["total_tokens"]
        report["tokenUnknown"] = usage["unknown_reason"]
        results, groups, unknowns = _claude_tool_metrics(capture)
        report.update(
            toolResults=results,
            uniqueToolResults=len(results),
            observedAssistantMessageGroups=groups,
            toolResultUtf8Bytes=sum(row["contentUtf8Bytes"] for row in results)
            if all(row["contentUtf8Bytes"] is not None for row in results)
            else None,
            serializedToolResultUtf8Bytes=sum(row["serializedContentUtf8Bytes"] for row in results),
            toolResultByteBasis="decoded UTF8 content vs json.dumps(content) default ensure_ascii UTF8; neither catalog file size nor full wrapper size",
            unknowns=unknowns,
        )
        report["nativeCountObservations"] = {
            "keptMessages": groups,
            "keptMessageUnit": "distinct exported assistant message IDs",
            "numTurns": report["turns"],
            "numTurnsUnit": "native SDK turns",
            "countsComparable": False,
            "causeCanFire": False,
            "causeAvailability": "instrument-unavailable",
            "limitation": "These are different units; no completeness comparison is made. The not-opened cause cannot fire on the current native export.",
        }

    else:
        report.update(
            nativeUsage=usage,
            tokenComponents=usage["components"],
            totalTokens=usage["total_tokens"],
            turns=None,
            turnCountUnknown="native judge turn total unavailable; tool ordinals are not API turns",
            toolResults=[
                {
                    "callId": event.payload.get("call_id"),
                    "nativeEventSequence": event.sequence,
                    "serializedPayloadUtf8Bytes": len(json.dumps(event.payload).encode()),
                    "turnIndex": None,
                    "remainingTurns": None,
                    "byteTurnExposure": None,
                    "turnIndexUnknown": "native-turn-index-unavailable",
                }
                for event in capture.events
                if event.kind == "tool_result"
            ],
        )
    return report


def _judge_before_files(disk: JudgeDiskEvidence) -> dict[str, dict[str, Any]]:
    # These hashes are the validated PRE-RUN manifest, never the current files
    # rehashed after the judge has had write access to its workspace.
    before = {
        row["path"]: {
            "sha256": row["sha256"],
            "sizeBytes": row["sizeBytes"],
            "criteria": list(row.get("criteria", [])),
        }
        for row in disk.rows
    }
    before[disk.manifest_path.relative_to(disk.root).as_posix()] = {
        "sha256": disk.manifest_sha256,
        "sizeBytes": disk.manifest_path.stat().st_size,
        "criteria": [],
    }
    return before


def _judge_current_file(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        target = os.fsencode(os.readlink(path))
        return {
            "sha256": hashlib.sha256(target).hexdigest(),
            "sizeBytes": len(target),
            "kind": "symlink",
            "digestBasis": "link target bytes; target not followed",
        }
    return {"sha256": sha256_file(path), "sizeBytes": path.stat().st_size}


def judge_folder_integrity(root: Path, before: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compare immutable pre-run manifest digests against every post-run file."""
    after = {
        path.relative_to(root).as_posix(): _judge_current_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() or path.is_symlink()
    }
    report: dict[str, Any] = {
        "schema": "ar-judge-folder-integrity/v1",
        "changed": [],
        "added": [],
        "removed": [],
        "unchangedCount": 0,
    }
    for name in sorted(set(before) | set(after)):
        old = (
            {key: before[name][key] for key in ("sha256", "sizeBytes")} if name in before else None
        )
        new = after.get(name)
        row = {"path": name, "criteria": list(before.get(name, {}).get("criteria", []))}
        if old is None:
            report["added"].append({**row, "after": new})
        elif new is None:
            report["removed"].append({**row, "before": old})
        elif new != old:
            report["changed"].append({**row, "before": old, "after": new})
        else:
            report["unchangedCount"] += 1
    return report


def _judge_integrity_audit(audit: dict[str, Any], integrity: dict[str, Any]) -> None:
    audit["folderIntegrity"] = integrity
    affected = {row["path"] for kind in ("changed", "added", "removed") for row in integrity[kind]}
    if not affected:
        return
    for row in audit.get("referenceStates", []):
        if row["path"] in affected and row["state"] != "not seen in kept log":
            row["state"] = "unknown_coverage"
    for row in audit.get("opened", []):
        if row["path"] in affected:
            row.update(originalEvidence=False, wholeRead=False, integrity="changed-or-added")
    for row in audit.get("missingReferencedEvidence", []):
        if any(path in affected for path in row["paths"]):
            row.update(cause=None, causeSuppressed="unknown_coverage")
    # Mutations change evidence provenance, never create an absence-rate signal.
    audit["criteriaCarryingCause"] = 0
    audit["observedEligibleCriteria"] = 0


def _finalize_judge_audit(root: Path, audit: dict, verdict: dict, rubric: tuple[str, ...]) -> None:
    finalize_judge_context(audit, verdict, rubric)
    (root / "judge-read-audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def run_native_judge(
    provisioned: TrialSources,
    actor: LaunchProfile,
    request: JudgeRequest,
    evidence: Path,
    options: NativeJudgeOptions,
) -> NativeCapture:
    """Run one actual blind judge via verified native CLI, retaining its raw output."""
    profile = judge_profile(actor)
    enforce_judge_limit(request, judge_harness=profile.harness)
    disk = load_judge_disk_evidence(evidence, request, profile.harness)
    before_files = _judge_before_files(disk)
    root = evidence / "rubric-judge"
    root.mkdir(parents=True, exist_ok=True)
    project = disk.root
    validate_judge_project(project)
    environment = provisioned.environment()
    command = "claude" if profile.harness == "claude" else "codex"
    selected_executable = (
        str(options.executable)
        if options.executable is not None
        else shutil.which(command, path=environment.get("PATH"))
    )
    if selected_executable is None:
        return NativeCapture(profile.harness, "", (), (), (), (f"native {command} unavailable",))
    identity = str(uuid.uuid4())
    argv = (
        opus_judge_argv(Path(selected_executable), identity)
        if profile.harness == "claude"
        else codex_judge_argv(Path(selected_executable), project)
    )
    # Provider transport uses the unchanged native authentication selectors.
    # The evidence contract grants only native, confined read tooling; no MCP.
    settings = root / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    home = root / "home"
    home.mkdir()
    binding = NativeBinding(project, home, settings, provisioned.tool_build, (), profile)
    invocation = NativeInvocation(
        argv, project, environment, root, binding, options.timeout_seconds
    )
    started = time.time()
    execution = execute_judge_native(invocation, prompt=request.prompt())
    if profile.harness == "claude":
        capture = capture_claude_cli_jsonl([execution.stdout_path], identity)
    else:
        source_root = provisioned.native_capture_roots.get("codex")
        if source_root is None:
            raise AuthorityError("Codex judge capture home is not bound")
        capture = discover_capture(
            "codex",
            native_candidates("codex", source_root, started),
            project=project,
            not_before=started,
        )
    unknowns = list(execution.unknowns)
    if execution.returncode != 0:
        unknowns.append(
            f"native rubric judge exit={execution.returncode}; stderr={execution.stderr_path}"
        )
    integrity = judge_folder_integrity(project, before_files)
    audit = audit_judge_reads(capture, disk)
    _judge_integrity_audit(audit, integrity)
    audit["runMetrics"] = judge_run_metrics(capture, request.prompt())
    audit["inlineCounts"] = {
        "characters": len(request.prompt()),
        "utf8Bytes": len(request.prompt().encode()),
    }
    audit["accessContract"] = disk.access_contract
    audit["appliedLimits"] = disk.applied_limits
    # Runtime import uses the existing verdict parser after module initialization;
    # cases imports JudgeRequest from judges, so no second parser is introduced.
    from ..graders.cases import parse_native_verdict, verdict_parse_provenance  # noqa: PLC0415

    verdict, verdict_source = parse_native_verdict(capture)
    _finalize_judge_audit(root, audit, verdict, request.rubric)
    metrics = answered_read_metrics(audit, verdict, request)
    integrity["criterionBindings"] = metrics["evidenceMutationCriterionBindings"]
    (root / "judge-evidence-integrity.json").write_text(
        json.dumps(integrity, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    metrics["nativeVerdictSource"] = verdict_source
    metrics["verdictParseProvenance"] = verdict_parse_provenance(capture)
    (root / "judge-read-metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (root / "judge-run-metrics.json").write_text(
        json.dumps(audit["runMetrics"], ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return replace(
        capture,
        unknowns=tuple(dict.fromkeys((*capture.unknowns, *unknowns, *audit["unknowns"]))),
    )

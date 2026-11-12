"""Blind judge packaging with complete inline or byte-exact disk evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .adapters.execution import LaunchProfile
from .adapters.judges import JudgeRequest, judge_profile
from .adapters.models import NativeCapture, NativeEvent
from .artifacts import EvidenceFile, json_digest
from .package_budget import (
    MAX_PROMPT_UTF8_BYTES,
    DiskPackageContext,
    JudgePackageTooLarge,
    enforce_judge_limit,
    fit_package,
)

__all__ = [
    "MAX_PROMPT_UTF8_BYTES",
    "EvidenceSelection",
    "JudgeEvidencePackage",
    "JudgePackageInputs",
    "JudgePackageTooLarge",
    "enforce_judge_limit",
    "package_judge_evidence",
    "token_projection",
]

TARGET_TOKENS = 100_000  # Reporting target, never permission to discard evidence.
# Manager 20261029T234203Z: approved three-observation linear fit.
# Actual usage remains in native result records for subsequent refits.
TOKEN_FIXED_OVERHEAD = 26_394
TOKEN_PER_BYTE_NUMERATOR = 4_782
TOKEN_PER_BYTE_DENOMINATOR = 10_000


def token_projection(prompt_bytes: int) -> dict:
    estimated = (
        TOKEN_FIXED_OVERHEAD
        + (prompt_bytes * TOKEN_PER_BYTE_NUMERATOR + TOKEN_PER_BYTE_DENOMINATOR - 1)
        // TOKEN_PER_BYTE_DENOMINATOR
    )
    return {
        "estimatedTokens": estimated,
        "overTarget": estimated > TARGET_TOKENS,
        "tokenEstimateBasis": {
            "model": "linear",
            "fixedOverheadTokens": TOKEN_FIXED_OVERHEAD,
            "tokensPerByteNumerator": TOKEN_PER_BYTE_NUMERATOR,
            "tokensPerByteDenominator": TOKEN_PER_BYTE_DENOMINATOR,
            "authority": "Manager 20261029T234203Z",
            "actualProviderUsageIsAuthoritative": True,
        },
    }


EXCLUDED_REPORT_PATHS = (
    "reports/outstanding-memory-actions.json",
    "reports/checks/memory-quality-full.json",
)

POLICY = {
    "id": "ar-judge-disk-evidence/v1",
    "artifacts": "Whole inline evidence or byte-exact files; no excerpts or summaries.",
    "observations": "Every action/result in source order; full opaque payloads and paired outputs on disk.",
}


@dataclass(frozen=True)
class EvidenceSelection:
    """Semantic decisions supplied by the case owner, never inferred from size."""

    case_id: str
    action_criteria: dict[str, tuple[int, ...]]
    report_dependencies: dict[str, tuple[int, ...]]
    report_entry_pointers: dict[str, tuple[str, ...]]
    message_criteria: dict[int, tuple[int, ...]]
    file_criteria: dict[str, tuple[int, ...]] = field(default_factory=dict)
    grading_context: dict[str, Any] = field(default_factory=dict)
    ledger_changes: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgePackageInputs:
    effect_diff: dict[str, Any]
    selection: EvidenceSelection
    evidence_root: Path
    trial_id: str
    actor_profile: LaunchProfile


@dataclass(frozen=True)
class JudgeEvidencePackage:
    request: JudgeRequest
    manifest: dict[str, Any]


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _artifact_entries(
    request: JudgeRequest,
    files: tuple[EvidenceFile, ...],
    diff: dict[str, Any],
    selection: EvidenceSelection | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Supply whole inputs to the one budget constructor; never elide carriers."""
    changed = {row["path"]: row for row in diff.get("files", [])}
    bindings = diff.get("filePathBindings", {})
    entries = []
    for index, (text, source) in enumerate(zip(request.artifact_evidence, files, strict=True)):
        phase, _, path = source.path.partition("/")
        if path in changed and changed[path].get(phase) != source.sha256:
            raise ValueError(f"changed artifact hash differs from effect inventory: {source.path}")
        mandatory = []
        if (
            phase == "after"
            and path.startswith(("projects/memory/", "onboarding/", "knowledge/"))
            and path in changed
        ):
            mandatory.append("changed-memory-carriers")
        if selection is not None and path in selection.file_criteria:
            mandatory.append("case-named-carriers")
        entries.append(
            {
                "artifactIndex": index,
                "sourcePath": source.path,
                "judgePath": phase + "/" + bindings.get(path, path),
                "originalFileSha256": source.sha256,
                "fullBlindedArtifactSha256": _text_digest(text),
                "fullUtf8Bytes": len(text.encode()),
                "diffReference": None,
                "mandatory": mandatory,
                "citations": [
                    {"claimId": row["claimId"], "fragment": row["fragment"]}
                    for row in (
                        selection.grading_context.get("citedInputs", []) if selection else []
                    )
                    if row["path"] == path
                ],
                "rationale": "Complete source is retained inline or in the bound evidence folder.",
            }
        )
    return list(request.artifact_evidence), entries


def _instrument_sources() -> list[dict[str, str]]:
    root = Path(__file__).resolve().parent
    modules = (
        "judge_evidence.py",
        "package_budget.py",
        "evidence_selection.py",
        "trials.py",
        "regrade.py",
        "adapters/judges.py",
        "adapters/capture.py",
        "adapters/hermes_dispatch.py",
        "adapters/opaque_dispatch.py",
        "adapters/server_calls.py",
        "adapters/native_trials.py",
    )
    return [
        {"module": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
        for name in modules
    ]


def _criteria(indices: tuple[int, ...], count: int) -> None:
    if (
        not indices
        or len(set(indices)) != len(indices)
        or any(type(i) is not int or not 0 <= i < count for i in indices)
    ):
        raise ValueError("selection must name distinct existing rubric criterion indices")


def _validate_observation(text: str, event: NativeEvent) -> str | None:
    value = json.loads(text)
    if (
        not isinstance(value, dict)
        or value.get("kind") != event.kind
        or not isinstance(value.get("payload"), dict)
    ):
        raise ValueError("blinded observation differs from native event shape")
    payload = value["payload"]
    keys = ("call_id", "tool_use_id", "id", "name", "server")
    if {k: payload[k] for k in keys if k in payload} != {
        k: event.payload[k] for k in keys if k in event.payload
    }:
        raise ValueError("blinded observation differs from native call/name/server identity")
    identity = next(
        (event.payload[k] for k in keys[:3] if isinstance(event.payload.get(k), str)), None
    )
    return identity


def _observations(
    request: JudgeRequest, events: list[NativeEvent], selection: EvidenceSelection
) -> tuple[tuple[str, ...], list[dict], list[dict]]:
    for indices in (*selection.action_criteria.values(), *selection.message_criteria.values()):
        _criteria(indices, len(request.rubric))
    opaque_ids = {
        event.payload.get("call_id", event.payload.get("id"))
        for event in events
        if event.kind == "tool_call"
        and (
            event.payload.get("opaque") is True
            or event.payload.get("native_dispatch", {}).get("classification") == "opaque"
        )
    }
    seen = {identity: [] for identity in selection.action_criteria}
    seen_messages = set()
    texts, entries, excluded = [], [], []
    for index, (text, event) in enumerate(zip(request.observations, events, strict=True)):
        identity = _validate_observation(text, event)
        entry = {
            "originalObservationIndex": index,
            "sourceSequence": event.sequence,
            "sourceEventSha256": json_digest(asdict(event)),
            "sourceFileSha256": event.source_sha256,
            "fullBlindedObservationSha256": _text_digest(text),
            "fullUtf8Bytes": len(text.encode()),
            "callIds": [identity] if identity else [],
            "criteria": list(range(len(request.rubric))),
        }
        if event.kind in ("tool_call", "tool_result"):
            if identity in seen:
                seen[identity].append(event.kind)
                entry["criteria"] = list(selection.action_criteria[identity])
            if identity in opaque_ids:
                entry["mandatoryClass"] = "opaque-input-and-outputs"
            entry["rationale"] = (
                "Complete native action inventory, including actions absent from criterion selectors."
            )
        elif event.kind == "message" and event.sequence in selection.message_criteria:
            seen_messages.add(event.sequence)
            entry["criteria"] = list(selection.message_criteria[event.sequence])
            entry["rationale"] = "Exact criterion-referenced decision or communication."
        else:
            entry["rationale"] = "Unselected message retained in hash-bound source capture."
            excluded.append(entry)
            continue
        texts.append(text)
        entries.append(entry)
    if any(sorted(kinds) != ["tool_call", "tool_result"] for kinds in seen.values()):
        raise ValueError("selected action must resolve to complete native call and result")
    if seen_messages != selection.message_criteria.keys():
        raise ValueError("selected message must resolve to exact source sequence")
    return tuple(texts), entries, excluded


def package_judge_evidence(
    request: JudgeRequest,
    capture: NativeCapture,
    rubric_files: tuple[EvidenceFile, ...],
    *,
    inputs: JudgePackageInputs,
) -> JudgeEvidencePackage:
    if capture.harness != inputs.actor_profile.harness:
        raise ValueError("judge package actor profile differs from native capture family")
    effect_diff, selection = inputs.effect_diff, inputs.selection
    frozen = capture.to_dict()
    events = [
        event for event in capture.events if event.kind in ("tool_call", "tool_result", "message")
    ]
    if len(events) != len(request.observations):
        raise ValueError(
            "blinded observations must match every native action/result/message exactly once"
        )
    observations, entries, excluded = _observations(request, events, selection)
    artifact_texts, artifacts = _artifact_entries(request, rubric_files, effect_diff, selection)
    paths = effect_diff.get("filePathBindings", {})
    prompt_diff = {key: value for key, value in effect_diff.items() if key != "filePathBindings"}
    prompt_diff["files"] = [
        {**row, "path": paths.get(row["path"], row["path"])} for row in effect_diff.get("files", [])
    ]
    diff_text = _json(prompt_diff)
    texts = [diff_text, *artifact_texts]
    count = len(request.rubric)
    bindings = [
        {"index": 0, "path": "effect-inventory", "mandatory": [], "criteria": list(range(count))}
    ]
    bindings.extend(
        {
            "index": i + 1,
            "path": row["judgePath"],
            "sourceSha256": row["originalFileSha256"],
            "mandatory": row["mandatory"],
            "criteria": list(
                selection.file_criteria.get(
                    rubric_files[i].path.partition("/")[2], tuple(range(count))
                )
            ),
            "citations": row["citations"],
        }
        for i, row in enumerate(artifacts)
    )
    context = selection.grading_context
    # Claims remain inline; source provenance is retained only outside the blind request.
    for item in context.get("caseInputs", []):
        content = _json(
            {"kind": "bound-case-input", "purpose": item["purpose"], "content": item["content"]}
        )
        bindings.append(
            {
                "index": len(texts),
                "path": f"bound-case-input/{len(texts)}",
                "mandatory": [],
                "inlineRequired": item["purpose"] == "gradingContext",
                "criteria": list(range(count)),
            }
        )
        texts.append(content)
    public_context = {
        key: value
        for key, value in context.items()
        if key not in ("sourceBindings", "caseInputs", "citedInputs")
    }
    if context.get("citedInputs"):
        public_context["citedInputs"] = [
            {
                "path": paths.get(row["path"], row["path"]),
                "claimId": row["claimId"],
                "fragment": row["fragment"],
            }
            for row in context["citedInputs"]
        ]
    if public_context:
        bindings.append(
            {
                "index": len(texts),
                "path": "bound-grading-context",
                "mandatory": [],
                "criteria": list(range(count)),
            }
        )
        texts.append(_json(public_context))
    whole = replace(request, observations=observations, artifact_evidence=tuple(texts))
    observation_bindings = [
        {
            "index": i,
            "path": f"observation/{i}",
            "criteria": row["criteria"],
            "sourceSequence": row["sourceSequence"],
        }
        for i, row in enumerate(entries)
    ]
    packaged, budget = fit_package(
        whole,
        bindings,
        context=DiskPackageContext(
            inputs.evidence_root, inputs.trial_id, judge_profile(inputs.actor_profile).harness
        ),
        observation_bindings=observation_bindings,
        opaque_indices=tuple(
            i
            for i, row in enumerate(entries)
            if row.get("mandatoryClass") == "opaque-input-and-outputs"
        ),
    )
    manifest = {
        **budget,
        "policy": POLICY,
        "captureRawExportSha256": frozen["raw_export_sha256"],
        "fullCaptureSha256": json_digest(frozen),
        "sourceFiles": frozen["source_files"],
        "criteria": [
            {"index": i, "sha256": _text_digest(text)} for i, text in enumerate(request.rubric)
        ],
        "observations": entries,
        "excludedObservations": excluded,
        "artifacts": artifacts,
        "semanticSelection": asdict(selection),
        "gradingContextSources": context.get("sourceBindings", []),
        "gradingContextSha256": json_digest(context) if context else None,
        "instrumentSources": _instrument_sources(),
        "packageBudget": budget,
    }
    manifest["sizes"].update(token_projection(len(packaged.prompt().encode())))
    enforce_judge_limit(
        packaged, judge_harness=judge_profile(inputs.actor_profile).harness, manifest=manifest
    )
    return JudgeEvidencePackage(packaged, manifest)

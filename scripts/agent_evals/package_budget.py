"""Per-judge package construction and the pure final send boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .adapters.judges import JudgeRequest, judge_access_contract

# Retained native rejection: 1,367,124 tokens rejected for the
# hash-verified 2,893,118-byte prompt at 1,000,000 tokens. Preserve the
# admitted 20% reserve: floor(1,000,000 * 2,893,118 * .8 / 1,367,124).
MAX_PROMPT_UTF8_BYTES = (1_000_000 * 2_893_118 * 4) // (5 * 1_367_124)
# Retained native stderr: turn/start input_too_large, max_chars1048576,
# actual_chars1070127. Apply the SAME admitted 20% reserve in this unit.
CODEX_MAX_CHARACTERS = 1_048_576
CODEX_SAFE_CHARACTERS = CODEX_MAX_CHARACTERS * 4 // 5
# Complete observations in six retained envelopes: n=1215, p25=1206B,
# p50=3228B. Round the lower quartile up to a power of two: 2048B.
# This fixed small-payload boundary never depends on a judge cap.
ACTION_INLINE_THRESHOLD_BYTES = 1 << 11
MANDATORY_CLASSES = (
    "changed-memory-carriers",
    "case-named-carriers",
    "opaque-input-and-outputs",
    "rubric",
    "in-package-manifest",
)

# Record-tool receipts do not turn ordinary native prose edits into manual
# recovery. Raw source provenance stays out of the blinded scope data.
COVERED_WRITE_POLICY: dict[str, Any] = {
    "scope": {
        "appliesWhen": "The covered record write is admitted to the actor's role.",
        "coveredRecordWrites": [
            "knowledge_write or admitted knowledge-ingest",
            "citation_fix",
            "citation_note_revision",
            "curator_coherence publish",
            "task_doc edits, including append_decision, begin_review, record_review and record_route_review",
        ],
        "nativeCardProse": {
            "route": "normal-native-edit",
            "scope": "card prose in the admitted leaf memory worktree",
            "coveredRecordReplacement": False,
        },
        "manualRecovery": (
            "The correct-input refusal ladder applies only to an open manual replacement of "
            "the same tool and input class that refused; other writes keep their normal routes. "
            "Native card-prose edits do not trigger this ladder or require a record-tool receipt."
        ),
    },
    "sourceBinding": {
        "sourcePath": "skills/l-01-agent-lifecycles/reference/refused-write.md",
        "sourceSha256": "bbcdb9673a875499f0738ff7c23318c70da16f75455bce3535a18df48392dea7",
        "sourceLines": [24, 27],
    },
    "authority": "Manager 20261030T094851Z G1, confirmed 20261030T095202Z",
}
_REFERENCE_EPOCH = "g1-20261030"
_REFERENCE_MANIFEST_SHA256 = "e91cdd5615571d26d9b9c7ae9df1a672c6955350d362120d33d0192dcc15c1b2"


def _retained_reference() -> tuple[bytes, dict[str, Any]]:
    """Verify the fixed grading epoch independently of current instruction text."""
    directory = Path(__file__).resolve().parent / "reference_epochs" / _REFERENCE_EPOCH
    manifest = (directory / "epoch.json").read_bytes()
    if hashlib.sha256(manifest).hexdigest() != _REFERENCE_MANIFEST_SHA256:
        raise ValueError("retained grading reference epoch manifest differs from its binding")
    epoch = json.loads(manifest)
    binding = COVERED_WRITE_POLICY["sourceBinding"]
    raw = (directory / "refused-write.md").read_bytes()
    if (
        {key: epoch[key] for key in binding} != binding
        or epoch["scopeSha256"] != _sha(_json(COVERED_WRITE_POLICY["scope"]))
        or len(raw) != epoch["sourceSizeBytes"]
        or hashlib.sha256(raw).hexdigest() != binding["sourceSha256"]
    ):
        raise ValueError("retained grading reference differs from its declared epoch binding")
    return raw, epoch


def _policy_trace(epoch: dict[str, Any]) -> dict[str, Any]:
    return {
        **COVERED_WRITE_POLICY,
        "scopeSha256": epoch["scopeSha256"],
        "referenceEpoch": epoch,
    }


def covered_write_policy_trace() -> dict[str, Any]:
    """Bind the policy to the same verified evidence copy used by the disk reference."""
    return _policy_trace(_retained_reference()[1])


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def measurements(text: str) -> dict[str, int]:
    return {"utf8Bytes": len(text.encode()), "characters": len(text)}


def _limits(judge_harness: str) -> dict[str, int]:
    if judge_harness not in ("claude", "codex"):
        raise ValueError(f"No measured judge package bound for harness: {judge_harness}")
    return {
        "utf8Bytes": MAX_PROMPT_UTF8_BYTES,
        **({"characters": CODEX_SAFE_CHARACTERS} if judge_harness == "codex" else {}),
    }


def _over(request: JudgeRequest, judge_harness: str) -> bool:
    values = measurements(request.prompt())
    return any(values[unit] > limit for unit, limit in _limits(judge_harness).items())


class JudgePackageTooLarge(ValueError):
    """Unsent immutable evidence and exact cause, not a failed semantic verdict."""

    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        self.cause = (
            f"{'mandatory-evidence-over-budget' if 'mandatoryClasses' in manifest else 'input-package-over-budget'}: {manifest['sizes']['finalCounts']}; "
            f"limits={manifest['sizes']['appliedLimits']}; "
            f"mandatory classes={_json(manifest.get('mandatoryClasses', {}))}; "
            f"fixed-threshold aggregate={manifest.get('fixedThresholdAggregate', {})}; "
            f"request sha256={manifest['packagedRequestPromptSha256']}; "
            f"top contributors={_json(manifest['sizes']['topContributors'])}"
        )
        super().__init__(self.cause)


def _contributors(request: JudgeRequest, paths: dict) -> list[dict]:
    rows = [
        {
            "kind": kind,
            "index": index,
            "name": paths.get(str(index), f"artifact[{index}]")
            if kind == "artifact"
            else f"{kind}[{index}]",
            **measurements(text),
            "sha256": _sha(text),
        }
        for kind, values in (
            ("rubric", request.rubric),
            ("observation", request.observations),
            ("artifact", request.artifact_evidence),
        )
        for index, text in enumerate(values)
    ]
    return sorted(rows, key=lambda row: row["utf8Bytes"], reverse=True)[:12]


def enforce_judge_limit(
    request: JudgeRequest, *, judge_harness: str, manifest: dict[str, Any] | None = None
) -> None:
    """Measure actual final bytes and characters before any native side effect."""
    prompt = request.prompt()
    counts = measurements(prompt)
    if manifest is not None and (
        manifest.get("packagedRequestPromptSha256") != _sha(prompt)
        or manifest["sizes"]["packagedPromptUtf8Bytes"] != counts["utf8Bytes"]
    ):
        raise ValueError("judge limit manifest does not bind the actual final request")
    result = {**(manifest or {}), "sizes": dict((manifest or {}).get("sizes", {}))}
    contributors = _contributors(request, result.get("artifactPaths", {}))
    result.update(packagedRequestPromptSha256=_sha(prompt))
    result["sizes"].update(
        packagedPromptUtf8Bytes=counts["utf8Bytes"],
        packagedPromptCharacters=counts["characters"],
        finalCounts=counts,
        appliedLimits=_limits(judge_harness),
        measuredByteGuard=MAX_PROMPT_UTF8_BYTES,
        nativeCodexCharacterCap=CODEX_MAX_CHARACTERS,
        marginPercent=20,
        maxPromptUtf8Bytes=MAX_PROMPT_UTF8_BYTES,
        topContributors=contributors,
        overLimit=_over(request, judge_harness),
        guardAction="blocked-before-native-call"
        if _over(request, judge_harness)
        else "within-measured-limit",
    )
    result["sizes"]["overBudgetClasses"] = [
        name
        for name, values in result.get("mandatoryClasses", {}).items()
        if any(values[unit] > limit for unit, limit in _limits(judge_harness).items())
    ]
    if _over(request, judge_harness):
        raise JudgePackageTooLarge(result)


@dataclass(frozen=True)
class JudgeDiskEvidence:
    """Verified folder, complete grant and inline request identity."""

    root: Path
    manifest_path: Path
    manifest_sha256: str
    rows: tuple[dict[str, Any], ...]
    judge_harness: str
    tool_grant: str
    cwd_rule: str
    applied_limits: dict[str, int]
    inline_sha256: str
    package_identity: str
    access_contract: dict[str, Any]


@dataclass(frozen=True)
class DiskPackageContext:
    evidence_root: Path
    trial_id: str
    judge_harness: str


def _write_exact(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"judge evidence changed at {path.name}")
        return
    with path.open("xb") as stream:
        stream.write(raw)


def _row(text: str, binding: dict, kind: str, index: int, judge_harness: str) -> dict:
    values = measurements(text)
    digest = _sha(text)
    row = {
        "path": binding["path"]
        if kind == "reference"
        else f"{'artifacts' if kind == 'artifact' else 'actions'}/{index:04d}.{'txt' if kind == 'artifact' else 'json'}",
        "label": binding["path"],
        "sizeBytes": values["utf8Bytes"],
        "characters": values["characters"],
        "sha256": digest,
        "mustRead": False,
        "criteria": list(binding.get("criteria", ())),
        "kind": kind,
        "inline": True,
        "mandatory": list(binding.get("mandatory", ())),
        "exceedsAppliedLimit": any(
            values[unit] > cap for unit, cap in _limits(judge_harness).items()
        ),
    }
    if kind != "reference":
        row["artifactIndex" if kind == "artifact" else "observationIndex"] = index
    row["inlineRequired"] = bool(binding.get("inlineRequired"))
    row["encoding"] = "utf-8"
    row["byteBasis"] = "Complete already-blinded request value; original source hash is distinct."
    if "sourceSha256" in binding:
        row["originalFileSha256"] = binding["sourceSha256"]
    if "sourceSequence" in binding:
        row["sourceSequence"] = binding["sourceSequence"]
    if binding.get("citations"):
        row["citations"] = binding["citations"]
    return row


def _grading_reference(root: Path, judge_harness: str, raw: bytes) -> dict:
    binding = COVERED_WRITE_POLICY["sourceBinding"]
    path = "reference/refused-write.md"
    row = _row(
        raw.decode("utf-8"),
        {"path": path, "sourceSha256": binding["sourceSha256"]},
        "reference",
        0,
        judge_harness,
    )
    row.update(
        inline=False,
        routeReason="bound-retained-grading-reference",
        byteBasis="Exact hash-bound grading evidence copy; not current instructions or actor evidence.",
    )
    _write_exact(root / path, raw)
    return row


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: type(item).__name__ for key, item in value.items()}
    if isinstance(value, list):
        return {"type": "array", "items": len(value)}
    return {"type": type(value).__name__}


def _short_arguments(value: Any, path: str) -> Any:
    """Readable index values only; the complete argument bytes are in the named file."""
    if isinstance(value, str) and len(value) > 96:
        return {"prefix": value[:64], "characters": len(value), "fullValueIn": path}
    if isinstance(value, dict):
        return {key: _short_arguments(item, path) for key, item in value.items()}
    if isinstance(value, list):
        return {
            "prefix": [_short_arguments(item, path) for item in value[:6]],
            "items": len(value),
            "fullValueIn": path,
        }
    return value


def _reference(row: dict, text: str) -> str:
    event = json.loads(text)
    payload = event.get("payload", {})
    value = {
        "path": row["path"],
        "kind": event.get("kind"),
        "identity": {
            key: payload[key]
            for key in ("call_id", "tool_use_id", "id", "name", "server")
            if key in payload
        },
        "shape": _shape(payload),
        "status": {
            key: payload[key]
            for key in ("status", "exit_code", "exitCode", "isError")
            if key in payload
        },
    }
    if "arguments" in payload or "input" in payload:
        value["shortArguments"] = _short_arguments(
            payload.get("arguments", payload.get("input")), row["path"]
        )
    return _json(value)


def _identity(inline_sha: str, manifest_sha: str, contract: dict, kind: str, limits: dict) -> str:
    return _sha(
        _json(
            {
                "inlineSha256": inline_sha,
                "folderManifestSha256": manifest_sha,
                "judgeHarness": kind,
                "accessContract": contract,
                "appliedLimits": limits,
            }
        )
    )


def _store_rows(
    request: JudgeRequest,
    artifacts: list[dict],
    observations: list[dict],
    root: Path,
    opaque_indices: tuple[int, ...],
) -> None:
    for row, text in zip(artifacts, request.artifact_evidence, strict=True):
        row["inline"] = row["inlineRequired"]
        row["mustRead"] = bool(row["mandatory"]) and not row["inline"]
        row["routeReason"] = "inline-claims-list" if row["inline"] else "artifact-on-disk"
        _write_exact(root / row["path"], text.encode())
    for row, text in zip(observations, request.observations, strict=True):
        row["inline"] = False
        row["mustRead"] = row["sizeBytes"] > ACTION_INLINE_THRESHOLD_BYTES
        row["routeReason"] = (
            "small-action-index" if not row["mustRead"] else "over-action-threshold"
        )
        if row["observationIndex"] in opaque_indices:
            row["routeReason"] = "opaque-full-payload"
            row["mustRead"] = True
            row["mandatory"] = ["opaque-input-and-outputs"]
        # Store every original observation, including small inline values, for exact replay.
        _write_exact(root / row["path"], text.encode())


def _class_measurements(request: JudgeRequest, rows: list[dict], packaged: JudgeRequest) -> dict:
    result = {
        name: {"utf8Bytes": 0, "characters": 0, "inlineUtf8Bytes": 0, "fileUtf8Bytes": 0}
        for name in MANDATORY_CLASSES
    }
    for row in rows:
        for name in row["mandatory"]:
            result[name]["utf8Bytes"] += row["sizeBytes"]
            result[name]["characters"] += row["characters"]
            result[name]["inlineUtf8Bytes" if row["inline"] else "fileUtf8Bytes"] += row[
                "sizeBytes"
            ]
    for name, values in (
        ("rubric", request.rubric),
        ("in-package-manifest", packaged.artifact_evidence[-1:]),
    ):
        for text in values:
            result[name]["utf8Bytes"] += len(text.encode())
            result[name]["characters"] += len(text)
            result[name]["inlineUtf8Bytes"] += len(text.encode())
    return result


def _compact_manifest(rows: list[dict]) -> dict:
    return {
        "kind": "judge-evidence-manifest",
        "schema": "ar-judge-disk-evidence/v1",
        "manifestPath": "manifest.json",
        "files": [{"path": row["path"], "sizeBytes": row["sizeBytes"]} for row in rows],
    }


def folder_manifest_bytes(folder: dict) -> bytes:
    """One complete row per line makes a native label search genuinely narrow."""
    headers = [
        f"{_json(key)}:{_json(value)}" for key, value in sorted(folder.items()) if key != "files"
    ]
    rows = ",\n".join(_json(row) for row in folder["files"])
    return ("{\n" + ",\n".join([*headers, '"files":[\n' + rows + "\n]"]) + "\n}\n").encode()


def fit_package(
    request: JudgeRequest,
    bindings: list[dict[str, Any]],
    *,
    context: DiskPackageContext,
    observation_bindings: list[dict[str, Any]],
    opaque_indices: tuple[int, ...],
) -> tuple[JudgeRequest, dict[str, Any]]:
    """Retain every byte inline or in the explicitly bound evidence folder."""
    evidence_root, trial_id, judge_harness = (
        context.evidence_root,
        context.trial_id,
        context.judge_harness,
    )
    if not trial_id or not evidence_root.is_absolute():
        raise ValueError(
            "judge disk evidence requires explicit absolute evidence root and trial identity"
        )
    if [row["index"] for row in bindings] != list(range(len(request.artifact_evidence))):
        raise ValueError("Every package artifact requires an exact path binding")
    if [row["index"] for row in observation_bindings] != list(range(len(request.observations))):
        raise ValueError("Every observation requires its exact ordered binding")
    contract = judge_access_contract(judge_harness)
    reference, epoch = _retained_reference()
    root = evidence_root / "rubric-judge" / "evidence"
    artifacts = [
        _row(text, binding, "artifact", index, judge_harness)
        for index, (text, binding) in enumerate(
            zip(request.artifact_evidence, bindings, strict=True)
        )
    ]
    observations = [
        _row(text, binding, "action-payload", index, judge_harness)
        for index, (text, binding) in enumerate(
            zip(request.observations, observation_bindings, strict=True)
        )
    ]
    _store_rows(request, artifacts, observations, root, opaque_indices)
    rows = [*artifacts, *observations, _grading_reference(root, judge_harness, reference)]
    manifest_path = root / "manifest.json"

    folder = {
        "schema": "ar-judge-disk-folder/v1",
        "judgeHarness": judge_harness,
        "accessContract": contract,
        "appliedLimits": _limits(judge_harness),
        "coveredWritePolicy": COVERED_WRITE_POLICY["scope"],
        "actionInlineThresholdUtf8Bytes": ACTION_INLINE_THRESHOLD_BYTES,
        "files": rows,
    }
    folder_bytes = folder_manifest_bytes(folder)
    facing = _compact_manifest(rows)
    texts = tuple(
        text
        for row, text in zip(artifacts, request.artifact_evidence, strict=True)
        if row["inline"]
    )
    events = tuple(
        _reference(row, text) for row, text in zip(observations, request.observations, strict=True)
    )
    packaged = replace(request, observations=events, artifact_evidence=(*texts, _json(facing)))
    inline_sha = _sha(packaged.prompt())
    folder_sha = hashlib.sha256(folder_bytes).hexdigest()
    identity = _identity(inline_sha, folder_sha, contract, judge_harness, _limits(judge_harness))
    disk = {
        "root": str(root),
        "manifestPath": str(manifest_path),
        "manifestSha256": folder_sha,
        "rows": rows,
        "judgeHarness": judge_harness,
        "toolGrant": contract["toolGrant"],
        "cwdRule": contract["cwdRule"],
        "accessContract": contract,
        "appliedLimits": _limits(judge_harness),
        "inlineSha256": inline_sha,
        "packageIdentity": identity,
    }
    manifest = {
        "schema": "ar-judge-disk-evidence/v1",
        "trialId": trial_id,
        "diskEvidence": disk,
        "coveredWritePolicy": _policy_trace(epoch),
        "artifactPaths": {str(i): row["label"] for i, row in enumerate(artifacts)},
        "packageType": "disk-evidence" if any(not row["inline"] for row in rows) else "inline",
        "judgeFacingManifestIndex": len(texts),
        "judgeFacingManifestSha256": _sha(packaged.artifact_evidence[-1]),
        "originalRequestPromptSha256": _sha(request.prompt()),
        "packagedRequestPromptSha256": inline_sha,
        "mandatoryClasses": _class_measurements(request, rows, packaged),
        "classMeasurementBasis": "Complete quoted text bytes/characters before outer JSON escaping, split by inline/file route; final request counts are measured separately.",
        "judgeUsageSource": "judge-usage.json",
        "sizes": {
            "packagedPromptUtf8Bytes": len(packaged.prompt().encode()),
            "packagedPromptCharacters": len(packaged.prompt()),
            "finalCounts": measurements(packaged.prompt()),
            "appliedLimits": _limits(judge_harness),
            "topContributors": _contributors(packaged, {}),
            "marginPercent": 20,
            "overLimit": _over(packaged, judge_harness),
            "guardAction": "within-measured-limit",
        },
    }
    enforce_judge_limit(packaged, judge_harness=judge_harness, manifest=manifest)
    _write_exact(manifest_path, folder_bytes)
    return packaged, manifest


def _verify_rows(root: Path, rows: list[dict]) -> None:
    for row in rows:
        location = root / row["path"]
        if (
            location.is_symlink()
            or not location.resolve().is_relative_to(root)
            or not location.is_file()
        ):
            raise ValueError("judge evidence file escapes its bound folder")
        content = location.read_bytes()
        if (
            hashlib.sha256(content).hexdigest() != row["sha256"]
            or len(content) != row["sizeBytes"]
            or len(content.decode()) != row["characters"]
        ):
            raise ValueError("judge evidence file bytes changed")


def _verify_embedded(request: JudgeRequest, manifest: dict, folder: dict) -> None:
    index = manifest["judgeFacingManifestIndex"]
    if type(index) is not int or not 0 <= index < len(request.artifact_evidence):
        raise ValueError("judge embedded manifest index changed")
    text = request.artifact_evidence[index]
    facing = json.loads(text)
    if facing != _compact_manifest(folder["files"]):
        raise ValueError(
            "judge embedded compact manifest differs from actual folder paths and sizes"
        )
    if manifest["judgeFacingManifestSha256"] != _sha(text):
        raise ValueError("judge embedded manifest bytes changed")


def load_judge_disk_evidence(
    evidence_root: Path, request: JudgeRequest, judge_harness: str
) -> JudgeDiskEvidence:
    """Accept only the current exact folder, inline digest and native grant."""
    manifest = json.loads((evidence_root / "judge-evidence-manifest.json").read_bytes())
    if manifest.get("schema") != "ar-judge-disk-evidence/v1":
        raise ValueError("judge disk evidence current schema is required")
    disk = manifest["diskEvidence"]
    if set(disk) != {
        "root",
        "manifestPath",
        "manifestSha256",
        "rows",
        "judgeHarness",
        "toolGrant",
        "cwdRule",
        "accessContract",
        "appliedLimits",
        "inlineSha256",
        "packageIdentity",
    }:
        raise ValueError("judge disk binding fields differ from the current grant schema")
    root = evidence_root.resolve() / "rubric-judge" / "evidence"
    path = root / "manifest.json"
    if Path(disk["root"]).resolve() != root or Path(disk["manifestPath"]).resolve() != path:
        raise ValueError("judge evidence root differs from explicit binding")
    if disk["judgeHarness"] != judge_harness or disk["accessContract"] != judge_access_contract(
        judge_harness
    ):
        raise ValueError("judge evidence grant or kind changed")
    if (
        disk["toolGrant"] != disk["accessContract"]["toolGrant"]
        or disk["cwdRule"] != disk["accessContract"]["cwdRule"]
        or disk["appliedLimits"] != _limits(judge_harness)
    ):
        raise ValueError("judge evidence limit/tool grant binding changed")
    inline_sha = _sha(request.prompt())
    raw = path.read_bytes()
    folder_sha = hashlib.sha256(raw).hexdigest()
    folder = json.loads(raw)
    if disk["inlineSha256"] != inline_sha or disk["manifestSha256"] != folder_sha:
        raise ValueError("judge evidence inline or manifest bytes changed")
    if folder != {
        "schema": "ar-judge-disk-folder/v1",
        "judgeHarness": judge_harness,
        "accessContract": disk["accessContract"],
        "appliedLimits": disk["appliedLimits"],
        "coveredWritePolicy": COVERED_WRITE_POLICY["scope"],
        "actionInlineThresholdUtf8Bytes": ACTION_INLINE_THRESHOLD_BYTES,
        "files": disk["rows"],
    }:
        raise ValueError("judge disk folder manifest differs from binding")
    _verify_embedded(request, manifest, folder)
    identity = _identity(
        inline_sha, folder_sha, disk["accessContract"], judge_harness, disk["appliedLimits"]
    )
    if disk["packageIdentity"] != identity:
        raise ValueError("judge evidence package identity changed")
    _verify_rows(root, disk["rows"])
    enforce_judge_limit(request, judge_harness=judge_harness)
    return JudgeDiskEvidence(
        root,
        path,
        folder_sha,
        tuple(disk["rows"]),
        judge_harness,
        disk["toolGrant"],
        disk["cwdRule"],
        disk["appliedLimits"],
        inline_sha,
        identity,
        disk["accessContract"],
    )


def attribute_unknowns(manifest: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Join exact reported evidence paths without changing verdicts or guessing causes."""
    files = manifest["diskEvidence"]["rows"]
    result = []
    counts = {"unknown": 0, "attributed": 0, "unattributed": 0, "unreasoned": 0}
    for index, criterion in enumerate(verdict.get("criteria", [])):
        if criterion.get("outcome") != "unknown":
            continue
        evidence = criterion.get("evidence", "")
        reason = criterion.get("reason", "")
        text = "\n".join(value for value in (evidence, reason) if isinstance(value, str))
        matches = [
            {
                "path": row["path"],
                "label": row["label"],
                "sha256": row["sha256"],
                "inline": row["inline"],
            }
            for row in files
            if any(
                re.search(
                    r"(?<![A-Za-z0-9_./-])"
                    + re.escape(path)
                    + r"(?![A-Za-z0-9_/-]|\.[A-Za-z0-9_.-])",
                    text,
                )
                for path in (row["path"], row["label"])
            )
        ]
        result.append(
            {"index": index, **criterion, "matchedFiles": matches, "attributed": bool(matches)}
        )
        counts["unknown"] += 1
        counts["attributed" if matches else "unattributed"] += 1
        counts["unreasoned"] += not text.strip()
    return {
        "schema": "ar-judge-evidence-attribution/v1",
        "counts": counts,
        "criteria": result,
        "interpretation": "Exact path mentions only; unmatched and unreasoned unknowns are retained without inferred cause.",
    }

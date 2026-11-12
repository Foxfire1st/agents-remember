"""Plan and judge retained unknown attempts without rerunning their actors."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError

from .accounting import UNKNOWN_STATUSES, Attempt, validate_attempt_sequence
from .adapters.capture import recapture_retained_codex
from .adapters.execution import ACTOR_PROFILES, LaunchProfile
from .adapters.hermes import recapture_retained_hermes
from .adapters.judges import JudgeRequest, judge_profile
from .adapters.models import Harness, NativeCapture
from .adapters.native_trials import NativeJudgeOptions, run_native_judge
from .adapters.server_calls import replay_server_placements
from .adapters.usage import native_usage
from .artifacts import (
    EvidenceFile,
    child_path,
    json_digest,
    manifest_files,
    read_object,
    sha256_file,
    write_object,
)
from .fixtures import CaseBundle
from .graders.cases import (
    RubricSubmission,
    parse_native_verdict,
    verdict_parse_provenance,
)
from .graders.models import Grade, current_grade_sources, inspect_grade_sources
from .judge_evidence import JudgePackageTooLarge, token_projection
from .judge_outcomes import grading_exception_cause, resolve_judge_outcome, unknown_verdict_reason
from .package_budget import attribute_unknowns, load_judge_disk_evidence
from .result_identity import (
    deterministic_identity,
    judged_identity,
    source_snapshot,
    validate_result_identity,
)
from .trials import (
    GradePreparationContext,
    _admitted_paths,
    prepare_case_grade,
)


@dataclass(frozen=True)
class RegradeRequest:
    evidence: Path | None = None
    batch: Path | None = None
    manifest: Path | None = None
    attempt_ids: tuple[str, ...] = ()
    execute: bool = False
    dry_run: bool = False
    tooling_check: bool = False
    timeout_seconds: float = 600
    judge_executable: Path | None = None


@dataclass(frozen=True)
class _JudgeSources:
    project_root: Path
    tool_build: str

    @property
    def paseo_home(self) -> Path:
        return self.project_root

    @property
    def settings_path(self) -> Path:
        return self.project_root / "unused-host-settings.json"

    @property
    def native_capture_roots(self) -> dict[str, Path]:
        return {
            "codex": Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            .expanduser()
            .resolve()
        }

    @property
    def host_event_paths(self) -> tuple[Path, ...]:
        return ()

    @property
    def turn_ends_path(self) -> None:
        return None

    def environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "XDG_CONFIG_HOME": str(self.project_root / "config"),
            "PASEO_HOME": str(self.project_root / "paseo"),
        }


def _new_output(path: Path, protected: Path) -> None:
    if path.resolve().is_relative_to(protected.resolve()):
        raise AuthorityError("Regrade output must be separate from its immutable input")
    path.mkdir(parents=True, exist_ok=False)


def _actor_profile(row: dict[str, Any]) -> LaunchProfile:
    model = row["originalAttempt"]["model"]
    for profile in ACTOR_PROFILES:
        if model == profile.model:
            return profile
    raise AuthorityError(f"No admitted regrade actor profile for model {model!r}")


def _retained_capture(value: dict[str, Any], actor: Harness) -> NativeCapture:
    if value.get("harness") != actor:
        raise AuthorityError("Retained capture family differs from its original actor model")
    if actor == "codex":
        return recapture_retained_codex(value)
    return replay_server_placements(recapture_retained_hermes(value))


def _git_failure_paths(status: object, causes: object) -> list[str]:
    if status not in ("wrong", "fail") or not isinstance(causes, (list, tuple)):
        return []
    prefix = "Unadmitted changed path: "
    return sorted(
        {
            cause[len(prefix) :]
            for cause in causes
            if isinstance(cause, str)
            and cause.startswith(prefix)
            and ".git" in Path(cause[len(prefix) :]).parts
        }
    )


def _git_verdicts(source: Path, attempt: Attempt, ledger: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    grade_path = source / "code-grade.json"
    if attempt.status == "wrong" and grade_path.is_file():
        grade = read_object(grade_path)
        paths = _git_failure_paths(grade.get("status"), grade.get("questions"))
        if paths:
            found.append(
                {
                    "kind": "original",
                    "verdict": ledger,
                    "codeGrade": {"path": str(grade_path), "sha256": sha256_file(grade_path)},
                    "causingPaths": paths,
                    "causes": grade["questions"],
                }
            )
    for path in sorted((source / "regrades").glob("*/regrade.json")):
        record = read_object(path)
        if record.get("originalAttemptId") != attempt.attempt_id:
            continue
        grade_path = path.parent / "code-grade.json"
        if grade_path.is_file():
            grade = read_object(grade_path)
            paths = _git_failure_paths(grade.get("status"), grade.get("questions"))
            causes = grade.get("questions", [])
        else:
            paths = (
                _git_failure_paths(record.get("status"), record.get("unknowns"))
                if record.get("judgeInvocations") == 0
                else []
            )
            causes = record.get("unknowns", [])
        if record.get("status") == "wrong" and paths:
            found.append(
                {
                    "kind": "regrade",
                    "verdict": {"path": str(path), "sha256": sha256_file(path)},
                    "causingPaths": paths,
                    "causes": causes,
                }
            )
    return found


def _semantic_unknown_cause(attempt: Attempt, source: Path) -> str | None:
    if attempt.status == "semantic_unknown":
        return attempt.unknown_cause
    for path in sorted((source / "regrades").glob("*/regrade.json")):
        record = read_object(path)
        if (
            record.get("originalAttemptId") == attempt.attempt_id
            and record.get("status") == "semantic_unknown"
        ):
            return "; ".join(str(value) for value in record.get("unknowns", []))
    return None


def _refuse_semantic_rejudge(cause: str) -> None:
    raise AuthorityError(
        "semantic_unknown: clarify the named criterion instead of rerunning a judge: " + cause
    )


def _require_answerable_plan_row(row: dict[str, Any]) -> None:
    cause = _semantic_unknown_cause(Attempt.from_dict(row["originalAttempt"]), Path(row["source"]))
    if cause is not None:
        _refuse_semantic_rejudge(cause)


def _candidate_attempts(
    batch: Path, explicitly_selected: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    ledgers = sorted(batch.glob("CE*/attempts.json"))
    if not ledgers:
        raise AuthorityError("Batch has no CE*/attempts.json ledgers")
    code_root = Path(__file__).resolve().parents[2]
    current_sources: dict[str, dict[str, Any]] = {}
    for ledger in ledgers:
        ledger_bytes = ledger.read_bytes()
        values = json.loads(ledger_bytes)["attempts"]
        ledger_digest = hashlib.sha256(ledger_bytes).hexdigest()
        attempts = [Attempt.from_dict(value) for value in values]
        validate_attempt_sequence(attempts)
        for index, (attempt, original) in enumerate(zip(attempts, values, strict=True)):
            if attempt.attempt_id in seen:
                raise AuthorityError("Duplicate original attempt identity in batch")
            seen.add(attempt.attempt_id)
            source = child_path(ledger.parent, attempt.attempt_id)
            semantic_cause = _semantic_unknown_cause(attempt, source)
            if semantic_cause is not None:
                if attempt.attempt_id in explicitly_selected:
                    _refuse_semantic_rejudge(semantic_cause)
                continue
            ledger_ref = {
                "path": str(ledger.resolve()),
                "sha256": ledger_digest,
                "jsonPointer": f"/attempts/{index}",
                "rowHash": json_digest(original),
            }
            git_verdicts = _git_verdicts(source, attempt, ledger_ref)
            grade_path = source / "code-grade.json"
            grade_sources = _inspect_code_grade(
                read_object(grade_path) if grade_path.is_file() else {},
                code_root,
                current_sources,
            )
            if (
                attempt.status not in UNKNOWN_STATUSES
                and not git_verdicts
                and grade_sources["status"] == "current"
                and attempt.attempt_id not in explicitly_selected
            ):
                continue
            old_manifest = source / "judge-evidence-manifest.json"
            manifest = read_object(old_manifest) if old_manifest.is_file() else {}
            rows.append(
                {
                    "originalAttemptId": attempt.attempt_id,
                    "originalAttempt": attempt.to_dict(),
                    "originalRowHash": json_digest(original),
                    "originalCause": attempt.unknown_cause,
                    "originalGradeSources": grade_sources,
                    "gitDependentVerdicts": git_verdicts,
                    "source": str(source.resolve()),
                    "ledger": {"path": str(ledger.resolve()), "sha256": ledger_digest},
                    "oldPackageHash": manifest.get("packagedRequestPromptSha256"),
                    "oldPackageManifest": {
                        "path": str(old_manifest),
                        "sha256": sha256_file(old_manifest),
                    }
                    if old_manifest.is_file()
                    else None,
                    "oldPackageSizes": manifest.get("sizes"),
                    "actor": False,
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            row["originalAttempt"]["case_id"],
            row["originalAttempt"]["trial_slot"],
            row["originalAttemptId"],
        ),
    )


def _copy_original(source: Path, target: Path, report: dict[str, Any]) -> CaseBundle:
    case = CaseBundle.read(source / "case")
    case.require_admitted()
    for item in (*case.files, EvidenceFile.capture(case.path, "case.json")):
        destination = child_path(target / "case", item.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item.verify(case.path), destination)
        item.verify(target / "case")
    for phase in ("before", "after"):
        for value in report[phase]:
            item = EvidenceFile(**value)
            destination = child_path(target / phase, item.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item.verify(source / phase), destination)
            item.verify(target / phase)
    return CaseBundle.read(target / "case")


def _record_supersessions(row: dict[str, Any], target: Path, code: Grade) -> None:
    prior = row.get("gitDependentVerdicts", [])
    if not prior:
        return
    if _git_failure_paths(code.status, code.questions):
        row["supersessionPending"] = (
            "Current deterministic grade still contains Git-byte admission causes"
        )
        return
    corrected = {
        "path": str(target / "code-grade.json"),
        "sha256": sha256_file(target / "code-grade.json"),
    }
    row["supersessions"] = [
        {
            "label": "superseded-false-fail",
            "priorVerdict": item["verdict"],
            "causeRecord": item.get("codeGrade", item["verdict"]),
            "causingPaths": item["causingPaths"],
            "originalCauses": item["causes"],
            "correctedRecord": corrected,
            "correctedDeterministicStatus": code.status,
            "interpretation": "The prior Git-byte admission is void; this establishes no semantic pass",
        }
        for item in prior
    ]
    write_object(
        target / "supersessions.json",
        {
            "schema": "ar-curator-verdict-supersessions/v1",
            "correctedRecord": corrected,
            "correctedDeterministicStatus": code.status,
            "supersededVerdicts": row["supersessions"],
            "semanticVerdict": "not established by this deterministic correction",
        },
    )


def _prepare(row: dict[str, Any], output: Path, grader_source: dict[str, Any]) -> dict[str, Any]:
    target = child_path(output / "prepared", row["originalAttemptId"])
    target.mkdir(parents=True, exist_ok=False)
    source = Path(row["source"])
    report_file = source / "attempt.json"
    row["originalReport"] = {"path": str(report_file), "sha256": sha256_file(report_file)}
    report = read_object(report_file)
    if (
        report.get("identity") != row["originalAttemptId"]
        or report.get("case") != row["originalAttempt"]["case_id"]
    ):
        raise AuthorityError("Original report identity does not match its selected ledger row")
    case = _copy_original(source, target, report)
    binding_file = EvidenceFile.capture(source, "native-binding.json")
    binding = read_object(binding_file.verify(source))
    row["nativeBinding"] = {"path": str(source / binding_file.path), "sha256": binding_file.sha256}
    shutil.copyfile(binding_file.verify(source), target / "native-binding.json")
    binding_file.verify(target)
    row["toolBuild"] = binding["toolBuild"]
    capture = _retained_capture(report["capture"], _actor_profile(row).harness)
    write_object(target / "actor-capture.json", capture.to_dict())
    report.update(
        capture=capture.to_dict(), unknowns=list(capture.unknowns), status="captured-ungraded"
    )
    write_object(target / "attempt-input.json", report)
    fixture_root = str(Path(binding["projectRoot"]).parent)

    def neutralize(text: str) -> str:
        return (
            text.replace(fixture_root, "[fixture]")
            .replace(row["originalAttemptId"], "[run]")
            .replace(case.case_id, "[case]")
        )

    row["executionPhase"] = report["executionPhase"] = "grading"
    report.pop("unknown_class", None)
    report.pop("unknown_cause", None)
    prepared = prepare_case_grade(
        case,
        report,
        target,
        capture,
        context=GradePreparationContext(neutralize, profile=_actor_profile(row)),
    )
    reasons: tuple[str, ...] = ()
    if prepared.code.status in ("fail", "unknown"):
        field = "questions" if prepared.code.questions else "evidence"
        reasons = prepared.code.questions or prepared.code.evidence
        row["preparationCauseProvenance"] = {
            "path": str(target / "code-grade.json"),
            "sha256": sha256_file(target / "code-grade.json"),
            "field": field,
        }
    row.update(
        prepared=str(target.relative_to(output)),
        ready=prepared.request is not None,
        codeStatus=prepared.code.status,
        codeGraderId=prepared.code.grader_id,
        preparationCauses=report["unknowns"] + list(reasons),
        **{key: report[key] for key in ("unknown_class", "unknown_cause") if key in report},
    )
    if prepared.request is not None:
        package = read_object(target / "judge-evidence-manifest.json")
        disk = load_judge_disk_evidence(
            target, prepared.request, judge_profile(_actor_profile(row)).harness
        )
        row.update(
            newPackageHash=package["packagedRequestPromptSha256"],
            newPackageIdentity=disk.package_identity,
            newPackageSizes=package["sizes"],
        )
    else:
        row.update(newPackageHash=None, newPackageIdentity=None, newPackageSizes=None)
    _set_result_identity(row, target, grader_source, case, report)
    _record_supersessions(row, target, prepared.code)
    row["files"] = [item.to_dict() for item in manifest_files(target)]
    return row


def _set_result_identity(
    row: dict[str, Any],
    target: Path,
    grader_source: dict[str, Any],
    case: CaseBundle,
    report: dict[str, Any],
) -> None:
    output = target.parent.parent
    effects = {
        phase: sorted(report[phase], key=lambda item: (item["path"], item["sha256"]))
        for phase in ("before", "after")
    }
    admission = {
        "caseManifest": case.manifest,
        "admittedPaths": sorted(_admitted_paths(case, report)),
        "helperWindowComplete": False,
        "attemptConfiguration": {
            key: value for key, value in report.items() if key not in ("capture", "before", "after")
        },
    }
    inputs = {
        "graderId": "prepare_case_grade/" + row["codeGraderId"],
        "graderSource": {
            "path": str(output / "grader-source.json"),
            "sha256": sha256_file(output / "grader-source.json"),
        },
        "capture": {
            "path": str(target / "actor-capture.json"),
            "sha256": sha256_file(target / "actor-capture.json"),
        },
        "effects": effects,
        "admission": admission,
    }
    write_object(target / "result-identity-inputs.json", inputs)
    row["resultIdentityInputs"] = {
        "path": str(target / "result-identity-inputs.json"),
        "sha256": sha256_file(target / "result-identity-inputs.json"),
    }
    row["preparationSource"] = {
        "schema": "ar-regrade-preparation-source/v1",
        "sha256": grader_source["sha256"],
        "snapshot": inputs["graderSource"],
    }
    row["resultIdentity"] = (
        judged_identity(row["newPackageIdentity"], judge_profile(_actor_profile(row)).model)
        if row["ready"]
        else deterministic_identity(
            grader_id=inputs["graderId"],
            source=grader_source,
            capture=report["capture"],
            effects=effects,
            admission=admission,
        )
    )


def _census_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "originalAttemptId",
            "originalAttempt",
            "originalRowHash",
            "originalCause",
            "originalGradeSources",
            "preparationSource",
            "oldPackageHash",
            "oldPackageSizes",
            "newPackageHash",
            "newPackageSizes",
            "ready",
            "preparationCauses",
            "executionPhase",
            "unknown_class",
            "unknown_cause",
            "preparationCauseProvenance",
            "resultIdentity",
            "resultIdentityStatus",
            "resultIdentityError",
            "existingRegrades",
            "wouldRunByMode",
            "wouldJudgeByMode",
            "gitDependentVerdicts",
            "supersessions",
            "supersessionPending",
        )
        if key in row
    }


def plan_regrades(request: RegradeRequest) -> Path:
    if (
        request.evidence is None
        or request.batch is None
        or request.manifest is not None
        or request.execute
        or request.dry_run
    ):
        raise AuthorityError("Planning requires --batch and no execute/plan argument")
    executable = request.judge_executable
    if executable is None or not executable.is_absolute() or not executable.is_file():
        raise AuthorityError("Census needs an explicit absolute native judge executable")
    wanted = set(request.attempt_ids)
    rows = _candidate_attempts(request.batch, frozenset(wanted))
    if len(wanted) != len(request.attempt_ids) or not wanted <= {
        row["originalAttemptId"] for row in rows
    }:
        raise AuthorityError("Plan selection has repeated or unavailable eligible actor attempts")
    excluded = [row for row in rows if wanted and row["originalAttemptId"] not in wanted]
    rows = [row for row in rows if not wanted or row["originalAttemptId"] in wanted]
    _new_output(request.evidence, request.batch)
    grader_source = source_snapshot(Path(__file__).resolve().parents[2])
    write_object(request.evidence / "grader-source.json", grader_source)
    print(
        json.dumps(
            {
                "stage": "regrade-census-before-preparation",
                "attempts": [_census_row(row) for row in rows],
            }
        ),
        flush=True,
    )
    for row in rows:
        try:
            _prepare(row, request.evidence, grader_source)
        except (OSError, ValueError, KeyError, AuthorityError) as error:
            row.update(ready=False, preparationCauses=[grading_exception_cause(row, error)])
        _prior_summary(row)
    plan = {
        "schema": "ar-curator-regrade-plan/v1",
        "batch": str(request.batch.resolve()),
        "attempts": rows,
        "gitDependentFailCounts": {
            "attempts": sum(bool(row["gitDependentVerdicts"]) for row in rows),
            "originalVerdicts": sum(
                item["kind"] == "original" for row in rows for item in row["gitDependentVerdicts"]
            ),
            "regradeVerdicts": sum(
                item["kind"] == "regrade" for row in rows for item in row["gitDependentVerdicts"]
            ),
        },
        "excludedUnknownAttempts": excluded,
        "selectionPolicy": "Frozen command-start identities only; later arrivals require a future pass",
        "actor": False,
        "judgeInvocations": 0,
        "judgeExecutable": {"path": str(executable), "sha256": sha256_file(executable)},
        "graderSource": {
            "path": "grader-source.json",
            "sha256": sha256_file(request.evidence / "grader-source.json"),
        },
        "projectedTokens": sum(
            row.get("newPackageSizes", {}).get("estimatedTokens", 0)
            for row in rows
            if row.get("newPackageSizes")
        ),
        "modeProjection": _mode_projection(rows),
        "projectedTokensUnavailableForAttempts": [
            row["originalAttemptId"] for row in rows if not row.get("newPackageSizes")
        ],
        "tokenInterpretation": "Projection only; actual native usage is recorded when explicitly selected judges run",
    }
    path = request.evidence / "manifest.json"
    write_object(path, plan)
    print(
        json.dumps(
            {
                "stage": "regrade-plan-before-spending",
                "plan": str(path),
                "attempts": [_census_row(row) for row in rows],
                "projectedTokens": plan["projectedTokens"],
                "modeProjection": plan["modeProjection"],
                "projectedTokensUnavailableForAttempts": plan[
                    "projectedTokensUnavailableForAttempts"
                ],
            }
        ),
        flush=True,
    )
    return path


def _verify_original(row: dict[str, Any]) -> None:
    for key in ("originalReport", "nativeBinding", "oldPackageManifest"):
        reference = row.get(key)
        if reference is not None and sha256_file(Path(reference["path"])) != reference["sha256"]:
            raise AuthorityError(f"Selected source evidence changed: {key}")
    for item in row.get("gitDependentVerdicts", []):
        ref = item["verdict"]
        if item["kind"] == "regrade" and sha256_file(Path(ref["path"])) != ref["sha256"]:
            raise AuthorityError("Superseded verdict evidence changed")
        grade_ref = item.get("codeGrade")
        if grade_ref is not None and sha256_file(Path(grade_ref["path"])) != grade_ref["sha256"]:
            raise AuthorityError("Original Git-dependent code grade changed")
    originals = [
        value
        for value in read_object(Path(row["ledger"]["path"]))["attempts"]
        if value.get("attempt_id") == row["originalAttemptId"]
    ]
    if len(originals) != 1 or json_digest(originals[0]) != row["originalRowHash"]:
        raise AuthorityError(
            "Selected original attempt row changed; ledger appends alone are permitted"
        )


def _selected_plan(request: RegradeRequest) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if (
        request.manifest is None
        or request.batch is not None
        or request.evidence is not None
        or request.execute == request.dry_run
        or request.attempt_ids
    ):
        raise AuthorityError(
            "Regrade requires --manifest and exactly one of --execute or --dry-run; selection is frozen in that manifest"
        )
    plan = read_object(request.manifest)
    if plan.get("schema") != "ar-curator-regrade-plan/v1" or plan.get("actor") is not False:
        raise AuthorityError("Execution needs a retained no-actor regrade plan")
    rows = {row["originalAttemptId"]: row for row in plan["attempts"]}
    if len(rows) != len(plan["attempts"]):
        raise AuthorityError("Selected attempts are absent or ambiguous in the retained plan")
    selected = list(rows.values())
    code_root = Path(__file__).resolve().parents[2]
    current_sources: dict[str, dict[str, Any]] = {}
    for row in selected:
        _require_answerable_plan_row(row)
        identity = validate_result_identity(row.get("resultIdentity"))
        if (
            identity["kind"] == "judged"
            and identity["components"]["judgeModel"] != judge_profile(_actor_profile(row)).model
        ):
            raise AuthorityError("Frozen result identity names a different intended judge route")
        _verify_original(row)
        _prior_summary(row)
        source = child_path(request.manifest.parent, row["prepared"])
        for value in row["files"]:
            EvidenceFile(**value).verify(source)
        _require_current_grade(source, code_root, current_sources)
        _verify_result_inputs(row, identity)
        if not row.get("ready"):
            continue
        payload = read_object(source / "judge-request.json")
        judge_request = JudgeRequest(
            tuple(payload["rubric"]),
            tuple(payload["observations"]),
            tuple(payload["artifact_evidence"]),
            task=payload["task"],
        )
        if hashlib.sha256(judge_request.prompt().encode()).hexdigest() != row["newPackageHash"]:
            raise AuthorityError("Planned judge package hash changed")
        disk = load_judge_disk_evidence(
            source, judge_request, judge_profile(_actor_profile(row)).harness
        )
        if (
            row["newPackageIdentity"] != disk.package_identity
            or identity["kind"] != "judged"
            or identity["components"]["packageSha256"] != disk.package_identity
        ):
            raise AuthorityError("Judge request and result identity name different packages")
    return plan, selected


def _inspect_code_grade(
    record: dict[str, Any], code_root: Path, sources: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    # prepare_case_grade may replace the case grade with a helper-window result.
    producer = (
        "scripts/agent_evals/graders/helpers.py"
        if record.get("grader_id") == "HW1"
        else "scripts/agent_evals/graders/cases.py"
    )
    if producer not in sources:
        sources[producer] = current_grade_sources(producer, code_root=code_root)
    return inspect_grade_sources(
        record,
        code_root=code_root,
        expected_producer=producer,
        current_sources=sources[producer],
    )


def _require_current_grade(
    source: Path, code_root: Path, current_sources: dict[str, dict[str, Any]]
) -> None:
    status = _inspect_code_grade(
        read_object(source / "code-grade.json"),
        code_root,
        current_sources,
    )
    if status["status"] != "current":
        raise AuthorityError(
            "Frozen code grade is stale; create a fresh census: " + "; ".join(status["reasons"])
        )


def _verify_result_inputs(row: dict[str, Any], identity: dict[str, Any]) -> None:
    ref = row["resultIdentityInputs"]
    if sha256_file(Path(ref["path"])) != ref["sha256"]:
        raise AuthorityError("Result identity input evidence changed")
    inputs = read_object(Path(ref["path"]))
    for key in ("graderSource", "capture"):
        item = inputs[key]
        if sha256_file(Path(item["path"])) != item["sha256"]:
            raise AuthorityError(f"Result identity {key} evidence changed")
    preparation = _preparation_source(row)
    if (
        preparation["snapshot"] != inputs["graderSource"]
        or preparation["sha256"] != read_object(Path(inputs["graderSource"]["path"]))["sha256"]
    ):
        raise AuthorityError("Preparation source differs from its frozen grader snapshot")
    if identity["kind"] == "deterministic":
        recomputed = deterministic_identity(
            grader_id=inputs["graderId"],
            source=read_object(Path(inputs["graderSource"]["path"])),
            capture=read_object(Path(inputs["capture"]["path"])),
            effects=inputs["effects"],
            admission=inputs["admission"],
        )
        if recomputed != identity:
            raise AuthorityError(
                "Deterministic identity does not match its recorded input evidence"
            )


def _rebind_judge_folder(original: Path, target: Path) -> None:
    # Only local folder addresses change when a frozen preparation is copied.
    # The prompt, folder bytes and result/package identities remain exact.
    local_manifest = read_object(target / "judge-evidence-manifest.json")
    prepared_root = str(original.resolve())
    for binding in (
        local_manifest["diskEvidence"],
        local_manifest["packageBudget"]["diskEvidence"],
    ):
        if binding["root"] != str(original / "rubric-judge/evidence") or binding[
            "manifestPath"
        ] != str(original / "rubric-judge/evidence/manifest.json"):
            raise AuthorityError("Prepared judge folder address differs from the verified copy")
        binding.update(
            root=str(target / "rubric-judge/evidence"),
            manifestPath=str(target / "rubric-judge/evidence/manifest.json"),
        )
    remaining = json.dumps(local_manifest).count(prepared_root)
    if remaining:
        raise AuthorityError(f"Prepared judge folder address remains in {remaining} fields")
    write_object(target / "judge-evidence-manifest.json", local_manifest)
    write_object(
        target / "judge-local-binding.json",
        {
            "preparedManifestSha256": sha256_file(original / "judge-evidence-manifest.json"),
            "boundManifestSha256": sha256_file(target / "judge-evidence-manifest.json"),
            "remainingPreparedRootOccurrences": remaining,
            "packageIdentity": local_manifest["diskEvidence"]["packageIdentity"],
        },
    )


def _judge(
    row: dict[str, Any], request: RegradeRequest, plan_reference: dict[str, str], executable: Path
) -> dict[str, Any]:
    assert request.manifest is not None
    result_identity = validate_result_identity(row.get("resultIdentity"))
    if (
        result_identity["kind"] == "judged"
        and result_identity["components"]["judgeModel"] != judge_profile(_actor_profile(row)).model
    ):
        raise AuthorityError("Frozen result identity names a different intended judge route")
    identity = json_digest(
        {
            "originalAttemptId": row["originalAttemptId"],
            "resultIdentityDigest": result_identity["digest"],
            "preparationSourceSha256": _preparation_source(row)["sha256"],
            "tooling_check": request.tooling_check,
        }
    )
    target = Path(row["source"]) / "regrades" / identity
    record = {
        "schema": "ar-curator-regrade/v1",
        "regradeId": str(uuid.uuid4()),
        "originalAttemptId": row["originalAttemptId"],
        "originalCause": row["originalCause"],
        "originalAttempt": row["originalAttempt"],
        "originalReport": row.get("originalReport"),
        "originalRowHash": row["originalRowHash"],
        "oldPackageHash": row["oldPackageHash"],
        "resultIdentity": result_identity,
        "preparationSource": _preparation_source(row),
        "plan": plan_reference,
        "actor": False,
        "measurement": not request.tooling_check,
        "tooling_check": request.tooling_check,
        "output": str(target),
        "judgeInvocations": 0,
        "status": "wrong" if row.get("codeStatus") == "fail" else "missing_capture",
        "unknowns": row.get("preparationCauses", []),
        "supersedes": row.get("supersessions", []),
        **{
            key: row[key]
            for key in ("executionPhase", "unknown_class", "unknown_cause")
            if key in row
        },
    }
    if result_identity["kind"] == "judged":
        record["newPackageHash"] = result_identity["components"]["packageSha256"]
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return {
            **record,
            "status": "missing_capture",
            "unknowns": [
                "The exclusive attempt/package/mode claim already exists; no second judge was invoked"
            ],
            "skipMode": "tooling_check" if request.tooling_check else "measurement",
            "stalled": True,
        }
    if not row.get("ready"):
        write_object(target / "regrade.json", record)
        _append_supersession_record(row, target)
        return record
    original = child_path(request.manifest.parent, row["prepared"])
    for value in row["files"]:
        item = EvidenceFile(**value)
        destination = child_path(target, item.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item.verify(original), destination)
        item.verify(target)
    _rebind_judge_folder(original, target)
    payload = read_object(target / "judge-request.json")
    judge_request = JudgeRequest(
        tuple(payload["rubric"]),
        tuple(payload["observations"]),
        tuple(payload["artifact_evidence"]),
        task=payload["task"],
    )
    write_object(target / "pending.json", {**record, "status": "pending", "judgeInvocations": None})
    try:
        record["executionPhase"] = "grading"
        record["judgeInvocations"] = 1
        captured = run_native_judge(
            _JudgeSources(target, row["toolBuild"]),
            _actor_profile(row),
            judge_request,
            target,
            NativeJudgeOptions(request.timeout_seconds, executable),
        )
        write_object(target / "judge-capture.json", captured.to_dict())
        usage = native_usage(captured)
        write_object(target / "judge-usage.json", usage)
        verdict, parse_path = parse_native_verdict(captured)
        write_object(
            target / "judge-evidence-attribution.json",
            {
                **attribute_unknowns(read_object(target / "judge-evidence-manifest.json"), verdict),
                "trialId": row["originalAttemptId"],
                "regradeId": record["regradeId"],
                "verdictParsePath": parse_path,
                "verdictParseProvenance": verdict_parse_provenance(captured),
            },
        )
        code = Grade(**read_object(target / "code-grade.json"))
        outcome = resolve_judge_outcome(
            code, RubricSubmission(judge_request, row["originalAttempt"]["model"], captured)
        )
        record.update(status=outcome, judgeUsage=usage, unknowns=list(captured.unknowns))
        if outcome in ("missing_capture", "semantic_unknown") and not captured.unknowns:
            record["unknowns"] = [unknown_verdict_reason(captured)]
    except JudgePackageTooLarge as error:
        record.update(judgeInvocations=0, unknownClass="schema-incompatible")
        record["unknowns"] = [error.cause]
        write_object(target / "judge-send-guard.json", error.manifest)
    except (OSError, ValueError, KeyError, AuthorityError) as error:
        record["unknowns"] = [grading_exception_cause(record, error)]
    write_object(target / "regrade.json", record)
    _append_supersession_record(row, target)
    return record


def _append_supersession_record(row: dict[str, Any], target: Path) -> None:
    if not row.get("supersessions"):
        return
    corrected = target / "regrade.json"
    write_object(
        target / "verdict-supersessions.json",
        {
            "schema": "ar-curator-verdict-supersessions/v1",
            "correctedRecord": {"path": str(corrected), "sha256": sha256_file(corrected)},
            "supersededVerdicts": row["supersessions"],
            "interpretation": "The original false Git-byte admission remains retained; read the corrected result first",
        },
    )


def execute_regrades(request: RegradeRequest) -> Path:
    started_ns = time.time_ns()
    plan, selected = _selected_plan(request)
    assert request.manifest is not None
    plan_reference = {
        "path": str(request.manifest.resolve()),
        "sha256": sha256_file(request.manifest),
    }
    executable = Path(plan["judgeExecutable"]["path"])
    if not executable.is_absolute() or sha256_file(executable) != plan["judgeExecutable"]["sha256"]:
        raise AuthorityError("Frozen native judge executable changed")
    print(
        json.dumps(
            {
                "stage": "selected-regrades-before-spending",
                "attempts": [_census_row(row) for row in selected],
            }
        ),
        flush=True,
    )
    records = []
    output = request.manifest.parent / "executions" / (str(uuid.uuid4()) + ".json")
    write_object(
        output,
        {
            "schema": "ar-curator-regrades/v1",
            "regrades": [],
            "actor": False,
            "measurement": not request.tooling_check,
            "exitCode": 0,
        },
    )
    for row in selected:
        previous = _previous_regrade(row, request.tooling_check, started_ns)
        record = (
            previous if previous is not None else _judge(row, request, plan_reference, executable)
        )
        records.append(record)
        write_object(
            output,
            {
                "schema": "ar-curator-regrades/v1",
                "regrades": records,
                "actor": False,
                "measurement": not request.tooling_check,
                "exitCode": int(
                    any(record["status"] not in ("right", "wrong") for record in records)
                ),
            },
        )
    print(
        json.dumps(
            {
                "stage": "regrade-complete",
                "summary": str(output),
                "exitCode": int(
                    any(record["status"] not in ("right", "wrong") for record in records)
                ),
            }
        ),
        flush=True,
    )
    return output


def _previous_regrade(
    row: dict[str, Any], tooling_check: bool, started_ns: int
) -> dict[str, Any] | None:
    matches = []
    for path in sorted((Path(row["source"]) / "regrades").glob("*/regrade.json")):
        record = read_object(path)
        if _same_regrade(record, row, tooling_check) and not _superseded_path(row, path):
            matches.append(record)
    if len(matches) > 1:
        raise AuthorityError(
            "Multiple completed regrades exist for the same frozen row and manifest"
        )
    if matches:
        return {
            **matches[0],
            "skippedAlreadyRegraded": True,
            "skipMode": "tooling_check" if tooling_check else "measurement",
        }
    for path in sorted((Path(row["source"]) / "regrades").glob("*/pending.json")):
        record = read_object(path)
        if _same_regrade(record, row, tooling_check):
            return {
                **record,
                "status": "missing_capture",
                "unknowns": [
                    "A previous regrade has no final record; reconcile it before any further judge invocation"
                ],
                "skippedAlreadyRegraded": True,
                "skipMode": "tooling_check" if tooling_check else "measurement",
                "stalled": path.stat().st_mtime_ns <= started_ns,
                "pendingRecord": {"path": str(path), "sha256": sha256_file(path)},
            }
    return None


def _same_regrade(record: dict, row: dict, tooling_check: bool) -> bool:
    current = validate_result_identity(row.get("resultIdentity"))
    try:
        previous = validate_result_identity(record.get("resultIdentity"))
        previous_source = _preparation_source(record)
    except AuthorityError:
        return False
    return (
        record.get("originalAttemptId") == row["originalAttemptId"]
        and previous == current
        and previous_source["sha256"] == _preparation_source(row)["sha256"]
        and record.get("tooling_check") == tooling_check
    )


def _preparation_source(value: dict[str, Any]) -> dict[str, Any]:
    source = value.get("preparationSource")
    if (
        not isinstance(source, dict)
        or source.get("schema") != "ar-regrade-preparation-source/v1"
        or not isinstance(source.get("sha256"), str)
        or len(source["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in source["sha256"])
        or not isinstance(source.get("snapshot"), dict)
        or not isinstance(source["snapshot"].get("path"), str)
        or not source["snapshot"]["path"]
        or not isinstance(source["snapshot"].get("sha256"), str)
        or len(source["snapshot"]["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in source["snapshot"]["sha256"])
    ):
        raise AuthorityError("Missing or invalid preparation source lineage")
    return source


def _superseded_path(row: dict[str, Any], path: Path) -> bool:
    return any(Path(item["priorVerdict"]["path"]) == path for item in row.get("supersessions", []))


def _prior_summary(row: dict[str, Any]) -> None:
    if row.get("newPackageSizes"):
        row["newPackageSizes"] = {
            **row["newPackageSizes"],
            **token_projection(row["newPackageSizes"]["packagedPromptUtf8Bytes"]),
        }
    try:
        identity = validate_result_identity(row.get("resultIdentity"))
        preparation = _preparation_source(row)
        row["resultIdentityStatus"] = "valid"
        current_digest = identity["digest"]
    except AuthorityError as error:
        current_digest = None
        row.update(resultIdentityStatus="invalid", resultIdentityError=str(error))
    records = []
    for directory in sorted((Path(row["source"]) / "regrades").glob("*")):
        path = directory / "regrade.json"
        if not path.is_file():
            path = directory / "pending.json"
        if not path.is_file():
            continue
        record = read_object(path)
        try:
            previous = validate_result_identity(record.get("resultIdentity"))
            previous_source = _preparation_source(record)
            identity_info = {
                "identityStatus": "valid",
                "identityDigest": previous["digest"],
                "preparationSourceSha256": previous_source["sha256"],
            }
        except AuthorityError as error:
            identity_info = {"identityStatus": "invalid", "identityError": str(error)}
        records.append(
            {
                **identity_info,
                "mode": "tooling_check" if record.get("tooling_check") else "measurement",
                "status": "superseded-false-fail"
                if _superseded_path(row, path)
                else ("pending" if path.name == "pending.json" else record.get("status")),
                "storedStatus": record.get("status"),
                "packageHash": record.get("newPackageHash"),
                "originalRowHash": record.get("originalRowHash"),
                "record": str(path),
                "sha256": sha256_file(path),
            }
        )
    row["existingRegrades"] = records
    row["wouldRunByMode"] = {
        mode: current_digest is not None
        and not any(
            record["mode"] == mode
            and record.get("identityDigest") == current_digest
            and record.get("preparationSourceSha256") == preparation["sha256"]
            and record["status"] != "superseded-false-fail"
            for record in records
        )
        for mode in ("tooling_check", "measurement")
    }
    row["wouldJudgeByMode"] = {
        mode: bool(row.get("ready")) and needed for mode, needed in row["wouldRunByMode"].items()
    }


def _mode_projection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for mode in ("tooling_check", "measurement"):
        fresh = previous = judged = deterministic = unresolved = skipped = stalled = 0
        for row in rows:
            if row.get("resultIdentityStatus") == "invalid":
                unresolved += 1
                continue
            if not row["wouldRunByMode"][mode]:
                if any(
                    record["mode"] == mode
                    and record.get("identityDigest") == row["resultIdentity"]["digest"]
                    and record.get("preparationSourceSha256") == row["preparationSource"]["sha256"]
                    and record["status"] == "pending"
                    for record in row["existingRegrades"]
                ):
                    stalled += 1
                else:
                    skipped += 1
                continue
            if not row["wouldJudgeByMode"][mode]:
                if row.get("codeStatus") == "fail":
                    deterministic += 1
                else:
                    unresolved += 1
                continue
            judged += 1
            tokens = row["newPackageSizes"]["estimatedTokens"]
            seen_tooling = any(
                record["mode"] == "tooling_check"
                and record.get("identityDigest") == row["resultIdentity"]["digest"]
                and record.get("preparationSourceSha256") == row["preparationSource"]["sha256"]
                and record["status"] not in ("pending", "superseded-false-fail")
                for record in row["existingRegrades"]
            )
            if seen_tooling:
                previous += tokens
            else:
                fresh += tokens
        result[mode] = {
            "judgeInvocationsThisRun": 0,
            "projectedJudgeInvocations": judged,
            "projectedDeterministicResults": deterministic,
            "projectedUnknownResults": unresolved,
            "skippedRecords": skipped,
            "stalledRecords": stalled,
            "freshEstimatedTokens": fresh,
            "previouslyToolingCheckedEstimatedTokens": previous,
            "totalEstimatedTokens": fresh + previous,
        }
    return result


def run_regrades(request: RegradeRequest) -> Path:
    if request.dry_run:
        _plan, rows = _selected_plan(request)
        for row in rows:
            _prior_summary(row)
        print(
            json.dumps(
                {
                    "stage": "regrade-dry-run",
                    "manifest": str(request.manifest),
                    "attempts": [_census_row(row) for row in rows],
                    "modeProjection": _mode_projection(rows),
                    "judgeInvocationsThisRun": 0,
                }
            ),
            flush=True,
        )
        assert request.manifest is not None
        return request.manifest
    return execute_regrades(request) if request.execute else plan_regrades(request)

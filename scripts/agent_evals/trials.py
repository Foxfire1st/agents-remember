"""Concrete v8 trial command orchestration; native evidence determines each outcome."""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from pathlib import Path

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.errors import AuthorityError

from .accounting import (
    HELPER_DEADLINE_STATE,
    Attempt,
    final_full_repeat_allowed,
    validate_attempt_sequence,
)
from .adapters.detail_reads import observe_detail_reads
from .adapters.execution import HERMES_GPT_ACTOR, LaunchProfile, NativeBinding, actor_profile
from .adapters.helper_wait import helper_wait_snapshot
from .adapters.judges import JudgeRequest, judge_profile
from .adapters.models import NativeCapture
from .adapters.native_trials import (
    NativeJudgeOptions,
    capture_trial_actor,
    host_window,
    run_native_judge,
)
from .adapters.paseo import ActorDeadlineError, PaseoConnection, PaseoNativeAdapter, PaseoRun
from .adapters.text_delivery import (
    PointerDelivery,
    TextDeliveryMarker,
    bind_text_details,
    observe_text_delivery,
    prepare_text_delivery,
    validate_detail_links,
)
from .adapters.usage import native_usage
from .artifacts import (
    EvidenceFile,
    child_path,
    json_digest,
    read_object,
    write_object,
)
from .auth_observation import AuthObservation, AuthObservationError
from .codex_access import AccessOnlyError
from .evidence_selection import cited_inputs, select_case_evidence
from .fixtures import CASE_IDS, CaseBundle
from .graders.cases import (
    CaseEvidence,
    RubricSubmission,
    case_judge_request,
    grade_case_code,
    parse_native_verdict,
    verdict_parse_provenance,
)
from .graders.helpers import HelperObservation, _declaration, grade_helpers
from .graders.models import Grade
from .judge_case_inputs import materialize_judge_inputs
from .judge_evidence import JudgePackageInputs, JudgePackageTooLarge, package_judge_evidence
from .judge_outcomes import grading_exception_cause, resolve_judge_outcome, unknown_verdict_reason
from .package_budget import attribute_unknowns
from .preflight import NativeIntakeBinding, preflight_project, write_preflight
from .runtime_evidence import collect_runtime_evidence
from .text import capture_curator_text
from .trial_credentials import actor_access_deadline, declare_credentials, record_auth_comparison
from .trial_effects import save_effects
from .trial_isolation import (
    purge_hermes_credentials,
    source_hermes_home,
)
from .trial_setup import BatchHost, ProvisionedTrial, TrialLaunch, provision_batch, provision_trial
from .trial_skill_policy import check_skill_policy_integrity, skill_policy_snapshot


@dataclass(frozen=True)
class TrialBatchRequest:
    cases: Path
    case_ids: tuple[str, ...]
    model: str
    text: str | Path
    trials: int
    evidence: Path
    sandbox_home: Path
    candidate_code_root: Path
    approver_ready: bool = False
    timeout_seconds: float = 3600
    phase: str = "measurement"
    actor_only: bool = False
    codex_access: Path | None = None


@dataclass(frozen=True)
class _TrialContext:
    request: TrialBatchRequest
    case: CaseBundle
    profile: LaunchProfile
    instructions: str
    text_artifact: dict
    signature: str
    batch: str
    slot: int
    previous: Attempt | None


@dataclass(frozen=True)
class _TrialRuntime:
    context: _TrialContext
    provisioned: ProvisionedTrial
    evidence: Path
    report: dict


def load_trial_text(code: Path, selected: str | Path) -> tuple[str, str, dict]:
    """Old is compiled unchanged; explicit files are either captured JSON or plain text."""
    if selected == "old":
        artifact = capture_curator_text(code)
    else:
        path = Path(selected)
        if path.suffix != ".json":
            instructions = path.read_text(encoding="utf-8")
            if not instructions.strip():
                raise AuthorityError("selected instruction file is empty")
            artifact = {"source": str(path.resolve()), "instructions": instructions}
            return instructions, json_digest(artifact), artifact
        artifact = read_object(path)
    entry = artifact["entries"]["curation"]
    instructions = entry["instructions"]
    details = entry["details"]
    if not isinstance(instructions, str) or not isinstance(details, list):
        raise AuthorityError("captured Curator text requires instructions and delivered details")
    return instructions, json_digest(artifact), artifact


def _ledger(path: Path) -> list[Attempt]:
    attempts = (
        [Attempt.from_dict(item) for item in read_object(path)["attempts"]] if path.exists() else []
    )
    validate_attempt_sequence(attempts)
    return attempts


def _batch(attempts: list[Attempt], profile: LaunchProfile, signature: str, phase: str) -> str:
    batches = {
        item.batch_id
        for item in attempts
        if item.model == profile.model
        and item.text_signature == signature
        and item.batch_kind == phase
    }
    if len(batches) > 1:
        raise AuthorityError(
            "text/model already has multiple batches; use its original attempt ledger"
        )
    return next(iter(batches)) if batches else str(uuid.uuid4())


def _previous(attempts: list[Attempt], batch: str, case: str, slot: int) -> Attempt | None:
    matching = [
        item
        for item in attempts
        if item.batch_id == batch and item.case_id == case and item.trial_slot == slot
    ]
    return matching[-1] if matching else None


def _admitted_paths(case: CaseBundle, report: dict) -> frozenset[str]:
    declared = case.manifest["allowedEffects"]
    if isinstance(declared, dict):
        if "writeRoots" in declared:
            declared = declared["writeRoots"]
        elif "writes" in declared:
            declared = declared["writes"]
        else:
            raise AuthorityError("case allowedEffects declares no writeRoots or writes")
    if not isinstance(declared, list):
        raise AuthorityError("case allowedEffects writes must be an explicit list")
    roots = [item["path"] if isinstance(item, dict) else item for item in declared]
    if not all(isinstance(item, str) and item for item in roots):
        raise AuthorityError("case allowedEffects contains an invalid path")
    paths = {item["path"] for phase in ("before", "after") for item in report[phase]}
    return frozenset(path for path in paths if any(_write_allowed(path, root) for root in roots))


def _write_allowed(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        root = pattern.removesuffix("/**")
        return path == root or path.startswith(root + "/")
    if any(character in pattern for character in "*?["):
        return fnmatchcase(path, pattern)
    return path == pattern or path.startswith(pattern.rstrip("/") + "/")


def _rubric_artifacts(case: CaseBundle, report: dict, evidence: Path) -> tuple[EvidenceFile, ...]:
    """Actual source and before/after changes, with no expected-outcome file or text label."""
    before = {item["path"]: item["sha256"] for item in report["before"]}
    after = {item["path"]: item["sha256"] for item in report["after"]}
    selected = []
    authority_sources = {case.manifest["setup"][name] for name in ("prompt", "authority", "queue")}
    authority_inputs = {
        item["destination"]
        for item in case.manifest["actorInputs"]
        if item["source"] in authority_sources
    }
    for path in sorted(before.keys() | after.keys()):
        if ".git" in Path(path).parts:
            continue
        changed = before.get(path) != after.get(path)
        for phase in ("before", "after"):
            if path not in (before if phase == "before" else after):
                continue
            source = phase == "before" and (
                path.startswith("projects/code/") or path in authority_inputs
            )
            preserved = (
                phase == "after"
                and path.startswith("projects/memory/")
                and not path.startswith("projects/memory/knowledge/history/")
            )
            if changed or source or preserved:
                selected.append(EvidenceFile.capture(evidence, phase + "/" + path))
    # Cited inputs are original case evidence even when no effect inventory row exists.
    inputs = {item["destination"]: item["source"] for item in case.manifest["actorInputs"]}
    sources = {item.path: item for item in case.files}
    selected_paths = {item.path for item in selected}
    for path in sorted({row["path"] for row in cited_inputs(case)}):
        source = sources.get(inputs[path])
        if source is None:
            raise AuthorityError(f"cited input is not a hash-bound case file: {path}")
        original = source.verify(case.path)
        relative = "before/" + path
        target = child_path(evidence, relative)
        if target.exists():
            EvidenceFile(relative, source.sha256).verify(evidence)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        if relative not in selected_paths:
            selected.append(EvidenceFile(relative, source.sha256))
    return (*selected, *materialize_judge_inputs(case, evidence))


def _observations(capture: NativeCapture) -> tuple[str, ...]:
    return tuple(
        json.dumps({"kind": event.kind, "payload": event.payload}, ensure_ascii=False)
        for event in capture.events
        if event.kind in ("tool_call", "tool_result", "message")
    )


def _effect_content(source: Path | None) -> tuple[str | None, int | None]:
    """Classify complete bytes before treating an effect artifact as text."""
    if source is None:
        return "", None
    data = source.read_bytes()
    if b"\0" in data:
        return None, len(data)
    try:
        return data.decode("utf-8"), len(data)
    except UnicodeDecodeError:
        return None, len(data)


def _effect_patch(path: str, before: str | None, after: str | None) -> str:
    if before is None or after is None:
        return ""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before/" + path,
            tofile="after/" + path,
        )
    )


def _effect_diff(report: dict, evidence: Path) -> dict:
    """Describe every effect delta; full changed memory files remain rubric artifacts."""
    inventories = {
        phase: {item["path"]: item["sha256"] for item in report[phase]}
        for phase in ("before", "after")
    }
    before, after = inventories["before"], inventories["after"]
    rows, patches = [], []
    for path in sorted(before.keys() | after.keys()):
        if ".git" in Path(path).parts:
            continue
        if before.get(path) == after.get(path):
            continue
        texts = {}
        sizes = {}
        for phase in ("before", "after"):
            digest = inventories[phase].get(path)
            source = (
                EvidenceFile(phase + "/" + path, digest).verify(evidence)
                if digest is not None
                else None
            )
            texts[phase], sizes[phase] = _effect_content(source)
        patch = _effect_patch(path, texts["before"], texts["after"])
        # difflib omits even the headers when an empty file appears/disappears.
        # Inventories carry existence, so retain that delta without a fictitious hunk.
        if not patch and texts["before"] == texts["after"] == "":
            patch = f"--- before/{path}\n+++ after/{path}\n"
        patches.append(patch)
        rows.append(
            {
                "path": path,
                "before": before.get(path),
                "after": after.get(path),
                "beforeBytes": sizes["before"],
                "afterBytes": sizes["after"],
                "binary": texts["before"] is None or texts["after"] is None,
                "diffBytes": len(patch.encode()),
                "diffSha256": hashlib.sha256(patch.encode()).hexdigest(),
            }
        )
    full = evidence / "effect-tree.diff"
    full.write_text("".join(patches))
    result = {
        "files": rows,
        "fullUnifiedDiff": full.read_text(),
        "fullDiff": EvidenceFile.capture(evidence, full.name).to_dict(),
        "fullDiffBytes": full.stat().st_size,
        "topContributors": sorted(rows, key=lambda item: item["diffBytes"], reverse=True)[:10],
        "selection": "Every changed memory card, sidecar and history file is supplied before/after in full.",
    }
    write_object(evidence / "effect-tree-diff.json", result)
    return result


def _neutral_text(text: str, runtime: _TrialRuntime) -> str:
    return (
        text.replace(str(runtime.provisioned.fixture.root), "[fixture]")
        .replace(runtime.provisioned.fixture.identity, "[run]")
        .replace(runtime.context.case.case_id, "[case]")
    )


def _blind_request(request: JudgeRequest, neutralize: Callable[[str], str]) -> JudgeRequest:
    """Hide routing identities, preserving actions and artifact content otherwise."""
    return replace(
        request,
        rubric=tuple(neutralize(text) for text in request.rubric),
        observations=tuple(neutralize(text) for text in request.observations),
        artifact_evidence=tuple(neutralize(text) for text in request.artifact_evidence),
    )


def _helper_observed(capture: NativeCapture) -> bool:
    for event in capture.events:
        if event.payload.get("type") == "agent.provider_subagents.update":
            return True
        if event.kind != "message" or event.payload.get("role") != "assistant":
            continue
        content = event.payload.get("content")
        if isinstance(content, list):
            content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
        if isinstance(content, str) and _declaration(content):
            return True
    return False


@dataclass(frozen=True)
class GradePreparationContext:
    neutralize: Callable[[str], str]
    profile: LaunchProfile
    helper_window_complete: bool = False


@dataclass(frozen=True)
class PreparedCaseGrade:
    code: Grade
    request: JudgeRequest | None


def _judge_runtime_context(report: dict, evidence: Path, capture: NativeCapture) -> dict:
    """Bind runtime facts to retained receipts; older missing receipts stay unavailable."""
    receipts = {}
    sources = {}
    for key, name in (("server", "server-info.json"), ("path", "path-snapshot.json")):
        path = evidence / name
        if path.is_file():
            receipts[key] = read_object(path)
            sources[key] = EvidenceFile.capture(evidence, name).to_dict()
    return {
        "schema": "ar-eval-judge-runtime-context/v1",
        "runtimeEvidence": collect_runtime_evidence(
            capture, server_info=receipts.get("server"), path_snapshot=receipts.get("path")
        ),
        "receiptSources": sources,
        "helperCompletionGradingNote": (
            "Completing with a helper pending is a completion, not a stop. "
            "Finish the rest means the admitted work the seat can do, and a truthful pending "
            "report is part of the completion. A stop is a halt claiming it cannot proceed: "
            "halting to wait for a helper without a decision or permission boundary fails the "
            "stop clause. Finishing the queue, running available checks, obtaining the handover "
            "where the outcome requires one, and reporting a pending helper has not stopped. "
            "This interpretation changes no stated outcome or criterion. "
            "Authority: Manager/Architect 20261030T185658Z."
            if report.get("case") in {"CE05", "CE06", "CE10", "CE12"}
            else None
        ),
        "completeness": {
            "actionsComplete": not bool(capture.unknowns),
            "captureSha256": json_digest(capture.to_dict()),
            "captureSources": [
                {"path": source.path, "sha256": source.sha256} for source in capture.source_files
            ],
            "captureLimitations": list(capture.unknowns),
            "reportsComplete": all(
                isinstance(report.get(phase), list) for phase in ("before", "after")
            ),
            "effectInventorySha256": json_digest(
                {phase: report.get(phase) for phase in ("before", "after")}
            ),
            "scope": "Complete captured actions and effect-root report inventory, not unobserved external state.",
            "unresolvedExecutablesAreNotAbsentConduct": True,
        },
    }


def prepare_case_grade(
    case: CaseBundle,
    report: dict,
    evidence: Path,
    capture: NativeCapture,
    *,
    context: GradePreparationContext,
) -> PreparedCaseGrade:
    """Prepare the same deterministic checks and rubric package without any model call."""
    inputs = CaseEvidence(
        case,
        report,
        _admitted_paths(case, report),
        evidence,
        _rubric_artifacts(case, report, evidence),
    )
    code = grade_case_code(inputs)
    if _helper_observed(capture):
        helper = grade_helpers(
            HelperObservation(
                capture, report["actorHostAgentId"], complete=context.helper_window_complete
            )
        )
        write_object(evidence / "helper-grade.json", helper.to_dict())
        if helper.status in ("fail", "unknown"):
            code = helper
    write_object(evidence / "code-grade.json", code.to_dict())
    if code.status in ("fail", "unknown"):
        if code.status == "unknown":
            report["unknowns"].extend(
                f"deterministic {code.grader_id}: {question}" for question in code.questions
            )
        return PreparedCaseGrade(code, None)
    report["judgeContext"] = _judge_runtime_context(report, evidence, capture)
    request = case_judge_request(inputs, _observations(capture))
    note = report["judgeContext"]["helperCompletionGradingNote"]
    if note is not None:
        before = request.prompt()
        request = replace(request, task=request.task + " " + note)
        report["gradingNotePromptBinding"] = {
            "note": note,
            "beforeSha256": hashlib.sha256(before.encode()).hexdigest(),
            "afterSha256": hashlib.sha256(request.prompt().encode()).hexdigest(),
            "scope": "full request before disk packaging",
            "sameForBothTextArms": True,
        }
    request = _blind_request(request, context.neutralize)
    write_object(evidence / "judge-request-full.json", json.loads(request.prompt()))
    delta = _effect_diff(report, evidence)
    delta["fullUnifiedDiff"] = context.neutralize(delta["fullUnifiedDiff"])
    delta["filePathBindings"] = {
        row["path"]: context.neutralize(row["path"]) for row in delta["files"]
    }
    try:
        selection = select_case_evidence(
            case, capture, report, evidence, neutralize=context.neutralize
        )
        package = package_judge_evidence(
            request,
            capture,
            inputs.rubric_files,
            inputs=JudgePackageInputs(
                delta, selection, evidence, report["identity"], context.profile
            ),
        )
    except JudgePackageTooLarge as error:
        report["unknown_class"] = "schema-incompatible"
        report["unknowns"].append(f"schema-incompatible: {error.cause}")
        write_object(evidence / "judge-evidence-manifest.json", error.manifest)
        write_object(
            evidence / "evidence-selection.json", {"status": "unknown", "reason": error.cause}
        )
        return PreparedCaseGrade(code, None)
    except (AuthorityError, OSError, ValueError) as error:
        report["unknowns"].append(grading_exception_cause(report, error))
        write_object(
            evidence / "evidence-selection.json", {"status": "unknown", "reason": str(error)}
        )
        return PreparedCaseGrade(code, None)
    write_object(evidence / "judge-evidence-manifest.json", package.manifest)
    request = package.request
    write_object(evidence / "judge-request.json", json.loads(request.prompt()))
    return PreparedCaseGrade(code, request)


def _grade(runtime: _TrialRuntime, capture: NativeCapture) -> tuple[str, int | None]:
    report, evidence = runtime.report, runtime.evidence
    profile = runtime.context.profile
    window = (
        host_window(runtime.provisioned, report["actorHostAgentId"])
        if _helper_observed(capture)
        else None
    )
    prepared = prepare_case_grade(
        runtime.context.case,
        report,
        evidence,
        capture,
        context=GradePreparationContext(
            lambda text: _neutral_text(text, runtime),
            profile,
            helper_window_complete=window is not None and window.complete,
        ),
    )
    code, request = prepared.code, prepared.request
    if request is None:
        return ("wrong" if code.status == "fail" else "missing_capture"), None
    judge = run_native_judge(
        runtime.provisioned,
        profile,
        request,
        evidence,
        NativeJudgeOptions(runtime.context.request.timeout_seconds),
    )
    write_object(evidence / "judge-capture.json", judge.to_dict())
    report["unknowns"].extend(judge.unknowns)
    usage = native_usage(judge)
    write_object(evidence / "judge-usage.json", usage)
    verdict, parse_path = parse_native_verdict(judge)
    write_object(
        evidence / "judge-evidence-attribution.json",
        {
            **attribute_unknowns(read_object(evidence / "judge-evidence-manifest.json"), verdict),
            "trialId": report["identity"],
            "verdictParsePath": parse_path,
            "verdictParseProvenance": verdict_parse_provenance(judge),
        },
    )
    outcome = resolve_judge_outcome(code, RubricSubmission(request, profile.model, judge))
    if outcome in {"missing_capture", "semantic_unknown"} and not judge.unknowns:
        report["unknowns"].append(unknown_verdict_reason(judge))
    return outcome, usage["total_tokens"]


def _prepare_actor_text(
    runtime: _TrialRuntime,
) -> tuple[str, PointerDelivery | None, TextDeliveryMarker]:
    context, provisioned, report = runtime.context, runtime.provisioned, runtime.report
    case = context.case
    delivery = (
        bind_text_details(
            context.text_artifact, provisioned.project_root / "reports/text-delivery/curator.md"
        )
        if "entries" in context.text_artifact
        else None
    )
    instructions = delivery.instructions if delivery is not None else context.instructions
    if delivery is not None:
        report["textPointerDelivery"] = delivery.to_dict()
        report["textDetailLinksBefore"] = validate_detail_links(delivery)
    report["before"] = save_effects(provisioned, case, runtime.evidence / "before")
    marker = prepare_text_delivery(
        instructions,
        case,
        tuple(
            EvidenceFile(**item).verify(runtime.evidence / "before") for item in report["before"]
        ),
        details=tuple(detail.path for detail in delivery.details) if delivery else (),
    )
    report["textDeliveryPlan"] = marker.to_dict()
    return instructions, delivery, marker


def _observe_actor_text(
    runtime: _TrialRuntime,
    delivery: PointerDelivery | None,
    marker: TextDeliveryMarker,
    capture: NativeCapture | None,
    stopped: bool,
) -> None:
    report = runtime.report
    if delivery is not None:
        report["textDetailLinksAfter"] = validate_detail_links(delivery)
        report["unknowns"].extend(report["textDetailLinksAfter"]["unknowns"])
    if capture is None:
        return
    report["textDelivery"] = observe_text_delivery(marker, capture)
    if not stopped:
        report["unknowns"].extend(report["textDelivery"]["unknowns"])
    if delivery is not None:
        report["textDetailReads"] = observe_detail_reads(
            delivery,
            capture,
            runtime.provisioned.project_root,
            frozen_contents=dict(delivery.frozen_contents),
        )


def _record_helper_boundary(runtime: _TrialRuntime, capture: NativeCapture) -> None:
    report = runtime.report
    snapshot = helper_wait_snapshot(capture)
    deadline = any(wait.get("actorDeadlineReached") is True for wait in report["waits"])
    report["helperBoundary"] = {
        **snapshot,
        "actorDeadlineReached": deadline,
        "actorDeadlineSeconds": runtime.context.request.timeout_seconds,
        "ending": "actor deadline"
        if deadline
        else report["waits"][-1].get("state")
        if report["waits"]
        else "unobserved",
        "helperWaitPerformed": False,
        "helperWaitElapsedSeconds": None,
        "reentryProof": "held; no native completion-to-ACP binding"
        if capture.harness == "hermes"
        else "not established by this observation",
    }
    if capture.harness == "hermes" and any(helper["dispatches"] for helper in snapshot["helpers"]):
        report["helperBoundary"].update(
            helperLaunched=True,
            resultDelivery="not delivered",
            mechanism="Hermes ACP has no asynchronous completion consumer or process-local roster read",
            basis="Manager/Architect R1 ruling 20261030T185331Z and installed ACP source audit",
            actorFault=False,
        )
    if deadline and snapshot["pending_ids"]:
        report["helperDeadlineState"] = HELPER_DEADLINE_STATE
        report["helperBoundary"]["state"] = HELPER_DEADLINE_STATE


def _actor_run(runtime: _TrialRuntime) -> NativeCapture | None:
    context, provisioned, report = runtime.context, runtime.provisioned, runtime.report
    case, profile = context.case, context.profile
    expected_end = actor_access_deadline(
        report, profile, provisioned.native_capture_roots.get("hermes")
    )
    binding = NativeBinding(
        provisioned.project_root,
        provisioned.paseo_home,
        provisioned.settings_path,
        provisioned.tool_build,
        ("agents-remember-task",),
        profile,
    )
    adapter = PaseoNativeAdapter(
        PaseoConnection(
            provisioned.config,
            binding,
            context.request.sandbox_home,
            provisioned.server_definition,
            actor_deadline_epoch=expected_end,
        )
    )
    instructions, delivery, marker = _prepare_actor_text(runtime)
    policy_home = (
        provisioned.native_capture_roots["hermes"] if profile.harness == "hermes" else None
    )
    if policy_home is not None:
        report["skillPolicyBefore"] = skill_policy_snapshot(policy_home)
    started = time.time()
    prompt_source = case.manifest["setup"]["prompt"]
    prompt = next(
        item["destination"]
        for item in case.manifest["actorInputs"]
        if item["source"] == prompt_source
    )
    report["actorStarted"] = None
    try:
        run = adapter.run(
            instructions=instructions,
            prompt=child_path(provisioned.project_root, prompt).read_text()
            + "\n\n"
            + provisioned.binding_prompt(),
            capture=lambda owner: capture_trial_actor(provisioned, profile.harness, owner, started),
            timeout_seconds=context.request.timeout_seconds,
        )
    finally:
        if policy_home is not None:
            integrity = check_skill_policy_integrity(policy_home, report["skillPolicyBefore"])
            report["skillPolicyIntegrity"] = integrity
            if not integrity["unchanged"]:
                report["unknowns"].append(
                    "instrument-altered: trial skill policy changed: "
                    + ", ".join(item["path"] for item in integrity["changes"])
                )
        partial = adapter.last_run
        if isinstance(partial, PaseoRun):
            report["hostCounts"] = partial.host_counts
            report["actorHostAgentId"] = partial.agent_id
    report["hostCounts"] = run.host_counts
    report["actorStarted"] = True
    report["actorHostAgentId"] = run.agent_id
    report["applied"] = run.applied
    report["waits"] = list(run.waits)
    report["unknowns"].extend(run.unknowns)
    if run.unknowns:
        report["primaryCause"] = run.unknowns[0]
    report["after"] = save_effects(provisioned, case, runtime.evidence / "after")
    capture = run.capture
    if capture is None:
        report["unknowns"].append("native actor capture absent")
    else:
        report["capture"] = capture.to_dict()
        report["unknowns"].extend(capture.unknowns)
        report["installedServerCalls"] = len(capture.installed_server_calls)
        report["actorUsage"] = native_usage(capture)
        _record_helper_boundary(runtime, capture)
    _observe_actor_text(runtime, delivery, marker, capture, bool(run.unknowns))
    initial, final = run.host_counts.get("before_first_start"), run.host_counts.get("at_end")
    if type(initial) is not int or type(final) is not int:
        report["unknowns"].append("native sandbox host-agent counts absent")
    elif initial != final:
        report["unknowns"].append("sandbox host-agent count did not return to initial value")
    return capture


def _attempt(context: _TrialContext, identity: str, values: dict) -> Attempt:
    return Attempt.from_dict(
        {
            "attempt_id": identity,
            "case_id": context.case.case_id,
            "model": context.profile.model,
            "text_signature": context.signature,
            "batch_id": context.batch,
            "batch_kind": context.request.phase,
            "trial_slot": context.slot,
            "retry_of": context.previous.attempt_id if context.previous else None,
            **values,
        }
    )


def _save_setup(runtime: _TrialRuntime) -> None:
    """Keep fixture binding/probe facts, without serializing the login environment."""
    root = runtime.provisioned.fixture.root
    sources = [
        root / name
        for name in (
            "reset.json",
            "server-info.json",
            "native-binding.json",
            "path-snapshot.json",
            "host-provision.json",
            "recorder-install.json",
        )
    ]
    sources.extend(
        runtime.provisioned.paseo_home / "agent-eval" / name
        for name in ("host-provision.json", "host-stop.json")
    )
    diagnostics = []
    for source in sources:
        if source.is_file():
            name = ("batch-" if source.parent != root else "") + source.name
            target = runtime.evidence / name
            shutil.copyfile(source, target)
            diagnostics.append(
                {"source": str(source), **EvidenceFile.capture(runtime.evidence, name).to_dict()}
            )
    runtime.report["setupDiagnostics"] = diagnostics


def _finish_trial(runtime: _TrialRuntime, outcome: str, auth: AuthObservation | None = None) -> str:
    provisioned, report = runtime.provisioned, runtime.report
    try:
        _save_setup(runtime)
    except (OSError, ValueError, AuthorityError) as error:
        report["unknowns"].append(f"setup diagnostics: {type(error).__name__}: {error}")
        outcome = "infrastructure_failure"
    try:
        if "after" not in report:
            report["after"] = save_effects(
                provisioned, runtime.context.case, runtime.evidence / "after"
            )
    except (OSError, ValueError, AuthorityError) as error:
        report["unknowns"].append(f"effect capture: {type(error).__name__}: {error}")
        outcome = "infrastructure_failure"
    finally:
        try:
            if not report.get("cleanupDeferred"):
                provisioned.cleanup()
        except (OSError, ValueError, AuthorityError) as error:
            report["unknowns"].append(f"cleanup: {type(error).__name__}: {error}")
            outcome = "infrastructure_failure"
    if not record_auth_comparison(report, runtime.context.profile, auth):
        outcome = "infrastructure_failure"
    report["status"] = outcome
    write_object(runtime.evidence / "attempt.json", report)
    return outcome


def _actor_only_check(runtime: _TrialRuntime, capture: NativeCapture) -> str:
    """Record a finished native Hermes turn, without deriving a case verdict."""
    sessions = [
        item["row"]
        for item in capture.raw_records
        if item.get("table") == "sessions"
        and isinstance(item.get("row"), dict)
        and item["row"].get("id") == capture.session_id
    ]
    waits = runtime.report.get("waits", [])
    final = waits[-1] if waits else {}
    finished = (final.get("state") == "ended" and final.get("outcome") == "finished") or final.get(
        "nativeSettlement", {}
    ).get("outcome") == "finished"
    row = sessions[0] if len(sessions) == 1 else {}
    expected = runtime.context.profile.model.partition(":")[2]
    calls = row.get("api_call_count")
    passed = (
        capture.harness == "hermes"
        and finished
        and row.get("model") == expected
        and type(calls) is int
        and calls > 0
    )
    proof = {
        "turnFinished": finished,
        "model": row.get("model"),
        "apiCalls": calls,
        "sessionId": capture.session_id,
        "rawExportSha256": capture.to_dict()["raw_export_sha256"],
        "actor": True,
        "measurement": False,
        "caseVerdict": False,
        "judgeInvocationsThisRun": 0,
        "pass": passed,
    }
    write_object(runtime.evidence / "actor-only-check.json", proof)
    if not passed:
        runtime.report["unknown_class"] = "capture-absent"
        runtime.report["unknowns"].append(
            "capture-absent: actor-only acceptance lacks a finished native Hermes turn "
            "with the admitted model and positive API count; see actor-only-check.json"
        )
        return "missing_capture"
    return "actor_only_complete"


def _run_one(context: _TrialContext, batch_host: BatchHost) -> Attempt:
    if context.profile.harness != "hermes":
        return _run_trial(context, batch_host, None)
    with AuthObservation(
        source_hermes_home() / "auth.json",
        provider="openai-codex" if context.profile == HERMES_GPT_ACTOR else None,
    ) as auth:
        return _run_trial(context, batch_host, auth)


def _defer_uncertain_actor(runtime: _TrialRuntime, batch_host: BatchHost) -> None:
    provisioned, report = runtime.provisioned, runtime.report
    report["cleanupDeferred"] = {
        "until": "supported batch host stop succeeds",
        "reason": "native agent creation outcome is uncertain; it may still use this fixture",
        "fixtureRoot": str(provisioned.fixture.root),
    }
    try:
        report["credentialCleanupBeforeDefer"] = purge_hermes_credentials(
            provisioned.fixture.root / "hermes", trials_root=provisioned.fixture.root.parent
        )
        remaining = report["credentialCleanupBeforeDefer"]["remainingPaths"]
        if remaining:
            report["unknowns"].append(
                "binding-unavailable: credential cleanup left files: " + "; ".join(remaining)
            )
    except AuthorityError as error:
        report["unknowns"].append(f"binding-unavailable: credential cleanup before defer: {error}")
    batch_host.defer_cleanup(provisioned.cleanup)


def _grade_actor_capture(
    runtime: _TrialRuntime, capture: NativeCapture | None
) -> tuple[str, int | None]:
    report, request = runtime.report, runtime.context.request
    if report.get("helperDeadlineState") == HELPER_DEADLINE_STATE:
        return HELPER_DEADLINE_STATE, None
    if report["unknowns"]:
        outcome = (
            "unanswered_prompt"
            if report.get("waits") and report["waits"][-1].get("state") == "permission"
            else "missing_capture"
        )
        return outcome, None
    if capture is None:
        return "infrastructure_failure", None
    if request.actor_only:
        report["executionPhase"] = "actor-only-check"
        return _actor_only_check(runtime, capture), None
    report["status"] = "captured-ungraded"
    report["executionPhase"] = "grading"
    return _grade(runtime, capture)


def _run_trial(
    context: _TrialContext, batch_host: BatchHost, auth: AuthObservation | None
) -> Attempt:
    request, case = context.request, context.case
    expected_end = (
        time.time() + request.timeout_seconds if context.profile == HERMES_GPT_ACTOR else None
    )
    provisioned = provision_trial(
        case,
        request.sandbox_home / "trials",
        request.candidate_code_root,
        batch_host=batch_host,
        launch=TrialLaunch(context.profile, request.codex_access, expected_end),
    )
    fixture = provisioned.fixture
    evidence = request.evidence / fixture.identity
    try:
        evidence.mkdir(parents=True, exist_ok=False)
        shutil.copytree(fixture.case_copy, evidence / "case")
    except (OSError, ValueError):
        provisioned.cleanup()
        raise
    report: dict = {
        "schema": "ar-agent-eval-native-attempt/v1",
        "case": case.case_id,
        "identity": fixture.identity,
        "caseDigest": fixture.case_digest,
        "effectiveText": str(request.evidence / "texts" / (context.signature + ".json")),
        "effectBindings": {
            "memoryAlias": str(provisioned.project_root / "projects/memory"),
            "memoryRoot": str(provisioned.memory_root),
            "logicalPrefix": "projects/memory",
        },
        "unknowns": [],
        "hostCounts": {},
        "status": "unknown",
        "executionPhase": "setup",
        "actorStarted": False,
    }
    runtime = _TrialRuntime(context, provisioned, evidence, report)
    if expected_end is not None:
        report["accessExpectedEnd"] = expected_end
    outcome = "infrastructure_failure"
    judge_tokens = None
    try:
        declare_credentials(report, evidence, context.profile, source_hermes_home())
        copied_preflight = preflight_project(
            case,
            provisioned.memory_root,
            provisioned.code_root,
            intake=NativeIntakeBinding(
                provisioned.config, provisioned.native_setup.get("leaf"), evidence / "native-intake"
            ),
        )
        write_object(evidence / "provisioned-preflight.json", copied_preflight)
        if copied_preflight["status"] != "pass":
            report["unknown_class"] = "binding-unavailable"
            raise AuthorityError(
                "binding-unavailable: provisioned case preflight refused before host or actor; see provisioned-preflight.json"
            )
        with provisioned.activated():
            report["executionPhase"] = "host-start"
            provisioned.start_host()
            report["executionPhase"] = "recorder-install"
            provisioned.install_plugin(Path(__file__).parent / "adapters/recorder_plugin")
            _save_setup(runtime)
            report["executionPhase"] = "native-actor"
            capture = _actor_run(runtime)
        outcome, judge_tokens = _grade_actor_capture(runtime, capture)
    except (OSError, ValueError, KeyError, AuthorityError, PaseoBridgeFailure) as error:
        cause = grading_exception_cause(report, error)
        if isinstance(error, (AccessOnlyError, ActorDeadlineError)):
            report["unknown_class"] = "binding-unavailable"
            cause = f"binding-unavailable: {cause}"
        elif isinstance(error, PaseoBridgeFailure):
            report["unknown_class"] = "capture-absent"
            cause = f"capture-absent: {cause}"
            if (
                report["executionPhase"] == "native-actor"
                and report["actorStarted"] is None
                and "agent-create" in str(error)
            ):
                _defer_uncertain_actor(runtime, batch_host)
        report["unknowns"].append(cause)
    finally:
        outcome = _finish_trial(runtime, outcome, auth)
    cause = report.get("primaryCause") or next(
        iter(report["unknowns"]), "native judge or grading evidence incomplete"
    )
    return _attempt(
        context,
        fixture.identity,
        {
            "status": outcome,
            "actor_tokens": report.get("actorUsage", {}).get("total_tokens"),
            "judge_tokens": judge_tokens,
            "judge_model": None if request.actor_only else judge_profile(context.profile).model,
            "unknown_cause": None
            if outcome in ("right", "wrong", "actor_only_complete")
            else cause,
        },
    )


def _setup_failure(context: _TrialContext, error: Exception) -> Attempt:
    identity = str(uuid.uuid4())
    cause = f"trial setup: {type(error).__name__}: {error}"
    if isinstance(error, (AuthObservationError, AccessOnlyError)):
        cause = f"binding-unavailable: {cause}"
    attempt = _attempt(
        context,
        identity,
        {
            "status": "infrastructure_failure",
            "unknown_cause": cause,
        },
    )
    write_object(
        context.request.evidence / identity / "attempt.json",
        {
            "schema": "ar-agent-eval-native-attempt/v1",
            "case": context.case.case_id,
            "identity": identity,
            "status": "infrastructure_failure",
            "unknowns": [cause],
            "actorStarted": False,
        },
    )
    return attempt


def _validate_request(request: TrialBatchRequest) -> None:
    if request.actor_only and request.phase != "tooling_check":
        raise AuthorityError("actor-only requires tooling_check phase")
    profile = actor_profile(request.model)
    if (profile == HERMES_GPT_ACTOR) != (request.codex_access is not None):
        raise AuthorityError("hermes-gpt requires --codex-access; other profiles forbid it")
    if request.actor_only and profile.harness != "hermes":
        raise AuthorityError("actor-only check requires the admitted Hermes profile")
    if request.phase not in ("measurement", "final_full", "tooling_check"):
        raise AuthorityError("trial phase must be measurement, final_full or tooling_check")
    if request.phase == "final_full" and not final_full_repeat_allowed():
        raise AuthorityError("final-full command is disabled pending the Architect's answer")
    if request.phase == "final_full" and set(request.case_ids) != set(CASE_IDS):
        raise AuthorityError("final-full phase requires the complete fixed fourteen-case set")
    if request.trials not in (1, 2, 3) or request.timeout_seconds <= 0:
        raise AuthorityError("trials must be 1, 2 or 3 and timeout must be positive")
    if not request.case_ids or any(case not in CASE_IDS for case in request.case_ids):
        raise AuthorityError("select fixed CE01-CE14 cases")
    if len(set(request.case_ids)) != len(request.case_ids):
        raise AuthorityError("select each fixed case once per command")


def _plan(
    request: TrialBatchRequest,
    attempts: list[Attempt],
    text: tuple[str, str, dict],
    profile: LaunchProfile,
) -> list[_TrialContext]:
    instructions, signature, artifact = text
    batch = _batch(attempts, profile, signature, request.phase)
    planned = []
    prospective = list(attempts)
    for case_id in request.case_ids:
        path = request.cases if (request.cases / "case.json").is_file() else request.cases / case_id
        case = CaseBundle.read(path)
        case.require_admitted()
        if case.case_id != case_id:
            raise AuthorityError("selected case folder differs from requested fixed case")
        for slot in range(1, request.trials + 1):
            previous = _previous(attempts, batch, case_id, slot)
            if previous is not None and previous.status in (
                "right",
                "wrong",
                "actor_only_complete",
                "semantic_unknown",
            ):
                continue
            context = _TrialContext(
                request,
                case,
                profile,
                instructions,
                artifact,
                signature,
                batch,
                slot,
                previous,
            )
            prospective.append(
                _attempt(
                    context,
                    str(uuid.uuid4()),
                    {"status": "infrastructure_failure", "unknown_cause": "prospective check only"},
                )
            )
            validate_attempt_sequence(prospective)
            planned.append(context)
    return planned


def run_trials(request: TrialBatchRequest) -> Path:
    """Provision every trial, run actor and judge, and retain chronological accounting."""
    _validate_request(request)
    preflight_path = request.evidence / "case-preflight.json"
    preflight = write_preflight(request.cases, preflight_path, request.case_ids)
    if not preflight["pass"]:
        raise AuthorityError(f"case tree preflight refused before any actor: {preflight_path}")
    profile = actor_profile(request.model)
    instructions, signature, text = load_trial_text(request.candidate_code_root, request.text)
    request.evidence.mkdir(parents=True, exist_ok=True)
    request.sandbox_home.mkdir(parents=True, exist_ok=True)
    print(f"Sandbox home: {request.sandbox_home.resolve()}", flush=True)
    if not request.approver_ready:
        answer = input("Start your approver copy for this home; type ready to continue: ")
        if answer.strip() != "ready":
            raise AuthorityError("approver readiness was not confirmed; actor was not started")
    write_object(request.evidence / "texts" / (signature + ".json"), text)
    ledger = request.evidence / "attempts.json"
    attempts = _ledger(ledger)
    plan = _plan(request, attempts, (instructions, signature, text), profile)
    if not plan:
        return ledger
    batch_host = provision_batch(
        request.sandbox_home, request.candidate_code_root, hermes=profile.harness == "hermes"
    )
    try:
        for context in plan:
            try:
                attempt = _run_one(context, batch_host)
            except (OSError, ValueError, KeyError, AuthorityError, AuthObservationError) as error:
                attempt = _setup_failure(context, error)
            validate_attempt_sequence([*attempts, attempt])
            attempts.append(attempt)
            write_object(
                ledger,
                {
                    "schema": "ar-curator-eval-attempts/v1",
                    "attempts": [item.to_dict() for item in attempts],
                },
            )
    finally:
        try:
            batch_host.cleanup()
        except Exception as error:
            failure = request.evidence / "batch-cleanup-failure.json"
            write_object(
                failure,
                {
                    "status": "infrastructure_failure",
                    "sandboxHome": str(request.sandbox_home),
                    "cause": f"{type(error).__name__}: {error}",
                    "attemptsPreserved": str(ledger),
                },
            )
            raise AuthorityError(
                f"batch host cleanup failed; retained evidence: {failure}"
            ) from error
        finally:
            for phase in ("start", "end"):
                name = f"credential-cleanup-{phase}.json"
                receipt = request.sandbox_home / "agent-eval" / name
                if receipt.is_file():
                    shutil.copyfile(receipt, request.evidence / name)
    return ledger

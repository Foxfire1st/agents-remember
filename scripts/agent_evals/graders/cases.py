"""Case-level code evidence and rubric verdicts; actor success text is never a verdict."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import AuthorityError
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodePathSet,
    KnowledgeTree,
    is_knowledge_path,
)

from ..adapters.judges import JudgeRequest
from ..adapters.models import NativeCapture
from ..artifacts import EvidenceFile, json_digest, write_object
from ..fixtures import CaseBundle
from ..git_semantics import compare_git_semantics
from ..validation import compare_validation
from .case_history import grade_case_history
from .models import Grade, current_grade_sources


@dataclass(frozen=True)
class CaseEvidence:
    case: CaseBundle
    attempt: dict
    admitted_paths: frozenset[str]
    artifacts_root: Path
    rubric_files: tuple[EvidenceFile, ...]


def _captured_trees(evidence: CaseEvidence, phase: str) -> tuple[KnowledgeTree, CodePathSet]:
    memory, code = {}, {}
    for item in evidence.attempt[phase]:
        name = item["path"]
        if name.startswith("projects/memory/"):
            relative = name.removeprefix("projects/memory/")
            if is_knowledge_path(relative):
                memory[relative] = (
                    EvidenceFile(**item).verify(evidence.artifacts_root / phase).read_bytes()
                )
        elif name.startswith("projects/code/") and ".git" not in Path(name).parts:
            code[name.removeprefix("projects/code/")] = (
                EvidenceFile(**item).verify(evidence.artifacts_root / phase).read_bytes()
            )
    return KnowledgeTree(phase, memory), CodePathSet(phase, frozenset(code), contents=code)


def _written_memory_paths(attempt: dict) -> frozenset[str]:
    """Retain successful native FileChange writes, including identical-byte writes."""
    paths = set()
    bindings = attempt.get("effectBindings", {})
    roots = [bindings[key] for key in ("memoryAlias", "memoryRoot") if key in bindings]
    for event in attempt["capture"].get("events", ()):
        item = event.get("payload", {}).get("native_item", {})
        if item.get("type") != "FileChange" or item.get("status") != "completed":
            continue
        for name in item.get("changes", {}):
            relative = (
                name.removeprefix("projects/memory/")
                if name.startswith("projects/memory/")
                else None
            )
            for root in roots:
                if Path(name).is_relative_to(root):
                    relative = Path(name).relative_to(root).as_posix()
            if (
                relative is not None
                and ".." not in Path(relative).parts
                and is_knowledge_path(relative)
            ):
                paths.add(relative)
    return frozenset(paths)


def _validation_grade(evidence: CaseEvidence) -> Grade:
    before, before_code = _captured_trees(evidence, "before")
    after, after_code = _captured_trees(evidence, "after")
    result = compare_validation(
        before,
        after,
        before_code,
        after_code,
        written_paths=_written_memory_paths(evidence.attempt),
    )
    path = evidence.artifacts_root / "validation-grade.json"
    write_object(
        path,
        {
            **result,
            "grader_id": "whole-tree-validation",
            "producer": current_grade_sources(__file__),
        },
    )
    reference = EvidenceFile.capture(evidence.artifacts_root, path.name)
    return Grade.produced(
        __file__,
        evidence.case.case_id,
        result["status"],
        (f"{reference.path}@sha256:{reference.sha256}",),
        tuple(
            f"{item['rule']} {item['path']} {item['field']}: {item['message']}"
            for item in result["blockingViolations"]
        ),
    )


def _git_effects(evidence: CaseEvidence, changed: set[str]) -> tuple[set[str], Grade | None]:
    roots = {
        Path(*Path(path).parts[: Path(path).parts.index(".git")])
        for path in changed
        if ".git" in Path(path).parts
    }
    benign: set[str] = set()
    for root in sorted(roots):
        # D641 C4b: a fresh repository inside the admitted trial scratch is
        # ordinary content, not a mutation of an existing repository. Its paths
        # still go through the normal allowed-effects check below.
        prefix = (root / ".git").as_posix() + "/"
        before = evidence.attempt["before"]
        if root.is_relative_to("reports/scratch") and not any(
            item["path"] == prefix[:-1] or item["path"].startswith(prefix) for item in before
        ):
            continue
        with tempfile.TemporaryDirectory(prefix="ar-eval-git-") as directory:
            result = compare_git_semantics(
                evidence.artifacts_root / "before" / root,
                evidence.artifacts_root / "after" / root,
                scratch_root=Path(directory),
            )
        reference = "git-semantics:" + json.dumps(result.to_dict(), sort_keys=True)
        if result.status != "pass":
            return benign, Grade.produced(
                __file__,
                evidence.case.case_id,
                result.status,
                (reference,),
                tuple(f"Unadmitted Git state change in {root}: {item}" for item in result.changes),
            )
        benign.update((root / ".git" / item).as_posix() for item in result.benign_metadata_paths)
    return benign, None


def grade_case_code(evidence: CaseEvidence) -> Grade:
    """Check actual before/after inventories and native calls, including forbidden effects."""
    attempt = evidence.attempt
    refs = ("attempt:" + json_digest(attempt),)
    if attempt.get("case") != evidence.case.case_id or attempt.get("caseDigest") != json_digest(
        evidence.case.manifest
    ):
        raise AuthorityError("case/attempt identity differs; use the exact reset case for grading")
    capture = attempt.get("capture")
    if (
        attempt.get("unknowns")
        or attempt.get("status") == "unknown"
        or not isinstance(capture, dict)
        or not capture.get("session_id")
        or not capture.get("raw_records")
        or not capture.get("source_files")
    ):
        return Grade.produced(
            __file__, evidence.case.case_id, "unknown", refs, tuple(attempt.get("unknowns", ()))
        )
    try:
        for phase in ("before", "after"):
            for item in attempt[phase]:
                EvidenceFile(**item).verify(evidence.artifacts_root / phase)
    except (OSError, AuthorityError) as error:
        return Grade.produced(__file__, evidence.case.case_id, "unknown", refs, (str(error),))
    before = {item["path"]: item["sha256"] for item in attempt["before"]}
    after = {item["path"]: item["sha256"] for item in attempt["after"]}
    changed = {
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
        and not (path.startswith("projects/code/") and ".pytest_cache" in Path(path).parts)
    }
    benign, git_failure = _git_effects(evidence, changed)
    if git_failure is not None:
        return git_failure
    excess = changed - evidence.admitted_paths - benign
    if excess:
        return Grade.produced(
            __file__,
            evidence.case.case_id,
            "fail",
            refs,
            tuple("Unadmitted changed path: " + path for path in sorted(excess)),
        )
    return _grade_contents(evidence, refs)


def _grade_contents(evidence: CaseEvidence, refs: tuple[str, ...]) -> Grade:
    captured = {item["path"] for item in evidence.attempt["before"]}
    missing = sorted(
        item["destination"]
        for item in evidence.case.manifest.get("actorInputs", ())
        if item["destination"].startswith(("projects/code/", "projects/memory/"))
        and item["destination"] not in captured
    )
    if missing:
        return Grade.produced(
            __file__,
            evidence.case.case_id,
            "unknown",
            refs,
            tuple("case input not captured before: " + path for path in missing),
        )
    try:
        validation = _validation_grade(evidence)
    except (OSError, ValueError, KeyError, TypeError, AuthorityError) as error:
        return Grade.produced(
            __file__, evidence.case.case_id, "unknown", refs, (f"validation evidence: {error}",)
        )
    history = grade_case_history(evidence.case, evidence.attempt, evidence.artifacts_root)
    refs = (*refs, *validation.evidence, *history.evidence)
    failures = tuple(grade for grade in (validation, history) if grade.status == "fail")
    if failures:
        return Grade.produced(
            __file__,
            evidence.case.case_id,
            "fail",
            refs,
            tuple(question for grade in failures for question in grade.questions),
        )
    if history.status == "unknown":
        return Grade.produced(__file__, evidence.case.case_id, "unknown", refs, history.questions)
    return Grade.produced(
        __file__,
        evidence.case.case_id,
        "needs_rubric",
        refs,
        (
            *evidence.case.manifest["specification"]["prospectiveGrade"]["deterministicChecks"],
            *history.questions,
        ),
    )


def _artifact_material(item: EvidenceFile, root: Path) -> str:
    """Keep documents exact; represent undecodable artifacts by their source identity."""
    raw = item.verify(root).read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return json.dumps(
            {
                "kind": "binary-artifact",
                "binary": True,
                "path": item.path,
                "sizeBytes": len(raw),
                "sha256": item.sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def case_judge_request(evidence: CaseEvidence, observations: tuple[str, ...]) -> JudgeRequest:
    grade = evidence.case.manifest["specification"]["prospectiveGrade"]
    # A neutral request carries no old/new labels, case title, checkpoint, source paths or model family.
    return JudgeRequest(
        tuple(grade["deterministicChecks"]) + tuple(grade["semanticRubric"]),
        observations,
        tuple(_artifact_material(item, evidence.artifacts_root) for item in evidence.rubric_files),
    )


@dataclass(frozen=True)
class RubricSubmission:
    request: JudgeRequest
    actor_model: str
    judge_capture: NativeCapture


def resolve_rubric(code: Grade, submission: RubricSubmission) -> str:
    """A complete independent rubric can settle a code-clean case, never override a code fail."""
    if code.status in ("fail", "unknown"):
        return "wrong" if code.status == "fail" else "missing_capture"
    capture = submission.judge_capture
    expected_model = {
        "gpt-6.1-sol": "claude-opus-5-5",
        "opencode-go:deepseek-v4.1-flash": "gpt-6.1-sol",
        "opencode-go:kimi-k3.1-pro": "gpt-6.1-sol",
        "openai-codex:gpt-6.1-sol-900k": "claude-opus-5-5",
    }.get(submission.actor_model)
    observed = {
        event.payload.get("model")
        for event in capture.events
        if isinstance(event.payload.get("model"), str)
    }
    if capture.unknowns or expected_model is None or observed != {expected_model}:
        return "missing_capture"
    verdict, _ = parse_native_verdict(capture)
    rows = verdict.get("criteria")
    if not isinstance(rows, list):
        return "missing_capture"
    expected = list(submission.request.rubric)
    actual = [row.get("criterion") for row in rows if isinstance(row, dict)]
    if actual != expected:
        raise AuthorityError(
            "judge omitted, reordered or invented rubric criteria; retain the attempt as unknown"
        )
    outcomes = [row.get("outcome") for row in rows]
    if any(not isinstance(row.get("evidence"), str) or not row["evidence"].strip() for row in rows):
        return "missing_capture"
    if any(outcome not in ("pass", "fail", "unknown") for outcome in outcomes):
        raise AuthorityError("judge outcome is not pass/fail/unknown; retain its raw response")
    return (
        "wrong" if "fail" in outcomes else "missing_capture" if "unknown" in outcomes else "right"
    )


def _final_verdict_text(capture: NativeCapture) -> tuple[str, int | None]:
    messages = [
        event
        for event in capture.events
        if event.kind == "message" and event.payload.get("role") == "assistant"
    ]
    if not messages:
        return "", None
    final = messages[-1]
    content = final.payload.get("content")
    if not isinstance(content, list):
        return "", final.sequence
    return "".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") in ("text", "output_text")
        and isinstance(block.get("text"), str)
    ), final.sequence


def _verdict_schema_valid(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("criteria"), list):
        return False
    return bool(value["criteria"]) and all(
        isinstance(row, dict)
        and isinstance(row.get("criterion"), str)
        and bool(row["criterion"].strip())
        and row.get("outcome") in ("pass", "fail", "unknown")
        and isinstance(row.get("evidence"), str)
        and bool(row["evidence"].strip())
        for row in value["criteria"]
    )


def _fenced_verdicts(text: str) -> list[tuple[object, dict]]:
    blocks = []
    pattern = r"(?m)^[ \t]*```json[ \t]*\r?\n(?P<payload>[\s\S]*?)\r?\n[ \t]*```[ \t]*(?=\r?$)"
    for index, match in enumerate(re.finditer(pattern, text)):
        try:
            value = json.loads(match.group("payload"))
        except ValueError:
            value = None
        positions = {
            "blockIndex": index,
            "fenceByteOffset": len(text[: match.start()].encode("utf-8")),
            "fenceByteEndOffset": len(text[: match.end()].encode("utf-8")),
            "payloadByteOffset": len(text[: match.start("payload")].encode("utf-8")),
            "payloadByteEndOffset": len(text[: match.end("payload")].encode("utf-8")),
            "schemaValid": _verdict_schema_valid(value),
        }
        blocks.append((value, positions))
    return blocks


def _decode_native_verdict(capture: NativeCapture) -> tuple[dict, str, dict]:
    text, sequence = _final_verdict_text(capture)
    provenance = {
        "schema": "ar-native-verdict-parse/v1",
        "status": "invalid",
        "reason": "final assistant message has no decodable verdict",
        "parsePath": "invalid",
        "messageSequence": sequence,
        "messageTextSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "fullMessageText": text,
        "validFencedCandidateCount": 0,
        "blockIndex": None,
        "fenceByteOffset": None,
        "fenceByteEndOffset": None,
        "payloadByteOffset": None,
        "payloadByteEndOffset": None,
        "offsetConvention": "Zero-based UTF-8 byte offsets; end offsets exclusive; block index counts complete json fences in the final assistant message.",
    }
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    if isinstance(raw, dict):
        # Raw JSON retains its existing downstream schema/order/model checks.
        provenance.update(status="decoded", reason=None, parsePath="raw_json")
        return raw, "raw_json", provenance
    blocks = _fenced_verdicts(text)
    valid = [row for row in blocks if row[1]["schemaValid"]]
    provenance["validFencedCandidateCount"] = len(valid)
    provenance["completeFences"] = [row[1] for row in blocks]
    if len(valid) > 1:
        provenance.update(
            status="ambiguous",
            reason="multiple valid fenced verdict candidates in final assistant message",
        )
        return {}, "invalid", provenance
    if not blocks or not blocks[-1][1]["schemaValid"]:
        provenance["reason"] = (
            "last complete json fence is absent or violates the unchanged verdict schema"
        )
        return {}, "invalid", provenance
    value, positions = blocks[-1]
    assert isinstance(value, dict)
    path = (
        "whole_response_json_fence"
        if text.strip().startswith("```json") and text.strip().endswith("```") and len(blocks) == 1
        else "final_message_json_fence"
    )
    provenance.update(positions)
    provenance.update(status="decoded", reason=None, parsePath=path)
    return value, path, provenance


def parse_native_verdict(capture: NativeCapture) -> tuple[dict, str]:
    """Decode raw JSON or the unique valid last complete fence in the final message."""
    verdict, path, _ = _decode_native_verdict(capture)
    return verdict, path


def verdict_parse_provenance(capture: NativeCapture) -> dict:
    """Retain the judge's prose and exact selected fence offsets without another parser."""
    return _decode_native_verdict(capture)[2]

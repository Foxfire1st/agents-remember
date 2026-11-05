"""Read-only admission of case trees through the serving writer's validation rules."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_writer import writer
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory_quality.knowledge_validator import validator
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodePathSet,
    KnowledgeTree,
    code_tree_from_working_directory,
    is_knowledge_path,
    knowledge_tree_from_directory,
)
from agents_remember.models.knowledge_files.sidecars import FileSidecar

from . import validation as validation_module
from .artifacts import child_path, read_object, write_object
from .fixtures import CASE_IDS, NATIVE_LAYOUT_BYTES, CaseBundle
from .trial_native_setup import configured_intake
from .validation import validation_snapshot


@dataclass(frozen=True)
class NativeIntakeBinding:
    config: McpRuntimeConfig
    leaf: dict[str, Any] | None
    output_root: Path


def _trees(case: CaseBundle) -> tuple[KnowledgeTree, CodePathSet]:
    """Use only actor-visible, hash-verified input destinations, never desired-after bytes."""
    memory: dict[str, bytes] = {}
    code: dict[str, bytes] = {}
    destinations: set[str] = set()
    declared = {item.path: item for item in case.files}
    for item in case.manifest["actorInputs"]:
        destination = item["destination"]
        child_path(case.path, destination)  # Check the same relative-path boundary as reset.
        if destination in destinations:
            raise AuthorityError(f"duplicate actor input destination: {destination}")
        destinations.add(destination)
        source = declared[item["source"]].verify(case.path)
        if destination.startswith("projects/memory/"):
            relative = destination.removeprefix("projects/memory/")
            if is_knowledge_path(relative):
                memory[relative] = source.read_bytes()
        elif destination.startswith("projects/code/"):
            relative = destination.removeprefix("projects/code/")
            code[relative] = source.read_bytes()
    # Match existing native provisioning, which supplies this marker before baseline commit.
    memory.setdefault("knowledge/layout.json", NATIVE_LAYOUT_BYTES)
    tree = KnowledgeTree(label=case.case_id + " actor input", files=memory)
    return tree, CodePathSet(
        label=case.case_id + " actor code", paths=frozenset(code), contents=code
    )


def _subject_bindings(
    case: CaseBundle, tree: KnowledgeTree, code: CodePathSet
) -> list[dict[str, Any]]:
    """A missing memory card does not establish that its code subject is missing."""
    parsed = parse_tree(tree)
    queue_path = child_path(case.path, case.manifest["setup"]["queue"])
    queue = read_object(queue_path)
    absent = {
        item["source"] for item in queue.get("items", ()) if item.get("currentSourceAbsent") is True
    }
    bindings = []
    for item in parsed.sidecars:
        if not isinstance(item.sidecar, FileSidecar) or item.markdown_path in parsed.markdown:
            continue
        subject = item.sidecar.path
        data = code.read(subject)
        bindings.append(
            {
                "sidecar": item.path,
                "subject": subject,
                "status": (
                    "resolved"
                    if data is not None
                    else "resolved-absent"
                    if subject in absent
                    else "binding-unavailable"
                ),
                "absenceAuthority": (
                    {
                        "path": str(queue_path),
                        "sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest(),
                    }
                    if data is None and subject in absent
                    else None
                ),
                "source": "current paired code tree",
                "sha256": None if data is None else hashlib.sha256(data).hexdigest(),
            }
        )
    return bindings


def preflight_case(case: CaseBundle) -> dict[str, Any]:
    case.require_admitted()
    tree, code = _trees(case)
    return _validate(case, tree, code)


def preflight_project(
    case: CaseBundle,
    memory_root: Path,
    code_root: Path,
    *,
    intake: NativeIntakeBinding | None = None,
) -> dict[str, Any]:
    """Validate the exact native trial copy after its baseline commit, before host or actor."""
    tree = knowledge_tree_from_directory(memory_root)
    paired = code_tree_from_working_directory(code_root)
    contents = {path: (code_root / path).read_bytes() for path in sorted(paired.paths)}
    code = CodePathSet(label=paired.label, paths=paired.paths, contents=contents)
    result = {
        **_validate(case, tree, code),
        "materializedMemoryRoot": str(memory_root),
        "materializedCodeRoot": str(code_root),
        "nativeIntake": {"status": "not-configured"},
    }
    result["treeStatus"] = result["status"]
    if intake is not None:
        native = configured_intake(intake.config, intake.leaf, intake.output_root)
        result["nativeIntake"] = native
        if not native["intakeAccepted"] or native["missingSubjects"]:
            result.update(
                status="unknown", admissionStatus="unknown", unknownClass="binding-unavailable"
            )
    return result


def _validate(case: CaseBundle, tree: KnowledgeTree, code: CodePathSet) -> dict[str, Any]:
    snapshot = validation_snapshot(tree, code, base=tree)
    blocking, reported = snapshot["blockingViolations"], snapshot["reportedViolations"]
    bindings = _subject_bindings(case, tree, code)
    external = {item["sidecar"] for item in bindings}
    unavailable = [item for item in bindings if item["status"] == "binding-unavailable"]

    # Manager 20261030T000647Z: source-only sidecars resolve from their subject in code.
    # Preserve raw writer diagnostics; reclassify only the missing-memory-card premise.
    def memory_card_premise(item: Any) -> bool:
        return item["path"] in external and (
            item["rule"] == "R22.3-sidecar-without-markdown"
            or (
                item["rule"] == "R22.3-markers"
                and item["message"].startswith("a file sidecar without Markdown")
            )
        )

    resolved_absences = {
        item["sidecar"] for item in bindings if item["status"] == "resolved-absent"
    }
    inherited = [
        {
            **item,
            "beforeSha256": snapshot["memoryFileHashes"][item["path"]],
            "afterSha256": snapshot["memoryFileHashes"][item["path"]],
        }
        for item in blocking
        if item["path"] in resolved_absences and memory_card_premise(item)
    ]
    effective_blocking = [item for item in blocking if not memory_card_premise(item)]
    admission = "fail" if effective_blocking else "unknown" if unavailable else "pass"
    # Manager 20261030T002450Z: authorized base restoration can remove these findings.
    # Initial native refusal is evidence, not an unreachable-outcome inference.
    assert code.contents is not None
    return {
        "case": case.case_id,
        "caseManifestSha256": hashlib.sha256((case.path / "case.json").read_bytes()).hexdigest(),
        "status": admission,
        "admissionStatus": admission,
        "nativeWriterRefusesStartingTree": bool(inherited),
        "admittedPreExistingViolations": inherited,
        "basePolicy": "exact actor input tree, as committed by native fixture provisioning",
        "materialization": "native layout marker if absent; writer's mechanical dependent lookup rebuilt in memory only",
        "memoryInputSha256": snapshot["memoryInputSha256"],
        "codeInputSha256": snapshot["codeInputSha256"],
        "blockingViolations": effective_blocking,
        "reportedViolations": [item for item in reported if not memory_card_premise(item)],
        "rawWriterBlockingViolations": blocking,
        "subjectBindings": bindings,
        "bindingUnavailable": unavailable,
    }


def preflight_cases(root: Path, case_ids: tuple[str, ...] = CASE_IDS) -> dict[str, Any]:
    """Report every requested case, including unreadable or invalid cases, without short-circuiting."""
    results = []
    for case_id in case_ids:
        path = root if (root / "case.json").is_file() else root / case_id
        try:
            case = CaseBundle.read(path)
            if case.case_id != case_id:
                raise AuthorityError(f"expected {case_id}, found {case.case_id}")
            results.append(preflight_case(case))
        except (OSError, ValueError, KeyError, TypeError, AuthorityError) as error:
            results.append(
                {
                    "case": case_id,
                    "status": "invalid",
                    "blockingViolations": [],
                    "reportedViolations": [],
                    "cause": f"{type(error).__name__}: {error}",
                }
            )
    passed = sum(item["status"] == "pass" for item in results)
    sources = [
        Path(__file__),
        Path(validation_module.__file__),
        Path(writer.__file__),
        *sorted(Path(validator.__file__).parent.glob("*.py")),
    ]
    return {
        "schema": "ar-curator-case-preflight/v1",
        "caseRoot": str(root.resolve()),
        "actor": False,
        "judgeInvocationsThisRun": 0,
        "validatorSources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "pass": passed == len(case_ids),
        "counts": {
            "cases": len(results),
            "pass": passed,
            "blocked": len(results) - passed,
            "unknown": sum(item["status"] == "unknown" for item in results),
        },
        "cases": results,
    }


def write_preflight(
    root: Path, output: Path, case_ids: tuple[str, ...] = CASE_IDS
) -> dict[str, Any]:
    report = preflight_cases(root, case_ids)
    write_object(output, report)
    return report

"""One reset, native execution and evidence capture; measurements belong to the Architect."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .adapters.execution import NativeExecution
from .adapters.models import NativeCapture
from .adapters.usage import native_usage
from .artifacts import child_path, manifest_files, write_object
from .fixtures import CaseBundle, TrialFixture, release_fixture, reset_case


@dataclass(frozen=True)
class TrialPaths:
    fixtures: Path
    evidence: Path


@dataclass(frozen=True)
class TrialRequest:
    case: CaseBundle
    paths: TrialPaths
    prompt: str


@dataclass(frozen=True)
class TrialHooks:
    """Native driver seams: probes inspect settings; capture reads actual native records."""

    prepare: Callable[[TrialFixture], None]
    execute: Callable[[TrialFixture, str], NativeExecution]
    capture: Callable[[TrialFixture], NativeCapture]
    host_count: Callable[[], int]
    cleanup: Callable[[TrialFixture], None]


def _save_effects(
    fixture: TrialFixture, case: CaseBundle, destination: Path
) -> list[dict[str, str]]:
    inventory = []
    for name in case.manifest["effectRoots"]:
        effect_root = child_path(fixture.actor_root, name)
        for item in manifest_files(effect_root):
            source = item.verify(effect_root)
            relative = name + "/" + item.path
            target = child_path(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            inventory.append({"path": relative, "sha256": item.sha256})
    return inventory


def run_trial(request: TrialRequest, hooks: TrialHooks) -> Path:
    """Keep raw attempts, unknowns, trees and host observations; never self-grade success."""
    case = request.case
    fixture = reset_case(case, request.paths.fixtures)
    evidence = request.paths.evidence / fixture.identity
    evidence.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "schema": "ar-agent-eval-native-attempt/v1",
        "case": case.case_id,
        "identity": fixture.identity,
        "caseDigest": fixture.case_digest,
        "unknowns": [],
        "hostCounts": {},
    }
    try:
        shutil.copytree(fixture.case_copy, evidence / "case")
        hooks.prepare(fixture)
        report["hostCounts"]["beforeStart"] = hooks.host_count()
        report["before"] = _save_effects(fixture, case, evidence / "before")
        execution = hooks.execute(fixture, request.prompt)
        report["hostCounts"]["afterExecution"] = hooks.host_count()
        report["returncode"] = execution.returncode
        report["unknowns"].extend(execution.unknowns)
        for source in (execution.stdout_path, execution.stderr_path):
            shutil.copyfile(source, evidence / source.name)
        capture = hooks.capture(fixture)
        report["unknowns"].extend(capture.unknowns)
        report["installedServerCalls"] = len(capture.installed_server_calls)
        report["capture"] = capture.to_dict()
        report["actorUsage"] = native_usage(capture)
        report["after"] = _save_effects(fixture, case, evidence / "after")
        # A native exit status does not attest the curation result. Graders inspect these files.
        report["status"] = "unknown" if report["unknowns"] else "captured-ungraded"
    except (OSError, ValueError, KeyError) as error:
        report["status"] = "unknown"
        report["unknowns"].append(f"{type(error).__name__}: {error}")
    finally:
        try:
            hooks.cleanup(fixture)
            report["hostCounts"]["end"] = hooks.host_count()
            if report["hostCounts"].get("beforeStart") != report["hostCounts"]["end"]:
                report["unknowns"].append("host agent count did not return to its initial value")
                report["status"] = "unknown"
            release_fixture(fixture)
        except (OSError, ValueError, KeyError) as error:
            report["status"] = "unknown"
            report["unknowns"].append(f"cleanup: {type(error).__name__}: {error}")
        finally:
            write_object(evidence / "attempt.json", report)
    return evidence / "attempt.json"

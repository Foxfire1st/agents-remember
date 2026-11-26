from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from agents_remember.benchmarks.runner_modules.models import BenchmarkCase


def manifest_relative_path(value: object, label: str) -> Path:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string path")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} must not be empty")

    normalized = text.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(text)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError(f"{label} must be a relative path: {text}")
    if any(part == ".." for part in posix_path.parts):
        raise ValueError(f"{label} must not contain '..': {text}")
    if any(part in {"", "."} for part in posix_path.parts):
        raise ValueError(f"{label} contains an unsupported path segment: {text}")
    return Path(*posix_path.parts)


def manifest_path_component(value: object, label: str) -> str:
    path = manifest_relative_path(value, label)
    if len(path.parts) != 1:
        raise ValueError(f"{label} must be a single path component: {value}")
    return path.parts[0]


def validate_case_manifest(case: BenchmarkCase) -> None:
    validate_workspace_paths(case)
    manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    if case.memory_repository.get("name"):
        manifest_path_component(
            case.memory_repository["name"], f"{case.case_id}.memoryRepository.name"
        )
    # Any legacy optional "providers" key on a case or variant is ignored.
    validate_prompt_manifests(case)


def validate_workspace_paths(case: BenchmarkCase) -> None:
    workspace = case.workspace
    manifest_relative_path(workspace["fixturePath"], f"{case.case_id}.workspace.fixturePath")
    for key in (
        "sourceOnlyRoot",
        "withMemoryRoot",
        "withOnboardingRoot",
        "repoRelativePath",
        "coordinationRoot",
        "withOnboardingCoordinationRoot",
    ):
        if key in workspace:
            manifest_relative_path(workspace[key], f"{case.case_id}.workspace.{key}")


def validate_prompt_manifests(case: BenchmarkCase) -> None:
    for prompt in case.prompts:
        prompt_id = prompt.get("id", "<unknown>")
        for variant in prompt.get("variants", []):
            validate_prompt_variant(case, prompt_id, variant)


def validate_prompt_variant(
    case: BenchmarkCase,
    prompt_id: object,
    variant: dict[str, Any],
) -> None:
    variant_id = variant.get("id", "<unknown>")
    manifest_relative_path(
        variant["promptPath"], f"{case.case_id}.{prompt_id}.{variant_id}.promptPath"
    )
    manifest_relative_path(variant["cwd"], f"{case.case_id}.{prompt_id}.{variant_id}.cwd")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def load_cases(benchmarks_root: Path) -> list[BenchmarkCase]:
    cases_root = benchmarks_root / "cases"
    if not cases_root.is_dir():
        raise RuntimeError(f"benchmark cases directory not found: {cases_root}")

    cases: list[BenchmarkCase] = []
    for manifest in sorted(cases_root.glob("*/case.json")):
        case = BenchmarkCase(manifest, load_json(manifest))
        if case.data.get("schemaVersion") != 1:
            raise RuntimeError(f"unsupported benchmark schema in {manifest}")
        validate_case_manifest(case)
        cases.append(case)
    return cases


def select_cases(
    cases: list[BenchmarkCase], target: str, case_id: str | None
) -> list[BenchmarkCase]:
    if target == "all":
        return cases
    if not case_id:
        raise RuntimeError("case id is required when target is 'case'")
    for case in cases:
        if case.case_id == case_id:
            return [case]
    raise RuntimeError(f"benchmark case not found: {case_id}")


def case_prompt(case: BenchmarkCase, prompt_id: str | None) -> list[dict[str, Any]]:
    prompts = case.prompts
    if prompt_id is None:
        return prompts
    selected = [prompt for prompt in prompts if prompt.get("id") == prompt_id]
    if not selected:
        raise RuntimeError(f"prompt {prompt_id!r} not found in case {case.case_id}")
    return selected


def prompt_variant(prompt: dict[str, Any], variant_id: str | None) -> list[dict[str, Any]]:
    variants = list(prompt.get("variants", []))
    if variant_id is None:
        return variants
    selected = [variant for variant in variants if variant.get("id") == variant_id]
    if not selected:
        raise RuntimeError(f"variant {variant_id!r} not found in prompt {prompt.get('id')}")
    return selected


def benchmark_stable_id(value: str) -> str:
    lowered = value.strip().lower()
    return "".join(
        character if character.isalnum() or character in "._-" else "-" for character in lowered
    ).strip(".-_")

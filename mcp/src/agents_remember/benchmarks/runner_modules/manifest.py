from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from agents_remember.benchmarks.runner_modules.constants import BENCHMARK_PROVIDER_IDS
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


def parse_benchmark_provider_ids(
    raw: object,
    label: str,
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if raw is None:
        return default
    if raw is False:
        return ()
    if isinstance(raw, str) and raw == "all":
        return BENCHMARK_PROVIDER_IDS
    if isinstance(raw, list):
        return unique_benchmark_provider_ids(raw, label)
    raise ValueError(f"{label} must be an array of provider ids, false, or 'all'")


def unique_benchmark_provider_ids(raw: list[object], label: str) -> tuple[str, ...]:
    provider_ids: list[str] = []
    for value in raw:
        provider_id = require_benchmark_provider_id(value, label)
        if provider_id not in provider_ids:
            provider_ids.append(provider_id)
    return tuple(provider_ids)


def require_benchmark_provider_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} entries must be non-empty provider ids")
    if value not in BENCHMARK_PROVIDER_IDS:
        raise ValueError(
            f"{label} contains unsupported provider id {value!r}; "
            f"supported ids: {', '.join(BENCHMARK_PROVIDER_IDS)}"
        )
    return value


def case_default_provider_ids(case: BenchmarkCase) -> tuple[str, ...]:
    return parse_benchmark_provider_ids(
        case.data.get("providers"),
        f"{case.case_id}.providers",
    )


def variant_provider_ids(
    case: BenchmarkCase,
    variant: dict[str, Any],
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    variant_id = str(variant.get("id", "<unknown>"))
    if "providers" in variant:
        return parse_benchmark_provider_ids(
            variant.get("providers"),
            f"{case.case_id}.{variant_id}.providers",
        )
    if variant.get("allowMemory") is True:
        return default
    return ()


def selected_provider_ids(
    case: BenchmarkCase,
    *,
    prompt_id: str | None = None,
    variant_id: str | None = None,
) -> tuple[str, ...]:
    case_default = case_default_provider_ids(case)
    provider_ids: list[str] = []
    for prompt in case_prompt(case, prompt_id):
        for variant in prompt_variant(prompt, variant_id):
            for provider_id in variant_provider_ids(case, variant, default=case_default):
                if provider_id not in provider_ids:
                    provider_ids.append(provider_id)
    return tuple(provider_ids)


def validate_case_manifest(case: BenchmarkCase) -> None:
    validate_workspace_paths(case)
    manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    if case.memory_repository.get("name"):
        manifest_path_component(
            case.memory_repository["name"], f"{case.case_id}.memoryRepository.name"
        )
    validate_prompt_manifests(case, case_default_provider_ids(case))


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


def validate_prompt_manifests(
    case: BenchmarkCase,
    default_providers: tuple[str, ...],
) -> None:
    for prompt in case.prompts:
        prompt_id = prompt.get("id", "<unknown>")
        for variant in prompt.get("variants", []):
            validate_prompt_variant(case, prompt_id, variant, default_providers)


def validate_prompt_variant(
    case: BenchmarkCase,
    prompt_id: object,
    variant: dict[str, Any],
    default_providers: tuple[str, ...],
) -> None:
    variant_id = variant.get("id", "<unknown>")
    manifest_relative_path(
        variant["promptPath"], f"{case.case_id}.{prompt_id}.{variant_id}.promptPath"
    )
    manifest_relative_path(variant["cwd"], f"{case.case_id}.{prompt_id}.{variant_id}.cwd")
    variant_provider_ids(case, variant, default=default_providers)


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

#!/usr/bin/env python3
"""Run and analyze Agents Remember benchmark cases."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from agents_remember.install.assets import long_path, packaged_source_root
from agents_remember.mcp.config import (
    McpRuntimeConfig,
    ProviderScope,
    RepositoryScope,
    provider_runtime_name,
)
from agents_remember.providers import provider_setup
from agents_remember.providers.settings import lifecycle_settings_from_config

AGENTS_MD_TARGETS = {
    Path("runtime/agents-md-files/coordinator/AGENTS.md"): Path("AGENTS.md"),
    Path("runtime/agents-md-files/system/AGENTS.md"): Path("system/AGENTS.md"),
    Path("runtime/agents-md-files/skills/AGENTS.md"): Path("skills/AGENTS.md"),
    Path("runtime/agents-md-files/tasks/AGENTS.md"): Path("tasks/AGENTS.md"),
}
PROVIDER_ASSET_DIRS = (Path("requirements"), Path("patches"))
BENCHMARK_PROVIDER_IDS = ("grepai-memory", "codegraphcontext-code")

TOKEN_KEYS = {
    "input_tokens": "input_tokens",
    "inputTokens": "input_tokens",
    "total_input_tokens": "input_tokens",
    "totalInputTokens": "input_tokens",
    "fresh_input_tokens": "fresh_input_tokens",
    "freshInputTokens": "fresh_input_tokens",
    "output_tokens": "output_tokens",
    "outputTokens": "output_tokens",
    "total_output_tokens": "output_tokens",
    "totalOutputTokens": "output_tokens",
    "reasoning_tokens": "reasoning_tokens",
    "reasoningTokens": "reasoning_tokens",
}

WORKSPACE_AGENTS_TEMPLATE = Path("templates/workspace-AGENTS.md")
SOURCE_ONLY_AGENTS_TEMPLATE = Path("templates/source-only-AGENTS.md")
BENCHMARK_ROOT_MARKER = ".benchmark-root"
SKILLS_EXPOSURE_NAMESPACE = "agents-remember-md"
CODEX_HARNESS_DIR = ".codex"
COPYTREE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
SKILL_EXPOSURE_MODES = ("copy", "none")
CODEX_EXECUTABLE_NAME = "codex"
CODEX_EXECUTABLE_RESOLUTION = "PATH"
CODEX_SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"
CODEX_SANDBOX_DEFAULT = "default"
CODEX_BENCHMARK_SANDBOX = CODEX_SANDBOX_DANGER_FULL_ACCESS
CODEX_BENCHMARK_SANDBOX_MODES = (CODEX_SANDBOX_DANGER_FULL_ACCESS, CODEX_SANDBOX_DEFAULT)
CODEX_BENCHMARK_SCOPE = "codex-benchmark-only"
BENCHMARK_MCP_SERVER_NAME = "agents_remember_benchmark"
BENCHMARK_MCP_SETTINGS_NAME = "agents-remember-benchmark.settings.json"
BENCHMARK_MCP_STARTUP_TIMEOUT_SECONDS = 120
BENCHMARK_MCP_SOURCE_ENV = "AGENTS_REMEMBER_BENCHMARK_MCP_SRC"


def is_ignored_package_path(relative: Path) -> bool:
    return "__pycache__" in relative.parts or relative.suffix in {".pyc", ".pyo"}


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
    manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    if case.memory_repository.get("name"):
        manifest_path_component(
            case.memory_repository["name"], f"{case.case_id}.memoryRepository.name"
        )
    default_providers = case_default_provider_ids(case)
    for prompt in case.prompts:
        prompt_id = prompt.get("id", "<unknown>")
        for variant in prompt.get("variants", []):
            variant_id = variant.get("id", "<unknown>")
            manifest_relative_path(
                variant["promptPath"], f"{case.case_id}.{prompt_id}.{variant_id}.promptPath"
            )
            manifest_relative_path(variant["cwd"], f"{case.case_id}.{prompt_id}.{variant_id}.cwd")
            variant_provider_ids(case, variant, default=default_providers)


@dataclass(frozen=True)
class BenchmarkCase:
    path: Path
    data: dict[str, Any]

    @property
    def case_id(self) -> str:
        return str(self.data["id"])

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.case_id))

    @property
    def repository(self) -> dict[str, Any]:
        return dict(self.data["repository"])

    @property
    def memory_repository(self) -> dict[str, Any]:
        return dict(self.data.get("memoryRepository", {}))

    @property
    def workspace(self) -> dict[str, Any]:
        return dict(self.data["workspace"])

    @property
    def prompts(self) -> list[dict[str, Any]]:
        return list(self.data.get("prompts", []))


@dataclass(frozen=True)
class BenchmarkPrepareRequest:
    benchmarks_root: Path
    target: str = "all"
    case_id: str | None = None
    dry_run: bool = True
    skill_exposure_mode: str = "copy"
    force_clone: bool = False
    provider_timeout: int = 1800


@dataclass(frozen=True)
class BenchmarkRunRequest:
    benchmarks_root: Path
    target: str = "all"
    case_id: str | None = None
    prompt: str | None = None
    variant: str | None = None
    repetitions: int | None = None
    jobs: int | None = None
    dry_run: bool = True
    skip_prepare: bool = False
    skill_exposure_mode: str = "copy"
    force_clone: bool = False
    provider_timeout: int = 1800
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX


@contextlib.contextmanager
def benchmark_root_context(benchmarks_root: Path | None) -> Iterator[Path]:
    if benchmarks_root is not None:
        yield benchmarks_root
        return

    with packaged_source_root() as source_root:
        yield source_root / "benchmarks"


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


def copy_file(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would copy {source} -> {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(long_path(source), long_path(destination))


def replace_tree(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would replace {destination} from {source}")
        return
    if destination.exists() or destination.is_symlink():
        remove_path(destination)
    shutil.copytree(long_path(source), long_path(destination), ignore=COPYTREE_IGNORE)


def copy_tree(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would copy tree {source} -> {destination}")
        return
    destination.mkdir(parents=True, exist_ok=True)
    scan_root = long_path(source)
    for child in sorted(scan_root.rglob("*")):
        relative = child.relative_to(scan_root)
        if is_ignored_package_path(relative):
            continue
        target = destination / relative
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif child.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(long_path(child), long_path(target))


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def is_windows_directory_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    return sys.platform == "win32" and path.is_dir() and (path.is_symlink() or is_junction())


def absolute_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


def removable_path(path: Path, *, resolve: bool = True) -> Path:
    if sys.platform != "win32":
        return path

    resolved = path.resolve() if resolve else absolute_path(path)
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        return resolved
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text.lstrip("\\"))
    return Path("\\\\?\\" + text)


def remove_readonly(function, path: str, exc_info) -> None:
    error = exc_info[1]
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)


def unlink_file(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def remove_directory_link(path: Path) -> None:
    try:
        path.rmdir()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.rmdir()


def remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    path_is_link = path.is_symlink() or getattr(path, "is_junction", lambda: False)()
    target = removable_path(path, resolve=not path_is_link)
    if is_windows_directory_link(path):
        remove_directory_link(target)
    elif path.is_dir() and not path_is_link:
        shutil.rmtree(target, onerror=remove_readonly)
    else:
        unlink_file(target)


def sync_runtime_assets(coordination_root: Path, dry_run: bool) -> None:
    with packaged_source_root() as source_root:
        if dry_run:
            print(f"Would ensure directory {coordination_root}")
        else:
            coordination_root.mkdir(parents=True, exist_ok=True)
        runtime_root = source_root / "runtime"
        replace_tree(runtime_root / "skills", coordination_root / "skills", dry_run)
        for source_rel, target_rel in AGENTS_MD_TARGETS.items():
            copy_file(source_root / source_rel, coordination_root / target_rel, dry_run)

        sync_provider_assets(source_root, coordination_root, dry_run)

    for folder in (
        "memory-repos",
        "tasks",
        "worktrees",
        "notes",
        "temp",
        "providers/data",
        "providers/logs",
        "providers/runners",
    ):
        path = coordination_root / folder
        if dry_run:
            print(f"Would ensure directory {path}")
        else:
            path.mkdir(parents=True, exist_ok=True)


def sync_provider_assets(root: Path, coordination_root: Path, dry_run: bool) -> None:
    providers_source = root / "runtime" / "providers"
    providers_target = coordination_root / "providers"
    if dry_run:
        print(f"Would replace benchmark provider assets in {providers_target}")
    else:
        remove_path(providers_target)
        providers_target.mkdir(parents=True, exist_ok=True)

    for relative in PROVIDER_ASSET_DIRS:
        source = providers_source / relative
        if source.exists():
            copy_tree(source, providers_target / relative, dry_run)


def copy_workspace_skill_exposure(
    workspace_root: Path, coordination_root: Path, dry_run: bool
) -> None:
    install_root = workspace_root / CODEX_HARNESS_DIR / "skills"
    exposure_path = install_root / SKILLS_EXPOSURE_NAMESPACE
    skills_source = coordination_root / "skills"
    if dry_run:
        print(f"Would copy benchmark skills {skills_source} -> {exposure_path}")
        return
    if not skills_source.is_dir():
        raise RuntimeError(f"benchmark runtime skills directory missing: {skills_source}")
    if exposure_path.exists() or exposure_path.is_symlink():
        remove_path(exposure_path)
    exposure_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skills_source, exposure_path, ignore=COPYTREE_IGNORE)


def sync_workspace_skill_exposure(
    workspace_root: Path,
    coordination_root: Path,
    dry_run: bool,
    mode: str = "copy",
) -> None:
    if mode not in SKILL_EXPOSURE_MODES:
        raise RuntimeError(f"unsupported skill exposure mode: {mode}")
    if mode == "none":
        print(f"Skipping benchmark skill exposure for {workspace_root}")
        return
    copy_workspace_skill_exposure(workspace_root, coordination_root, dry_run)


def benchmark_mcp_settings_path(workspace_root: Path) -> Path:
    return workspace_root / CODEX_HARNESS_DIR / "mcp" / BENCHMARK_MCP_SETTINGS_NAME


def benchmark_agents_config_path(workspace_root: Path) -> Path:
    return workspace_root / CODEX_HARNESS_DIR / "config.toml"


def benchmark_mcp_source_path() -> Path | None:
    value = os.environ.get(BENCHMARK_MCP_SOURCE_ENV)
    if not value:
        return None

    source_path = Path(value).expanduser().resolve()
    if not source_path.is_dir():
        raise RuntimeError(
            f"{BENCHMARK_MCP_SOURCE_ENV} must point to an existing mcp/src directory: "
            f"{source_path}"
        )
    return source_path


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def benchmark_mcp_settings_payload(
    *,
    repo_id: str,
    coordination_root: Path,
    workspace_root: Path,
    provider_ids: tuple[str, ...],
    provider_timeout: int,
) -> dict[str, Any]:
    return {
        "version": 1,
        "coordinationRoot": coordination_root.resolve().as_posix(),
        "workspaceRoot": workspace_root.resolve().as_posix(),
        "repositories": {repo_id: {}},
        "providers": {provider_id: {} for provider_id in provider_ids},
        "timeoutCaps": {
            "toolSeconds": 30,
            "providerSeconds": provider_timeout,
        },
    }


def benchmark_agents_config_text(settings_path: Path) -> str:
    lines = [
        f"[mcp_servers.{BENCHMARK_MCP_SERVER_NAME}]",
        f"command = {toml_string(Path(sys.executable).resolve())}",
        "args = [",
        '  "-m",',
        '  "agents_remember.mcp",',
        '  "--config",',
        f"  {toml_string(settings_path.resolve())},",
        "]",
        f"startup_timeout_sec = {BENCHMARK_MCP_STARTUP_TIMEOUT_SECONDS}",
        "",
        f"[mcp_servers.{BENCHMARK_MCP_SERVER_NAME}.env]",
        'PYTHONIOENCODING = "utf-8"',
        'PYTHONUTF8 = "1"',
    ]
    source_path = benchmark_mcp_source_path()
    if source_path is not None:
        lines.append(f"PYTHONPATH = {toml_string(source_path)}")
    lines.append("")
    return "\n".join(lines)


def write_benchmark_mcp_registration(
    *,
    case: BenchmarkCase,
    workspace_root: Path,
    coordination_root: Path,
    source_repo_root: Path,
    memory_repo: Path,
    provider_ids: tuple[str, ...],
    provider_timeout: int,
    dry_run: bool,
) -> tuple[Path, Path]:
    repo_id = manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    expected_memory_repo = coordination_root / "memory-repos" / f"ar-{repo_id}"
    if source_repo_root.name != repo_id:
        raise RuntimeError(
            "benchmark MCP registration requires the source repository directory "
            f"to match the repository id {repo_id!r}: {source_repo_root}"
        )
    if memory_repo.resolve() != expected_memory_repo.resolve():
        raise RuntimeError(
            "benchmark MCP registration requires memory repository "
            f"{expected_memory_repo}; got {memory_repo}"
        )

    mcp_settings_path = benchmark_mcp_settings_path(workspace_root)
    agents_config_path = benchmark_agents_config_path(workspace_root)
    settings_payload = benchmark_mcp_settings_payload(
        repo_id=repo_id,
        coordination_root=coordination_root,
        workspace_root=source_repo_root.parent,
        provider_ids=provider_ids,
        provider_timeout=provider_timeout,
    )
    if dry_run:
        print(f"Would write benchmark MCP settings {mcp_settings_path}")
        print(f"Would write benchmark Codex MCP registration {agents_config_path}")
        return mcp_settings_path, agents_config_path

    mcp_settings_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_settings_path.write_text(
        json.dumps(settings_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    agents_config_path.parent.mkdir(parents=True, exist_ok=True)
    agents_config_path.write_text(
        benchmark_agents_config_text(mcp_settings_path),
        encoding="utf-8",
    )
    return mcp_settings_path, agents_config_path


def run_command(command: list[str], dry_run: bool, cwd: Path | None = None) -> None:
    printable = " ".join(command)
    if dry_run:
        location = f" in {cwd}" if cwd else ""
        print(f"Would run{location}: {printable}")
        return
    subprocess.run(command, cwd=cwd, check=True)


def default_cgc_seed_source_coordination_root(
    benchmarks_root: Path, target_coordination_root: Path
) -> Path | None:
    candidates: list[Path] = [benchmarks_root.parent]

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or resolved == target_coordination_root.resolve():
            continue
        seen.add(resolved)
        if (resolved / "providers" / "runners" / "codegraphcontext").is_dir():
            return resolved
    return None


def benchmark_mcp_config(
    *,
    case: BenchmarkCase,
    coordination_root: Path,
    source_repo_root: Path,
    memory_repo: Path,
    provider_ids: tuple[str, ...],
) -> McpRuntimeConfig:
    repo_id = manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    providers = {
        provider_id: ProviderScope(
            provider_id=provider_id,
            runtime_root=coordination_root
            / "providers"
            / "runners"
            / provider_runtime_name(provider_id),
            log_root=coordination_root / "providers" / "logs" / provider_runtime_name(provider_id),
        )
        for provider_id in provider_ids
    }
    return McpRuntimeConfig(
        config_path=coordination_root / ".benchmark-mcp-settings.generated.json",
        coordination_root=coordination_root.resolve(),
        workspace_root=coordination_root.parent.resolve(),
        transcript_root=coordination_root / "providers" / "logs" / "mcp",
        repositories={
            repo_id: RepositoryScope(
                repo_id=repo_id,
                path=source_repo_root.resolve(),
                memory_root=memory_repo.resolve(),
            )
        },
        providers=providers,
        timeout_caps={},
    )


def benchmark_lifecycle_settings(
    *,
    case: BenchmarkCase,
    coordination_root: Path,
    source_repo_root: Path,
    memory_repo: Path,
    provider_ids: tuple[str, ...],
) -> dict[str, Any]:
    config = benchmark_mcp_config(
        case=case,
        coordination_root=coordination_root,
        source_repo_root=source_repo_root,
        memory_repo=memory_repo,
        provider_ids=provider_ids,
    )
    settings = lifecycle_settings_from_config(config)
    providers = settings["contextProviders"]["providers"]
    case_id = benchmark_stable_id(case.case_id)
    grepai = providers.get("grepai-memory")
    if isinstance(grepai, dict) and isinstance(grepai.get("backend"), dict):
        grepai["backend"]["containerName"] = f"ar-grepai-postgres-bench-{case_id}"
    cgc = providers.get("codegraphcontext-code")
    if isinstance(cgc, dict) and isinstance(cgc.get("backend"), dict):
        cgc["backend"]["containerName"] = f"ar-cgc-falkordb-bench-{case_id}"
    return settings


def write_temp_provider_settings(settings: dict[str, Any]) -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        suffix="-agents-remember-benchmark-provider-settings.json",
    ) as handle:
        path = Path(handle.name)
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    return path


def prepare_configured_providers(
    case: BenchmarkCase,
    coordination_root: Path,
    source_repo_root: Path,
    memory_repo: Path,
    dry_run: bool,
    provider_timeout: int,
    *,
    provider_ids: tuple[str, ...],
    cgc_seed_source_coordination_root: Path | None,
    cgc_seed_repo_id: str,
) -> None:
    if not provider_ids:
        if dry_run:
            print(f"Would skip benchmark provider setup for {case.case_id}; no variants request providers")
        return

    settings = benchmark_lifecycle_settings(
        case=case,
        coordination_root=coordination_root,
        source_repo_root=source_repo_root,
        memory_repo=memory_repo,
        provider_ids=provider_ids,
    )
    settings_path = write_temp_provider_settings(settings)
    if dry_run:
        print(
            "Would run provider setup service for "
            f"{coordination_root} with generated settings {settings_path}"
        )
    try:
        cgc_seed = provider_setup.CgcSeedOptions()
        if (
            "codegraphcontext-code" in provider_ids
            and cgc_seed_source_coordination_root is not None
        ):
            cgc_seed = provider_setup.CgcSeedOptions(
                source_coordination_root=cgc_seed_source_coordination_root,
                repo_id=cgc_seed_repo_id,
            )
        payload = provider_setup.run_provider_setup(
            provider_setup.ProviderSetupRequest(
                action="prepare",
                coordination_root=coordination_root,
                settings_path=settings_path,
                timeout=provider_timeout,
                dry_run=dry_run,
                cgc_seed=cgc_seed,
            )
        )
    finally:
        with contextlib.suppress(OSError):
            settings_path.unlink()
    if not payload.get("ok"):
        raise RuntimeError(json.dumps(payload, indent=2))


def git_command(*args: str) -> list[str]:
    return ["git", "-c", "core.longpaths=true", "-c", "safe.directory=*", *args]


def repo_has_commit(repo_root: Path, commit: str) -> bool:
    result = subprocess.run(
        git_command("-C", str(repo_root), "cat-file", "-e", f"{commit}^{{commit}}"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def prepare_repo(
    repository: dict[str, Any], repo_root: Path, dry_run: bool, force_clone: bool = False
) -> None:
    url = str(repository["url"])
    commit = str(repository["commit"])
    if dry_run:
        print(f"Would ensure directory {repo_root.parent}")
    else:
        repo_root.parent.mkdir(parents=True, exist_ok=True)
    if force_clone and (repo_root.exists() or repo_root.is_symlink()):
        if dry_run:
            print(f"Would remove existing repository {repo_root}")
        else:
            remove_path(repo_root)
    existing_repo = not force_clone and (repo_root / ".git").exists()
    if not existing_repo:
        run_command(git_command("clone", url, str(repo_root)), dry_run)
    elif repo_has_commit(repo_root, commit):
        if dry_run:
            print(f"Would reuse cached repository {repo_root} at {commit}")
    else:
        run_command(git_command("-C", str(repo_root), "fetch", "--all", "--tags"), dry_run)
    run_command(git_command("-C", str(repo_root), "checkout", "--detach", commit), dry_run)
    run_command(git_command("-C", str(repo_root), "reset", "--hard", commit), dry_run)
    run_command(git_command("-C", str(repo_root), "clean", "-fdx"), dry_run)


def workspace_root(benchmarks_root: Path, case: BenchmarkCase) -> Path:
    return benchmarks_root / manifest_relative_path(
        case.workspace["fixturePath"], f"{case.case_id}.workspace.fixturePath"
    )


def source_only_workspace_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("sourceOnlyRoot")
    if configured:
        return manifest_relative_path(configured, f"{case.case_id}.workspace.sourceOnlyRoot")
    return Path("source-only")


def with_memory_workspace_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("withMemoryRoot") or case.workspace.get("withOnboardingRoot")
    if configured:
        return manifest_relative_path(configured, f"{case.case_id}.workspace.withMemoryRoot")
    return Path("with-memory")


def source_only_workspace_root(benchmarks_root: Path, case: BenchmarkCase) -> Path:
    return workspace_root(benchmarks_root, case) / source_only_workspace_path(case)


def with_memory_workspace_root(benchmarks_root: Path, case: BenchmarkCase) -> Path:
    return workspace_root(benchmarks_root, case) / with_memory_workspace_path(case)


def repository_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("repoRelativePath")
    if configured:
        return manifest_relative_path(configured, f"{case.case_id}.workspace.repoRelativePath")
    repo_name = manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    return Path("repos") / repo_name


def coordination_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("coordinationRoot") or case.workspace.get(
        "withOnboardingCoordinationRoot"
    )
    if configured:
        return manifest_relative_path(configured, f"{case.case_id}.workspace.coordinationRoot")
    return Path("ar-coordination")


def memory_repo_name(case: BenchmarkCase) -> str:
    configured = case.memory_repository.get("name") or case.workspace.get("externalMemoryRepo")
    if not configured:
        raise RuntimeError(f"memory repository name is missing for case {case.case_id}")
    return manifest_path_component(configured, f"{case.case_id}.memoryRepository.name")


def render_workspace_agents(benchmarks_root: Path, case: BenchmarkCase, dry_run: bool) -> Path:
    template_path = benchmarks_root / WORKSPACE_AGENTS_TEMPLATE
    if not template_path.is_file():
        raise RuntimeError(f"benchmark workspace template not found: {template_path}")

    repo_relative_path = repository_path(case).as_posix()
    coordination_root = coordination_path(case).as_posix()
    destination = with_memory_workspace_root(benchmarks_root, case) / "AGENTS.md"
    rendered = render_template(
        template_path.read_text(encoding="utf-8"),
        {
            "case_id": case.case_id,
            "repository_name": str(case.repository["name"]),
            "repo_relative_path": repo_relative_path,
            "coordination_root": coordination_root,
            "memory_repository_name": memory_repo_name(case),
        },
    )
    if dry_run:
        print(f"Would render {template_path} -> {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    return destination


def render_source_only_agents(benchmarks_root: Path, case: BenchmarkCase, dry_run: bool) -> Path:
    template_path = benchmarks_root / SOURCE_ONLY_AGENTS_TEMPLATE
    if not template_path.is_file():
        raise RuntimeError(f"benchmark source-only template not found: {template_path}")

    repo_relative_path = repository_path(case).as_posix()
    destination = source_only_workspace_root(benchmarks_root, case) / "AGENTS.md"
    rendered = render_template(
        template_path.read_text(encoding="utf-8"),
        {
            "case_id": case.case_id,
            "repository_name": str(case.repository["name"]),
            "repo_relative_path": repo_relative_path,
        },
    )
    if dry_run:
        print(f"Would render {template_path} -> {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    return destination


def write_benchmark_root_marker(root: Path, dry_run: bool) -> Path:
    marker = root / BENCHMARK_ROOT_MARKER
    if dry_run:
        print(f"Would write benchmark root marker {marker}")
        return marker
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("Codex benchmark project root.\n", encoding="utf-8")
    return marker


def benchmark_stable_id(value: str) -> str:
    lowered = value.strip().lower()
    return "".join(
        character if character.isalnum() or character in "._-" else "-" for character in lowered
    ).strip(".-_")


def prune_legacy_workspace_paths(root: Path, dry_run: bool) -> None:
    for relative in (Path("AGENTS.md"), Path("repos"), Path("ar-coordination")):
        path = root / relative
        if not path.exists() and not path.is_symlink():
            continue
        if dry_run:
            print(f"Would remove legacy workspace path {path}")
            continue
        remove_path(path)


def prepare_memory_repo(
    case: BenchmarkCase, coordination_root: Path, dry_run: bool, force_clone: bool = False
) -> Path:
    memory_repo = coordination_root / "memory-repos" / memory_repo_name(case)
    memory_repository = case.memory_repository
    if memory_repository:
        prepare_repo(memory_repository, memory_repo, dry_run, force_clone=force_clone)
    elif not memory_repo.exists() and not dry_run:
        raise RuntimeError(f"workspace memory repo missing after preparation: {memory_repo}")
    return memory_repo


def prepare_case(
    benchmarks_root: Path,
    case: BenchmarkCase,
    dry_run: bool,
    skill_exposure_mode: str = "copy",
    force_clone: bool = False,
    provider_timeout: int = 1800,
    provider_ids: tuple[str, ...] = (),
) -> None:
    repository = case.repository
    root = workspace_root(benchmarks_root, case)
    source_only_root = source_only_workspace_root(benchmarks_root, case)
    with_memory_root = with_memory_workspace_root(benchmarks_root, case)
    source_only_repo_root = source_only_root / repository_path(case)
    with_memory_repo_root = with_memory_root / repository_path(case)
    print(f"Preparing {case.case_id}")

    prune_legacy_workspace_paths(root, dry_run)
    render_source_only_agents(benchmarks_root, case, dry_run)
    render_workspace_agents(benchmarks_root, case, dry_run)
    write_benchmark_root_marker(source_only_root, dry_run)
    write_benchmark_root_marker(with_memory_root, dry_run)
    prepare_repo(repository, source_only_repo_root, dry_run, force_clone=force_clone)
    prepare_repo(repository, with_memory_repo_root, dry_run, force_clone=force_clone)

    coordination_root = with_memory_root / coordination_path(case)
    sync_runtime_assets(coordination_root, dry_run)
    sync_workspace_skill_exposure(with_memory_root, coordination_root, dry_run, skill_exposure_mode)
    memory_repo = prepare_memory_repo(case, coordination_root, dry_run, force_clone=force_clone)
    write_benchmark_mcp_registration(
        case=case,
        workspace_root=with_memory_root,
        coordination_root=coordination_root,
        source_repo_root=with_memory_repo_root,
        memory_repo=memory_repo,
        provider_ids=provider_ids,
        provider_timeout=provider_timeout,
        dry_run=dry_run,
    )
    prepare_configured_providers(
        case,
        coordination_root,
        with_memory_repo_root,
        memory_repo,
        dry_run,
        provider_timeout,
        provider_ids=provider_ids,
        cgc_seed_source_coordination_root=default_cgc_seed_source_coordination_root(
            benchmarks_root, coordination_root
        ),
        cgc_seed_repo_id=manifest_path_component(
            repository["name"], f"{case.case_id}.repository.name"
        ),
    )

    if dry_run:
        print(f"Would verify benchmark memory repo exists: {memory_repo}")


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


def run_id() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


class CodexExecutableNotFound(RuntimeError):
    """Raised when the Codex benchmark runner cannot resolve Codex from PATH."""


def resolve_codex_executable() -> str:
    resolved = shutil.which(CODEX_EXECUTABLE_NAME)
    if resolved:
        return resolved
    raise CodexExecutableNotFound(
        "codex executable was not found on PATH; benchmark execution resolves "
        "Codex only from the MCP server process PATH"
    )


def validate_codex_sandbox(codex_sandbox: str) -> str:
    if codex_sandbox not in CODEX_BENCHMARK_SANDBOX_MODES:
        allowed = ", ".join(CODEX_BENCHMARK_SANDBOX_MODES)
        raise ValueError(f"unsupported Codex benchmark sandbox {codex_sandbox!r}; expected {allowed}")
    return codex_sandbox


def codex_sandbox_argument(codex_sandbox: str) -> str | None:
    sandbox = validate_codex_sandbox(codex_sandbox)
    if sandbox == CODEX_SANDBOX_DEFAULT:
        return None
    return sandbox


def codex_execution_policy(
    resolved_executable: str | None = None,
    *,
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
) -> dict[str, Any]:
    sandbox = validate_codex_sandbox(codex_sandbox)
    sandbox_argument = codex_sandbox_argument(sandbox)
    policy: dict[str, Any] = {
        "scope": CODEX_BENCHMARK_SCOPE,
        "benchmarkOnly": True,
        "hostProcess": True,
        "executable": CODEX_EXECUTABLE_NAME,
        "resolution": CODEX_EXECUTABLE_RESOLUTION,
        "pathVariable": CODEX_EXECUTABLE_RESOLUTION,
        "sandbox": sandbox,
        "sandboxArgument": sandbox_argument or "omitted",
        "sandboxModes": list(CODEX_BENCHMARK_SANDBOX_MODES),
        "approvalPolicy": "never",
        "genericExecutableOverride": False,
        "arbitraryShell": False,
    }
    if resolved_executable:
        policy["resolvedExecutable"] = resolved_executable
    return policy


def codex_command(
    cwd: Path,
    final_message_path: Path,
    *,
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
) -> list[str]:
    resolved_executable = resolve_codex_executable()
    command = [
        resolved_executable,
        "exec",
        "--json",
        "--ephemeral",
        "--cd",
        str(cwd),
        "--skip-git-repo-check",
        "--output-last-message",
        str(final_message_path),
        "-c",
        'approval_policy="never"',
        "-c",
        f"project_root_markers=['{BENCHMARK_ROOT_MARKER}']",
    ]
    sandbox_argument = codex_sandbox_argument(codex_sandbox)
    if sandbox_argument is not None:
        command[7:7] = ["--sandbox", sandbox_argument]
    return command


def write_metadata(path: Path, metadata: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        print(f"Would write metadata {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_one(
    *,
    benchmarks_root: Path,
    case: BenchmarkCase,
    prompt: dict[str, Any],
    variant: dict[str, Any],
    repetition: int,
    output_root: Path,
    dry_run: bool,
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
) -> None:
    prompt_id = str(prompt["id"])
    variant_id = str(variant["id"])
    prompt_path = case.path.parent / manifest_relative_path(
        variant["promptPath"],
        f"{case.case_id}.{prompt_id}.{variant_id}.promptPath",
    )
    prompt_text = prompt_path.read_text(encoding="utf-8")
    cwd = benchmarks_root / manifest_relative_path(
        variant["cwd"],
        f"{case.case_id}.{prompt_id}.{variant_id}.cwd",
    )
    run_prefix = output_root / prompt_id / variant_id / f"run-{repetition:03d}"
    jsonl_path = run_prefix.with_suffix(".jsonl")
    stderr_path = run_prefix.with_suffix(".stderr")
    metadata_path = run_prefix.with_suffix(".metadata.json")
    final_message_path = run_prefix.with_suffix(".final.md")
    command = codex_command(cwd, final_message_path, codex_sandbox=codex_sandbox)
    execution_policy = codex_execution_policy(command[0], codex_sandbox=codex_sandbox)

    if dry_run:
        print(f"Would write JSONL to {jsonl_path}")
        print(f"Would write stderr to {stderr_path}")
        print(f"Would write final message to {final_message_path}")
        print(
            "Benchmark host execution: "
            f"{CODEX_BENCHMARK_SCOPE}; {CODEX_EXECUTABLE_NAME} resolved from "
            f"{CODEX_EXECUTABLE_RESOLUTION} to {command[0]}"
        )
        print(f"Benchmark sandbox: {execution_policy['sandbox']}")
        print("Would run: " + " ".join(command) + " <prompt via stdin>")
        write_metadata(
            metadata_path,
            {
                "case": case.case_id,
                "prompt": prompt_id,
                "variant": variant_id,
                "repetition": repetition,
                "cwd": str(cwd),
                "finalMessagePath": str(final_message_path),
                "dryRun": True,
                "codexExecutionPolicy": execution_policy,
            },
            dry_run=True,
        )
        return

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with (
        jsonl_path.open("w", encoding="utf-8") as stdout,
        stderr_path.open("w", encoding="utf-8") as stderr,
    ):
        completed = subprocess.run(
            command,
            input=prompt_text,
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=False,
        )
    duration = time.monotonic() - started
    write_metadata(
        metadata_path,
        {
            "case": case.case_id,
            "prompt": prompt_id,
            "variant": variant_id,
            "repetition": repetition,
            "cwd": str(cwd),
            "command": [*command, "<prompt via stdin>"],
            "codexExecutionPolicy": execution_policy,
            "durationSeconds": round(duration, 3),
            "exitCode": completed.returncode,
            "finalMessagePath": str(final_message_path),
            "jsonlPath": str(jsonl_path),
            "stderrPath": str(stderr_path),
        },
        dry_run=False,
    )


def run_case(
    benchmarks_root: Path,
    case: BenchmarkCase,
    *,
    prompt_id: str | None,
    variant_id: str | None,
    repetitions: int | None,
    jobs: int | None,
    dry_run: bool,
    skip_prepare: bool,
    skill_exposure_mode: str,
    force_clone: bool,
    provider_timeout: int,
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
) -> Path:
    provider_ids = selected_provider_ids(case, prompt_id=prompt_id, variant_id=variant_id)
    if not skip_prepare:
        prepare_case(
            benchmarks_root,
            case,
            dry_run=dry_run,
            skill_exposure_mode=skill_exposure_mode,
            force_clone=force_clone,
            provider_timeout=provider_timeout,
            provider_ids=provider_ids,
        )

    current_run_id = run_id()
    output_root = benchmarks_root / "user-runs" / case.case_id / current_run_id
    if dry_run:
        print(f"Would create run output root {output_root}")
    else:
        output_root.mkdir(parents=True, exist_ok=False)

    task_batches: list[list[tuple[dict[str, Any], dict[str, Any], int]]] = []
    default_jobs = 1
    for prompt in case_prompt(case, prompt_id):
        prompt_runs = int(repetitions or prompt.get("runs") or 3)
        variants = prompt_variant(prompt, variant_id)
        default_jobs = max(default_jobs, len(variants))
        for repetition in range(1, prompt_runs + 1):
            task_batches.append([(prompt, variant, repetition) for variant in variants])

    if dry_run:
        for task_batch in task_batches:
            for prompt, variant, repetition in task_batch:
                run_one(
                    benchmarks_root=benchmarks_root,
                    case=case,
                    prompt=prompt,
                    variant=variant,
                    repetition=repetition,
                    output_root=output_root,
                    dry_run=True,
                    codex_sandbox=codex_sandbox,
                )
        return output_root

    max_workers = jobs or default_jobs
    if max_workers < 1:
        raise RuntimeError("--jobs must be greater than zero")

    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for task_batch in task_batches:
            future_to_task = {
                executor.submit(
                    run_one,
                    benchmarks_root=benchmarks_root,
                    case=case,
                    prompt=prompt,
                    variant=variant,
                    repetition=repetition,
                    output_root=output_root,
                    dry_run=False,
                    codex_sandbox=codex_sandbox,
                ): (prompt, variant, repetition)
                for prompt, variant, repetition in task_batch
            }
            for future in concurrent.futures.as_completed(future_to_task):
                prompt, variant, repetition = future_to_task[future]
                try:
                    future.result()
                except Exception as error:
                    failures.append(
                        f"{prompt.get('id')}/{variant.get('id')}/run-{repetition:03d}: {error}"
                    )

    write_summary(output_root, analyze_run_root(output_root))
    if failures:
        joined = "\n".join(f"  - {failure}" for failure in failures)
        raise RuntimeError(f"benchmark run completed with failed subprocesses:\n{joined}")
    return output_root


def walk_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(walk_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(walk_values(child))
    return values


def collect_strings(value: Any, keys: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, str):
                found.append(child)
            found.extend(collect_strings(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(collect_strings(child, keys))
    return found


def update_token_metrics(event: Any, metrics: dict[str, Any]) -> None:
    if isinstance(event, dict):
        for key, value in event.items():
            metric_key = TOKEN_KEYS.get(key)
            if metric_key and isinstance(value, int):
                metrics[metric_key] = max(int(metrics.get(metric_key, 0)), value)
            update_token_metrics(value, metrics)
    elif isinstance(event, list):
        for child in event:
            update_token_metrics(child, metrics)


def analyze_jsonl(path: Path) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "jsonl_size_bytes": path.stat().st_size,
        "event_count": 0,
        "command_event_count": 0,
        "errors": [],
        "final_answer": "",
    }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            metrics["errors"].append(f"invalid jsonl line: {error}")
            continue
        metrics["event_count"] += 1
        raw = json.dumps(event, sort_keys=True)
        if any(marker in raw for marker in ("exec_command", "tool_call", '"cmd"', '"command"')):
            metrics["command_event_count"] += 1
        update_token_metrics(event, metrics)
        errors = collect_strings(event, {"error", "stderr"})
        metrics["errors"].extend(error for error in errors if error)
        text_candidates = collect_strings(event, {"content", "text", "message"})
        for candidate in text_candidates:
            if len(candidate) > len(str(metrics.get("final_answer", ""))):
                metrics["final_answer"] = candidate
    return metrics


def load_metadata(jsonl_path: Path) -> dict[str, Any]:
    metadata_path = jsonl_path.with_suffix(".metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        return load_json(metadata_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def analyze_run_root(run_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for jsonl_path in sorted(run_root.rglob("*.jsonl")):
        metadata = load_metadata(jsonl_path)
        metrics = analyze_jsonl(jsonl_path)
        row = {
            "path": jsonl_path,
            "prompt": metadata.get("prompt", jsonl_path.parent.parent.name),
            "variant": metadata.get("variant", jsonl_path.parent.name),
            "repetition": metadata.get("repetition", jsonl_path.stem),
            "duration_seconds": metadata.get("durationSeconds"),
            "exit_code": metadata.get("exitCode"),
            **metrics,
        }
        rows.append(row)
    return rows


def range_text(values: list[Any]) -> str:
    clean = [value for value in values if isinstance(value, (int, float))]
    if not clean:
        return "n/a"
    low = min(clean)
    high = max(clean)
    if low == high:
        return str(low)
    return f"{low} - {high}"


def grouped(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("prompt", "unknown")), str(row.get("variant", "unknown")))
        result.setdefault(key, []).append(row)
    return result


def summary_markdown(run_root: Path, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# Benchmark Summary: {run_root.name}",
        "",
        f"Run root: `{run_root}`",
        "",
    ]
    if not rows:
        lines.append("No JSONL files found.")
        lines.append("")
        return "\n".join(lines)

    numeric_keys = [
        "duration_seconds",
        "event_count",
        "command_event_count",
        "input_tokens",
        "fresh_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "jsonl_size_bytes",
    ]

    for (prompt, variant), group_rows in grouped(rows).items():
        lines.extend([f"## {prompt} / {variant}", "", "| Metric | Range |", "| --- | --- |"])
        for key in numeric_keys:
            lines.append(f"| {key} | {range_text([row.get(key) for row in group_rows])} |")
        exit_codes = sorted(
            {str(row.get("exit_code")) for row in group_rows if row.get("exit_code") is not None}
        )
        lines.append(f"| exit_code | {', '.join(exit_codes) if exit_codes else 'n/a'} |")
        lines.append("")
        lines.extend(["| Run | Duration | JSONL Size | Errors |", "| --- | ---: | ---: | --- |"])
        for row in group_rows:
            error_count = len(row.get("errors", []))
            lines.append(
                f"| {row.get('repetition')} | {row.get('duration_seconds', 'n/a')} | "
                f"{row.get('jsonl_size_bytes', 'n/a')} | {error_count} |"
            )
        lines.append("")

    return "\n".join(lines)


def write_summary(run_root: Path, rows: list[dict[str, Any]]) -> Path:
    summary_path = run_root / "summary.md"
    summary_path.write_text(summary_markdown(run_root, rows), encoding="utf-8")
    return summary_path


def _capture_messages(run: Any) -> tuple[Any, list[str]]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        value = run()
    messages = [line for line in stdout.getvalue().splitlines() if line]
    return value, messages


def prepare_benchmarks(request: BenchmarkPrepareRequest) -> dict[str, Any]:
    benchmarks_root = request.benchmarks_root.resolve()
    cases = select_cases(load_cases(benchmarks_root), request.target, request.case_id)

    def run_prepare() -> None:
        for case in cases:
            prepare_case(
                benchmarks_root,
                case,
                dry_run=request.dry_run,
                skill_exposure_mode=request.skill_exposure_mode,
                force_clone=request.force_clone,
                provider_timeout=request.provider_timeout,
                provider_ids=selected_provider_ids(case),
            )

    _, messages = _capture_messages(run_prepare)
    return {
        "ok": True,
        "operation": "codex_benchmark_prepare",
        "benchmarksRoot": benchmarks_root.as_posix(),
        "target": request.target,
        "caseId": request.case_id or "",
        "dryRun": request.dry_run,
        "cases": [case.case_id for case in cases],
        "messages": messages,
    }


def run_codex_benchmark(request: BenchmarkRunRequest) -> dict[str, Any]:
    benchmarks_root = request.benchmarks_root.resolve()
    cases = select_cases(load_cases(benchmarks_root), request.target, request.case_id)
    validate_codex_sandbox(request.codex_sandbox)
    resolved_codex_executable = resolve_codex_executable()

    def run_selected_cases() -> list[Path]:
        output_roots: list[Path] = []
        for case in cases:
            output_roots.append(
                run_case(
                    benchmarks_root,
                    case,
                    prompt_id=request.prompt,
                    variant_id=request.variant,
                    repetitions=request.repetitions,
                    jobs=request.jobs,
                    dry_run=request.dry_run,
                    skip_prepare=request.skip_prepare,
                    skill_exposure_mode=request.skill_exposure_mode,
                    force_clone=request.force_clone,
                    provider_timeout=request.provider_timeout,
                    codex_sandbox=request.codex_sandbox,
                )
            )
        return output_roots

    output_roots, messages = _capture_messages(run_selected_cases)
    return {
        "ok": True,
        "operation": "codex_benchmark_run",
        "benchmarksRoot": benchmarks_root.as_posix(),
        "target": request.target,
        "caseId": request.case_id or "",
        "prompt": request.prompt or "",
        "variant": request.variant or "",
        "dryRun": request.dry_run,
        "codexExecutionPolicy": codex_execution_policy(
            resolved_codex_executable,
            codex_sandbox=request.codex_sandbox,
        ),
        "cases": [case.case_id for case in cases],
        "runOutputRoots": [path.as_posix() for path in output_roots],
        "messages": messages,
    }


def command_list(args: argparse.Namespace) -> int:
    with benchmark_root_context(args.benchmarks_root) as selected_benchmarks_root:
        benchmarks_root = selected_benchmarks_root.resolve()
        for case in load_cases(benchmarks_root):
            repository = case.repository
            repo_name = repository.get("name")
            repo_commit = repository.get("commit")
            print(
                f"{case.case_id}\t{case.data.get('status', 'unknown')}\t"
                f"{case.data.get('sizeBand', 'unknown')}\t{repo_name}@{repo_commit}"
            )
    return 0


def command_prepare(args: argparse.Namespace) -> int:
    with benchmark_root_context(args.benchmarks_root) as benchmarks_root:
        payload = prepare_benchmarks(
            BenchmarkPrepareRequest(
                benchmarks_root=benchmarks_root,
                target=args.target,
                case_id=args.case_id,
                dry_run=args.dry_run,
                skill_exposure_mode=args.skill_exposure_mode,
                force_clone=args.force_clone,
                provider_timeout=args.provider_timeout,
            )
        )
    for message in payload["messages"]:
        print(message)
    return 0


def command_run(args: argparse.Namespace) -> int:
    with benchmark_root_context(args.benchmarks_root) as benchmarks_root:
        payload = run_codex_benchmark(
            BenchmarkRunRequest(
                benchmarks_root=benchmarks_root,
                target=args.target,
                case_id=args.case_id,
                prompt=args.prompt,
                variant=args.variant,
                repetitions=args.repetitions,
                jobs=args.jobs,
                dry_run=args.dry_run,
                skip_prepare=args.skip_prepare,
                skill_exposure_mode=args.skill_exposure_mode,
                force_clone=args.force_clone,
                provider_timeout=args.provider_timeout,
                codex_sandbox=args.codex_sandbox,
            )
        )
    for message in payload["messages"]:
        print(message)
    for output_root in payload["runOutputRoots"]:
        print(f"Run output: {output_root}")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    run_root = args.run_root.resolve()
    rows = analyze_run_root(run_root)
    markdown = summary_markdown(run_root, rows)
    if args.write_summary:
        path = write_summary(run_root, rows)
        print(f"Wrote {path}")
    else:
        print(markdown)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmarks-root",
        type=Path,
        default=None,
        help="Benchmark root. Defaults to packaged benchmark cases.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List benchmark cases.")
    list_parser.set_defaults(func=command_list)

    prepare_parser = subparsers.add_parser(
        "prepare", help="Prepare resettable benchmark workspaces."
    )
    prepare_parser.add_argument("target", choices=("all", "case"))
    prepare_parser.add_argument("case_id", nargs="?")
    prepare_parser.add_argument(
        "--skill-exposure-mode", choices=SKILL_EXPOSURE_MODES, default="copy"
    )
    prepare_parser.add_argument(
        "--force-clone",
        action="store_true",
        help="Discard existing benchmark repository checkouts before cloning.",
    )
    prepare_parser.add_argument(
        "--provider-timeout",
        type=int,
        default=1800,
        help="Seconds allowed for each benchmark provider install/index command.",
    )
    prepare_parser.add_argument("--dry-run", action="store_true")
    prepare_parser.set_defaults(func=command_prepare)

    run_parser = subparsers.add_parser("run", help="Run benchmark cases with codex exec --json.")
    run_parser.add_argument("target", choices=("all", "case"))
    run_parser.add_argument("case_id", nargs="?")
    run_parser.add_argument("--prompt", help="Run only one prompt id.")
    run_parser.add_argument("--variant", help="Run only one variant id.")
    run_parser.add_argument(
        "--repetitions", type=int, help="Override repetitions per prompt variant."
    )
    run_parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Maximum concurrent Codex runs. Defaults to the number of selected variants.",
    )
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument(
        "--skip-prepare", action="store_true", help="Use the existing workspace fixture state."
    )
    run_parser.add_argument("--skill-exposure-mode", choices=SKILL_EXPOSURE_MODES, default="copy")
    run_parser.add_argument(
        "--force-clone",
        action="store_true",
        help="Discard existing benchmark repository checkouts during preparation.",
    )
    run_parser.add_argument(
        "--provider-timeout",
        type=int,
        default=1800,
        help="Seconds allowed for each benchmark provider install/index command during preparation.",
    )
    run_parser.add_argument(
        "--codex-sandbox",
        choices=CODEX_BENCHMARK_SANDBOX_MODES,
        default=CODEX_BENCHMARK_SANDBOX,
        help=(
            "Codex benchmark sandbox mode. Use 'default' to omit --sandbox and "
            "let the Codex CLI use its configured default."
        ),
    )
    run_parser.set_defaults(func=command_run)

    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze an existing user-runs directory."
    )
    analyze_parser.add_argument("run_root", type=Path)
    analyze_parser.add_argument("--write-summary", action="store_true")
    analyze_parser.set_defaults(func=command_analyze)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""CodeGraphContext context provider layouts and runtime cleanup."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers.context_modules.cgc.constants import (
    CGC_ENV_FILE_EXCLUDED_KEYS,
    CGC_FALKORDB_DEFAULT_HOST,
    CGC_FALKORDB_DEFAULT_PORT,
    CGC_PROVIDER,
    CGC_REQUIREMENTS,
    DEFAULT_CGCIGNORE,
    SOURCE_ARTIFACT_NAMES,
    read_gitignore_patterns,
)
from agents_remember.providers.context_modules.common import (
    ContextProviderError,
    expand_template,
    provider_requirements_file,
    remove_runtime_path,
    stable_provider_id,
)


@dataclass(frozen=True)
class CgcRuntimeLayout:
    coordination_root: Path
    repo_id: str
    code_repo_root: Path
    providers_root: Path
    runtime_root: Path
    cgc_root: Path
    venv_root: Path
    requirements_file: Path
    patches_root: Path
    state_file: Path
    cgcignore_path: Path
    cgcignore_patterns: tuple[str, ...]
    config_file: Path
    env_file: Path
    backend_root: Path
    backend_data_root: Path
    backend_state_file: Path
    run_root: Path
    logs_root: Path
    process_env_template: dict[str, str] | None
    watch_cwd: Path
    watch_log_file: Path

    def env(self) -> dict[str, str]:
        """Return the environment required to keep CGC under the runtime root."""

        defaults = {
            "HOME": (self.run_root / "home").as_posix(),
            "CGC_RUNTIME_DB_TYPE": "falkordb-remote",
            "DEFAULT_DATABASE": "falkordb-remote",
            "FALKORDB_HOST": CGC_FALKORDB_DEFAULT_HOST,
            "FALKORDB_PORT": CGC_FALKORDB_DEFAULT_PORT,
            "FALKORDB_GRAPH_NAME": f"cgc_{self.repo_id.replace('-', '_')}",
            "LOG_FILE_PATH": (self.logs_root / "cgc.log").as_posix(),
            "DEBUG_LOG_PATH": (self.logs_root / "debug.log").as_posix(),
            "ENABLE_AUTO_WATCH": "false",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        if os.name == "nt":
            defaults.update(
                {
                    "USERPROFILE": str(self.run_root / "home"),
                    "APPDATA": str(self.run_root / "appdata"),
                    "LOCALAPPDATA": str(self.run_root / "localappdata"),
                }
            )
        if self.process_env_template is None:
            return defaults

        variables = {
            "repoId": self.repo_id,
            "repoGraphId": self.repo_id.replace("-", "_"),
            "runtimeRoot": self.runtime_root.parent.as_posix(),
            "instanceRoot": self.runtime_root.as_posix(),
            "backendRuntimeRoot": self.backend_root.as_posix(),
            "backendDataRoot": self.backend_data_root.as_posix(),
        }
        env = {
            key: expand_template(str(value), variables)
            for key, value in self.process_env_template.items()
        }
        return env

    def cgc_executable(self) -> Path:
        if os.name == "nt":
            return self.venv_root / "Scripts" / "cgc.exe"
        return self.venv_root / "bin" / "cgc"


def cgc_runtime_layout(
    *,
    coordination_root: Path,
    repo_id: str,
    code_repo_root: Path,
    runtime_root: Path | None = None,
    venv_root: Path | None = None,
    requirements_file: Path | None = None,
    patches_root: Path | None = None,
    state_file: Path | None = None,
    backend_root: Path | None = None,
    backend_data_root: Path | None = None,
    backend_state_file: Path | None = None,
    process_env_template: dict[str, str] | None = None,
    cgcignore_patterns: tuple[str, ...] = (),
    watch_cwd: Path | None = None,
    watch_log_file: Path | None = None,
) -> CgcRuntimeLayout:
    """Build the managed CGC runtime layout for one code repository."""

    coordination_root = coordination_root.resolve()
    repo_id = stable_provider_id(repo_id)
    providers_root = coordination_root / "providers"
    provider_data_root = coordination_root / "providers" / "data"
    runtime_root = _resolve_optional_path(
        runtime_root,
        providers_root / "runners" / CGC_PROVIDER / repo_id,
    )
    cgc_root = runtime_root / ".codegraphcontext"
    backend_root = _resolve_optional_path(
        backend_root,
        provider_data_root / CGC_PROVIDER / "falkordb",
    )
    backend_data_root = _resolve_optional_path(backend_data_root, backend_root / "data")
    return CgcRuntimeLayout(
        coordination_root=coordination_root,
        repo_id=repo_id,
        code_repo_root=code_repo_root.resolve(),
        providers_root=providers_root,
        runtime_root=runtime_root,
        cgc_root=cgc_root,
        venv_root=_resolve_optional_path(venv_root, providers_root / "_venvs" / CGC_PROVIDER),
        requirements_file=_resolve_optional_path(
            requirements_file,
            provider_requirements_file(coordination_root, CGC_PROVIDER),
        ),
        patches_root=_resolve_optional_path(
            patches_root,
            providers_root / "patches" / CGC_PROVIDER,
        ),
        state_file=_resolve_optional_path(state_file, runtime_root / "provider-state.json"),
        cgcignore_path=cgc_root / ".cgcignore",
        cgcignore_patterns=tuple(pattern for pattern in cgcignore_patterns if pattern),
        config_file=cgc_root / "config.yaml",
        env_file=cgc_root / ".env",
        backend_root=backend_root,
        backend_data_root=backend_data_root,
        backend_state_file=_resolve_optional_path(
            backend_state_file,
            backend_root / "backend-state.json",
        ),
        run_root=cgc_root / "run",
        logs_root=cgc_root / "logs",
        process_env_template=process_env_template,
        watch_cwd=_resolve_optional_path(watch_cwd, runtime_root),
        watch_log_file=_resolve_optional_path(watch_log_file, cgc_root / "logs" / "watch.log"),
    )


def _resolve_optional_path(candidate: Path | None, default: Path) -> Path:
    return (candidate or default).resolve()


def cgc_runtime_layout_from_provider_settings(
    *,
    coordination_root: Path,
    provider_settings: dict[str, Any],
    root_settings: dict[str, Any],
) -> CgcRuntimeLayout:
    """Build a CGC runtime layout from a codegraphcontext-code settings entry."""

    coordination_root = coordination_root.resolve()
    repo_id = stable_provider_id(str(root_settings["repoId"]))
    base_variables = _cgc_base_variables(coordination_root)
    code_repo_root = _validated_cgc_code_repo_root(root_settings, base_variables)
    provider_runtime_root = _template_path(provider_settings["runtimeRoot"], base_variables)
    instance_root = _template_path(
        provider_settings.get("instanceRootTemplate", "<runtimeRoot>/<repoId>"),
        {"runtimeRoot": provider_runtime_root.as_posix(), "repoId": repo_id},
    )
    backend_settings = _dict_setting(provider_settings.get("backend"))
    backend_runtime_root, backend_data_root = _cgc_backend_roots(
        coordination_root,
        provider_runtime_root,
        backend_settings,
    )
    backend_bind_host, backend_host_port = _cgc_backend_host_settings(
        backend_runtime_root,
        backend_settings,
    )
    watch_cwd, watch_log_file, state_file = _cgc_watch_paths(
        provider_settings,
        provider_runtime_root,
        instance_root,
        repo_id,
    )

    return cgc_runtime_layout(
        coordination_root=coordination_root,
        repo_id=repo_id,
        code_repo_root=code_repo_root,
        runtime_root=instance_root,
        venv_root=Path(
            expand_template(
                str(provider_settings["venvRoot"]),
                base_variables,
            )
        ),
        requirements_file=Path(
            expand_template(
                str(provider_settings["requirementsFile"]),
                base_variables,
            )
        ),
        patches_root=Path(
            expand_template(
                str(provider_settings["patchesRoot"]),
                base_variables,
            )
        ),
        state_file=state_file,
        backend_root=backend_runtime_root,
        backend_data_root=backend_data_root,
        process_env_template=_cgc_process_env_template(
            provider_settings,
            backend_bind_host=backend_bind_host,
            backend_host_port=backend_host_port,
        ),
        cgcignore_patterns=_cgcignore_patterns_from_settings(provider_settings, root_settings),
        watch_cwd=watch_cwd,
        watch_log_file=watch_log_file,
    )


def _cgc_base_variables(coordination_root: Path) -> dict[str, str]:
    return {
        "coordination_root": coordination_root.as_posix(),
        "workspace_root": coordination_root.parent.as_posix(),
    }


def _template_path(value: Any, variables: dict[str, str]) -> Path:
    return Path(expand_template(str(value), variables)).resolve()


def _dict_setting(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _validated_cgc_code_repo_root(
    root_settings: dict[str, Any],
    base_variables: dict[str, str],
) -> Path:
    code_repo_root = Path(expand_template(str(root_settings["path"]), base_variables))
    if "<" in code_repo_root.as_posix() or ">" in code_repo_root.as_posix():
        raise ContextProviderError(
            f"unresolved codegraphcontext root path placeholder: {code_repo_root.as_posix()}"
        )
    if not code_repo_root.exists() or not code_repo_root.is_dir():
        raise ContextProviderError(
            f"codegraphcontext root path does not exist or is not a directory: {code_repo_root.as_posix()}"
        )
    return code_repo_root


def _cgc_backend_roots(
    coordination_root: Path,
    provider_runtime_root: Path,
    backend_settings: dict[str, Any],
) -> tuple[Path, Path]:
    variables = {
        "coordination_root": coordination_root.as_posix(),
        "runtimeRoot": provider_runtime_root.as_posix(),
    }
    backend_runtime_root = _template_path(
        backend_settings.get(
            "runtimeRoot",
            "<coordination_root>/providers/data/codegraphcontext/falkordb",
        ),
        variables,
    )
    backend_data_root = _template_path(
        backend_settings.get("dataRoot", "<backendRuntimeRoot>/data"),
        {
            **variables,
            "backendRuntimeRoot": backend_runtime_root.as_posix(),
        },
    )
    return backend_runtime_root, backend_data_root


def _cgc_backend_host_settings(
    backend_runtime_root: Path,
    backend_settings: dict[str, Any],
) -> tuple[str, str]:
    backend_state = _load_cgc_backend_state(backend_runtime_root / "backend-state.json")
    state_ports = backend_state.get("backend", {}).get("ports", {})
    state_falkordb_port = _dict_setting(state_ports.get("falkordb"))
    falkordb_port = _dict_setting(_dict_setting(backend_settings.get("ports")).get("falkordb"))
    backend_bind_host = str(
        state_falkordb_port.get(
            "bindHost",
            falkordb_port.get("bindHost", CGC_FALKORDB_DEFAULT_HOST),
        )
    )
    backend_host_port = str(
        state_falkordb_port.get(
            "hostPort",
            falkordb_port.get("hostPort", CGC_FALKORDB_DEFAULT_PORT),
        )
    )
    if backend_host_port == "auto":
        backend_host_port = CGC_FALKORDB_DEFAULT_PORT
    return backend_bind_host, backend_host_port


def _load_cgc_backend_state(backend_state_file: Path) -> dict[str, Any]:
    if not backend_state_file.exists():
        return {}
    try:
        data = json.loads(backend_state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _cgc_process_env_template(
    provider_settings: dict[str, Any],
    *,
    backend_bind_host: str,
    backend_host_port: str,
) -> dict[str, str] | None:
    if not isinstance(provider_settings.get("processEnvTemplate"), dict):
        return None
    process_env_variables = {
        "backend.ports.falkordb.bindHost": backend_bind_host,
        "backend.ports.falkordb.hostPort": backend_host_port,
    }
    return {
        str(key): expand_template(str(value), process_env_variables)
        for key, value in provider_settings["processEnvTemplate"].items()
    }


def _cgcignore_patterns_from_settings(
    provider_settings: dict[str, Any],
    root_settings: dict[str, Any],
) -> tuple[str, ...]:
    cgcignore_patterns: list[str] = []
    for pattern_group in (
        provider_settings.get("cgcignorePatterns", []),
        root_settings.get("cgcignorePatterns", []),
    ):
        if isinstance(pattern_group, list):
            cgcignore_patterns.extend(
                str(pattern).strip() for pattern in pattern_group if str(pattern).strip()
            )
    return tuple(cgcignore_patterns)


def _cgc_watch_paths(
    provider_settings: dict[str, Any],
    provider_runtime_root: Path,
    instance_root: Path,
    repo_id: str,
) -> tuple[Path, Path, Path]:
    variables = {
        "instanceRoot": instance_root.as_posix(),
        "runtimeRoot": provider_runtime_root.as_posix(),
        "repoId": repo_id,
    }
    watch_settings = _dict_setting(provider_settings.get("watch"))
    watch_cwd = _template_path(watch_settings.get("cwdTemplate", "<instanceRoot>"), variables)
    watch_log_file = _template_path(
        watch_settings.get("logFileTemplate", "<instanceRoot>/.codegraphcontext/logs/watch.log"),
        variables,
    )
    state_file = _template_path(
        provider_settings.get("stateFileTemplate", "<instanceRoot>/provider-state.json"),
        variables,
    )
    return watch_cwd, watch_log_file, state_file


def ensure_cgc_runtime_layout(layout: CgcRuntimeLayout) -> None:
    """Create runtime directories and default CGC config files."""

    for path in _cgc_runtime_directories(layout):
        path.mkdir(parents=True, exist_ok=True)

    if not layout.requirements_file.exists():
        layout.requirements_file.write_text("\n".join(CGC_REQUIREMENTS) + "\n", encoding="utf-8")
    layout.cgcignore_path.write_text(_cgcignore_text(layout), encoding="utf-8")
    layout.config_file.write_text("database: falkordb-remote\n", encoding="utf-8")
    layout.env_file.write_text(_cgc_env_text(layout), encoding="utf-8")


def _cgc_runtime_directories(layout: CgcRuntimeLayout) -> list[Path]:
    return [
        layout.venv_root,
        layout.requirements_file.parent,
        layout.patches_root,
        layout.cgc_root,
        layout.backend_data_root,
        layout.backend_state_file.parent,
        layout.run_root,
        layout.run_root / "home",
        layout.run_root / "appdata",
        layout.run_root / "localappdata",
        layout.logs_root,
    ]


def _cgcignore_text(layout: CgcRuntimeLayout) -> str:
    cgcignore_lines = DEFAULT_CGCIGNORE.rstrip("\n").splitlines()
    gitignore_patterns = read_gitignore_patterns(layout.code_repo_root)
    if gitignore_patterns:
        cgcignore_lines.extend(["", "# Inherited from source .gitignore"])
        cgcignore_lines.extend(gitignore_patterns)
    if layout.cgcignore_patterns:
        cgcignore_lines.extend(["", "# Repo-specific managed exclusions"])
        cgcignore_lines.extend(layout.cgcignore_patterns)
    return "\n".join(cgcignore_lines) + "\n"


def _cgc_env_text(layout: CgcRuntimeLayout) -> str:
    lines = ["# Managed by Agents Remember for CodeGraphContext"]
    lines.extend(
        f"{key}={value}"
        for key, value in layout.env().items()
        if key not in CGC_ENV_FILE_EXCLUDED_KEYS
    )
    return "\n".join(lines) + "\n"


def source_provider_artifacts(code_repo_root: Path) -> list[Path]:
    """Return provider-created artifacts that should not exist in source repos."""

    root = code_repo_root.resolve()
    return [root / name for name in SOURCE_ARTIFACT_NAMES if (root / name).exists()]


def assert_no_source_provider_artifacts(code_repo_root: Path) -> None:
    artifacts = source_provider_artifacts(code_repo_root)
    if artifacts:
        rendered = ", ".join(path.name for path in artifacts)
        raise ContextProviderError(f"provider artifacts found in source repo: {rendered}")


def cleanup_cgc_runtime_artifacts(
    layouts: list[CgcRuntimeLayout], *, dry_run: bool = False
) -> list[dict[str, str]]:
    """Remove stale CGC runtime artifacts that are outside the desired layout."""

    if not layouts:
        return []

    provider_root = layouts[0].runtime_root.parent.resolve()
    configured_roots = {layout.runtime_root.resolve() for layout in layouts}
    removals: list[dict[str, str]] = []
    removals.extend(
        _unconfigured_cgc_runtime_removals(
            provider_root,
            configured_roots,
            dry_run=dry_run,
        )
    )
    removals.extend(_obsolete_cgc_runtime_removals(layouts, dry_run=dry_run))
    return removals


def _unconfigured_cgc_runtime_removals(
    provider_root: Path,
    configured_roots: set[Path],
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    removals: list[dict[str, str]] = []
    for child in sorted(provider_root.iterdir()) if provider_root.exists() else []:
        if not _should_remove_cgc_runtime_child(child, configured_roots):
            continue
        _assert_cgc_child_under_provider_root(child, provider_root)
        removals.append({"path": child.as_posix(), "reason": "unconfigured-cgc-instance"})
        remove_runtime_path(child, dry_run)
    return removals


def _should_remove_cgc_runtime_child(child: Path, configured_roots: set[Path]) -> bool:
    if not child.is_dir() or child.resolve() in configured_roots:
        return False
    return (
        (child / ".codegraphcontext").exists()
        or (child / "provider-state.json").exists()
        or child.name.startswith("_")
    )


def _assert_cgc_child_under_provider_root(child: Path, provider_root: Path) -> None:
    if not child.resolve().is_relative_to(provider_root):
        raise ContextProviderError(f"refusing to remove CGC path outside provider root: {child}")


def _obsolete_cgc_runtime_removals(
    layouts: list[CgcRuntimeLayout],
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    removals: list[dict[str, str]] = []
    for layout in layouts:
        cgc_root = layout.cgc_root.resolve()
        for name in ("db", "global", "kuzu", "kuzu.wal"):
            target = (layout.cgc_root / name).resolve()
            if not target.exists():
                continue
            if not target.is_relative_to(cgc_root):
                raise ContextProviderError(
                    f"refusing to remove CGC path outside instance root: {target}"
                )
            removals.append({"path": target.as_posix(), "reason": f"legacy-embedded-{name}"})
            remove_runtime_path(target, dry_run)
    return removals

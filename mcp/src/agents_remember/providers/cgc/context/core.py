"""CodeGraphContext context provider layouts and runtime cleanup."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers.cgc.context.constants import (
    CGC_FALKORDB_CONTAINER_NAME,
    CGC_FALKORDB_DEFAULT_HOST,
    CGC_FALKORDB_DEFAULT_PORT,
    CGC_NETWORK_NAME,
    CGC_PROVIDER,
    CGC_REQUIREMENTS,
    CGC_RUNNER_IMAGE_REPOSITORY,
    CGC_WATCHER_CONTAINER_PREFIX,
)
from agents_remember.providers.context.common import (
    ContextProviderError,
    expand_template,
    provider_requirements_file,
    stable_provider_id,
)


def to_container_path(path: Path | str) -> str:
    """Map a host path to the POSIX path seen inside a Linux provider container.

    Bind mounts and in-container arguments require POSIX paths. On Windows a
    resolved path renders as ``C:/ew/x`` via :meth:`Path.as_posix`, whose drive
    colon both breaks Docker's ``host:container`` mount parsing ("too many
    colons") and is not a valid Linux path. Stripping the leading ``<drive>:``
    yields ``/ew/x``. On POSIX hosts there is no drive letter, so the value is
    returned unchanged and Linux/macOS behavior is preserved exactly.
    """

    posix = path.as_posix() if isinstance(path, Path) else str(path).replace("\\", "/")
    if len(posix) >= 2 and posix[0].isalpha() and posix[1] == ":":
        remainder = posix[2:]
        return remainder if remainder.startswith("/") else f"/{remainder}"
    return posix


@dataclass(frozen=True)
class CgcRuntimeLayout:
    coordination_root: Path
    repo_id: str
    code_repo_root: Path
    providers_root: Path
    runtime_root: Path
    cgc_root: Path
    requirements_file: Path
    patches_root: Path
    image_build_root: Path
    image_lock_file: Path
    runner_image: str
    watcher_container_name: str
    state_file: Path
    cgcignore_path: Path
    cgcignore_patterns: tuple[str, ...]
    config_file: Path
    env_file: Path
    backend_root: Path
    backend_data_root: Path
    backend_container_name: str
    network_name: str
    backend_state_file: Path
    run_root: Path
    logs_root: Path
    process_env_template: dict[str, str] | None
    watch_cwd: Path
    watch_log_file: Path

    @property
    def container_runtime_root(self) -> str:
        """Runtime root as seen inside the provider container (POSIX path)."""

        return to_container_path(self.runtime_root)

    @property
    def container_code_repo_root(self) -> str:
        """Code repository root as seen inside the provider container (POSIX path)."""

        return to_container_path(self.code_repo_root)

    def env(self, *, for_container: bool = False) -> dict[str, str]:
        """Return the environment required to keep CGC under the runtime root.

        When ``for_container`` is set, filesystem paths are rendered as the
        POSIX paths visible inside the Linux provider container (drive letter
        stripped on Windows) and host-only Windows variables are omitted. On
        POSIX hosts this matches the host environment exactly.
        """

        def render_path(path: Path) -> str:
            return to_container_path(path) if for_container else path.as_posix()

        defaults = {
            "HOME": render_path(self.run_root / "home"),
            "CGC_RUNTIME_DB_TYPE": "falkordb-remote",
            "DEFAULT_DATABASE": "falkordb-remote",
            "FALKORDB_HOST": CGC_FALKORDB_DEFAULT_HOST,
            "FALKORDB_PORT": CGC_FALKORDB_DEFAULT_PORT,
            "FALKORDB_GRAPH_NAME": f"cgc_{self.repo_id.replace('-', '_')}",
            "LOG_FILE_PATH": render_path(self.logs_root / "cgc.log"),
            "DEBUG_LOG_PATH": render_path(self.logs_root / "debug.log"),
            "ENABLE_AUTO_WATCH": "false",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        if not for_container and os.name == "nt":
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
            "runtimeRoot": render_path(self.runtime_root.parent),
            "instanceRoot": render_path(self.runtime_root),
            "backendRuntimeRoot": render_path(self.backend_root),
            "backendDataRoot": render_path(self.backend_data_root),
        }
        env = {
            key: expand_template(str(value), variables)
            for key, value in self.process_env_template.items()
        }
        return env

def cgc_runtime_layout(
    *,
    coordination_root: Path,
    repo_id: str,
    code_repo_root: Path,
    runtime_root: Path | None = None,
    requirements_file: Path | None = None,
    patches_root: Path | None = None,
    image_build_root: Path | None = None,
    image_lock_file: Path | None = None,
    runner_image: str | None = None,
    watcher_container_name: str | None = None,
    state_file: Path | None = None,
    backend_root: Path | None = None,
    backend_data_root: Path | None = None,
    backend_container_name: str | None = None,
    network_name: str | None = None,
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
        requirements_file=_resolve_optional_path(
            requirements_file,
            provider_requirements_file(coordination_root, CGC_PROVIDER),
        ),
        patches_root=_resolve_optional_path(
            patches_root,
            providers_root / "patches" / CGC_PROVIDER,
        ),
        image_build_root=_resolve_optional_path(
            image_build_root,
            providers_root / "runners" / CGC_PROVIDER / "image",
        ),
        image_lock_file=_resolve_optional_path(
            image_lock_file,
            providers_root / "requirements" / "codegraphcontext-runner-docker.lock",
        ),
        runner_image=runner_image or _cgc_runner_image(),
        watcher_container_name=watcher_container_name
        or f"{CGC_WATCHER_CONTAINER_PREFIX}-{repo_id}",
        state_file=_resolve_optional_path(state_file, runtime_root / "provider-state.json"),
        cgcignore_path=cgc_root / ".cgcignore",
        cgcignore_patterns=tuple(pattern for pattern in cgcignore_patterns if pattern),
        config_file=cgc_root / "config.yaml",
        env_file=cgc_root / ".env",
        backend_root=backend_root,
        backend_data_root=backend_data_root,
        backend_container_name=backend_container_name or CGC_FALKORDB_CONTAINER_NAME,
        network_name=network_name or CGC_NETWORK_NAME,
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
    if "venvRoot" in provider_settings:
        raise ContextProviderError(
            "codegraphcontext-code settings may not define venvRoot; "
            "managed CGC execution uses the Docker runner image"
        )
    repo_id = stable_provider_id(str(root_settings["repoId"]))
    base_variables = _cgc_base_variables(coordination_root)
    code_repo_root = _validated_cgc_code_repo_root(root_settings, base_variables)
    provider_runtime_root = _template_path(provider_settings["runtimeRoot"], base_variables)
    instance_root = _template_path(
        provider_settings.get("instanceRootTemplate", "<runtimeRoot>/<repoId>"),
        {"runtimeRoot": provider_runtime_root.as_posix(), "repoId": repo_id},
    )
    backend_settings = _dict_setting(provider_settings.get("backend"))
    runtime_settings = _dict_setting(provider_settings.get("runtime"))
    runner_settings = _dict_setting(runtime_settings.get("runner"))
    backend_runtime_root, backend_data_root = _cgc_backend_roots(
        coordination_root,
        provider_runtime_root,
        backend_settings,
    )
    image_build_root, image_lock_file, runner_image, watcher_container_name = _cgc_runner_settings(
        coordination_root,
        provider_runtime_root,
        runner_settings,
        repo_id,
    )
    backend_bind_host, backend_host_port = _cgc_backend_host_settings(
        backend_runtime_root,
        backend_settings,
    )
    backend_container_name = str(backend_settings.get("containerName", CGC_FALKORDB_CONTAINER_NAME))
    network_name = _cgc_backend_network_name(backend_settings)
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
        requirements_file=Path(
            expand_template(
                str(
                    provider_settings.get(
                        "requirementsFile",
                        "<coordination_root>/providers/requirements/codegraphcontext.txt",
                    )
                ),
                base_variables,
            )
        ),
        patches_root=Path(
            expand_template(
                str(
                    provider_settings.get(
                        "patchesRoot",
                        "<coordination_root>/providers/patches/codegraphcontext",
                    )
                ),
                base_variables,
            )
        ),
        image_build_root=image_build_root,
        image_lock_file=image_lock_file,
        runner_image=runner_image,
        watcher_container_name=watcher_container_name,
        state_file=state_file,
        backend_root=backend_runtime_root,
        backend_data_root=backend_data_root,
        backend_container_name=backend_container_name,
        network_name=network_name,
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


def _cgc_version() -> str:
    return CGC_REQUIREMENTS[0].split("==", 1)[1]


def _cgc_runner_image() -> str:
    return f"{CGC_RUNNER_IMAGE_REPOSITORY}:{_cgc_version()}"


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


def _cgc_runner_settings(
    coordination_root: Path,
    provider_runtime_root: Path,
    runner_settings: dict[str, Any],
    repo_id: str,
) -> tuple[Path, Path, str, str]:
    variables = {
        "coordination_root": coordination_root.as_posix(),
        "runtimeRoot": provider_runtime_root.as_posix(),
        "repoId": repo_id,
    }
    image_build_root = _template_path(
        runner_settings.get("buildRoot", "<runtimeRoot>/image"),
        variables,
    )
    image_lock_file = _template_path(
        runner_settings.get(
            "imageLockFile",
            "<coordination_root>/providers/requirements/codegraphcontext-runner-docker.lock",
        ),
        variables,
    )
    runner_image = str(runner_settings.get("image", _cgc_runner_image())).strip()
    if not runner_image or "<" in runner_image or ">" in runner_image:
        raise ContextProviderError("codegraphcontext runner.image must be a concrete Docker tag")
    container_name = expand_template(
        str(
            runner_settings.get(
                "containerNameTemplate",
                f"{CGC_WATCHER_CONTAINER_PREFIX}-<repoId>",
            )
        ),
        variables,
    )
    return image_build_root, image_lock_file, runner_image, container_name


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


def _cgc_backend_network_name(backend_settings: dict[str, Any]) -> str:
    network = _dict_setting(backend_settings.get("network"))
    return str(network.get("name", CGC_NETWORK_NAME))


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

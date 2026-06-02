"""Docker-owned GrepAI provider layout helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers.context.common import (
    ContextProviderError,
    ensure_provider_requirements_file,
    expand_template,
    provider_requirements_file,
    stable_provider_id,
)
from agents_remember.providers.grepai.context.constants import GREPAI_PIN, GREPAI_PROVIDER


def _resolve_optional_path(candidate: Path | None, default: Path) -> Path:
    return (candidate or default).resolve()


@dataclass(frozen=True)
class GrepaiMemoryRoot:
    project_id: str
    path: Path


@dataclass(frozen=True)
class GrepaiRuntimeLayout:
    coordination_root: Path
    workspace_name: str
    roots: tuple[GrepaiMemoryRoot, ...]
    providers_root: Path
    runtime_root: Path
    requirements_file: Path
    config_root: Path
    workspace_config_file: Path
    state_root: Path
    state_file: Path
    logs_root: Path
    home_root: Path
    run_root: Path
    cache_root: Path
    backend_root: Path
    backend_data_root: Path
    backend_state_file: Path

    def env(self) -> dict[str, str]:
        """Return process env that keeps GrepAI runtime state under providers/runners/grepai."""

        env = {
            "HOME": self.home_root.as_posix(),
            "XDG_STATE_HOME": (self.state_root / "xdg").as_posix(),
            "XDG_CACHE_HOME": (self.cache_root / "xdg").as_posix(),
        }
        if os.name == "nt":
            env.update(
                {
                    "USERPROFILE": str(self.home_root),
                    "APPDATA": str(self.run_root / "appdata"),
                    "LOCALAPPDATA": str(self.run_root / "localappdata"),
                }
            )
        return env


def ensure_grepai_requirements_file(coordination_root: Path) -> Path:
    return ensure_provider_requirements_file(coordination_root, GREPAI_PROVIDER, GREPAI_PIN)


def grepai_runtime_layout(
    *,
    coordination_root: Path,
    workspace_name: str = "agents-remember-memory",
    roots: tuple[GrepaiMemoryRoot, ...] = (),
    runtime_root: Path | None = None,
    logs_root: Path | None = None,
    requirements_file: Path | None = None,
    state_file: Path | None = None,
    backend_root: Path | None = None,
    backend_data_root: Path | None = None,
    backend_state_file: Path | None = None,
) -> GrepaiRuntimeLayout:
    """Build the managed GrepAI runtime layout for memory-root indexing."""

    coordination_root = coordination_root.resolve()
    providers_root = coordination_root / "providers"
    provider_data_root = coordination_root / "providers" / "data"
    runtime_root = _resolve_optional_path(
        runtime_root, providers_root / "runners" / GREPAI_PROVIDER
    )
    backend_root = _resolve_optional_path(
        backend_root,
        provider_data_root / GREPAI_PROVIDER / "postgres",
    )
    backend_data_root = _resolve_optional_path(backend_data_root, backend_root / "data")
    workspace_name = stable_provider_id(workspace_name)
    return GrepaiRuntimeLayout(
        coordination_root=coordination_root,
        workspace_name=workspace_name,
        roots=tuple(roots),
        providers_root=providers_root,
        runtime_root=runtime_root,
        requirements_file=_resolve_optional_path(
            requirements_file,
            provider_requirements_file(coordination_root, GREPAI_PROVIDER),
        ),
        config_root=runtime_root / "config",
        workspace_config_file=runtime_root / "home" / ".grepai" / "workspace.yaml",
        state_root=runtime_root / "state",
        state_file=_resolve_optional_path(
            state_file, runtime_root / "state" / "provider-state.json"
        ),
        logs_root=_resolve_optional_path(logs_root, runtime_root / "logs"),
        home_root=runtime_root / "home",
        run_root=runtime_root / "run",
        cache_root=runtime_root / "cache",
        backend_root=backend_root,
        backend_data_root=backend_data_root,
        backend_state_file=_resolve_optional_path(
            backend_state_file,
            backend_root / "backend-state.json",
        ),
    )


def grepai_roots_from_provider_settings(
    coordination_root: Path,
    provider_settings: dict[str, Any],
) -> tuple[GrepaiMemoryRoot, ...]:
    roots = provider_settings.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ContextProviderError("grepai-memory.roots must be a non-empty array")

    base_variables = {
        "coordination_root": coordination_root.as_posix(),
        "workspace_root": coordination_root.parent.as_posix(),
    }
    normalized: list[GrepaiMemoryRoot] = []
    seen: set[str] = set()
    allow_missing_roots = provider_settings.get("allowMissingRoots") is True
    for root in roots:
        project_id, expanded = _grepai_root_identity(
            root,
            base_variables,
            allow_missing=allow_missing_roots,
        )
        if project_id in seen:
            raise ContextProviderError(f"duplicate grepai project id: {project_id}")
        seen.add(project_id)
        normalized.append(GrepaiMemoryRoot(project_id=project_id, path=expanded))
    return tuple(normalized)


def _grepai_root_identity(
    root: Any,
    base_variables: dict[str, str],
    *,
    allow_missing: bool = False,
) -> tuple[str, Path]:
    raw_path, project_id = _grepai_raw_root(root)
    expanded = Path(expand_template(raw_path, base_variables)).resolve()
    _validate_grepai_root_path(expanded, allow_missing=allow_missing)
    return project_id, expanded


def _grepai_raw_root(root: Any) -> tuple[str, str]:
    if isinstance(root, str):
        return root, stable_provider_id(Path(root).name)
    if not isinstance(root, dict):
        raise ContextProviderError("each grepai-memory root must be a path string or object")
    return _grepai_dict_root(root)


def _grepai_dict_root(root: dict[str, Any]) -> tuple[str, str]:
    if "path" not in root:
        raise ContextProviderError("each grepai-memory root must define path")
    raw_path = str(root["path"])
    project_id = stable_provider_id(
        str(root.get("projectId") or root.get("repoId") or Path(raw_path).name)
    )
    return raw_path, project_id


def _validate_grepai_root_path(path: Path, *, allow_missing: bool = False) -> None:
    if "<" in path.as_posix() or ">" in path.as_posix():
        raise ContextProviderError(f"unresolved grepai root path placeholder: {path.as_posix()}")
    if allow_missing:
        return
    if not path.exists() or not path.is_dir():
        raise ContextProviderError(
            f"grepai root path does not exist or is not a directory: {path.as_posix()}"
        )


def grepai_runtime_layout_from_provider_settings(
    *,
    coordination_root: Path,
    provider_settings: dict[str, Any],
) -> GrepaiRuntimeLayout:
    coordination_root = coordination_root.resolve()
    base_variables = {
        "coordination_root": coordination_root.as_posix(),
        "workspace_root": coordination_root.parent.as_posix(),
    }
    provider_runtime_root = Path(
        expand_template(
            str(
                provider_settings.get("runtimeRoot", "<coordination_root>/providers/runners/grepai")
            ),
            base_variables,
        )
    ).resolve()
    backend_settings = provider_settings.get("backend", {})
    if not isinstance(backend_settings, dict):
        backend_settings = {}
    backend_runtime_root = Path(
        expand_template(
            str(
                backend_settings.get(
                    "runtimeRoot", "<coordination_root>/providers/data/grepai/postgres"
                )
            ),
            {
                "coordination_root": coordination_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
            },
        )
    ).resolve()
    backend_data_root = Path(
        expand_template(
            str(backend_settings.get("dataRoot", "<backendRuntimeRoot>/data")),
            {
                "coordination_root": coordination_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
                "backendRuntimeRoot": backend_runtime_root.as_posix(),
            },
        )
    ).resolve()
    requirements_file = Path(
        expand_template(
            str(
                provider_settings.get(
                    "requirementsFile", "<coordination_root>/providers/requirements/grepai.txt"
                )
            ),
            base_variables,
        )
    ).resolve()
    state_file = Path(
        expand_template(
            str(provider_settings.get("stateFile", "<runtimeRoot>/state/provider-state.json")),
            {
                "coordination_root": coordination_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
            },
        )
    ).resolve()
    watch_settings = provider_settings.get("watch", {})
    if not isinstance(watch_settings, dict):
        watch_settings = {}
    logs_root = Path(
        expand_template(
            str(watch_settings.get("logDir", "<coordination_root>/logs/providers/grepai")),
            {
                "coordination_root": coordination_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
            },
        )
    ).resolve()
    workspace_name = str(provider_settings.get("workspace", "agents-remember-memory"))
    # The containerized watcher indexes the live memory roots directly (bind-mounted
    # read-write into the container). grepai drops a `.grepai/` working dir into each
    # indexed root; `ensure_grepai_root_gitignore` keeps that out of git so closeout
    # never stages it.
    roots = grepai_roots_from_provider_settings(coordination_root, provider_settings)
    return grepai_runtime_layout(
        coordination_root=coordination_root,
        workspace_name=workspace_name,
        roots=roots,
        runtime_root=provider_runtime_root,
        logs_root=logs_root,
        requirements_file=requirements_file,
        state_file=state_file,
        backend_root=backend_runtime_root,
        backend_data_root=backend_data_root,
        backend_state_file=backend_runtime_root / "backend-state.json",
    )


def ensure_grepai_runtime_layout(layout: GrepaiRuntimeLayout) -> None:
    """Create provider-owned GrepAI runtime directories and requirement pin."""

    for path in [
        layout.runtime_root,
        layout.requirements_file.parent,
        layout.config_root,
        layout.workspace_config_file.parent,
        layout.state_root,
        layout.logs_root,
        layout.home_root,
        layout.run_root,
        layout.run_root / "appdata",
        layout.run_root / "localappdata",
        layout.cache_root,
        layout.backend_data_root,
        layout.backend_state_file.parent,
    ]:
        path.mkdir(parents=True, exist_ok=True)
    if not layout.requirements_file.exists():
        layout.requirements_file.write_text(f"{GREPAI_PIN}\n", encoding="utf-8")


def ensure_grepai_root_gitignore(roots: tuple[GrepaiMemoryRoot, ...]) -> list[dict[str, str]]:
    """Ensure each indexed root ignores grepai's `.grepai/` working dir.

    The watcher indexes the live memory roots, and grepai writes a `.grepai/` working
    directory into each one. Without ignoring it, the memory closeout's `git add` would
    stage that provider artifact. Idempotently append `.grepai/` to each root's
    `.gitignore` so the watcher can do its job without dirtying the tracked tree.
    """

    updated: list[dict[str, str]] = []
    for root in roots:
        gitignore = root.path / ".gitignore"
        entry = ".grepai/"
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if entry in {line.strip() for line in existing.splitlines()}:
            continue
        if not root.path.is_dir():
            continue
        prefix = existing if existing.endswith("\n") or not existing else existing + "\n"
        gitignore.write_text(f"{prefix}{entry}\n", encoding="utf-8")
        updated.append({"projectId": root.project_id, "path": gitignore.as_posix()})
    return updated

"""GrepAI workspace configuration rendering."""

from __future__ import annotations

import json
from typing import Any

from agents_remember.providers.grepai.context.layout import GrepaiRuntimeLayout


def _yaml_quote(value: str) -> str:
    return json.dumps(value)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_quote(str(value))


def grepai_workspace_config_text(
    *,
    layout: GrepaiRuntimeLayout,
    dsn: str,
    embedder_settings: dict[str, Any] | None = None,
    project_paths: dict[str, str] | None = None,
) -> str:
    if embedder_settings is None:
        embedder_settings = {}
    provider = str(embedder_settings.get("provider", "ollama"))
    model = str(embedder_settings.get("model", "nomic-embed-text"))
    lines = _grepai_workspace_header(layout, dsn, provider=provider, model=model)
    endpoint = _grepai_embedder_endpoint(embedder_settings, provider)
    if endpoint:
        lines.append(f"      endpoint: {_yaml_quote(str(endpoint))}")
    dimensions = _grepai_embedder_dimensions(embedder_settings, provider=provider, model=model)
    if dimensions is not None:
        lines.append(f"      dimensions: {_yaml_scalar(dimensions)}")
    lines.extend(_grepai_project_lines(layout, project_paths))
    return "\n".join(lines) + "\n"


def _grepai_workspace_header(
    layout: GrepaiRuntimeLayout,
    dsn: str,
    *,
    provider: str,
    model: str,
) -> list[str]:
    return [
        "version: 1",
        "workspaces:",
        f"  {layout.workspace_name}:",
        f"    name: {_yaml_quote(layout.workspace_name)}",
        "    store:",
        "      backend: postgres",
        "      postgres:",
        f"        dsn: {_yaml_quote(dsn)}",
        "    embedder:",
        f"      provider: {_yaml_quote(provider)}",
        f"      model: {_yaml_quote(model)}",
    ]


def _grepai_embedder_endpoint(embedder_settings: dict[str, Any], provider: str) -> Any:
    endpoint = embedder_settings.get("endpoint")
    if endpoint:
        return endpoint
    endpoints = {
        "ollama": "http://localhost:11434",
        "lmstudio": "http://127.0.0.1:1234",
    }
    return endpoints.get(provider)


def _grepai_embedder_dimensions(
    embedder_settings: dict[str, Any],
    *,
    provider: str,
    model: str,
) -> Any:
    dimensions = embedder_settings.get("dimensions")
    if dimensions is not None:
        return dimensions
    if provider == "ollama" and model in {"nomic-embed-text", "nomic-embed-text-v2-moe"}:
        return 768
    return None


def _grepai_project_lines(
    layout: GrepaiRuntimeLayout,
    project_paths: dict[str, str] | None,
) -> list[str]:
    lines = ["    projects:"]
    for root in layout.roots:
        project_path = (
            project_paths[root.project_id]
            if project_paths and root.project_id in project_paths
            else root.path.as_posix()
        )
        lines.extend(
            [
                f"      - name: {_yaml_quote(root.project_id)}",
                f"        path: {_yaml_quote(project_path)}",
            ]
        )
    return lines


def write_grepai_workspace_config(
    layout: GrepaiRuntimeLayout,
    *,
    dsn: str,
    embedder_settings: dict[str, Any] | None = None,
    project_paths: dict[str, str] | None = None,
) -> None:
    layout.workspace_config_file.parent.mkdir(parents=True, exist_ok=True)
    layout.workspace_config_file.write_text(
        grepai_workspace_config_text(
            layout=layout,
            dsn=dsn,
            embedder_settings=embedder_settings,
            project_paths=project_paths,
        ),
        encoding="utf-8",
    )

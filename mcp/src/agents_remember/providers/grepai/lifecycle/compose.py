"""GrepAI Docker Compose rendering."""

from __future__ import annotations

from typing import Any

from agents_remember.providers.grepai.lifecycle.core import (
    GrepaiRuntimeLayout,
    grepai_container_env,
    grepai_embedder_backend_settings,
    grepai_network_name,
)
from agents_remember.providers.lifecycle.compose_runtime import (
    ComposeRender,
    host_user_block,
    provider_asset_path,
    provider_asset_text,
    render_template,
    required_ownership_labels,
    yaml_environment,
    yaml_labels,
    yaml_port_mapping,
    yaml_scalar,
)

GREPAI_COMPOSE_PROJECT = "agents-remember-grepai"


def grepai_compose_project(provider_settings: dict[str, Any]) -> str:
    runtime = provider_settings.get("runtime")
    if not isinstance(runtime, dict):
        return GREPAI_COMPOSE_PROJECT
    return str(runtime.get("composeProject", GREPAI_COMPOSE_PROJECT))


def grepai_ownership_labels(provider_settings: dict[str, Any]) -> dict[str, str]:
    return required_ownership_labels(provider_settings, "grepai-memory")


def grepai_compose_render(
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
    backend: dict[str, Any],
    *,
    postgres_port: int | str | None = None,
    ollama_port: int | str | None = None,
) -> ComposeRender:
    embedder = grepai_embedder_backend_settings(provider_settings, layout)
    postgres_port = postgres_port or backend["postgresHostPort"]
    ollama_port = ollama_port or embedder["httpHostPort"]
    values = {
        "POSTGRES_IMAGE": yaml_scalar(backend["image"]),
        "POSTGRES_CONTAINER_NAME": yaml_scalar(backend["containerName"]),
        "POSTGRES_PORT": yaml_port_mapping(
            backend["postgresHost"], postgres_port, backend["postgresContainerPort"]
        ),
        "POSTGRES_DATA_VOLUME": yaml_scalar(
            f"{layout.backend_data_root.as_posix()}:{backend['dataDestination']}"
        ),
        "OLLAMA_IMAGE": yaml_scalar(embedder["image"]),
        "OLLAMA_CONTAINER_NAME": yaml_scalar(embedder["containerName"]),
        "OLLAMA_PORT": yaml_port_mapping(
            embedder["httpHost"], ollama_port, embedder["httpContainerPort"]
        ),
        "OLLAMA_DATA_VOLUME": yaml_scalar(
            f"{embedder['dataRoot'].as_posix()}:{embedder['dataDestination']}"
        ),
        "RUNNER_IMAGE": yaml_scalar(runner["image"]),
        "RUNNER_BUILD_CONTEXT": yaml_scalar(
            provider_asset_path("docker", "grepai").as_posix()
        ),
        "GREPAI_VERSION": yaml_scalar(runner["version"]),
        "GREPAI_ARCH": yaml_scalar(runner["releaseArch"]),
        "WATCHER_CONTAINER_NAME": yaml_scalar(runner["containerName"]),
        "WATCHER_USER_BLOCK": host_user_block(),
        "SERVICE_LABELS": yaml_labels(grepai_ownership_labels(provider_settings)),
        "WATCHER_ENVIRONMENT": yaml_environment(grepai_container_env(runner)),
        "WATCHER_RUNTIME_VOLUME": yaml_scalar(
            f"{layout.runtime_root.as_posix()}:{runner['runtimeMount']}"
        ),
        "WATCHER_LOGS_VOLUME": yaml_scalar(
            f"{layout.logs_root.as_posix()}:{runner['logsMount']}"
        ),
        "WORKSPACE_NAME": yaml_scalar(layout.workspace_name),
        "LOGS_MOUNT": yaml_scalar(runner["logsMount"]),
        "NETWORK_NAME": yaml_scalar(grepai_network_name(provider_settings)),
    }
    override_yaml = render_template(
        provider_asset_text("compose", "grepai.override.yaml.tmpl"),
        values,
    )
    return ComposeRender(
        project_name=grepai_compose_project(provider_settings),
        base_file=provider_asset_path("compose", "grepai.compose.yaml"),
        override_yaml=override_yaml,
    )


def grepai_compose_summary(render: ComposeRender) -> dict[str, Any]:
    return {
        "project": render.project_name,
        "baseFile": render.base_file.as_posix(),
        "overrideSha256": render.override_sha256,
        "overrideMode": "stdin",
    }

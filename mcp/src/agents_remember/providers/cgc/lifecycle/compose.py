"""CodeGraphContext Docker Compose rendering."""

from __future__ import annotations

import os
from typing import Any

from agents_remember.providers.cgc.lifecycle.core import cgc_backend_settings
from agents_remember.providers.lifecycle.compose_runtime import (
    ComposeRender,
    optional_yaml_line,
    provider_asset_path,
    provider_asset_text,
    render_template,
    required_ownership_labels,
    yaml_environment,
    yaml_labels,
    yaml_port_mapping,
    yaml_scalar,
)

CGC_COMPOSE_PROJECT = "agents-remember-cgc"


def cgc_compose_project(provider_settings: dict[str, Any]) -> str:
    runtime = provider_settings.get("runtime")
    if not isinstance(runtime, dict):
        return CGC_COMPOSE_PROJECT
    return str(runtime.get("composeProject", CGC_COMPOSE_PROJECT))


def cgc_ownership_labels(provider_settings: dict[str, Any]) -> dict[str, str]:
    return required_ownership_labels(provider_settings, "codegraphcontext-code")


def cgc_compose_render(
    provider_settings: dict[str, Any],
    layouts: list[Any],
    *,
    falkordb_port: int | str | None = None,
    browser_port: int | str | None = None,
) -> ComposeRender:
    if not layouts:
        raise ValueError("cgc_compose_render requires at least one layout")
    layout = layouts[0]
    backend = cgc_backend_settings(provider_settings, layout)
    falkordb_port = falkordb_port or backend["falkordbHostPort"]
    browser_port = browser_port or backend["browserHostPort"]
    watcher_services = "\n".join(
        cgc_watcher_service_yaml(provider_settings, watcher_layout) for watcher_layout in layouts
    )
    values = {
        "FALKORDB_IMAGE": yaml_scalar(backend["image"]),
        "FALKORDB_CONTAINER_NAME": yaml_scalar(backend["containerName"]),
        "FALKORDB_PORT": yaml_port_mapping(
            backend["falkordbHost"], falkordb_port, backend["falkordbContainerPort"]
        ),
        "BROWSER_PORT": yaml_port_mapping(
            backend["browserHost"], browser_port, backend["browserContainerPort"]
        ),
        "FALKORDB_DATA_VOLUME": yaml_scalar(
            f"{layout.backend_data_root.as_posix()}:/data"
        ),
        "RUNNER_IMAGE": yaml_scalar(layout.runner_image),
        "RUNNER_BUILD_CONTEXT": yaml_scalar(
            provider_asset_path("docker", "codegraphcontext").as_posix()
        ),
        "RUNNER_USER_BLOCK": cgc_user_block(),
        "SERVICE_LABELS": yaml_labels(cgc_ownership_labels(provider_settings)),
        "RUNNER_WORKING_DIR": yaml_scalar(layout.runtime_root.as_posix()),
        "RUNNER_RUNTIME_VOLUME": yaml_scalar(
            f"{layout.runtime_root.as_posix()}:{layout.runtime_root.as_posix()}"
        ),
        "RUNNER_REPO_VOLUME": yaml_scalar(
            f"{layout.code_repo_root.as_posix()}:{layout.code_repo_root.as_posix()}:ro"
        ),
        "RUNNER_ENVIRONMENT": yaml_environment(cgc_compose_env(layout)),
        "WATCHER_SERVICES": watcher_services,
        "NETWORK_NAME": yaml_scalar(backend["networkName"]),
    }
    override_yaml = render_template(
        provider_asset_text("compose", "codegraphcontext.override.yaml.tmpl"),
        values,
    )
    return ComposeRender(
        project_name=cgc_compose_project(provider_settings),
        base_file=provider_asset_path("compose", "codegraphcontext.compose.yaml"),
        override_yaml=override_yaml,
    )


def cgc_watcher_service_name(layout: Any) -> str:
    return f"watcher-{layout.repo_id}"


def cgc_compose_env(layout: Any) -> dict[str, str]:
    env = layout.env()
    env["FALKORDB_HOST"] = layout.backend_container_name
    return env


def cgc_user() -> str | None:
    if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        return None
    return f"{os.getuid()}:{os.getgid()}"


def cgc_user_block() -> str:
    user = cgc_user()
    return optional_yaml_line("user", user, indent=4)


def cgc_watcher_service_yaml(provider_settings: dict[str, Any], layout: Any) -> str:
    template = provider_asset_text("compose", "codegraphcontext.watcher.yaml.tmpl")
    values = {
        "WATCHER_SERVICE": cgc_watcher_service_name(layout),
        "RUNNER_IMAGE": yaml_scalar(layout.runner_image),
        "WATCHER_CONTAINER_NAME": yaml_scalar(layout.watcher_container_name),
        "WATCHER_USER_BLOCK": cgc_user_block(),
        "SERVICE_LABELS": yaml_labels(cgc_ownership_labels(provider_settings)),
        "WATCHER_WORKING_DIR": yaml_scalar(layout.runtime_root.as_posix()),
        "WATCHER_RUNTIME_VOLUME": yaml_scalar(
            f"{layout.runtime_root.as_posix()}:{layout.runtime_root.as_posix()}"
        ),
        "WATCHER_REPO_VOLUME": yaml_scalar(
            f"{layout.code_repo_root.as_posix()}:{layout.code_repo_root.as_posix()}:ro"
        ),
        "WATCHER_ENVIRONMENT": yaml_environment(cgc_compose_env(layout)),
        "WATCHER_REPO_PATH": yaml_scalar(layout.code_repo_root.as_posix()),
    }
    return render_template(template, values).rstrip()


def cgc_compose_summary(render: ComposeRender) -> dict[str, Any]:
    return {
        "project": render.project_name,
        "baseFile": render.base_file.as_posix(),
        "overrideSha256": render.override_sha256,
        "overrideMode": "stdin",
    }

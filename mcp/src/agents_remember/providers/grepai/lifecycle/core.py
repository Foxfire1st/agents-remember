"""GrepAI lifecycle settings and workspace helpers."""

from __future__ import annotations

import argparse
import platform
import urllib.parse
from pathlib import Path
from typing import Any

# Import from the grepai context package and the common leaf module directly,
# not the providers.context aggregator: the aggregator star-imports this
# provider's context back, so routing through it creates a circular import
# that breaks any entry point touching grepai modules first (surfaced by the
# seed timeout tests).
from agents_remember.providers.context_common import (
    ContextProviderError,
    expand_template,
    stable_provider_id,
)
from agents_remember.providers.grepai.context import (
    GREPAI_NETWORK_NAME,
    GREPAI_OLLAMA_CONTAINER_NAME,
    GREPAI_OLLAMA_DEFAULT_HOST,
    GREPAI_OLLAMA_IMAGE,
    GREPAI_PIN,
    GREPAI_POSTGRES_BACKEND_ID,
    GREPAI_POSTGRES_CONTAINER_NAME,
    GREPAI_POSTGRES_DEFAULT_HOST,
    GREPAI_RUNNER_CONTAINER_NAME,
    GREPAI_RUNNER_IMAGE_REPOSITORY,
    GrepaiMemoryRoot,
    GrepaiRuntimeLayout,
    ensure_grepai_root_gitignore,
    grepai_runtime_layout,
    grepai_runtime_layout_from_provider_settings,
    write_grepai_workspace_config,
)
from agents_remember.providers.lifecycle.provider_settings import (
    grepai_settings_from_file,
)


def grepai_layout_from_args(args: argparse.Namespace) -> tuple[Path, dict[str, Any], GrepaiRuntimeLayout]:
    settings_path, provider_settings = grepai_settings_from_file(
        getattr(args, "from_settings", None)
    )
    if grepai_settings_layout_requested(args, provider_settings):
        return (
            settings_path,
            provider_settings,
            grepai_runtime_layout_from_provider_settings(
                coordination_root=args.coordination_root,
                provider_settings=provider_settings,
            ),
        )

    root = (args.root or args.coordination_root / "memory-repos").resolve()
    runtime_root = (args.runtime_root or args.coordination_root / "providers" / "grepai").resolve()
    return (
        settings_path,
        provider_settings,
        grepai_runtime_layout(
            coordination_root=args.coordination_root,
            workspace_name=str(provider_settings.get("workspace", "agents-remember-memory")),
            roots=(GrepaiMemoryRoot(project_id=stable_provider_id(root.name), path=root),),
            runtime_root=runtime_root,
        ),
    )


def grepai_settings_layout_requested(
    args: argparse.Namespace, provider_settings: dict[str, Any]
) -> bool:
    return (
        bool(provider_settings)
        and getattr(args, "root", None) is None
        and getattr(args, "runtime_root", None) is None
    )


def prepare_grepai_workspace(
    layout: GrepaiRuntimeLayout,
    provider_settings: dict[str, Any],
    *,
    dsn: str,
    dry_run: bool = False,
    project_paths: dict[str, str] | None = None,
    embedder_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gitignored = [] if dry_run else ensure_grepai_root_gitignore(layout.roots)
    if not dry_run:
        write_grepai_workspace_config(
            layout,
            dsn=dsn,
            embedder_settings=embedder_settings or grepai_embedder_settings(provider_settings),
            project_paths=project_paths,
        )
    return {
        "gitignoredRoots": gitignored,
        "workspaceConfigFile": layout.workspace_config_file.as_posix(),
    }


def grepai_runtime_settings(provider_settings: dict[str, Any]) -> dict[str, Any]:
    runtime = provider_settings.get("runtime")
    return runtime if isinstance(runtime, dict) else {"mode": "docker"}


def grepai_docker_mode(provider_settings: dict[str, Any]) -> bool:
    if not provider_settings:
        return False
    return str(grepai_runtime_settings(provider_settings).get("mode", "docker")) == "docker"


def grepai_network_name(provider_settings: dict[str, Any]) -> str:
    network = grepai_runtime_settings(provider_settings).get("network")
    if not isinstance(network, dict):
        network = {}
    return str(network.get("name", GREPAI_NETWORK_NAME))


def grepai_release_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    raise ContextProviderError(
        f"unsupported CPU architecture for GrepAI Docker runner image: {platform.machine()}"
    )


def grepai_runner_settings(
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    *,
    version: str | None = None,
) -> dict[str, Any]:
    runner = grepai_runner_config(provider_settings)
    version = version or GREPAI_PIN.split("==", 1)[1]
    base_variables = {
        "coordination_root": layout.coordination_root.as_posix(),
        "runtimeRoot": layout.runtime_root.as_posix(),
    }
    return {
        "mode": "docker",
        "image": grepai_runner_image(runner, version),
        "version": version,
        "releaseArch": grepai_release_arch(),
        "imageLockFile": grepai_runner_image_lock_file(runner, base_variables),
        "buildRoot": grepai_runner_build_root(runner, base_variables),
        "containerName": str(runner.get("containerName", GREPAI_RUNNER_CONTAINER_NAME)),
        "runtimeMount": str(runner.get("runtimeMount", "/grepai/runtime")),
        "logsMount": str(runner.get("logsMount", "/grepai/logs")),
        "rootsMount": str(runner.get("rootsMount", "/grepai/roots")),
    }


def grepai_runner_config(provider_settings: dict[str, Any]) -> dict[str, Any]:
    runner = grepai_runtime_settings(provider_settings).get("runner")
    return runner if isinstance(runner, dict) else {}


def grepai_runner_image(runner: dict[str, Any], version: str) -> str:
    image = str(runner.get("image", f"{GREPAI_RUNNER_IMAGE_REPOSITORY}:{version}")).strip()
    if not image or "<" in image or ">" in image:
        raise ContextProviderError("grepai runner.image must be a concrete Docker image tag")
    return image


def grepai_runner_build_root(runner: dict[str, Any], base_variables: dict[str, str]) -> Path:
    return Path(
        expand_template(str(runner.get("buildRoot", "<runtimeRoot>/image")), base_variables)
    ).resolve()


def grepai_runner_image_lock_file(
    runner: dict[str, Any], base_variables: dict[str, str]
) -> Path:
    template = str(
        runner.get(
            "imageLockFile",
            "<coordination_root>/providers/requirements/grepai-runner-docker.lock",
        )
    )
    return Path(expand_template(template, base_variables)).resolve()


def grepai_embedder_backend_settings(
    provider_settings: dict[str, Any], layout: GrepaiRuntimeLayout
) -> dict[str, Any]:
    embedder = grepai_embedder_settings(provider_settings)
    provider = str(embedder.get("provider", "ollama"))
    if provider != "ollama":
        return {"provider": provider, "mode": "external", "ok": True}
    backend_settings = embedder.get("backend")
    if not isinstance(backend_settings, dict):
        backend_settings = {}
    ports = backend_settings.get("ports")
    if not isinstance(ports, dict):
        ports = {}
    http_port = ports.get("http")
    if not isinstance(http_port, dict):
        http_port = {}
    base_variables = {
        "coordination_root": layout.coordination_root.as_posix(),
        "runtimeRoot": layout.runtime_root.as_posix(),
    }
    runtime_root = Path(
        expand_template(
            str(
                backend_settings.get(
                    "runtimeRoot", "<coordination_root>/providers/data/grepai/ollama"
                )
            ),
            base_variables,
        )
    ).resolve()
    data_root = Path(
        expand_template(
            str(backend_settings.get("dataRoot", "<embedderRuntimeRoot>/data")),
            {
                **base_variables,
                "embedderRuntimeRoot": runtime_root.as_posix(),
            },
        )
    ).resolve()
    image_lock_file = Path(
        expand_template(
            str(
                backend_settings.get(
                    "imageLockFile",
                    "<coordination_root>/providers/requirements/grepai-ollama-docker.lock",
                )
            ),
            base_variables,
        )
    ).resolve()
    resolved: dict[str, Any] = {
        "provider": provider,
        "mode": str(backend_settings.get("mode", "docker")),
        "image": str(backend_settings.get("image", GREPAI_OLLAMA_IMAGE)),
        "imageLockFile": image_lock_file,
        "runtimeRoot": runtime_root,
        "dataRoot": data_root,
        "dataDestination": str(backend_settings.get("dataDestination", "/root/.ollama")),
        "containerName": str(backend_settings.get("containerName", GREPAI_OLLAMA_CONTAINER_NAME)),
        "httpHost": str(http_port.get("bindHost", GREPAI_OLLAMA_DEFAULT_HOST)),
        "httpHostPort": http_port.get("hostPort", "auto"),
        "httpContainerPort": int(http_port.get("containerPort", 11434)),
        "model": str(embedder.get("model", "nomic-embed-text")),
        "dimensions": embedder.get("dimensions", 768),
    }
    # Worktree embedders carry the workspace ollama container to seed the model from,
    # avoiding a per-worktree network re-pull. Absent for the workspace embedder itself.
    seed_from = backend_settings.get("seedFromContainer")
    if isinstance(seed_from, str) and seed_from:
        resolved["seedFromContainer"] = seed_from
    return resolved


def grepai_root_container_path(project_id: str, runner: dict[str, Any]) -> str:
    """Container path where a live memory root is bind-mounted inside the watcher."""

    return f"{runner['rootsMount'].rstrip('/')}/{project_id}"


def grepai_container_project_paths(layout: GrepaiRuntimeLayout, runner: dict[str, Any]) -> dict[str, str]:
    return {
        root.project_id: grepai_root_container_path(root.project_id, runner)
        for root in layout.roots
    }


def grepai_container_env(runner: dict[str, Any]) -> dict[str, str]:
    runtime_mount = runner["runtimeMount"].rstrip("/")
    return {
        "HOME": f"{runtime_mount}/home",
        "XDG_STATE_HOME": f"{runtime_mount}/state/xdg",
        "XDG_CACHE_HOME": f"{runtime_mount}/cache/xdg",
    }


def grepai_container_dsn(backend: dict[str, Any]) -> str:
    return grepai_dsn(
        backend,
        host=backend["containerName"],
        port=backend["postgresContainerPort"],
    )


def grepai_container_embedder_settings(
    provider_settings: dict[str, Any], embedder_backend: dict[str, Any]
) -> dict[str, Any]:
    settings = dict(grepai_embedder_settings(provider_settings))
    if embedder_backend.get("provider") == "ollama" and embedder_backend.get("mode") == "docker":
        settings["provider"] = "ollama"
        settings["model"] = embedder_backend["model"]
        settings["endpoint"] = (
            f"http://{embedder_backend['containerName']}:{embedder_backend['httpContainerPort']}"
        )
        settings.setdefault("dimensions", embedder_backend.get("dimensions", 768))
    return settings


def grepai_backend_settings(provider_settings: dict[str, Any], layout: GrepaiRuntimeLayout) -> dict[str, Any]:
    backend_settings = grepai_backend_settings_dict(provider_settings)
    ports = dict_value(backend_settings.get("ports"))
    postgres_port = dict_value(ports.get("postgres"))
    postgres_settings = dict_value(backend_settings.get("postgres"))
    image = concrete_grepai_backend_image(backend_settings)

    return {
        "id": backend_settings.get("id", GREPAI_POSTGRES_BACKEND_ID),
        "type": backend_settings.get("type", "postgres"),
        "mode": backend_settings.get("mode", "docker"),
        "image": image,
        "imageLockFile": grepai_backend_image_lock_path(layout, backend_settings),
        "containerName": str(backend_settings.get("containerName", GREPAI_POSTGRES_CONTAINER_NAME)),
        "postgresHost": str(postgres_port.get("bindHost", GREPAI_POSTGRES_DEFAULT_HOST)),
        "postgresHostPort": postgres_port.get("hostPort", "auto"),
        "postgresContainerPort": int(postgres_port.get("containerPort", 5432)),
        "postgresUser": str(postgres_settings.get("user", "grepai")),
        "postgresPassword": str(postgres_settings.get("password", "grepai")),
        "postgresDatabase": str(postgres_settings.get("database", "grepai")),
        "dataDestination": str(backend_settings.get("dataDestination", "/var/lib/postgresql/data")),
    }


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def grepai_backend_settings_dict(provider_settings: dict[str, Any]) -> dict[str, Any]:
    return dict_value(provider_settings.get("backend"))


def concrete_grepai_backend_image(backend_settings: dict[str, Any]) -> str:
    image = str(backend_settings.get("image", "pgvector/pgvector:pg16")).strip()
    if image and "<" not in image and ">" not in image:
        return image
    raise ContextProviderError(
        "grepai backend.image must be a concrete pgvector/postgres tag or digest"
    )


def grepai_backend_image_lock_path(layout: GrepaiRuntimeLayout, backend_settings: dict[str, Any]) -> Path:
    image_lock_file = backend_settings.get("imageLockFile")
    if not image_lock_file:
        return (
            layout.coordination_root / "providers" / "requirements" / "grepai-postgres-docker.lock"
        )
    return Path(expand_template(str(image_lock_file), grepai_backend_template_vars(layout))).resolve()


def grepai_backend_template_vars(layout: GrepaiRuntimeLayout) -> dict[str, str]:
    return {
        "coordination_root": layout.coordination_root.as_posix(),
        "runtimeRoot": layout.runtime_root.as_posix(),
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
    }


def grepai_dsn(backend: dict[str, Any], *, host: str, port: int | str) -> str:
    user = urllib.parse.quote(str(backend["postgresUser"]), safe="")
    password = urllib.parse.quote(str(backend["postgresPassword"]), safe="")
    database = urllib.parse.quote(str(backend["postgresDatabase"]), safe="")
    return f"postgres://{user}:{password}@{host}:{port}/{database}?sslmode=disable"


def grepai_embedder_settings(provider_settings: dict[str, Any]) -> dict[str, Any]:
    embedder = provider_settings.get("embedder")
    if not isinstance(embedder, dict):
        embedder = provider_settings.get("embedding")
    return embedder if isinstance(embedder, dict) else {}


def command_ok(result: dict[str, Any] | None) -> bool:
    return result is None or result.get("returncode") == 0


def model_present(model: dict[str, Any] | None) -> bool:
    return model is None or bool(model["present"])

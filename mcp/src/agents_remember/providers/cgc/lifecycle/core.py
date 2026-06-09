"""CodeGraphContext lifecycle settings and layout helpers."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.context import (
    CgcRuntimeLayout,
    ContextProviderError,
    cgc_runtime_layout,
    cgc_runtime_layout_from_provider_settings,
    cleanup_cgc_runtime_artifacts,
    ensure_cgc_runtime_layout,
    expand_template,
    stable_provider_id,
)
from agents_remember.providers.lifecycle.provider_settings import (
    cgc_settings_from_file,
)
from agents_remember.providers.lifecycle.state_files import (
    read_json,
    write_json,
)


def cgc_uses_settings(args: argparse.Namespace) -> bool:
    return (
        getattr(args, "from_settings", None) is not None
        or getattr(args, "code_repo_root", None) is None
    )


def cgc_layout_from_args(args: argparse.Namespace) -> CgcRuntimeLayout:
    if cgc_uses_settings(args):
        _, provider_settings = cgc_settings_from_file(
            args.coordination_root, getattr(args, "from_settings", None)
        )
        root_settings = cgc_root_from_settings(provider_settings, args.repo_id)
        return cgc_runtime_layout_from_provider_settings(
            coordination_root=args.coordination_root,
            provider_settings=provider_settings,
            root_settings=root_settings,
        )
    if not args.repo_id or not args.code_repo_root:
        raise ContextProviderError(
            "CGC manual override commands require --repo-id and --code-repo-root"
        )
    return cgc_runtime_layout(
        coordination_root=args.coordination_root,
        repo_id=args.repo_id,
        code_repo_root=args.code_repo_root,
    )


def cgc_validated_roots(provider_settings: dict[str, Any]) -> list[dict[str, Any]]:
    roots = provider_settings.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ContextProviderError("codegraphcontext-code.roots must be a non-empty array")
    return [cgc_validated_root(root) for root in roots]


def cgc_validated_root(root: Any) -> dict[str, Any]:
    if not isinstance(root, dict):
        raise ContextProviderError("each codegraphcontext-code root must define repoId and path")
    if "repoId" not in root or "path" not in root:
        raise ContextProviderError("each codegraphcontext-code root must define repoId and path")
    return root


def cgc_select_root(roots: list[dict[str, Any]], repo_id: str | None) -> dict[str, Any]:
    if repo_id is None:
        if len(roots) != 1:
            raise ContextProviderError("select one configured CGC root with --repo-id")
        return roots[0]
    stable = stable_provider_id(repo_id)
    for root in roots:
        if stable_provider_id(str(root["repoId"])) == stable:
            return root
    raise ContextProviderError(
        f"repo id is not configured in codegraphcontext-code.roots: {repo_id}"
    )


def cgc_root_from_settings(
    provider_settings: dict[str, Any], repo_id: str | None
) -> dict[str, Any]:
    return cgc_select_root(cgc_validated_roots(provider_settings), repo_id)


def cgc_all_layouts_from_settings(
    args: argparse.Namespace,
) -> tuple[Path, dict[str, Any], list[CgcRuntimeLayout]]:
    settings_path, provider_settings = cgc_settings_from_file(
        args.coordination_root, getattr(args, "from_settings", None)
    )
    repo_id = getattr(args, "repo_id", None)
    selected = (
        [cgc_root_from_settings(provider_settings, repo_id)]
        if repo_id
        else cgc_validated_roots(provider_settings)
    )
    layouts = [
        cgc_runtime_layout_from_provider_settings(
            coordination_root=args.coordination_root,
            provider_settings=provider_settings,
            root_settings=root_settings,
        )
        for root_settings in selected
    ]
    return settings_path, provider_settings, layouts


def cgc_project_layouts_from_settings(
    args: argparse.Namespace,
    primary_repo_id: str,
) -> tuple[Path, dict[str, Any], list[CgcRuntimeLayout]]:
    """Return all configured layouts with the target repo first for runner mounts."""

    settings_path, provider_settings = cgc_settings_from_file(
        args.coordination_root, getattr(args, "from_settings", None)
    )
    roots = cgc_validated_roots(provider_settings)
    primary = cgc_root_from_settings(provider_settings, primary_repo_id)
    primary_stable = stable_provider_id(str(primary["repoId"]))
    ordered_roots = [
        primary,
        *[
            root
            for root in roots
            if stable_provider_id(str(root["repoId"])) != primary_stable
        ],
    ]
    layouts = [
        cgc_runtime_layout_from_provider_settings(
            coordination_root=args.coordination_root,
            provider_settings=provider_settings,
            root_settings=root_settings,
        )
        for root_settings in ordered_roots
    ]
    return settings_path, provider_settings, layouts


def cgc_backend_settings(provider_settings: dict[str, Any], layout: CgcRuntimeLayout) -> dict[str, Any]:
    backend_settings = cgc_backend_settings_dict(provider_settings)
    ports = cgc_backend_ports_dict(backend_settings)
    network = dict_value(backend_settings.get("network"))
    falkordb_port = dict_value(ports.get("falkordb"))
    browser_port = dict_value(ports.get("browser"))
    image = concrete_cgc_backend_image(backend_settings)
    image_lock_path = cgc_backend_image_lock_path(layout, backend_settings)

    falkordb_host = str(falkordb_port.get("bindHost", "127.0.0.1"))
    browser_host = str(browser_port.get("bindHost", "127.0.0.1"))
    return {
        "id": backend_settings.get("id", "codegraphcontext-falkordb"),
        "type": backend_settings.get("type", "falkordb-remote"),
        "mode": backend_settings.get("mode", "docker"),
        "image": image,
        "imageLockFile": image_lock_path,
        "containerName": str(backend_settings.get("containerName", "ar-cgc-falkordb")),
        # FalkorDB v4 writes to /var/lib/falkordb/data, not /data; binding the
        # wrong destination leaves graphs in the ephemeral container layer.
        "dataDestination": str(
            backend_settings.get("dataDestination", "/var/lib/falkordb/data")
        ),
        "networkName": str(network.get("name", layout.network_name)),
        "falkordbHost": falkordb_host,
        "falkordbHostPort": falkordb_port.get("hostPort", "auto"),
        "falkordbContainerPort": int(falkordb_port.get("containerPort", 6379)),
        "browserHost": browser_host,
        "browserHostPort": browser_port.get("hostPort", "auto"),
        "browserContainerPort": int(browser_port.get("containerPort", 3000)),
    }


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def cgc_backend_settings_dict(provider_settings: dict[str, Any]) -> dict[str, Any]:
    return dict_value(provider_settings.get("backend"))


def cgc_backend_ports_dict(backend_settings: dict[str, Any]) -> dict[str, Any]:
    return dict_value(backend_settings.get("ports"))


def concrete_cgc_backend_image(backend_settings: dict[str, Any]) -> str:
    image = str(backend_settings.get("image", "")).strip()
    if image and "<" not in image and ">" not in image:
        return image
    raise ContextProviderError(
        "codegraphcontext backend.image must be a concrete falkordb/falkordb tag or digest"
    )


def cgc_backend_image_lock_path(layout: CgcRuntimeLayout, backend_settings: dict[str, Any]) -> Path:
    image_lock_file = backend_settings.get("imageLockFile")
    if not image_lock_file:
        return (
            layout.coordination_root
            / "providers"
            / "requirements"
            / "codegraphcontext-falkordb-docker.lock"
        )
    return Path(expand_template(str(image_lock_file), cgc_backend_template_vars(layout))).resolve()


def cgc_backend_template_vars(layout: CgcRuntimeLayout) -> dict[str, str]:
    return {
        "coordination_root": layout.coordination_root.as_posix(),
        "runtimeRoot": layout.runtime_root.parent.as_posix(),
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
    }


def cgc_scoped_args(
    args: argparse.Namespace, repo_id: str, action: str | None = None
) -> argparse.Namespace:
    """Return a copy of args scoped to one configured CGC repository."""

    scoped = argparse.Namespace(**vars(args))
    scoped.repo_id = repo_id
    if action is not None:
        scoped.action = action
    return scoped


def cgc_apply_layout_state(layout: CgcRuntimeLayout, args: argparse.Namespace, settings_path: Path) -> None:
    if args.dry_run:
        return
    ensure_cgc_runtime_layout(layout)
    state = read_json(layout.state_file)
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "codeRepoRoot": layout.code_repo_root.as_posix(),
            "runtimeRoot": layout.runtime_root.as_posix(),
            "stateFile": layout.state_file.as_posix(),
            "backendStateFile": layout.backend_state_file.as_posix(),
            "lastAction": "apply-settings",
            "settingsFile": settings_path.as_posix(),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    write_json(layout.state_file, state)


def cgc_layout_instance(layout: CgcRuntimeLayout) -> dict[str, Any]:
    return {
        "repoId": layout.repo_id,
        "codeRepoRoot": layout.code_repo_root.as_posix(),
        "runtimeRoot": layout.runtime_root.as_posix(),
        "stateFile": layout.state_file.as_posix(),
        "watchLog": layout.watch_log_file.as_posix(),
        "graphName": layout.env()["FALKORDB_GRAPH_NAME"],
    }


def cgc_apply_backend_state(
    layouts: list[CgcRuntimeLayout],
    *,
    args: argparse.Namespace,
    settings_path: Path,
    backend_settings: dict[str, Any],
) -> None:
    if not layouts or args.dry_run:
        return
    backend_state = read_json(layouts[0].backend_state_file)
    backend_state.setdefault("provider", "codegraphcontext")
    backend_state.setdefault("backend", {})
    backend_state["backend"].update(cgc_backend_configured_state(layouts[0], backend_settings))
    backend_state["settingsFile"] = settings_path.as_posix()
    backend_state["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(layouts[0].backend_state_file, backend_state)


def cgc_backend_configured_state(layout: CgcRuntimeLayout, backend_settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": backend_settings.get("id", "codegraphcontext-falkordb"),
        "type": backend_settings.get("type", "falkordb-remote"),
        "mode": backend_settings.get("mode", "docker"),
        "image": backend_settings.get("image"),
        "imageLockFile": backend_settings.get("imageLockFile"),
        "containerName": backend_settings.get("containerName"),
        "network": backend_settings.get("network"),
        "runtimeRoot": layout.backend_root.as_posix(),
        "dataRoot": layout.backend_data_root.as_posix(),
        "status": read_json(layout.backend_state_file).get("backend", {}).get(
            "status", "configured"
        ),
    }


def cgc_apply_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    cleanup = cleanup_cgc_runtime_artifacts(layouts, dry_run=args.dry_run)
    for layout in layouts:
        cgc_apply_layout_state(layout, args, settings_path)
    cgc_apply_backend_state(
        layouts,
        args=args,
        settings_path=settings_path,
        backend_settings=provider_settings.get("backend", {}),
    )
    return {
        "provider": "codegraphcontext",
        "action": "apply-settings",
        "ok": True,
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "backendRuntimeRoot": layouts[0].backend_root.as_posix() if layouts else None,
        "backendDataRoot": layouts[0].backend_data_root.as_posix() if layouts else None,
        "removedArtifacts": cleanup,
        "instances": [cgc_layout_instance(layout) for layout in layouts],
    }

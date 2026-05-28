"""CodeGraphContext provider setup facade."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers.cgc.seed import (
    CGC_PROVIDER_ID,
    cgc_extra_args,
    cgc_seed_bundle,
)
from agents_remember.providers.context import (
    CGC_NETWORK_NAME,
    CGC_WATCHER_CONTAINER_PREFIX,
)
from agents_remember.providers.identity import (
    provider_instance_id,
    provider_ownership_labels,
    scoped_name,
)
from agents_remember.providers.setup_common import (
    context_providers,
    run_lifecycle,
    selected_provider_enabled,
    stable_provider_id,
)


@dataclass(frozen=True)
class IsolatedCgcOptions:
    runtime_root: Path | None = None
    settings_path: Path | None = None
    container_name: str | None = None


def isolated_cgc_settings(
    args: Any,
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    if args.cgc_isolated_runtime_root is None:
        return None

    provider = _cgc_provider(settings)
    if provider is None:
        return None
    _require_isolated_target_root(args)
    cgc = _isolated_cgc_provider(args, provider)
    return _isolated_settings_payload(cgc)


def _cgc_provider(settings: dict[str, Any]) -> dict[str, Any] | None:
    provider = context_providers(settings).get(CGC_PROVIDER_ID)
    return provider if isinstance(provider, dict) else None


def _require_isolated_target_root(args: Any) -> None:
    if args.cgc_seed_target_repo_root is None:
        raise RuntimeError("--cgc-isolated-runtime-root requires --cgc-seed-target-repo-root")


def _isolated_settings_payload(cgc: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "contextProviders": {
            "enabled": True,
            "providers": {
                CGC_PROVIDER_ID: cgc,
            },
            "policy": {
                "discoveryOnly": True,
                "sourceProofRequired": True,
            },
        },
    }


def _isolated_cgc_provider(args: Any, provider: dict[str, Any]) -> dict[str, Any]:
    isolated_root = args.cgc_isolated_runtime_root.resolve()
    repo_id = stable_provider_id(args.cgc_seed_repo_id or args.cgc_seed_target_repo_root.name)
    instance_id = provider_instance_id(
        "worktree",
        isolated_root,
        workspace_name=args.coordination_root.parent.name,
    )
    cgc = json.loads(json.dumps(provider))
    cgc.update(_isolated_cgc_base_fields(args, isolated_root, repo_id, instance_id))
    cgc["runtime"] = _isolated_cgc_runtime(cgc.get("runtime"), instance_id)
    cgc["backend"] = _isolated_cgc_backend(
        args,
        cgc.get("backend"),
        isolated_root,
        repo_id,
        instance_id,
    )
    return cgc


def _isolated_cgc_base_fields(
    args: Any,
    isolated_root: Path,
    repo_id: str,
    instance_id: str,
) -> dict[str, Any]:
    runtime_root = isolated_root / "providers" / "runners" / "codegraphcontext" / instance_id
    log_file = (
        isolated_root
        / "logs"
        / "providers"
        / "codegraphcontext"
        / instance_id
        / "<repoId>"
        / "watch.log"
    )
    return {
        "enabled": True,
        "instance": {
            "id": instance_id,
            "scope": "worktree",
            "labels": provider_ownership_labels(
                provider_id=CGC_PROVIDER_ID,
                instance_id=instance_id,
                scope="worktree",
                coordination_root=args.coordination_root,
            ),
        },
        "roots": [
            {
                "repoId": repo_id,
                "path": args.cgc_seed_target_repo_root.resolve().as_posix(),
            }
        ],
        "runtimeRoot": runtime_root.as_posix(),
        "instanceRootTemplate": "<runtimeRoot>/<repoId>",
        "requirementsFile": (
            args.coordination_root / "providers" / "requirements" / "codegraphcontext.txt"
        ).as_posix(),
        "patchesRoot": (
            args.coordination_root / "providers" / "patches" / "codegraphcontext"
        ).as_posix(),
        "stateFileTemplate": "<instanceRoot>/provider-state.json",
        "watch": {
            "mode": "managed-foreground",
            "cwdTemplate": "<instanceRoot>",
            "logFileTemplate": log_file.as_posix(),
        },
    }


def _isolated_cgc_runtime(runtime_settings: Any, instance_id: str) -> dict[str, Any]:
    runtime = runtime_settings if isinstance(runtime_settings, dict) else {}
    runtime = dict(runtime)
    runner = runtime.get("runner")
    runner = dict(runner) if isinstance(runner, dict) else {}
    runner["buildRoot"] = "<runtimeRoot>/image"
    runner["containerNameTemplate"] = (
        f"{scoped_name(CGC_WATCHER_CONTAINER_PREFIX, instance_id)}-<repoId>"
    )
    runtime["mode"] = "docker"
    runtime["composeProject"] = scoped_name("agents-remember-cgc", instance_id)
    runtime["runner"] = runner
    return runtime


def _isolated_cgc_backend(
    args: Any,
    backend_settings: Any,
    isolated_root: Path,
    repo_id: str,
    instance_id: str,
) -> dict[str, Any]:
    backend = backend_settings if isinstance(backend_settings, dict) else {}
    backend = dict(backend)
    backend.update(
        {
            "runtimeRoot": (
                isolated_root
                / "providers"
                / "data"
                / "codegraphcontext"
                / instance_id
                / "falkordb"
            ).as_posix(),
            "dataRoot": "<backendRuntimeRoot>/data",
            "imageLockFile": (
                isolated_root
                / "providers"
                / "requirements"
                / "codegraphcontext-falkordb-docker.lock"
            ).as_posix(),
            "containerName": _isolated_cgc_container_name(args, instance_id, repo_id),
            "network": {"name": scoped_name(CGC_NETWORK_NAME, instance_id)},
        }
    )
    return backend


def _isolated_cgc_container_name(args: Any, instance_id: str, repo_id: str) -> str:
    return (
        args.cgc_isolated_container_name
        or scoped_name("ar-cgc-falkordb", instance_id, repo_id)
    )


def write_isolated_cgc_settings(
    args: Any,
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    data = isolated_cgc_settings(args, settings)
    if data is None:
        args.cgc_from_settings = None
        return None

    path = args.cgc_isolated_settings_path or (
        args.cgc_isolated_runtime_root.resolve()
        / "settings"
        / "codegraphcontext-provider-settings.json"
    )
    args.cgc_from_settings = path.resolve()
    if not args.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {
        "path": args.cgc_from_settings.as_posix(),
        "dryRun": args.dry_run,
        "settings": data if args.dry_run else None,
    }


def install_enabled_provider(args: Any, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not selected_provider_enabled(args, settings, CGC_PROVIDER_ID):
        return []
    return [
        run_lifecycle(
            args.coordination_root,
            "cgc",
            "install-all",
            timeout=args.timeout,
            dry_run=args.dry_run,
            extra_args=cgc_extra_args(args),
        )
    ]


def prepare_enabled_provider(args: Any, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not selected_provider_enabled(args, settings, CGC_PROVIDER_ID):
        return []
    seed = cgc_seed_bundle(args, settings)
    results = [{"provider": "codegraphcontext", "action": "seed", **seed}]
    results.append(_refresh_after_seed(args, seed))
    return results


def _refresh_after_seed(args: Any, seed: dict[str, Any]) -> dict[str, Any]:
    if seed.get("ok"):
        return {
            "ok": True,
            "provider": "codegraphcontext",
            "action": "refresh-all",
            "skipped": True,
            "reason": "CGC seed loaded successfully",
        }
    if args.cgc_refresh_fallback:
        return run_lifecycle(
            args.coordination_root,
            "cgc",
            "refresh-all",
            timeout=args.timeout,
            dry_run=args.dry_run,
            extra_args=cgc_extra_args(args),
        )
    return {
        "ok": False,
        "provider": "codegraphcontext",
        "action": "refresh-all",
        "skipped": True,
        "reason": "CGC seed failed and refresh fallback is disabled",
    }

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
    cgc = json.loads(json.dumps(provider))
    cgc.update(_isolated_cgc_base_fields(args, isolated_root, repo_id))
    cgc["backend"] = _isolated_cgc_backend(args, cgc.get("backend"), isolated_root, repo_id)
    return cgc


def _isolated_cgc_base_fields(args: Any, isolated_root: Path, repo_id: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "roots": [
            {
                "repoId": repo_id,
                "path": args.cgc_seed_target_repo_root.resolve().as_posix(),
            }
        ],
        "runtimeRoot": (isolated_root / "providers" / "runners" / "codegraphcontext").as_posix(),
        "instanceRootTemplate": "<runtimeRoot>/<repoId>",
        "venvRoot": (
            args.coordination_root / "providers" / "_venvs" / "codegraphcontext"
        ).as_posix(),
        "requirementsFile": (
            args.coordination_root / "providers" / "requirements" / "codegraphcontext.txt"
        ).as_posix(),
        "patchesRoot": (
            args.coordination_root / "providers" / "patches" / "codegraphcontext"
        ).as_posix(),
        "stateFileTemplate": "<instanceRoot>/provider-state.json",
    }


def _isolated_cgc_backend(
    args: Any,
    backend_settings: Any,
    isolated_root: Path,
    repo_id: str,
) -> dict[str, Any]:
    backend = backend_settings if isinstance(backend_settings, dict) else {}
    backend = dict(backend)
    backend.update(
        {
            "runtimeRoot": (
                isolated_root / "providers" / "data" / "codegraphcontext" / "falkordb"
            ).as_posix(),
            "dataRoot": "<backendRuntimeRoot>/data",
            "imageLockFile": (
                isolated_root
                / "providers"
                / "requirements"
                / "codegraphcontext-falkordb-docker.lock"
            ).as_posix(),
            "containerName": _isolated_cgc_container_name(args, isolated_root, repo_id),
        }
    )
    return backend


def _isolated_cgc_container_name(args: Any, isolated_root: Path, repo_id: str) -> str:
    return (
        args.cgc_isolated_container_name
        or f"ar-cgc-falkordb-{repo_id}-{stable_provider_id(isolated_root.name)}"
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

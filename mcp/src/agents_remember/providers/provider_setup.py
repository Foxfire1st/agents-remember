#!/usr/bin/env python3
"""Prepare Agents Remember context providers from package-local MCP code."""

from __future__ import annotations

import argparse
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.providers import setup_common, setup_reporting
from agents_remember.providers.cgc import bundle as cgc_bundle
from agents_remember.providers.cgc import seed as cgc_seed
from agents_remember.providers.cgc import setup as cgc_setup
from agents_remember.providers.cgc.seed import CgcSeedOptions
from agents_remember.providers.cgc.setup import IsolatedCgcOptions
from agents_remember.providers.grepai import setup as grepai_setup
from agents_remember.providers.grepai.setup import GrepaiSeedOptions, IsolatedGrepaiOptions

load_settings = setup_common.load_settings
provider_enabled = setup_common.provider_enabled
require_settings_path = setup_common.require_settings_path
run_command = setup_common.run_command
run_lifecycle = setup_common.run_lifecycle
selected_provider_enabled = setup_common.selected_provider_enabled
settings_path = setup_common.settings_path
subprocess = setup_common.subprocess

cgc_extra_args = cgc_seed.cgc_extra_args
cgc_seed_bundle = cgc_seed.cgc_seed_bundle
isolated_cgc_settings = cgc_setup.isolated_cgc_settings
isolated_grepai_settings = grepai_setup.isolated_grepai_settings
rewrite_cgc_bundle_paths = cgc_bundle.rewrite_cgc_bundle_paths


@dataclass(frozen=True)
class ProviderSetupRequest:
    action: str
    coordination_root: Path
    settings_path: Path
    timeout: int = 1800
    dry_run: bool = False
    skip_watchers: bool = False
    skip_grepai: bool = False
    cgc_refresh_fallback: bool = True
    cgc_seed: CgcSeedOptions = field(default_factory=CgcSeedOptions)
    cgc_isolated: IsolatedCgcOptions = field(default_factory=IsolatedCgcOptions)
    grepai_seed: GrepaiSeedOptions = field(default_factory=GrepaiSeedOptions)
    grepai_isolated: IsolatedGrepaiOptions = field(default_factory=IsolatedGrepaiOptions)

    def normalized(self) -> ProviderSetupRequest:
        return ProviderSetupRequest(
            action=self.action,
            coordination_root=self.coordination_root.resolve(),
            settings_path=self.settings_path.resolve(),
            timeout=self.timeout,
            dry_run=self.dry_run,
            skip_watchers=self.skip_watchers,
            skip_grepai=self.skip_grepai,
            cgc_refresh_fallback=self.cgc_refresh_fallback,
            cgc_seed=CgcSeedOptions(
                source_coordination_root=_resolve_optional_path(
                    self.cgc_seed.source_coordination_root
                ),
                source_settings_path=_resolve_optional_path(self.cgc_seed.source_settings_path),
                repo_id=self.cgc_seed.repo_id,
                source_repo_id=self.cgc_seed.source_repo_id,
                source_repo_root=_resolve_optional_path(self.cgc_seed.source_repo_root),
                target_repo_root=_resolve_optional_path(self.cgc_seed.target_repo_root),
                bundle_dir=_resolve_optional_path(self.cgc_seed.bundle_dir),
                allow_commit_mismatch=self.cgc_seed.allow_commit_mismatch,
                allow_same_coordination_root=self.cgc_seed.allow_same_coordination_root,
            ),
            cgc_isolated=IsolatedCgcOptions(
                runtime_root=_resolve_optional_path(self.cgc_isolated.runtime_root),
                settings_path=_resolve_optional_path(self.cgc_isolated.settings_path),
                container_name=self.cgc_isolated.container_name,
            ),
            grepai_seed=GrepaiSeedOptions(
                source_coordination_root=_resolve_optional_path(
                    self.grepai_seed.source_coordination_root
                ),
                source_settings_path=_resolve_optional_path(
                    self.grepai_seed.source_settings_path
                ),
                project_id=self.grepai_seed.project_id,
                target_memory_root=_resolve_optional_path(self.grepai_seed.target_memory_root),
            ),
            grepai_isolated=IsolatedGrepaiOptions(
                runtime_root=_resolve_optional_path(self.grepai_isolated.runtime_root),
                settings_path=_resolve_optional_path(self.grepai_isolated.settings_path),
                project_id=self.grepai_isolated.project_id,
                target_memory_root=_resolve_optional_path(
                    self.grepai_isolated.target_memory_root
                ),
                allow_missing_roots=self.grepai_isolated.allow_missing_roots,
            ),
        )


def _resolve_optional_path(path: Path | None) -> Path | None:
    return path.resolve() if path is not None else None


def request_from_args(args: argparse.Namespace) -> ProviderSetupRequest:
    return ProviderSetupRequest(
        action=args.action,
        coordination_root=args.coordination_root,
        settings_path=require_settings_path(args.from_settings),
        timeout=args.timeout,
        dry_run=args.dry_run,
        skip_watchers=args.skip_watchers,
        skip_grepai=args.skip_grepai,
        cgc_refresh_fallback=args.cgc_refresh_fallback,
        cgc_seed=CgcSeedOptions(
            source_coordination_root=args.cgc_seed_source_coordination_root,
            source_settings_path=args.cgc_seed_source_from_settings,
            repo_id=args.cgc_seed_repo_id,
            source_repo_id=args.cgc_seed_source_repo_id,
            source_repo_root=args.cgc_seed_source_repo_root,
            target_repo_root=args.cgc_seed_target_repo_root,
            bundle_dir=args.cgc_seed_bundle_dir,
            allow_commit_mismatch=args.cgc_seed_allow_commit_mismatch,
            allow_same_coordination_root=args.cgc_seed_allow_same_coordination_root,
        ),
        cgc_isolated=IsolatedCgcOptions(
            runtime_root=args.cgc_isolated_runtime_root,
            settings_path=args.cgc_isolated_settings_path,
            container_name=args.cgc_isolated_container_name,
        ),
        grepai_seed=GrepaiSeedOptions(
            source_coordination_root=args.grepai_seed_source_coordination_root,
            source_settings_path=args.grepai_seed_source_from_settings,
            project_id=args.grepai_seed_project_id,
            target_memory_root=args.grepai_seed_target_memory_root,
        ),
        grepai_isolated=IsolatedGrepaiOptions(
            runtime_root=args.grepai_isolated_runtime_root,
            settings_path=args.grepai_isolated_settings_path,
            project_id=args.grepai_seed_project_id,
            target_memory_root=args.grepai_seed_target_memory_root,
            allow_missing_roots=args.grepai_allow_missing_roots,
        ),
    )


def args_from_request(request: ProviderSetupRequest) -> argparse.Namespace:
    normalized = request.normalized()
    return argparse.Namespace(
        action=normalized.action,
        coordination_root=normalized.coordination_root,
        from_settings=normalized.settings_path,
        timeout=normalized.timeout,
        dry_run=normalized.dry_run,
        skip_watchers=normalized.skip_watchers,
        skip_grepai=normalized.skip_grepai,
        cgc_refresh_fallback=normalized.cgc_refresh_fallback,
        cgc_seed_source_coordination_root=normalized.cgc_seed.source_coordination_root,
        cgc_seed_source_from_settings=normalized.cgc_seed.source_settings_path,
        cgc_seed_repo_id=normalized.cgc_seed.repo_id,
        cgc_seed_source_repo_id=normalized.cgc_seed.source_repo_id,
        cgc_seed_source_repo_root=normalized.cgc_seed.source_repo_root,
        cgc_seed_target_repo_root=normalized.cgc_seed.target_repo_root,
        cgc_seed_bundle_dir=normalized.cgc_seed.bundle_dir,
        cgc_seed_allow_commit_mismatch=normalized.cgc_seed.allow_commit_mismatch,
        cgc_seed_allow_same_coordination_root=normalized.cgc_seed.allow_same_coordination_root,
        cgc_isolated_runtime_root=normalized.cgc_isolated.runtime_root,
        cgc_isolated_settings_path=normalized.cgc_isolated.settings_path,
        cgc_isolated_container_name=normalized.cgc_isolated.container_name,
        grepai_seed_source_coordination_root=normalized.grepai_seed.source_coordination_root,
        grepai_seed_source_from_settings=normalized.grepai_seed.source_settings_path,
        grepai_seed_project_id=normalized.grepai_seed.project_id
        or normalized.grepai_isolated.project_id,
        grepai_seed_target_memory_root=normalized.grepai_seed.target_memory_root
        or normalized.grepai_isolated.target_memory_root,
        grepai_isolated_runtime_root=normalized.grepai_isolated.runtime_root,
        grepai_isolated_settings_path=normalized.grepai_isolated.settings_path,
        grepai_allow_missing_roots=normalized.grepai_isolated.allow_missing_roots,
        cgc_from_settings=None,
        grepai_from_settings=None,
        provider_from_settings=None,
        provider_isolated_settings_data=None,
    )


def install_enabled_providers(
    args: argparse.Namespace, settings: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        *grepai_setup.install_enabled_provider(args, settings),
        *cgc_setup.install_enabled_provider(args, settings),
    ]


def prepare_enabled_providers(
    args: argparse.Namespace, settings: dict[str, Any]
) -> list[dict[str, Any]]:
    results = install_enabled_providers(args, settings)
    results.extend(grepai_setup.prepare_enabled_provider(args, settings))
    results.extend(grepai_setup.refresh_enabled_provider(args, settings))
    results.extend(cgc_setup.prepare_enabled_provider(args, settings))
    results.extend(_watcher_results(args, settings))
    return results


def _watcher_results(args: argparse.Namespace, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if args.skip_watchers or not _watchers_needed(args, settings):
        return []
    return [
        run_lifecycle(
            args.coordination_root,
            "watchers",
            "start",
            timeout=args.timeout,
            dry_run=args.dry_run,
            extra_args=provider_settings_extra_args(args),
        ),
        run_lifecycle(
            args.coordination_root,
            "watchers",
            "status",
            timeout=args.timeout,
            dry_run=args.dry_run,
            extra_args=provider_settings_extra_args(args),
        ),
    ]


def provider_settings_extra_args(args: argparse.Namespace) -> list[str]:
    path = getattr(args, "provider_from_settings", None)
    if path is not None:
        return ["--from-settings", path.as_posix()]
    cgc_args = cgc_extra_args(args)
    if cgc_args:
        return cgc_args
    return grepai_setup.grepai_extra_args(args)


def _watchers_needed(args: argparse.Namespace, settings: dict[str, Any]) -> bool:
    return selected_provider_enabled(
        args, settings, grepai_setup.GREPAI_PROVIDER_ID
    ) or selected_provider_enabled(args, settings, cgc_setup.CGC_PROVIDER_ID)


def run_provider_setup(request: ProviderSetupRequest) -> dict[str, Any]:
    return _action_payload_from_args(args_from_request(request))


def action_payload(args: argparse.Namespace) -> dict[str, Any]:
    return run_provider_setup(request_from_args(args))


def _action_payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.from_settings)
    if settings is None:
        return _missing_settings_payload(args)

    enabled = _enabled_provider_summary(args, settings)
    isolated = write_isolated_provider_settings(args, settings)
    results = _action_results(args, settings)

    payload = {
        "action": args.action,
        "dryRun": args.dry_run,
        "coordinationRoot": args.coordination_root.as_posix(),
        "settingsFile": settings_path(args.from_settings).as_posix(),
        "isolatedProviderSettings": isolated,
        "enabled": enabled,
        "results": results,
    }
    return setup_reporting.finalize_setup_payload(
        args,
        payload,
        result_ok=lambda result: result_ok_for_prepare(result, args),
    )


def write_isolated_provider_settings(
    args: argparse.Namespace,
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    providers: dict[str, Any] = {}
    cgc = isolated_cgc_settings(args, settings)
    grepai = (
        None if getattr(args, "skip_grepai", False) else isolated_grepai_settings(args, settings)
    )
    if cgc is not None:
        providers.update(cgc["contextProviders"]["providers"])
    if grepai is not None:
        providers.update(grepai["contextProviders"]["providers"])
    if not providers:
        args.cgc_from_settings = None
        args.grepai_from_settings = None
        args.provider_from_settings = None
        args.provider_isolated_settings_data = None
        return None

    path = _isolated_provider_settings_path(args)
    args.provider_from_settings = path.resolve()
    args.cgc_from_settings = (
        args.provider_from_settings if cgc_setup.CGC_PROVIDER_ID in providers else None
    )
    args.grepai_from_settings = (
        args.provider_from_settings if grepai_setup.GREPAI_PROVIDER_ID in providers else None
    )
    data = _isolated_provider_settings_payload(providers)
    args.provider_isolated_settings_data = data
    if not args.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {
        "path": args.provider_from_settings.as_posix(),
        "dryRun": args.dry_run,
        "providers": sorted(providers),
        "settings": data if args.dry_run else None,
    }


def _isolated_provider_settings_path(args: argparse.Namespace) -> Path:
    explicit = (
        getattr(args, "grepai_isolated_settings_path", None)
        or getattr(args, "cgc_isolated_settings_path", None)
    )
    if explicit is not None:
        return explicit
    root = (
        getattr(args, "grepai_isolated_runtime_root", None)
        or getattr(args, "cgc_isolated_runtime_root", None)
    )
    if root is None:
        raise RuntimeError("isolated provider settings require an isolated runtime root")
    return root.resolve() / "settings" / "provider-settings.json"


def _isolated_provider_settings_payload(providers: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "contextProviders": {
            "enabled": True,
            "providers": providers,
            "policy": {
                "discoveryOnly": True,
                "sourceProofRequired": True,
            },
        },
    }


def _missing_settings_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": True,
        "action": args.action,
        "coordinationRoot": args.coordination_root.as_posix(),
        "settingsFile": settings_path(args.from_settings).as_posix(),
        "enabled": {},
        "results": [],
        "note": "settings.json is missing; no providers configured",
    }


def _enabled_provider_summary(
    args: argparse.Namespace,
    settings: dict[str, Any],
) -> dict[str, bool]:
    return {
        grepai_setup.GREPAI_PROVIDER_ID: selected_provider_enabled(
            args, settings, grepai_setup.GREPAI_PROVIDER_ID
        ),
        cgc_setup.CGC_PROVIDER_ID: selected_provider_enabled(
            args, settings, cgc_setup.CGC_PROVIDER_ID
        ),
    }


def _action_results(args: argparse.Namespace, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if args.action == "install":
        return install_enabled_providers(args, settings)
    if args.action == "prepare":
        return prepare_enabled_providers(args, settings)
    raise RuntimeError(f"unsupported action: {args.action}")


def result_ok_for_prepare(result: dict[str, Any], args: argparse.Namespace) -> bool:
    if result.get("ok"):
        return True
    return bool(
        args.action == "prepare"
        and args.cgc_refresh_fallback
        and result.get("provider") == "codegraphcontext"
        and result.get("action") == "seed"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "prepare"))
    parser.add_argument("--coordination-root", type=Path, required=True)
    parser.add_argument("--from-settings", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-watchers", action="store_true")
    parser.add_argument("--skip-grepai", action="store_true")
    parser.add_argument(
        "--no-cgc-refresh-fallback", dest="cgc_refresh_fallback", action="store_false"
    )
    parser.set_defaults(cgc_refresh_fallback=True)
    parser.add_argument("--cgc-seed-source-coordination-root", type=Path)
    parser.add_argument("--cgc-seed-source-from-settings", type=Path)
    parser.add_argument("--cgc-seed-repo-id")
    parser.add_argument("--cgc-seed-source-repo-id")
    parser.add_argument("--cgc-seed-source-repo-root", type=Path)
    parser.add_argument("--cgc-seed-target-repo-root", type=Path)
    parser.add_argument("--cgc-seed-bundle-dir", type=Path)
    parser.add_argument("--cgc-isolated-runtime-root", type=Path)
    parser.add_argument("--cgc-isolated-settings-path", type=Path)
    parser.add_argument("--cgc-isolated-container-name")
    parser.add_argument("--cgc-seed-allow-commit-mismatch", action="store_true")
    parser.add_argument("--cgc-seed-allow-same-coordination-root", action="store_true")
    parser.add_argument("--grepai-seed-source-coordination-root", type=Path)
    parser.add_argument("--grepai-seed-source-from-settings", type=Path)
    parser.add_argument("--grepai-seed-project-id")
    parser.add_argument("--grepai-seed-target-memory-root", type=Path)
    parser.add_argument("--grepai-isolated-runtime-root", type=Path)
    parser.add_argument("--grepai-isolated-settings-path", type=Path)
    parser.add_argument("--grepai-allow-missing-roots", action="store_true")
    return parser


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        f"provider setup {payload['action']}: {payload.get('state') or _payload_status(payload)}",
        f"coordination root: {payload['coordinationRoot']}",
    ]
    summary = payload.get("setupSummary")
    if isinstance(summary, dict) and summary.get("written"):
        lines.append(f"summary: {summary['last']}")
    lines.extend(_result_line(result) for result in payload.get("results", []))
    return "\n".join(lines)


def _payload_status(payload: dict[str, Any]) -> str:
    return "ok" if payload.get("ok") else "failed"


def _result_line(result: dict[str, Any]) -> str:
    provider = result.get("provider", "provider")
    action = result.get("action", "action")
    suffix = _result_detail_suffix(result)
    return f"- {provider} {action}: {_result_status(result)}{suffix}"


def _result_status(result: dict[str, Any]) -> str:
    if result.get("skipped"):
        return "skipped"
    return "ok" if result.get("ok") else "failed"


def _result_detail_suffix(result: dict[str, Any]) -> str:
    detail = result.get("reason") or result.get("stage") or ""
    return f" ({detail})" if detail else ""


def main(argv: list[str] | None = None) -> int:
    args = _normalized_args(build_parser().parse_args(argv))
    try:
        payload = action_payload(args)
    except (
        RuntimeError,
        OSError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as error:
        payload = _error_payload(args, error)

    print(json.dumps(payload, indent=2) if args.json else render_text(payload))
    return 0 if payload.get("ok") else 1


def _normalized_args(args: argparse.Namespace) -> argparse.Namespace:
    args.coordination_root = args.coordination_root.resolve()
    for name in _PATH_ARG_NAMES:
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    return args


_PATH_ARG_NAMES = (
    "cgc_seed_source_coordination_root",
    "cgc_seed_source_from_settings",
    "cgc_seed_source_repo_root",
    "cgc_seed_target_repo_root",
    "cgc_seed_bundle_dir",
    "cgc_isolated_runtime_root",
    "cgc_isolated_settings_path",
    "grepai_seed_source_coordination_root",
    "grepai_seed_source_from_settings",
    "grepai_seed_target_memory_root",
    "grepai_isolated_runtime_root",
    "grepai_isolated_settings_path",
    "from_settings",
)


def _error_payload(args: argparse.Namespace, error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "action": args.action,
        "dryRun": args.dry_run,
        "coordinationRoot": args.coordination_root.as_posix(),
        "error": str(error),
    }


if __name__ == "__main__":
    raise SystemExit(main())

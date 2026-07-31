#!/usr/bin/env python3
"""Prepare Agents Remember context providers from package-local MCP code."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import tempfile
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # POSIX-only; the docker-backed provider stack is POSIX-hosted anyway.
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback: lock becomes a no-op
    fcntl = None  # type: ignore[assignment]

from agents_remember.providers import setup_common, setup_reporting
from agents_remember.providers.cgc import bundle as cgc_bundle
from agents_remember.providers.cgc import seed as cgc_seed
from agents_remember.providers.cgc import setup as cgc_setup
from agents_remember.providers.cgc.seed import CgcSeedOptions
from agents_remember.providers.cgc.setup import IsolatedCgcOptions
from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.context_common import expand_template
from agents_remember.providers.grepai import setup as grepai_setup
from agents_remember.providers.grepai.setup import GrepaiSeedOptions, IsolatedGrepaiOptions
from agents_remember.providers.lifecycle.docker_runtime import docker_command
from agents_remember.providers.metrics import ProviderMetricsStore
from agents_remember.providers.setup_progress import SetupProgress

load_settings = setup_common.load_settings
provider_enabled = setup_common.provider_enabled
provider_settings = setup_common.provider_settings
require_settings_path = setup_common.require_settings_path
run_command = setup_common.run_command
run_lifecycle = setup_common.run_lifecycle
selected_provider_enabled = setup_common.selected_provider_enabled
settings_path = setup_common.settings_path
stable_provider_id = setup_common.stable_provider_id
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
    # 260707-HFX-L2: the implicit refresh-all fallback is OFF by default — a
    # refused seed must never cost a from-zero reindex on its own; the seed's
    # diff-scoped catch-up handles the normal worktree case and `cgc refresh`
    # stays the explicit rebuild.
    cgc_refresh_fallback: bool = False
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
                delta_max_files=self.cgc_seed.delta_max_files,
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
                source_settings_path=_resolve_optional_path(self.grepai_seed.source_settings_path),
                project_id=self.grepai_seed.project_id,
                target_memory_root=_resolve_optional_path(self.grepai_seed.target_memory_root),
            ),
            grepai_isolated=IsolatedGrepaiOptions(
                runtime_root=_resolve_optional_path(self.grepai_isolated.runtime_root),
                settings_path=_resolve_optional_path(self.grepai_isolated.settings_path),
                project_id=self.grepai_isolated.project_id,
                target_memory_root=_resolve_optional_path(self.grepai_isolated.target_memory_root),
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
            delta_max_files=getattr(args, "cgc_seed_delta_max_files", 0),
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
        cgc_seed_delta_max_files=normalized.cgc_seed.delta_max_files,
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
    # Copy/seed everything BEFORE any watcher starts. Starting the grepai watcher here
    # (the old `refresh_enabled_provider` step) made `watchers start` re-run the index-root
    # rmtree+copytree under a live watcher — a burst of "moving files" that floods the event
    # channel and forces a full re-embed. The single watcher start in `_watcher_results`
    # now runs after the DB clone, cgc seed, and the index-root copy, so the initial scan
    # sees a stable filesystem (and, with mtime sync, reuses the cloned index).
    results = install_enabled_providers(args, settings)
    results.extend(grepai_setup.prepare_enabled_provider(args, settings))
    results.extend(cgc_setup.prepare_enabled_provider(args, settings))
    results.extend(_watcher_results(args, settings))
    results.extend(_seed_catchup_results(args, settings))
    return results


# Watcher readiness bound for the catch-up touch (review L2/B1): the cgc
# container subscribes to inotify only after guard PONG wait + python boot +
# graph checks; inotify has no replay and seeded graphs skip the initial scan,
# so a touch before subscription is silently lost. The markers cover cgc's
# post-subscribe log line; no marker within the bound means NO touch and an
# honest staleIndex instead of a silent lie.
CGC_WATCHER_READY_TIMEOUT_SECONDS = 90
# Only the marker VERIFIED against the pinned codegraphcontext wheel (review
# L2 round 2): speculative extra markers risk a false-positive on some future
# pre-subscribe line, which would silently re-open the B1 race. Re-verify this
# marker on every cgc version bump.
_CGC_WATCH_READY_MARKERS = ("monitoring",)


def _seed_catchup_results(
    args: argparse.Namespace, settings: dict[str, Any]
) -> list[dict[str, Any]]:
    """Diff-scoped index catch-up after the watchers attach (260707-HFX-L2).

    The cgc seed clones the source graph even when the target checkout's HEAD
    differs (relatable divergence, recorded by `_seed_commit_mismatch`). The
    watcher is event-driven, so touching exactly the changed files makes it
    re-index just the delta — a small diff becomes an index UPDATE, never a
    teardown. Honesty rules (review L2/B1+B2): the touch waits for the
    watcher's post-subscribe marker, deletions/rename-sources/missing files
    are RESIDUAL staleness a touch cannot deliver, and ``caughtUp`` is claimed
    only when every touchable path reached a ready watcher with zero
    residuals. Anything less reports ``staleIndex`` (served; a from-zero
    rebuild stays an explicit ``cgc refresh``).
    """
    divergence = getattr(args, "_cgc_seed_divergence", None)
    if not divergence or args.dry_run:
        return []
    target_root = getattr(args, "cgc_seed_target_repo_root", None)
    count = int(divergence.get("count", 0))
    if count == 0 or target_root is None:
        return []
    payload: dict[str, Any] = {
        "ok": True,
        "provider": "codegraphcontext",
        "action": "seed-catch-up",
        "divergence": {
            "count": count,
            "sourceHead": divergence.get("sourceHead"),
            "targetHead": divergence.get("targetHead"),
        },
    }
    limit = getattr(args, "cgc_seed_delta_max_files", 0) or cgc_seed.DEFAULT_SEED_DELTA_MAX_FILES
    if count > limit:
        payload["skipped"] = True
        payload["staleIndex"] = {
            "served": True,
            "behindFiles": count,
            "deltaMaxFiles": limit,
            "reindex": "explicit 'cgc refresh' only",
        }
        _record_index_state(args, settings, payload)
        return [payload]
    root = Path(target_root)
    touch_paths: list[Path] = []
    residuals: list[dict[str, str]] = []
    for entry in divergence.get("entries", []):
        status = str(entry.get("status", ""))
        path = str(entry.get("path", ""))
        if status == "R" and entry.get("from"):
            residuals.append({"kind": "rename-source-phantom", "path": str(entry["from"])})
        if status == "D":
            residuals.append({"kind": "deleted-phantom", "path": path})
            continue
        candidate = root / path
        if candidate.is_file():
            touch_paths.append(candidate)
        else:
            residuals.append({"kind": "missing-on-disk", "path": path})
    watcher = _wait_for_cgc_watcher_ready(args, settings)
    payload["watcherReady"] = watcher
    if not watcher.get("ready"):
        payload["skipped"] = True
        payload["staleIndex"] = {
            "served": True,
            "behindFiles": count,
            "reason": "watcher not ready before the touch window; delta not delivered",
            "reindex": "explicit 'cgc refresh' only",
        }
        _record_index_state(args, settings, payload)
        return [payload]
    for candidate in touch_paths:
        os.utime(candidate)
    payload["touched"] = len(touch_paths)
    payload["mechanism"] = "watcher-event catch-up (utime on the diff files)"
    if residuals:
        payload["caughtUp"] = False
        payload["staleIndex"] = {
            "served": True,
            "residuals": residuals,
            "reindex": "explicit 'cgc refresh' only",
        }
    else:
        payload["caughtUp"] = True
    _record_index_state(args, settings, payload)
    return [payload]


def _cgc_watcher_container_name(args: argparse.Namespace, settings: dict[str, Any]) -> str | None:
    provider = provider_settings(settings, cgc_setup.CGC_PROVIDER_ID)
    repo_id = getattr(args, "cgc_seed_repo_id", None)
    if not isinstance(provider, dict) or not repo_id:
        return None
    runtime = provider.get("runtime")
    runner = runtime.get("runner") if isinstance(runtime, dict) else None
    template = str((runner or {}).get("containerNameTemplate") or "")
    if not template:
        return None
    return expand_template(template, {"repoId": stable_provider_id(repo_id)})


def _wait_for_cgc_watcher_ready(
    args: argparse.Namespace,
    settings: dict[str, Any],
    *,
    timeout_seconds: int = CGC_WATCHER_READY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Poll the watcher container's logs for cgc's post-subscribe marker."""
    container = _cgc_watcher_container_name(args, settings)
    if container is None:
        return {"ready": False, "reason": "watcher container name unresolved"}
    try:
        docker = docker_command()
    except ContextProviderError as error:
        return {"ready": False, "container": container, "reason": str(error)}
    deadline = time.monotonic() + max(timeout_seconds, 1)
    waited = 0.0
    while True:
        # --since bounds the window to THIS boot's output: a marker left in the
        # logs by a previous watcher boot must not satisfy a fresh one.
        logs = run_command(
            [docker, "logs", "--since", "15m", "--tail", "200", container],
            cwd=args.coordination_root,
            timeout=20,
            dry_run=False,
        )
        if logs["returncode"] == 0:
            text = (str(logs.get("stdout") or "") + str(logs.get("stderr") or "")).lower()
            if any(marker in text for marker in _CGC_WATCH_READY_MARKERS):
                return {
                    "ready": True,
                    "container": container,
                    "waitedSeconds": round(waited, 1),
                }
        if time.monotonic() >= deadline:
            return {
                "ready": False,
                "container": container,
                "reason": f"no watch-ready marker within {timeout_seconds}s",
            }
        time.sleep(2)
        waited += 2


def _record_index_state(
    args: argparse.Namespace, settings: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Best-effort index-lifecycle metrics row (L7's feed); never fails the setup."""
    with contextlib.suppress(Exception):  # observability must not break setup
        provider = provider_settings(settings, cgc_setup.CGC_PROVIDER_ID)
        instance = (provider or {}).get("instance") or {}
        ProviderMetricsStore(args.coordination_root).record_index_state(
            {
                "provider": payload.get("provider"),
                "action": payload.get("action"),
                "repoId": getattr(args, "cgc_seed_repo_id", None),
                "instance": instance.get("id"),
                "divergence": payload.get("divergence"),
                "touched": payload.get("touched"),
                "caughtUp": payload.get("caughtUp"),
                "watcherReady": payload.get("watcherReady"),
                "staleIndex": payload.get("staleIndex"),
            }
        )


def _watcher_results(args: argparse.Namespace, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if args.skip_watchers or not _watchers_needed(args, settings):
        return []
    progress = setup_common.setup_progress_from(args)
    results: list[dict[str, Any]] = []
    for action in ("start", "status"):
        progress.phase_start("watchers", action)
        result = run_lifecycle(
            args.coordination_root,
            "watchers",
            action,
            timeout=args.timeout,
            dry_run=args.dry_run,
            extra_args=provider_settings_extra_args(args),
        )
        progress.phase_done(result)
        results.append(result)
    return results


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


def fleet_setup_lock_path() -> Path:
    """HOST-scoped setup lock path (containment R2, 260707-HFX-L1).

    The guarded resource is the host's memory/docker daemon, shared by every
    coordination root AND every benchmark workspace, so the lock must not live
    under any one of them: `runtime_install` prunes `providers/` (review
    finding B1 — a coordination-root lock file was deleted mid-hold), and
    benchmark prepares run against workspace-local roots that would otherwise
    never serialize with fleet setups (finding B2).
    """
    uid = os.getuid() if hasattr(os, "getuid") else "shared"  # pragma: no branch
    return Path(tempfile.gettempdir()) / f"agents-remember-provider-setup-{uid}.lock"


@contextlib.contextmanager
def _fleet_setup_lock(lock_path: Path, timeout: int) -> Iterator[None]:
    """Serialize provider setup host-wide (containment R2, 260707-HFX-L1).

    The 2026-07-07 OOM was an aggregate storm: several sessions launched
    provider stacks concurrently, each inside its per-container caps (L12) but
    summing past the host. One setup at a time bounds the aggregate; a waiter
    blocks up to the setup timeout, then fails loudly instead of piling on.
    """
    if fcntl is None:  # pragma: no cover - non-POSIX
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        deadline = time.monotonic() + max(timeout, 1)
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"provider setup lock busy after {timeout}s: {lock_path} — another "
                        "session's provider setup is running; one setup at a time bounds "
                        "aggregate container load (containment R2, 260707-HFX-L1)"
                    ) from None
                time.sleep(2)
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()} {datetime.now(UTC).isoformat()}\n")
        handle.flush()
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def run_provider_setup(
    request: ProviderSetupRequest,
    progress: SetupProgress | None = None,
) -> dict[str, Any]:
    args = args_from_request(request)
    # The sink rides on the namespace (like provider_from_settings) so the
    # install/prepare functions keep their (args, settings) signatures.
    args.setup_progress = progress
    return _action_payload_from_args(args)


def action_payload(args: argparse.Namespace) -> dict[str, Any]:
    return run_provider_setup(request_from_args(args))


def _action_payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.from_settings)
    if settings is None:
        return _missing_settings_payload(args)

    enabled = _enabled_provider_summary(args, settings)
    isolated = write_isolated_provider_settings(args, settings)
    if args.action == "prepare" and not args.dry_run:
        with _fleet_setup_lock(fleet_setup_lock_path(), args.timeout):
            results = _action_results(args, settings)
    else:
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
    explicit = getattr(args, "grepai_isolated_settings_path", None) or getattr(
        args, "cgc_isolated_settings_path", None
    )
    if explicit is not None:
        return explicit
    root = getattr(args, "grepai_isolated_runtime_root", None) or getattr(
        args, "cgc_isolated_runtime_root", None
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
    # 260707-HFX-L2: a benign seed skip — no seed was intended or possible
    # (hermetic benchmark, source not configured) — never fails a prepare: the
    # watcher builds the index from scratch, which is that path's designed
    # behavior. A REFUSED seed carries sourceHead/targetHead (unrelated heads)
    # and is forgiven only under the explicit refresh-all opt-in.
    if result.get("skipped") and "sourceHead" not in result:
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
    parser.add_argument(
        "--cgc-refresh-fallback",
        dest="cgc_refresh_fallback",
        action="store_true",
        help="Opt IN to the full-reindex fallback after a refused seed (260707-HFX-L2: off by default).",
    )
    parser.set_defaults(cgc_refresh_fallback=False)
    parser.add_argument(
        "--cgc-seed-delta-max-files",
        dest="cgc_seed_delta_max_files",
        type=int,
        default=0,
        help="Diff-scoped catch-up bound; 0 uses the built-in default.",
    )
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

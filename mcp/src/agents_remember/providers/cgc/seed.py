"""CodeGraphContext provider seed orchestration."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers.cgc.bundle import rewrite_cgc_bundle_paths
from agents_remember.providers.setup_common import (
    context_providers,
    expand_template,
    load_settings,
    run_lifecycle,
    settings_path,
    stable_provider_id,
)

CGC_PROVIDER_ID = "codegraphcontext-code"


@dataclass(frozen=True)
class CgcSeedOptions:
    source_coordination_root: Path | None = None
    source_settings_path: Path | None = None
    repo_id: str | None = None
    source_repo_id: str | None = None
    source_repo_root: Path | None = None
    target_repo_root: Path | None = None
    bundle_dir: Path | None = None
    allow_commit_mismatch: bool = False
    allow_same_coordination_root: bool = False


@dataclass(frozen=True)
class CgcSeedContext:
    target_coordination_root: Path
    source_coordination_root: Path
    target_repo_id: str
    source_repo_id: str
    target_repo_root: Path
    source_repo_root: Path
    target_runtime_root: Path
    source_runtime_root: Path
    target_head: str | None
    source_head: str | None


def cgc_extra_args(args: Any) -> list[str]:
    path = (
        getattr(args, "cgc_from_settings", None)
        or getattr(args, "provider_from_settings", None)
        or getattr(args, "from_settings", None)
    )
    return ["--from-settings", path.as_posix()] if path is not None else []


def configured_cgc_repo_root(
    coordination_root: Path,
    settings: dict[str, Any],
    repo_id: str | None,
) -> tuple[str, Path] | None:
    provider = _cgc_provider(settings)
    if provider is None:
        return None
    selected = _selected_cgc_root(provider, repo_id)
    if selected is None:
        return None
    return _configured_cgc_root_tuple(selected, repo_id, coordination_root)


def _cgc_provider(settings: dict[str, Any]) -> dict[str, Any] | None:
    provider = context_providers(settings).get(CGC_PROVIDER_ID)
    return provider if isinstance(provider, dict) else None


def _selected_cgc_root(
    provider: dict[str, Any],
    repo_id: str | None,
) -> dict[str, Any] | None:
    candidates = _cgc_root_candidates(provider)
    if repo_id is None:
        return candidates[0] if len(candidates) == 1 else None
    stable = stable_provider_id(repo_id)
    return next(
        (root for root in candidates if stable_provider_id(str(root.get("repoId", ""))) == stable),
        None,
    )


def _cgc_root_candidates(provider: dict[str, Any]) -> list[dict[str, Any]]:
    roots = provider.get("roots")
    if not isinstance(roots, list):
        return []
    return [root for root in roots if isinstance(root, dict)]


def _configured_cgc_root_tuple(
    selected: dict[str, Any],
    repo_id: str | None,
    coordination_root: Path,
) -> tuple[str, Path] | None:
    raw_path = selected.get("path")
    if not isinstance(raw_path, str):
        return None
    selected_repo_id = stable_provider_id(str(selected.get("repoId", repo_id or "")))
    expanded = Path(expand_template(raw_path, coordination_root)).resolve()
    return selected_repo_id, expanded


def _seed_runtime_root(
    coordination_root: Path,
    settings: dict[str, Any],
    repo_id: str,
) -> Path | dict[str, Any]:
    provider = _cgc_provider(settings)
    if provider is None:
        return _seed_skip("CGC provider is not configured")
    runtime_root = _configured_cgc_runtime_root(coordination_root, provider)
    instance_template = str(provider.get("instanceRootTemplate", "<runtimeRoot>/<repoId>"))
    variables = {
        "coordination_root": coordination_root.as_posix(),
        "workspace_root": coordination_root.parent.as_posix(),
        "runtimeRoot": runtime_root.as_posix(),
        "repoId": stable_provider_id(repo_id),
    }
    return Path(_expand_tokens(instance_template, variables)).resolve()


def _configured_cgc_runtime_root(
    coordination_root: Path,
    provider: dict[str, Any],
) -> Path:
    raw_root = str(
        provider.get("runtimeRoot", "<coordination_root>/providers/runners/codegraphcontext")
    )
    return Path(expand_template(raw_root, coordination_root)).resolve()


def _expand_tokens(value: str, variables: dict[str, str]) -> str:
    expanded = value
    for key, replacement in variables.items():
        expanded = expanded.replace(f"<{key}>", replacement)
    return expanded


def cgc_seed_source_settings_path(
    args: Any,
    source_coordination_root: Path,
    target_coordination_root: Path,
) -> Path | None:
    explicit = getattr(args, "cgc_seed_source_from_settings", None)
    if explicit is not None:
        return explicit
    if source_coordination_root.resolve() == target_coordination_root.resolve():
        return getattr(args, "from_settings", None)
    return None


def cgc_seed_source_extra_args(
    args: Any,
    source_coordination_root: Path,
    target_coordination_root: Path,
) -> list[str]:
    path = cgc_seed_source_settings_path(args, source_coordination_root, target_coordination_root)
    return ["--from-settings", path.as_posix()] if path is not None else []


def git_head(repo_root: Path) -> str | None:
    if not repo_root.exists():
        return None
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repo_root.as_posix()}",
            "-C",
            repo_root.as_posix(),
            "rev-parse",
            "HEAD",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def cgc_seed_bundle(args: Any, settings: dict[str, Any]) -> dict[str, Any]:
    context = _resolve_seed_context(args, settings)
    if _is_seed_skip(context):
        return context

    paths = _seed_bundle_paths(args, context)
    source_backend = _seed_source_backend(args, context)
    if not source_backend.get("ok"):
        return {"ok": False, "stage": "source-backend-start", "command": source_backend}

    export = _seed_export(args, context, paths["source"])
    if not export.get("ok"):
        return {"ok": False, "stage": "export", "command": export}

    rewrite = _seed_rewrite(args, context, paths["source"], paths["rewritten"])
    load = _seed_load(args, context, paths["rewritten"])
    if not load.get("ok"):
        return {"ok": False, "stage": "load", "rewrite": rewrite, "command": load}

    return _seed_success_payload(context, source_backend, export, rewrite, load)


def _resolve_seed_context(args: Any, settings: dict[str, Any]) -> CgcSeedContext | dict[str, Any]:
    source_coordination_root = args.cgc_seed_source_coordination_root
    if source_coordination_root is None:
        return _seed_skip("no seed source coordination root configured")

    source_settings = _load_seed_source_settings(args, source_coordination_root)
    if _is_seed_skip(source_settings):
        return source_settings

    target, source = _seed_roots(args, settings, source_settings, source_coordination_root)
    root_failure = _first_seed_skip(target, source)
    if root_failure is not None:
        return root_failure
    target_runtime = _seed_runtime_root(args.coordination_root, settings, target[0])
    source_runtime = _seed_runtime_root(source_coordination_root, source_settings, source[0])
    runtime_failure = _first_seed_skip(target_runtime, source_runtime)
    if runtime_failure is not None:
        return runtime_failure
    return _validated_seed_context(
        args,
        source_coordination_root,
        target,
        source,
        target_runtime,
        source_runtime,
    )


def _load_seed_source_settings(
    args: Any,
    source_coordination_root: Path,
) -> dict[str, Any]:
    source_settings_file = cgc_seed_source_settings_path(
        args, source_coordination_root, args.coordination_root
    )
    source_settings = load_settings(source_coordination_root, source_settings_file)
    if source_settings is None:
        return _seed_skip(
            f"source settings missing: {settings_path(source_coordination_root, source_settings_file)}"
        )
    return source_settings


def _seed_roots(
    args: Any,
    target_settings: dict[str, Any],
    source_settings: dict[str, Any],
    source_coordination_root: Path,
) -> tuple[tuple[str, Path] | dict[str, Any], tuple[str, Path] | dict[str, Any]]:
    target = _seed_repo_root(
        args, args.coordination_root, target_settings, args.cgc_seed_repo_id, "target"
    )
    source = _seed_repo_root(
        args,
        source_coordination_root,
        source_settings,
        args.cgc_seed_source_repo_id or args.cgc_seed_repo_id,
        "source",
    )
    return target, source


def _first_seed_skip(*values: Any) -> dict[str, Any] | None:
    return next((value for value in values if _is_seed_skip(value)), None)


def _is_seed_skip(value: Any) -> bool:
    return isinstance(value, dict) and value.get("skipped") is True and value.get("ok") is False


def _seed_repo_root(
    args: Any,
    coordination_root: Path,
    settings: dict[str, Any],
    repo_id: str | None,
    side: str,
) -> tuple[str, Path] | dict[str, Any]:
    explicit_root = getattr(args, f"cgc_seed_{side}_repo_root")
    root_info = configured_cgc_repo_root(coordination_root, settings, repo_id)
    if explicit_root is not None:
        selected_id = repo_id or (root_info[0] if root_info else explicit_root.name)
        return stable_provider_id(selected_id), explicit_root.resolve()
    if root_info is not None:
        return root_info
    return _seed_skip(f"{side} CGC root is not configured")


def _validated_seed_context(
    args: Any,
    source_coordination_root: Path,
    target: tuple[str, Path],
    source: tuple[str, Path],
    target_runtime_root: Path,
    source_runtime_root: Path,
) -> CgcSeedContext | dict[str, Any]:
    target_repo_id, target_repo_root = target
    source_repo_id, source_repo_root = source
    source_head = git_head(source_repo_root)
    target_head = git_head(target_repo_root)
    failure = _seed_validation_failure(
        args,
        source_coordination_root,
        source_repo_root,
        target_repo_root,
        source_head,
        target_head,
    )
    if failure is not None:
        return failure
    return CgcSeedContext(
        target_coordination_root=args.coordination_root,
        source_coordination_root=source_coordination_root,
        target_repo_id=target_repo_id,
        source_repo_id=source_repo_id,
        target_repo_root=target_repo_root,
        source_repo_root=source_repo_root,
        target_runtime_root=target_runtime_root,
        source_runtime_root=source_runtime_root,
        target_head=target_head,
        source_head=source_head,
    )


def _seed_validation_failure(
    args: Any,
    source_coordination_root: Path,
    source_repo_root: Path,
    target_repo_root: Path,
    source_head: str | None,
    target_head: str | None,
) -> dict[str, Any] | None:
    mismatch = _seed_commit_mismatch(
        args, source_repo_root, target_repo_root, source_head, target_head
    )
    if mismatch is not None:
        return mismatch
    return _seed_same_coordination_root_failure(
        args, source_coordination_root, source_repo_root, target_repo_root
    )


def _seed_commit_mismatch(
    args: Any,
    source_repo_root: Path,
    target_repo_root: Path,
    source_head: str | None,
    target_head: str | None,
) -> dict[str, Any] | None:
    if args.cgc_seed_allow_commit_mismatch or not source_head or not target_head:
        return None
    if source_head == target_head:
        return None
    return {
        "ok": False,
        "skipped": True,
        "reason": "source and target repository HEAD commits differ",
        "sourceHead": source_head,
        "targetHead": target_head,
        "sourceRepoRoot": source_repo_root.as_posix(),
        "targetRepoRoot": target_repo_root.as_posix(),
    }


def _seed_same_coordination_root_failure(
    args: Any,
    source_coordination_root: Path,
    source_repo_root: Path,
    target_repo_root: Path,
) -> dict[str, Any] | None:
    if args.cgc_seed_allow_same_coordination_root or args.cgc_isolated_runtime_root is not None:
        return None
    if source_coordination_root.resolve() != args.coordination_root.resolve():
        return None
    if source_repo_root.resolve() == target_repo_root.resolve():
        return None
    return {
        "ok": False,
        "skipped": True,
        "reason": "refusing to seed a different repo path into the same coordination root without --cgc-seed-allow-same-coordination-root",
        "sourceRepoRoot": source_repo_root.as_posix(),
        "targetRepoRoot": target_repo_root.as_posix(),
    }


def _seed_bundle_paths(args: Any, context: CgcSeedContext) -> dict[str, Path]:
    seed_id = stable_provider_id(str(context.target_repo_id or context.target_repo_root.name))
    source_bundle_root = _seed_bundle_root(args, context.source_runtime_root, "source")
    target_bundle_root = _seed_bundle_root(args, context.target_runtime_root, "target")
    if not args.dry_run:
        source_bundle_root.mkdir(parents=True, exist_ok=True)
        target_bundle_root.mkdir(parents=True, exist_ok=True)
    return {
        "source": source_bundle_root / f"{seed_id}.source.cgc",
        "rewritten": target_bundle_root / f"{seed_id}.target.cgc",
    }


def _seed_bundle_root(args: Any, runtime_root: Path, side: str) -> Path:
    if args.cgc_seed_bundle_dir is not None:
        return args.cgc_seed_bundle_dir.resolve() / side
    return runtime_root / "seed-bundles"


def _seed_source_backend(args: Any, context: CgcSeedContext) -> dict[str, Any]:
    return run_lifecycle(
        context.source_coordination_root,
        "cgc",
        "backend-start",
        timeout=args.timeout,
        dry_run=args.dry_run,
        extra_args=cgc_seed_source_extra_args(
            args, context.source_coordination_root, context.target_coordination_root
        ),
    )


def _seed_export(args: Any, context: CgcSeedContext, source_bundle: Path) -> dict[str, Any]:
    return run_lifecycle(
        context.source_coordination_root,
        "cgc",
        "run",
        timeout=args.timeout,
        dry_run=args.dry_run,
        extra_args=[
            *cgc_seed_source_extra_args(
                args, context.source_coordination_root, context.target_coordination_root
            ),
            "--repo-id",
            str(context.source_repo_id),
        ],
        native_args=[
            "--",
            "export",
            source_bundle.as_posix(),
            "--repo",
            context.source_repo_root.as_posix(),
            "--no-stats",
        ],
    )


def _seed_rewrite(
    args: Any,
    context: CgcSeedContext,
    source_bundle: Path,
    rewritten_bundle: Path,
) -> dict[str, Any]:
    if args.dry_run:
        return {
            "sourceBundle": source_bundle.as_posix(),
            "targetBundle": rewritten_bundle.as_posix(),
            "sourceRoot": context.source_repo_root.as_posix(),
            "targetRoot": context.target_repo_root.as_posix(),
            "dryRun": True,
        }
    return rewrite_cgc_bundle_paths(
        source_bundle, rewritten_bundle, context.source_repo_root, context.target_repo_root
    )


def _seed_load(args: Any, context: CgcSeedContext, rewritten_bundle: Path) -> dict[str, Any]:
    return run_lifecycle(
        context.target_coordination_root,
        "cgc",
        "run",
        timeout=args.timeout,
        dry_run=args.dry_run,
        extra_args=[*cgc_extra_args(args), "--repo-id", str(context.target_repo_id)],
        native_args=["--", "bundle", "import", rewritten_bundle.as_posix()],
    )


def _seed_success_payload(
    context: CgcSeedContext,
    source_backend: dict[str, Any],
    export: dict[str, Any],
    rewrite: dict[str, Any],
    load: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "seeded": True,
        "repoId": context.target_repo_id,
        "sourceRepoId": context.source_repo_id,
        "sourceCoordinationRoot": context.source_coordination_root.as_posix(),
        "targetCoordinationRoot": context.target_coordination_root.as_posix(),
        "sourceRepoRoot": context.source_repo_root.as_posix(),
        "targetRepoRoot": context.target_repo_root.as_posix(),
        "sourceHead": context.source_head,
        "targetHead": context.target_head,
        "sourceBackend": source_backend,
        "export": export,
        "rewrite": rewrite,
        "load": load,
    }


def _seed_skip(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "skipped": True,
        "reason": reason,
    }

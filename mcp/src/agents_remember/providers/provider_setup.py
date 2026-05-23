#!/usr/bin/env python3
"""Prepare Agents Remember context providers from package-local MCP code."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from agents_remember.mcp.command_capture import run_package_main
from agents_remember.providers import provider_lifecycle


def stable_provider_id(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered).strip(".-_")
    return slug or "repo"


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"cannot read JSON from {path}: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return data


def settings_path(coordination_root: Path, from_settings: Path | None = None) -> Path:
    return from_settings or coordination_root / "system" / "settings.json"


def load_settings(coordination_root: Path, from_settings: Path | None = None) -> dict[str, Any] | None:
    path = settings_path(coordination_root, from_settings)
    if not path.exists():
        return None
    return load_json(path)


def context_providers(settings: dict[str, Any]) -> dict[str, Any]:
    context = settings.get("contextProviders")
    if not isinstance(context, dict) or context.get("enabled") is not True:
        return {}
    providers = context.get("providers")
    return providers if isinstance(providers, dict) else {}


def provider_enabled(settings: dict[str, Any], provider_id: str) -> bool:
    provider = context_providers(settings).get(provider_id)
    return isinstance(provider, dict) and provider.get("enabled") is True


def selected_provider_enabled(args: argparse.Namespace, settings: dict[str, Any], provider_id: str) -> bool:
    if provider_id == "grepai-memory" and args.skip_grepai:
        return False
    return provider_enabled(settings, provider_id)


def expand_template(value: str, coordination_root: Path) -> str:
    return (
        value.replace("<coordination_root>", coordination_root.as_posix())
        .replace("<workspace_root>", coordination_root.parent.as_posix())
    )


def configured_cgc_repo_root(
    coordination_root: Path,
    settings: dict[str, Any],
    repo_id: str | None,
) -> tuple[str, Path] | None:
    provider = context_providers(settings).get("codegraphcontext-code")
    if not isinstance(provider, dict):
        return None
    roots = provider.get("roots")
    if not isinstance(roots, list):
        return None

    candidates = [root for root in roots if isinstance(root, dict)]
    if not candidates:
        return None
    if repo_id is None:
        selected = candidates[0] if len(candidates) == 1 else None
    else:
        stable = stable_provider_id(repo_id)
        selected = next(
            (
                root
                for root in candidates
                if stable_provider_id(str(root.get("repoId", ""))) == stable
            ),
            None,
        )
    if selected is None:
        return None

    selected_repo_id = stable_provider_id(str(selected.get("repoId", repo_id or "")))
    raw_path = selected.get("path")
    if not isinstance(raw_path, str):
        return None
    expanded = Path(expand_template(raw_path, coordination_root)).resolve()
    return selected_repo_id, expanded


def cgc_extra_args(args: argparse.Namespace) -> list[str]:
    path = getattr(args, "cgc_from_settings", None)
    return ["--from-settings", path.as_posix()] if path is not None else []


def cgc_seed_source_settings_path(
    args: argparse.Namespace,
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
    args: argparse.Namespace,
    source_coordination_root: Path,
    target_coordination_root: Path,
) -> list[str]:
    path = cgc_seed_source_settings_path(args, source_coordination_root, target_coordination_root)
    return ["--from-settings", path.as_posix()] if path is not None else []


def isolated_cgc_settings(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any] | None:
    if args.cgc_isolated_runtime_root is None:
        return None

    provider = context_providers(settings).get("codegraphcontext-code")
    if not isinstance(provider, dict):
        return None
    if args.cgc_seed_target_repo_root is None:
        raise RuntimeError("--cgc-isolated-runtime-root requires --cgc-seed-target-repo-root")

    isolated_root = args.cgc_isolated_runtime_root.resolve()
    repo_id = stable_provider_id(args.cgc_seed_repo_id or args.cgc_seed_target_repo_root.name)
    cgc = json.loads(json.dumps(provider))
    cgc["enabled"] = True
    cgc["roots"] = [
        {
            "repoId": repo_id,
            "path": args.cgc_seed_target_repo_root.resolve().as_posix(),
        }
    ]
    cgc["runtimeRoot"] = (isolated_root / "providers" / "runners" / "codegraphcontext").as_posix()
    cgc["instanceRootTemplate"] = "<runtimeRoot>/<repoId>"
    cgc["venvRoot"] = (args.coordination_root / "providers" / "_venvs" / "codegraphcontext").as_posix()
    cgc["requirementsFile"] = (args.coordination_root / "providers" / "requirements" / "codegraphcontext.txt").as_posix()
    cgc["patchesRoot"] = (args.coordination_root / "providers" / "patches" / "codegraphcontext").as_posix()
    cgc["stateFileTemplate"] = "<instanceRoot>/provider-state.json"

    backend = cgc.get("backend")
    if not isinstance(backend, dict):
        backend = {}
    backend["runtimeRoot"] = (isolated_root / "providers" / "data" / "codegraphcontext" / "falkordb").as_posix()
    backend["dataRoot"] = "<backendRuntimeRoot>/data"
    backend["imageLockFile"] = (
        isolated_root / "providers" / "requirements" / "codegraphcontext-falkordb-docker.lock"
    ).as_posix()
    backend["containerName"] = args.cgc_isolated_container_name or f"ar-cgc-falkordb-{repo_id}-{stable_provider_id(isolated_root.name)}"
    cgc["backend"] = backend

    return {
        "version": 1,
        "contextProviders": {
            "enabled": True,
            "providers": {
                "codegraphcontext-code": cgc,
            },
            "policy": {
                "discoveryOnly": True,
                "sourceProofRequired": True,
            },
        },
    }


def write_isolated_cgc_settings(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any] | None:
    data = isolated_cgc_settings(args, settings)
    if data is None:
        args.cgc_from_settings = None
        return None

    path = args.cgc_isolated_settings_path or (
        args.cgc_isolated_runtime_root.resolve() / "settings" / "codegraphcontext-provider-settings.json"
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


def command_display(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "command": command,
            "cwd": cwd.as_posix(),
        }

    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=subprocess_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "cwd": cwd.as_posix(),
        "durationSeconds": round(time.monotonic() - started, 3),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "json": parse_json_stdout(completed.stdout),
    }


def parse_json_stdout(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def run_lifecycle(
    coordination_root: Path,
    provider: str,
    action: str,
    *,
    timeout: int,
    dry_run: bool,
    extra_args: list[str] | None = None,
    native_args: list[str] | None = None,
) -> dict[str, Any]:
    argv = [
        provider,
        "--coordination-root",
        coordination_root.as_posix(),
        "--timeout",
        str(timeout),
        "--json",
        *(extra_args or []),
        action,
        *(native_args or []),
    ]
    command = [sys.executable, "-m", "agents_remember.providers.provider_lifecycle", *argv]
    if dry_run:
        result = {
            "ok": True,
            "dryRun": True,
            "command": command,
            "cwd": coordination_root.as_posix(),
            "json": None,
        }
    else:
        result = run_package_main(
            operation=f"provider_setup.{provider}.{action}",
            main=provider_lifecycle.main,
            argv=argv,
        )
        result.update(
            {
                "command": command,
                "cwd": coordination_root.as_posix(),
                "json": result.get("payload"),
            }
        )
    result.update({"provider": provider, "action": action})
    return result


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


def path_replacements(source_root: Path, target_root: Path) -> list[tuple[str, str]]:
    source_resolved = source_root.resolve()
    target_resolved = target_root.resolve()
    pairs = [
        (str(source_resolved), str(target_resolved)),
        (source_resolved.as_posix(), target_resolved.as_posix()),
        (str(source_resolved).replace("/", "\\"), str(target_resolved).replace("/", "\\")),
        (source_resolved.as_posix().replace("/", "\\"), target_resolved.as_posix().replace("/", "\\")),
    ]
    unique: dict[str, str] = {}
    for source, target in pairs:
        if source and source not in unique:
            unique[source] = target
    return sorted(unique.items(), key=lambda item: len(item[0]), reverse=True)


def rewrite_string(value: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    rewritten = value
    count = 0
    for source, target in replacements:
        occurrences = rewritten.count(source)
        if occurrences:
            rewritten = rewritten.replace(source, target)
            count += occurrences
    return rewritten, count


def rewrite_json_value(value: Any, replacements: list[tuple[str, str]]) -> tuple[Any, int]:
    if isinstance(value, str):
        return rewrite_string(value, replacements)
    if isinstance(value, list):
        total = 0
        rewritten_items = []
        for item in value:
            rewritten, count = rewrite_json_value(item, replacements)
            rewritten_items.append(rewritten)
            total += count
        return rewritten_items, total
    if isinstance(value, dict):
        total = 0
        rewritten_dict: dict[str, Any] = {}
        for key, item in value.items():
            rewritten_key, key_count = rewrite_string(key, replacements)
            rewritten_item, item_count = rewrite_json_value(item, replacements)
            rewritten_dict[rewritten_key] = rewritten_item
            total += key_count + item_count
        return rewritten_dict, total
    return value, 0


def rewrite_cgc_bundle_paths(
    source_bundle: Path,
    target_bundle: Path,
    source_root: Path,
    target_root: Path,
) -> dict[str, Any]:
    replacements = path_replacements(source_root, target_root)
    total_replacements = 0
    files_rewritten: list[str] = []

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        with zipfile.ZipFile(source_bundle, "r") as zip_ref:
            for entry in zip_ref.namelist():
                resolved = (temp_root / entry).resolve()
                if not str(resolved).startswith(str(temp_root.resolve())):
                    raise RuntimeError(f"unsafe CGC bundle entry escapes extraction root: {entry}")
            zip_ref.extractall(temp_root)

        for path in sorted(temp_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(temp_root).as_posix()
            if path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                rewritten, count = rewrite_json_value(data, replacements)
                if count:
                    path.write_text(json.dumps(rewritten, indent=2) + "\n", encoding="utf-8")
                    files_rewritten.append(relative)
                    total_replacements += count
            elif path.suffix == ".jsonl":
                output_lines: list[str] = []
                count = 0
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        output_lines.append(line)
                        continue
                    data = json.loads(line)
                    rewritten, line_count = rewrite_json_value(data, replacements)
                    output_lines.append(json.dumps(rewritten, separators=(",", ":")))
                    count += line_count
                if count:
                    path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
                    files_rewritten.append(relative)
                    total_replacements += count
            elif path.suffix.lower() in {".md", ".txt"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                rewritten, count = rewrite_string(text, replacements)
                if count:
                    path.write_text(rewritten, encoding="utf-8")
                    files_rewritten.append(relative)
                    total_replacements += count

        target_bundle.parent.mkdir(parents=True, exist_ok=True)
        if target_bundle.exists():
            target_bundle.unlink()
        with zipfile.ZipFile(target_bundle, "w", compression=zipfile.ZIP_DEFLATED) as zip_ref:
            for path in sorted(temp_root.rglob("*")):
                if path.is_file():
                    zip_ref.write(path, path.relative_to(temp_root).as_posix())

    return {
        "sourceBundle": source_bundle.as_posix(),
        "targetBundle": target_bundle.as_posix(),
        "sourceRoot": source_root.resolve().as_posix(),
        "targetRoot": target_root.resolve().as_posix(),
        "replacementCount": total_replacements,
        "filesRewritten": files_rewritten,
    }


def cgc_seed_bundle(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Any]:
    target_coordination_root = args.coordination_root
    source_coordination_root = args.cgc_seed_source_coordination_root
    if source_coordination_root is None:
        return {"ok": False, "skipped": True, "reason": "no seed source coordination root configured"}

    target_repo_id = args.cgc_seed_repo_id
    source_repo_id = args.cgc_seed_source_repo_id or target_repo_id
    source_settings_file = cgc_seed_source_settings_path(args, source_coordination_root, target_coordination_root)
    source_settings = load_settings(source_coordination_root, source_settings_file)
    if source_settings is None:
        return {
            "ok": False,
            "skipped": True,
            "reason": f"source settings missing: {settings_path(source_coordination_root, source_settings_file)}",
        }

    target_root_info = configured_cgc_repo_root(target_coordination_root, settings, target_repo_id)
    source_root_info = configured_cgc_repo_root(source_coordination_root, source_settings, source_repo_id)
    if args.cgc_seed_target_repo_root is not None:
        target_repo_root = args.cgc_seed_target_repo_root.resolve()
        selected_target_id = target_repo_id or (target_root_info[0] if target_root_info else target_repo_root.name)
        target_repo_id = stable_provider_id(selected_target_id)
    elif target_root_info is not None:
        target_repo_id, target_repo_root = target_root_info
    else:
        return {"ok": False, "skipped": True, "reason": "target CGC root is not configured"}

    if args.cgc_seed_source_repo_root is not None:
        source_repo_root = args.cgc_seed_source_repo_root.resolve()
        selected_source_id = source_repo_id or (source_root_info[0] if source_root_info else source_repo_root.name)
        source_repo_id = stable_provider_id(selected_source_id)
    elif source_root_info is not None:
        source_repo_id, source_repo_root = source_root_info
    else:
        return {"ok": False, "skipped": True, "reason": "source CGC root is not configured"}

    source_head = git_head(source_repo_root)
    target_head = git_head(target_repo_root)
    if not args.cgc_seed_allow_commit_mismatch and source_head and target_head and source_head != target_head:
        return {
            "ok": False,
            "skipped": True,
            "reason": "source and target repository HEAD commits differ",
            "sourceHead": source_head,
            "targetHead": target_head,
            "sourceRepoRoot": source_repo_root.as_posix(),
            "targetRepoRoot": target_repo_root.as_posix(),
        }

    if (
        source_coordination_root.resolve() == target_coordination_root.resolve()
        and source_repo_root.resolve() != target_repo_root.resolve()
        and args.cgc_isolated_runtime_root is None
        and not args.cgc_seed_allow_same_coordination_root
    ):
        return {
            "ok": False,
            "skipped": True,
            "reason": "refusing to seed a different repo path into the same coordination root without --cgc-seed-allow-same-coordination-root",
            "sourceRepoRoot": source_repo_root.as_posix(),
            "targetRepoRoot": target_repo_root.as_posix(),
        }

    bundle_root = (args.cgc_seed_bundle_dir or target_coordination_root / "temp" / "provider-seeds").resolve()
    seed_id = stable_provider_id(str(target_repo_id or target_repo_root.name))
    source_bundle = bundle_root / f"{seed_id}.source.cgc"
    rewritten_bundle = bundle_root / f"{seed_id}.target.cgc"
    if not args.dry_run:
        bundle_root.mkdir(parents=True, exist_ok=True)

    source_backend = run_lifecycle(
        source_coordination_root,
        "cgc",
        "backend-start",
        timeout=args.timeout,
        dry_run=args.dry_run,
        extra_args=cgc_seed_source_extra_args(args, source_coordination_root, target_coordination_root),
    )
    if not source_backend.get("ok"):
        return {"ok": False, "stage": "source-backend-start", "command": source_backend}

    export = run_lifecycle(
        source_coordination_root,
        "cgc",
        "run",
        timeout=args.timeout,
        dry_run=args.dry_run,
        extra_args=[
            *cgc_seed_source_extra_args(args, source_coordination_root, target_coordination_root),
            "--repo-id",
            str(source_repo_id),
        ],
        native_args=[
            "--",
            "export",
            source_bundle.as_posix(),
            "--repo",
            source_repo_root.as_posix(),
            "--no-stats",
        ],
    )
    if not export.get("ok"):
        return {"ok": False, "stage": "export", "command": export}

    if args.dry_run:
        rewrite = {
            "sourceBundle": source_bundle.as_posix(),
            "targetBundle": rewritten_bundle.as_posix(),
            "sourceRoot": source_repo_root.as_posix(),
            "targetRoot": target_repo_root.as_posix(),
            "dryRun": True,
        }
    else:
        rewrite = rewrite_cgc_bundle_paths(source_bundle, rewritten_bundle, source_repo_root, target_repo_root)

    load = run_lifecycle(
        target_coordination_root,
        "cgc",
        "run",
        timeout=args.timeout,
        dry_run=args.dry_run,
        extra_args=[*cgc_extra_args(args), "--repo-id", str(target_repo_id)],
        native_args=["--", "load", rewritten_bundle.as_posix(), "--clear"],
    )
    if not load.get("ok"):
        return {"ok": False, "stage": "load", "rewrite": rewrite, "command": load}

    return {
        "ok": True,
        "seeded": True,
        "repoId": target_repo_id,
        "sourceRepoId": source_repo_id,
        "sourceCoordinationRoot": source_coordination_root.as_posix(),
        "targetCoordinationRoot": target_coordination_root.as_posix(),
        "sourceRepoRoot": source_repo_root.as_posix(),
        "targetRepoRoot": target_repo_root.as_posix(),
        "sourceHead": source_head,
        "targetHead": target_head,
        "sourceBackend": source_backend,
        "export": export,
        "rewrite": rewrite,
        "load": load,
    }


def install_enabled_providers(args: argparse.Namespace, settings: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if selected_provider_enabled(args, settings, "grepai-memory"):
        results.append(
            run_lifecycle(
                args.coordination_root,
                "grepai",
                "install",
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
        )
    if selected_provider_enabled(args, settings, "codegraphcontext-code"):
        results.append(
            run_lifecycle(
                args.coordination_root,
                "cgc",
                "install-all",
                timeout=args.timeout,
                dry_run=args.dry_run,
                extra_args=cgc_extra_args(args),
            )
        )
    return results


def prepare_enabled_providers(args: argparse.Namespace, settings: dict[str, Any]) -> list[dict[str, Any]]:
    results = install_enabled_providers(args, settings)

    if selected_provider_enabled(args, settings, "grepai-memory"):
        results.append(
            run_lifecycle(
                args.coordination_root,
                "grepai",
                "refresh",
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
        )

    if selected_provider_enabled(args, settings, "codegraphcontext-code"):
        seed = cgc_seed_bundle(args, settings)
        results.append({"provider": "codegraphcontext", "action": "seed", **seed})
        if not seed.get("ok"):
            if args.cgc_refresh_fallback:
                results.append(
                    run_lifecycle(
                        args.coordination_root,
                        "cgc",
                        "refresh-all",
                        timeout=args.timeout,
                        dry_run=args.dry_run,
                        extra_args=cgc_extra_args(args),
                    )
                )
            else:
                results.append(
                    {
                        "ok": False,
                        "provider": "codegraphcontext",
                        "action": "refresh-all",
                        "skipped": True,
                        "reason": "CGC seed failed and refresh fallback is disabled",
                    }
                )

    if not args.skip_watchers and (
        selected_provider_enabled(args, settings, "grepai-memory")
        or selected_provider_enabled(args, settings, "codegraphcontext-code")
    ):
        results.append(
            run_lifecycle(
                args.coordination_root,
                "watchers",
                "start",
                timeout=args.timeout,
                dry_run=args.dry_run,
                extra_args=cgc_extra_args(args),
            )
        )
        results.append(
            run_lifecycle(
                args.coordination_root,
                "watchers",
                "status",
                timeout=args.timeout,
                dry_run=args.dry_run,
                extra_args=cgc_extra_args(args),
            )
        )
    return results


def action_payload(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(args.coordination_root, args.from_settings)
    if settings is None:
        return {
            "ok": True,
            "action": args.action,
            "coordinationRoot": args.coordination_root.as_posix(),
            "settingsFile": settings_path(args.coordination_root, args.from_settings).as_posix(),
            "enabled": {},
            "results": [],
            "note": "settings.json is missing; no providers configured",
        }

    enabled = {
        "grepai-memory": selected_provider_enabled(args, settings, "grepai-memory"),
        "codegraphcontext-code": selected_provider_enabled(args, settings, "codegraphcontext-code"),
    }
    isolated = write_isolated_cgc_settings(args, settings)
    if args.action == "install":
        results = install_enabled_providers(args, settings)
    elif args.action == "prepare":
        results = prepare_enabled_providers(args, settings)
    else:
        raise RuntimeError(f"unsupported action: {args.action}")

    return {
        "ok": all(result.get("ok") for result in results),
        "action": args.action,
        "dryRun": args.dry_run,
        "coordinationRoot": args.coordination_root.as_posix(),
        "settingsFile": settings_path(args.coordination_root, args.from_settings).as_posix(),
        "isolatedCgcSettings": isolated,
        "enabled": enabled,
        "results": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "prepare"))
    parser.add_argument("--coordination-root", type=Path, required=True)
    parser.add_argument("--from-settings", type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-watchers", action="store_true")
    parser.add_argument("--skip-grepai", action="store_true")
    parser.add_argument("--no-cgc-refresh-fallback", dest="cgc_refresh_fallback", action="store_false")
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
    return parser


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        f"provider setup {payload['action']}: {'ok' if payload.get('ok') else 'failed'}",
        f"coordination root: {payload['coordinationRoot']}",
    ]
    for result in payload.get("results", []):
        status = "ok" if result.get("ok") else "failed"
        if result.get("skipped"):
            status = "skipped"
        provider = result.get("provider", "provider")
        action = result.get("action", "action")
        detail = result.get("reason") or result.get("stage") or ""
        suffix = f" ({detail})" if detail else ""
        lines.append(f"- {provider} {action}: {status}{suffix}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.coordination_root = args.coordination_root.resolve()
    for name in (
        "cgc_seed_source_coordination_root",
        "cgc_seed_source_from_settings",
        "cgc_seed_source_repo_root",
        "cgc_seed_target_repo_root",
        "cgc_seed_bundle_dir",
        "cgc_isolated_runtime_root",
        "cgc_isolated_settings_path",
        "from_settings",
    ):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())

    try:
        payload = action_payload(args)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, zipfile.BadZipFile, json.JSONDecodeError) as error:
        payload = {
            "ok": False,
            "action": args.action,
            "dryRun": args.dry_run,
            "coordinationRoot": args.coordination_root.as_posix(),
            "error": str(error),
        }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(render_text(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Manage optional Agents Remember context providers."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def runtime_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def install_shared_import_path() -> None:
    shared_root = runtime_root_from_script() / "skills" / "U-01-core-skills" / "_shared"
    sys.path.insert(0, str(shared_root))


install_shared_import_path()

from agents_remember.context_providers import (  # noqa: E402
    CGC_CGCIGNORE_PATCH_ID,
    ContextProviderError,
    apply_cgc_cgcignore_patch,
    cgc_cgcignore_patch_applied,
    cgc_runtime_layout,
    ensure_cgc_runtime_layout,
    file_sha256,
    find_cgc_cgcignore_module,
    source_provider_artifacts,
    stable_provider_id,
    write_provider_state,
)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_cmdline(pid: int) -> str:
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    if not proc_cmdline.exists():
        return ""
    try:
        return proc_cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def render(data: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    status = "ok" if data.get("ok") else "needs-attention"
    print(f"{data['provider']} {data['action']}: {status}")
    for key, value in data.items():
        if key in {"provider", "action", "ok"}:
            continue
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, sort_keys=True)}")
        else:
            print(f"{key}: {value}")


def cgc_layout_from_args(args: argparse.Namespace):
    return cgc_runtime_layout(
        coordination_root=args.coordination_root,
        repo_id=args.repo_id,
        code_repo_root=args.code_repo_root,
    )


def cgc_status(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    artifacts = [path.as_posix() for path in source_provider_artifacts(layout.code_repo_root)]
    cgc_executable = layout.cgc_executable()
    state = read_json(layout.state_file)
    pid = state.get("process", {}).get("pid")
    patch = {
        "module": None,
        "applied": False,
        "error": None,
    }
    if cgc_executable.exists():
        try:
            module = find_cgc_cgcignore_module(layout.venv_root)
            patch["module"] = module.as_posix()
            patch["applied"] = cgc_cgcignore_patch_applied(module)
        except ContextProviderError as error:
            patch["error"] = str(error)

    return {
        "provider": "codegraphcontext",
        "action": "status",
        "ok": cgc_executable.exists() and not artifacts and bool(patch["applied"]),
        "repoId": layout.repo_id,
        "codeRepoRoot": layout.code_repo_root.as_posix(),
        "runtimeRoot": layout.runtime_root.as_posix(),
        "cgcRoot": layout.cgc_root.as_posix(),
        "venvRoot": layout.venv_root.as_posix(),
        "cgcExecutable": cgc_executable.as_posix(),
        "cgcExecutableExists": cgc_executable.exists(),
        "requirementsFile": layout.requirements_file.as_posix(),
        "patchesRoot": layout.patches_root.as_posix(),
        "sourceArtifacts": artifacts,
        "patch": patch,
        "process": {"pid": pid, "alive": process_alive(int(pid)) if isinstance(pid, int) else False},
    }


def cgc_init_layout(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    if not args.dry_run:
        ensure_cgc_runtime_layout(layout)
        write_provider_state(
            layout,
            {
                "provider": "codegraphcontext",
                "repoId": layout.repo_id,
                "codeRepoRoot": layout.code_repo_root.as_posix(),
                "runtimeRoot": layout.runtime_root.as_posix(),
                "requirementsFile": layout.requirements_file.as_posix(),
                "requirementsSha256": file_sha256(layout.requirements_file),
                "patchesRoot": layout.patches_root.as_posix(),
                "lastAction": "init-layout",
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
    return {
        "provider": "codegraphcontext",
        "action": "init-layout",
        "ok": True,
        "dryRun": args.dry_run,
        "repoId": layout.repo_id,
        "runtimeRoot": layout.runtime_root.as_posix(),
        "cgcRoot": layout.cgc_root.as_posix(),
        "venvRoot": layout.venv_root.as_posix(),
        "requirementsFile": layout.requirements_file.as_posix(),
        "patchesRoot": layout.patches_root.as_posix(),
        "stateFile": layout.state_file.as_posix(),
    }


def cgc_patch(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    module = find_cgc_cgcignore_module(layout.venv_root)
    already_applied = cgc_cgcignore_patch_applied(module)
    changed = False
    if not args.dry_run and not already_applied:
        changed = apply_cgc_cgcignore_patch(module)
    elif args.dry_run:
        text = module.read_text(encoding="utf-8")
        changed = not already_applied and "local_cgcignore_path = ignore_root / \".cgcignore\"" in text

    state = read_json(layout.state_file)
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "appliedPatches": sorted(set(state.get("appliedPatches", [])) | {CGC_CGCIGNORE_PATCH_ID})
            if already_applied or changed
            else state.get("appliedPatches", []),
            "patchVerification": {
                CGC_CGCIGNORE_PATCH_ID: {
                    "module": module.as_posix(),
                    "applied": cgc_cgcignore_patch_applied(module) if not args.dry_run else already_applied,
                    "dryRunWouldChange": changed if args.dry_run else False,
                }
            },
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    if not args.dry_run:
        write_json(layout.state_file, state)

    return {
        "provider": "codegraphcontext",
        "action": "patch",
        "ok": already_applied or changed,
        "dryRun": args.dry_run,
        "patchId": CGC_CGCIGNORE_PATCH_ID,
        "module": module.as_posix(),
        "alreadyApplied": already_applied,
        "changed": changed,
    }


def cgc_doctor(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    status = cgc_status(args)
    checks: list[dict[str, Any]] = [
        {"name": "runtime-root-contained", "ok": layout.runtime_root.is_relative_to(layout.providers_root)},
        {"name": "source-artifact-clean", "ok": not status["sourceArtifacts"], "artifacts": status["sourceArtifacts"]},
        {"name": "cgc-executable", "ok": status["cgcExecutableExists"], "path": status["cgcExecutable"]},
        {"name": "cgcignore-patch", "ok": bool(status["patch"]["applied"]), "details": status["patch"]},
    ]
    command_result = None
    if status["cgcExecutableExists"] and not args.dry_run:
        command_result = run_command(
            [layout.cgc_executable().as_posix(), "doctor"],
            cwd=layout.runtime_root,
            env=layout.env(),
            timeout=args.timeout,
        )
        checks.append({"name": "cgc-doctor-command", "ok": command_result["returncode"] == 0})
    elif args.dry_run:
        command_result = {
            "command": [layout.cgc_executable().as_posix(), "doctor"],
            "cwd": layout.runtime_root.as_posix(),
            "env": layout.env(),
        }

    ok = all(check["ok"] for check in checks)
    return {
        "provider": "codegraphcontext",
        "action": "doctor",
        "ok": ok,
        "dryRun": args.dry_run,
        "checks": checks,
        "command": command_result,
    }


def cgc_start(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "start",
            "ok": True,
            "dryRun": True,
            "command": [layout.cgc_executable().as_posix(), "watch", layout.code_repo_root.as_posix()],
            "cwd": layout.runtime_root.as_posix(),
            "env": layout.env(),
        }
    ensure_cgc_runtime_layout(layout)
    doctor = cgc_doctor(args)
    if not doctor["ok"]:
        return {**doctor, "action": "start", "ok": False}

    watch_log = layout.logs_root / "watch.log"
    watch_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = watch_log.open("ab")
    process = subprocess.Popen(
        [layout.cgc_executable().as_posix(), "watch", layout.code_repo_root.as_posix()],
        cwd=str(layout.runtime_root),
        env={**os.environ, **layout.env()},
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    write_provider_state(
        layout,
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "codeRepoRoot": layout.code_repo_root.as_posix(),
            "runtimeRoot": layout.runtime_root.as_posix(),
            "process": {"pid": process.pid, "logFile": watch_log.as_posix(), "mode": "watch"},
            "lastAction": "start",
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return {
        "provider": "codegraphcontext",
        "action": "start",
        "ok": True,
        "pid": process.pid,
        "logFile": watch_log.as_posix(),
    }


def cgc_stop(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    state = read_json(layout.state_file)
    pid = state.get("process", {}).get("pid")
    if not isinstance(pid, int):
        return {"provider": "codegraphcontext", "action": "stop", "ok": True, "message": "no managed pid"}
    cmdline = process_cmdline(pid)
    if cmdline and "cgc" not in cmdline and "codegraphcontext" not in cmdline.lower():
        return {
            "provider": "codegraphcontext",
            "action": "stop",
            "ok": False,
            "message": "managed pid no longer looks like a CGC process",
            "pid": pid,
            "cmdline": cmdline,
        }
    if args.dry_run:
        return {"provider": "codegraphcontext", "action": "stop", "ok": True, "dryRun": True, "pid": pid}
    if process_alive(pid):
        os.kill(pid, signal.SIGTERM)
    state["process"] = {"pid": pid, "alive": False, "stoppedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    state["lastAction"] = "stop"
    write_json(layout.state_file, state)
    return {"provider": "codegraphcontext", "action": "stop", "ok": True, "pid": pid}


def cgc_refresh(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    command = [layout.cgc_executable().as_posix(), "index", layout.code_repo_root.as_posix(), "--force"]
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "refresh",
            "ok": True,
            "dryRun": True,
            "command": command,
            "cwd": layout.runtime_root.as_posix(),
            "env": layout.env(),
        }
    doctor = cgc_doctor(args)
    if not doctor["ok"]:
        return {**doctor, "action": "refresh", "ok": False}
    result = run_command(command, cwd=layout.runtime_root, env=layout.env(), timeout=args.timeout)
    state = read_json(layout.state_file)
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "lastAction": "refresh",
            "lastRefresh": {
                "returncode": result["returncode"],
                "durationSeconds": result["durationSeconds"],
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
    )
    write_json(layout.state_file, state)
    return {
        "provider": "codegraphcontext",
        "action": "refresh",
        "ok": result["returncode"] == 0,
        "command": result,
    }


def grepai_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    root = args.root or args.coordination_root / "memory-repos"
    runtime_root = args.runtime_root or args.coordination_root / "providers" / "grepai" / "memory-repos"
    return root.resolve(), runtime_root.resolve()


def grepai_run(args: argparse.Namespace, action: str) -> dict[str, Any]:
    root, runtime_root = grepai_paths(args)
    executable = shutil.which("grepai")
    commands: list[list[str]]
    if action == "status":
        commands = [["grepai", "status", "--no-ui"], ["grepai", "watch", "--status"]]
    elif action == "start":
        commands = [["grepai", "watch", "--background", "--log-dir", (runtime_root / "logs").as_posix()]]
    elif action == "stop":
        commands = [["grepai", "watch", "--stop"]]
    elif action == "refresh":
        commands = [["grepai", "watch", "--stop"], ["grepai", "watch", "--background", "--log-dir", (runtime_root / "logs").as_posix()]]
    else:
        raise ValueError(action)

    if args.dry_run:
        return {
            "provider": "grepai",
            "action": action,
            "ok": True,
            "dryRun": True,
            "root": root.as_posix(),
            "runtimeRoot": runtime_root.as_posix(),
            "commands": commands,
        }
    if not executable:
        return {
            "provider": "grepai",
            "action": action,
            "ok": False,
            "root": root.as_posix(),
            "message": "grepai command not found",
        }
    runtime_root.mkdir(parents=True, exist_ok=True)
    results = [run_command(command, cwd=root, timeout=args.timeout) for command in commands]
    state = {
        "provider": "grepai",
        "root": root.as_posix(),
        "runtimeRoot": runtime_root.as_posix(),
        "lastAction": action,
        "commands": [
            {
                "command": result["command"],
                "returncode": result["returncode"],
                "durationSeconds": result["durationSeconds"],
            }
            for result in results
        ],
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json(runtime_root / "provider-state.json", state)
    return {
        "provider": "grepai",
        "action": action,
        "ok": all(result["returncode"] == 0 for result in results),
        "root": root.as_posix(),
        "runtimeRoot": runtime_root.as_posix(),
        "commands": results,
    }


def add_common_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coordination-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    providers = parser.add_subparsers(dest="provider", required=True)

    cgc = providers.add_parser("cgc", help="Manage a CodeGraphContext code provider instance.")
    cgc.add_argument("action", choices=["status", "init-layout", "patch", "doctor", "start", "stop", "refresh"])
    add_common_provider_args(cgc)
    cgc.add_argument("--repo-id", required=True, help="Stable provider id for this code repository.")
    cgc.add_argument("--code-repo-root", type=Path, required=True)

    grepai = providers.add_parser("grepai", help="Manage a GrepAI memory provider instance.")
    grepai.add_argument("action", choices=["status", "start", "stop", "refresh"])
    add_common_provider_args(grepai)
    grepai.add_argument("--root", type=Path, help="Indexed memory root. Defaults to <coordination-root>/memory-repos.")
    grepai.add_argument(
        "--runtime-root",
        type=Path,
        help="Provider runtime root. Defaults to <coordination-root>/providers/grepai/memory-repos.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.coordination_root = args.coordination_root.resolve()
    if hasattr(args, "code_repo_root"):
        args.code_repo_root = args.code_repo_root.resolve()
        args.repo_id = stable_provider_id(args.repo_id)

    try:
        if args.provider == "cgc":
            handlers = {
                "status": cgc_status,
                "init-layout": cgc_init_layout,
                "patch": cgc_patch,
                "doctor": cgc_doctor,
                "start": cgc_start,
                "stop": cgc_stop,
                "refresh": cgc_refresh,
            }
            data = handlers[args.action](args)
        elif args.provider == "grepai":
            data = grepai_run(args, args.action)
        else:
            parser.error(f"unsupported provider: {args.provider}")
            return 2
    except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
        data = {"provider": args.provider, "action": args.action, "ok": False, "error": str(error)}

    render(data, as_json=args.json)
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

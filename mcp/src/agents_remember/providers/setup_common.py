"""Shared provider setup settings and lifecycle helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agents_remember.mcp.command_capture import run_package_main
from agents_remember.providers import lifecycle


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


def require_settings_path(from_settings: Path | None) -> Path:
    if from_settings is None:
        raise RuntimeError(
            "provider setup requires an explicit settings path; "
            "coordinator system/settings.json is not an authority source"
        )
    return from_settings.resolve()


def settings_path(coordination_root: Path, from_settings: Path | None = None) -> Path:
    _ = coordination_root
    return require_settings_path(from_settings)


def load_settings(
    coordination_root: Path, from_settings: Path | None = None
) -> dict[str, Any] | None:
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


def selected_provider_enabled(args: Any, settings: dict[str, Any], provider_id: str) -> bool:
    if provider_id == "grepai-memory" and args.skip_grepai:
        return False
    return provider_enabled(settings, provider_id)


def expand_template(value: str, coordination_root: Path) -> str:
    return value.replace("<coordination_root>", coordination_root.as_posix()).replace(
        "<workspace_root>", coordination_root.parent.as_posix()
    )


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
        capture_output=True,
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
    command = [sys.executable, "-m", "agents_remember.providers.lifecycle", *argv]
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
            main=lifecycle.main,
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

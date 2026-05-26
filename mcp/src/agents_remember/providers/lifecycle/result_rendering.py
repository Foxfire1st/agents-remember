"""Lifecycle result rendering helpers."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def render(data: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
        return

    status = "ok" if data.get("ok") else "needs-attention"
    print(f"{data['provider']} {data['action']}: {status}")
    for key, value in data.items():
        render_plain_field(key, value)


def render_plain_field(key: str, value: Any) -> None:
    if key in {"provider", "action", "ok"}:
        return
    if isinstance(value, (dict, list)):
        print(f"{key}: {json.dumps(value, sort_keys=True)}")
        return
    print(f"{key}: {value}")


def render_captured_command_output(data: dict[str, Any]) -> bool:
    command = data.get("command")
    if not isinstance(command, dict):
        return False

    print_optional_stream(command.get("stdout") or "")
    print_optional_stream(command.get("stderr") or "", stderr=True)
    return True


def print_optional_stream(text: str, *, stderr: bool = False) -> None:
    if not text:
        return
    output = sys.stderr if stderr else sys.stdout
    print(text, end="" if text.endswith("\n") else "\n", file=output)


def cgc_run_api_payload(data: dict[str, Any]) -> dict[str, Any]:
    command = data.get("command")
    if not isinstance(command, dict):
        return data

    payload: dict[str, Any] = {
        key: data[key]
        for key in ("provider", "action", "ok", "repoId", "workspace", "dryRun")
        if key in data
    }
    payload.update(command_metadata(command))
    payload["outputLines"] = command_output_lines(command)
    return payload


def command_metadata(command: dict[str, Any]) -> dict[str, Any]:
    return {key: command[key] for key in ("returncode", "durationSeconds") if key in command}


def command_output_lines(command: dict[str, Any]) -> list[str]:
    return [
        line
        for stream_name in ("stdout", "stderr")
        for line in str(command.get(stream_name) or "").splitlines()
    ]


def render_cgc_run_result(data: dict[str, Any], args: argparse.Namespace) -> bool:
    if getattr(args, "lifecycle_json", False):
        render(cgc_run_api_payload(data), as_json=True)
        return True
    if not getattr(args, "dry_run", False):
        return render_captured_command_output(data)
    return False


def render_grepai_run_result(data: dict[str, Any], args: argparse.Namespace) -> bool:
    return render_cgc_run_result(data, args)

#!/usr/bin/env python3
"""Invoke the tooling's public renderer once on native shared setup; retain provenance.

Default comparison is inert data from that same renderer, not another installation/tree.
The native public build owns setup and this invocation. Reset retires its receipt with the
disposable sandbox. Provider values are neither printed nor copied into the receipt.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(args: argparse.Namespace) -> dict:
    settings, workspace, coordination = (
        args.settings,
        args.workspace,
        args.coordination,
    )
    host_port, dashboard_port, repo = args.host_port, args.dashboard_port, args.repo
    source = Path(__file__).resolve().parents[1] / "harness/render_starter.py"
    spec = importlib.util.spec_from_file_location("pnt_starter_renderer", source)
    if spec is None or spec.loader is None:
        raise ValueError(f"no public renderer at {source}")
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    before = json.loads(settings.read_text(encoding="utf-8"))
    shared = coordination / "system/settings.json"
    shared_before = digest(shared)
    block = json.loads(shared.read_text(encoding="utf-8"))["paseoRuntime"]
    default = renderer.settings_payload(before, workspace, [repo], renderer.RenderOptions())
    default_host = renderer.fresh_host_block()
    input_hash = digest(settings)
    options = renderer.RenderOptions(coordination, host_port, dashboard_port)
    with contextlib.redirect_stdout(io.StringIO()):
        renderer.render_settings(settings, workspace, [repo], options)
    if digest(shared) != shared_before:
        raise ValueError("public renderer changed the configured shared input")
    actual = json.loads(settings.read_text(encoding="utf-8"))
    settings_differences = [
        key for key in sorted(set(default) | set(actual)) if default.get(key) != actual.get(key)
    ]
    if set(settings_differences) - {"coordinationRoot", "transcriptRoot", "dashboard"}:
        raise ValueError(f"unapproved settings differences: {settings_differences}")
    differences = [
        key
        for key in sorted(set(default_host) | set(block))
        if default_host.get(key) != block.get(key)
    ]
    if set(differences) - {"installPrefix", "home", "listen", "providers", "embed"}:
        raise ValueError(f"unapproved host differences: {differences}")
    return {
        "rendererSource": str(source),
        "rendererSha256": digest(source),
        "inputSettingsPath": str(settings),
        "inputSettingsSha256": input_hash,
        "renderedSettingsPath": str(settings),
        "renderedSettingsSha256": digest(settings),
        "sharedInputPath": str(shared),
        "sharedBeforeSha256": shared_before,
        "sharedAfterSha256": digest(shared),
        "invocations": 1,
        "arguments": {
            "coordinationRoot": str(coordination),
            "hostPort": host_port,
            "dashboardPort": dashboard_port,
        },
        "defaultSettings": default,
        "renderedSettings": actual,
        "settingsDifferenceKeys": settings_differences,
        "hostDifferenceKeys": differences,
        "providerIds": sorted(block["providers"]),
        "hostDifferences": [
            {
                "key": key,
                "reason": {
                    "installPrefix": "native sandbox-owned install prefix",
                    "home": "native sandbox-owned daemon home",
                    "listen": "recorded reserved host port",
                    "embed": "recorded host/dashboard ports and both loopbacks",
                    "providers": "native guarded harness provider entries",
                }[key],
            }
            for key in differences
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("settings", "workspace", "coordination"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("host-port", "dashboard-port"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    print(json.dumps(render(args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

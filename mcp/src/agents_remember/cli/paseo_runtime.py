"""CLI adapter: provision, inspect and stop the one Paseo runtime the settings describe.

    agents-remember paseo provision --config <MCP settings file>
    agents-remember paseo status    --config <MCP settings file>
    agents-remember paseo stop      --config <MCP settings file>

Each command reads the ``paseoRuntime`` block of the named settings file and prints one JSON
document. ``--config`` is required: these commands install software and start or stop a daemon,
so they never fall back to a discovered settings file.

Exit status: 0 when the command did what it reports; 1 when a step failed (the document's
``error`` names the step and carries Paseo's text); 2 when the command refused before doing
anything, because the settings are unusable or hold no ``paseoRuntime`` block.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from agents_remember.cli.paseo_command import PaseoRuntimeFailure
from agents_remember.cli.paseo_daemon import runtime_status, stop_runtime
from agents_remember.cli.paseo_provision import provision_runtime
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeNotConfigured,
    PaseoRuntimeSettings,
    require_paseo_runtime,
)
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    load_paseo_runtime_settings,
)

_COMMANDS: dict[str, tuple[str, Callable[[PaseoRuntimeSettings], dict[str, Any]]]] = {
    "provision": (
        "Install the pinned Paseo version, write the daemon configuration, install the AR "
        "plugin and start the daemon; report every change.",
        provision_runtime,
    ),
    "status": ("Report whether the configured home's daemon runs, and with what.", runtime_status),
    "stop": ("Stop the daemon of the configured home.", stop_runtime),
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="paseo_command", required=True)
    for name, (help_text, _operation) in _COMMANDS.items():
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--config",
            required=True,
            help="Absolute path of the MCP settings file that holds the paseoRuntime block.",
        )


def run(args: argparse.Namespace) -> int:
    try:
        settings = require_paseo_runtime(
            load_paseo_runtime_settings(args.config), source=args.config
        )
    except PaseoRuntimeNotConfigured as error:
        return _refuse(error.code, str(error))
    except ConfigError as error:
        return _refuse("settings_invalid", str(error))
    _help_text, operation = _COMMANDS[args.paseo_command]
    try:
        report = operation(settings)
    except PaseoRuntimeFailure as failure:
        report = {"ok": False, "error": failure.as_payload()}
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def _refuse(code: str, message: str) -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}, indent=2))
    return 2

"""CLI adapter: provision, inspect and stop the one Paseo runtime the settings describe.

    agents-remember paseo provision --config <MCP settings file>
    agents-remember paseo status    --config <MCP settings file>
    agents-remember paseo stop      --config <MCP settings file>

Each command uses the named MCP file's coordinationRoot to read the shared
``system/settings.json`` host block and prints one JSON document. ``--config`` is required: these commands install software and start or stop a daemon,
so they never fall back to a discovered settings file.

Exit status: 0 when the command did what it reports; 1 when a step failed (the document's
``error`` names the step and carries Paseo's text); 2 when the command refused before doing
anything, because the settings are unusable or hold no ``paseoRuntime`` block. Every failure ends
in that document and one of those codes, never in a traceback.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeNotConfigured,
    PaseoRuntimeSettings,
    require_paseo_runtime,
)
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    load_paseo_runtime_settings,
)
from agents_remember.serving.paseo.paseo_command import PaseoRuntimeFailure
from agents_remember.serving.paseo.paseo_daemon import runtime_status, stop_runtime
from agents_remember.serving.paseo.paseo_provision import provision_runtime

_COMMANDS: dict[str, tuple[str, Callable[[PaseoRuntimeSettings], dict[str, Any]]]] = {
    "provision": (
        "Install the pinned Paseo version, write the daemon configuration, install the AR "
        "plugin and start the daemon; report every change. A changed version, product Node or start-only setting restarts a running host: open sessions close, running turns and permission prompts are lost; sessions remain resumable.",
        provision_runtime,
    ),
    "status": ("Report whether the configured home's daemon runs, and with what.", runtime_status),
    "stop": (
        "Stop the configured home's daemon: open sessions close, running turns and permission prompts are lost; sessions can be resumed after the next start.",
        stop_runtime,
    ),
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="paseo_command", required=True)
    for name, (help_text, _operation) in _COMMANDS.items():
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--config",
            required=True,
            help="Absolute path of the harness MCP settings file; its coordinationRoot links the shared system/settings.json host block.",
        )


def run(args: argparse.Namespace) -> int:
    try:
        loaded = load_paseo_runtime_settings(args.config)
        settings = require_paseo_runtime(
            loaded, source="shared coordinator system/settings.json linked by " + args.config
        )
    except PaseoRuntimeNotConfigured as error:
        return _refuse(error.code, str(error))
    except ConfigError as error:
        return _refuse("settings_invalid", str(error))
    except OSError as error:
        return _refuse("settings_unreadable", f"cannot read {args.config}: {error}")
    except (ValueError, RecursionError) as error:
        # Not UTF-8, or nested beyond what the JSON reader takes: the file is not usable settings.
        problem = f"{type(error).__name__}: {error}"
        return _refuse("settings_invalid", f"cannot parse {args.config}: {problem}")
    _help_text, operation = _COMMANDS[args.paseo_command]
    try:
        report = operation(settings)
    except PaseoRuntimeFailure as failure:
        report = {"ok": False, "error": failure.as_payload()}
    except OSError as error:
        report = _failed("filesystem_error", args.paseo_command, str(error))
    except Exception as error:
        # The contract is one JSON document per run; an unforeseen failure still honours it.
        report = _failed("unexpected_error", args.paseo_command, f"{type(error).__name__}: {error}")
    if settings.version_notice:
        report["notice"] = f"{settings.version_notice}; settings: {settings.source_path}"
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def _failed(code: str, step: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": PaseoRuntimeFailure(code, step, message).as_payload()}


def _refuse(code: str, message: str) -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}, indent=2))
    return 2

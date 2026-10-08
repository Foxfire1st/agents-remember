"""The explicit configured terminal transition when a live host cannot migrate in-place."""

from __future__ import annotations

import shlex

from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings


def terminal_provision_remedy(settings: PaseoRuntimeSettings) -> str:
    command = "agents-remember paseo provision --config "
    command += (
        shlex.quote(str(settings.command_config_path))
        if settings.command_config_path
        else "<the active harness settings file>"
    )
    return f"Run {command} from a terminal outside the host; it can restart the host and ends agent sessions, running turns and pending prompts."

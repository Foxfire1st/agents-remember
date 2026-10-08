"""Session identity a host must never hand to the next agent it starts.

Harness logins remain inherited. Only named session variables are removed; dropping
all CLAUDE_ or CODEX_ variables would also discard credentials and user configuration.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

SESSION_VARIABLES = frozenset(
    json.loads(
        (
            Path(__file__).resolve().parents[2] / "package_data/host-session-environment.json"
        ).read_text(encoding="utf-8")
    )
) | {"AR_HOSTED_SESSION_ID"}

SESSION_PREFIXES = ("PASEO_", "AR_SPAWN_")
HOST_OWN_VARIABLES = frozenset({"PASEO_HOME"})


def is_session_variable(name: str) -> bool:
    return name in SESSION_VARIABLES or name.startswith(SESSION_PREFIXES)


def host_environment(base: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in base.items() if not is_session_variable(name)}


def carried_session_variables(names: Iterable[str]) -> tuple[str, ...]:
    """Report names only, excluding the home Paseo sets in its own supervisor."""
    return tuple(
        sorted(
            name for name in names if is_session_variable(name) and name not in HOST_OWN_VARIABLES
        )
    )

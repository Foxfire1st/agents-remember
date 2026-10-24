"""The ``paseoRuntime`` block a cold host needs before it can dispatch a start.

MIK-R196 rule 1 lets one call start the free seat on a host that has never been provisioned. The
first run therefore has to write the block the installer writes, and the block names the runtime's
own address: the build-pinned install prefix, the daemon home, the listen address and the dashboard
origins. Those names live in this layer, beside the provisioning commands that write them, so the
settings writer asks for a fresh block instead of spelling an address of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.paseo_node_paths import xdg_home
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    DASHBOARD_ORIGINS,
    DEFAULT_HOST_LISTEN,
)

#: The directory names of the provisioned runtime under the XDG data and state homes.
RUNTIME_DIRECTORY = "paseo"


def fresh_host_block(port: int) -> dict[str, Any]:
    """The complete ``paseoRuntime`` block of a fresh installation (c-13 step 3)."""

    data = xdg_home("XDG_DATA_HOME", Path.home() / ".local/share")
    state = xdg_home("XDG_STATE_HOME", Path.home() / ".local/state")
    return {
        "installPrefix": (data / "agents-remember" / RUNTIME_DIRECTORY / "prefix").as_posix(),
        "home": (state / "agents-remember" / RUNTIME_DIRECTORY).as_posix(),
        "listen": DEFAULT_HOST_LISTEN,
        "providers": {},
        "embed": [
            {
                "dashboardOrigin": origin.format(port=port),
                "frameBaseUrl": f"http://{DEFAULT_HOST_LISTEN}",
            }
            for origin in DASHBOARD_ORIGINS
        ],
    }

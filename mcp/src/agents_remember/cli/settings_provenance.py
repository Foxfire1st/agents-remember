"""Read-only reporting of discovered CLI settings and a differently configured dashboard."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import ConfigError
from agents_remember.serving.daemon import daemon_state_directory, read_state
from agents_remember.serving.daemon_identity import dashboard_coordination_root, observe_dashboard


def discovered_settings_line(path: Path, coordination_root: Path) -> str:
    """Name the selected file and the actual daemon's other file, without changing either."""
    line = f"settings discovered: {path}"
    state = read_state(daemon_state_directory(coordination_root))
    if state is None:
        return line
    try:
        identity = observe_dashboard(state.host, state.port)
        if identity is None or not identity.config_path:
            return line
        other = Path(identity.config_path)
        if other.resolve() == path.resolve():
            return line
        if dashboard_coordination_root(identity) != coordination_root.resolve():
            return line
    except (OSError, ConfigError) as error:
        return f"{line}; dashboard settings comparison is unknown: {error}"
    return (
        f"{line}; dashboard daemon pid {identity.pid} uses {other} for the same coordination root"
    )

"""Release-owned Node paths and the supported host platform."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_host_contract import HOST_CONTRACT, NODE_VERSION


@dataclass(frozen=True)
class NodeRuntime:
    root: Path
    cache: Path
    url: str
    sha256: str
    platform: str = "linux-x64"
    version: str = NODE_VERSION

    @property
    def node(self) -> Path:
        return self.root / "bin" / "node"

    @property
    def npm(self) -> Path:
        return self.root / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"

    def payload(self) -> dict:
        return {
            "version": self.version,
            "platform": self.platform,
            "path": self.node.as_posix(),
            "url": self.url,
            "sha256": self.sha256,
        }


def product_node() -> NodeRuntime:
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or not Path("/proc/self").is_dir()
    ):
        raise PaseoRuntimeFailure(
            "node_platform_unsupported",
            "node",
            f"Node {NODE_VERSION} for the host supports Linux x86_64 with /proc only; "
            f"this platform is {platform.system()} {platform.machine()}; nothing installed",
        )
    archive = HOST_CONTRACT["node"]["archives"]["linux-x64"]
    data = xdg_home("XDG_DATA_HOME", Path.home() / ".local/share")
    cache = xdg_home("XDG_CACHE_HOME", Path.home() / ".cache")
    return NodeRuntime(
        data / "agents-remember/node" / f"node-v{NODE_VERSION}-linux-x64",
        cache / "agents-remember/node",
        archive["url"],
        archive["sha256"],
    )


def xdg_home(name: str, default: Path) -> Path:
    """Empty and unset use the same home default; a relative override is never cwd authority."""
    path = Path(os.environ.get(name) or default).expanduser()
    if not path.is_absolute():
        raise PaseoRuntimeFailure(
            "xdg_path_invalid", "node", f"{name} must be an absolute path: {path}"
        )
    return path.resolve()

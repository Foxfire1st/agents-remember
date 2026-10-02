"""Where everything in a PNT sandbox lives, and the settings file generated for it.

One settings file serves the dashboard and the tool server (they share the ``--config`` contract)
and carries the ``paseoRuntime`` block of PNT-R01. Every path it names lies inside the sandbox
directory; the reserved ports are constants, never chosen at run time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SANDBOX_SCHEMA = "pnt-sandbox/v1"
MARKER_NAME = ".pnt-sandbox.json"
REPOSITORY_ID = "sandbox-app"
INTEGRATION_BRANCH = "sandbox-sprint"
PASEO_VERSION = "0.11.0-beta.2"
LOOPBACK = "127.0.0.1"
PASEO_PORT = 6820
DASHBOARD_PORT = 9797
# Configuration a harness applies to every session started below the folder that holds it. The
# sandbox must not sit under such a folder (PNT-R11 item 6). The user's home is exempt: what a
# harness keeps there is user-level configuration, which no sandbox location can avoid.
PROJECT_HARNESS_MARKERS = (
    ".claude",
    ".codex",
    ".pi",
    ".hermes",
    ".dsh",
    ".mcp.json",
    "AGENTS.md",
    "CLAUDE.md",
)
# A harness's own configuration directory. A sandbox inside one would put the sandbox's files
# into the developer's harness configuration, wherever that directory lies.
HARNESS_HOME_NAMES = (".claude", ".codex", ".pi", ".hermes", ".dsh")


class SandboxRefusal(Exception):
    """A command refused before changing anything; the message says why."""


def default_sandbox_root() -> Path:
    return Path.home() / ".local" / "state" / "ar-pnt" / "sandbox"


def default_eve_project() -> Path:
    return Path.home() / "projects" / ".eve"


@dataclass(frozen=True)
class SandboxLayout:
    """Every path of one sandbox, derived from its directory."""

    root: Path
    paseo_port: int = PASEO_PORT
    dashboard_port: int = DASHBOARD_PORT

    def __post_init__(self) -> None:
        # One directory has one layout, however it was spelled: through a link or with ``..`` in
        # it, the lock file, the settings path and the process record must be the same.
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def marker(self) -> Path:
        return self.root / MARKER_NAME

    @property
    def projects(self) -> Path:
        """The Projects folder: the workspace of the taskless and coordinating roles."""
        return self.root / "projects"

    @property
    def repository(self) -> Path:
        return self.projects / REPOSITORY_ID

    @property
    def origin(self) -> Path:
        return self.root / "remotes" / f"{REPOSITORY_ID}.git"

    @property
    def coordination(self) -> Path:
        return self.root / "coordination"

    @property
    def memory(self) -> Path:
        return self.coordination / "memory-repos" / f"ar-{REPOSITORY_ID}"

    @property
    def task_root(self) -> Path:
        return self.coordination / "tasks" / REPOSITORY_ID

    @property
    def settings_file(self) -> Path:
        return self.root / "settings" / "agents-remember-settings.json"

    @property
    def harness_skills(self) -> Path:
        return self.root / "harness" / "skills"

    @property
    def paseo_home(self) -> Path:
        return self.root / "paseo" / "home"

    @property
    def paseo_prefix(self) -> Path:
        return self.root / "paseo" / "prefix"

    @property
    def eve_app(self) -> Path:
        return self.root / "eve" / "app"

    @property
    def eve_launcher(self) -> Path:
        return self.root / "eve" / "eve-acp-launcher.mjs"

    @property
    def dagger_authority(self) -> Path:
        """The registry the build's quality tools would otherwise keep in the user's home."""
        return self.root / "dagger-authority"

    @property
    def lock_file(self) -> Path:
        """Beside the sandbox directory, so it also guards a build and a reset of it."""
        return self.root.parent / f"{self.root.name}.lock"

    @property
    def run_dir(self) -> Path:
        return self.root / "run"

    @property
    def process_record(self) -> Path:
        return self.run_dir / "processes.json"

    @property
    def dashboard_log(self) -> Path:
        return self.run_dir / "dashboard.log"

    @property
    def tmux_dir(self) -> Path:
        return self.run_dir / "tmux"

    @property
    def pycache(self) -> Path:
        return self.root / "cache" / "pycache"

    @property
    def paseo_listen(self) -> str:
        return f"{LOOPBACK}:{self.paseo_port}"

    @property
    def paseo_url(self) -> str:
        return f"http://{self.paseo_listen}"

    @property
    def dashboard_url(self) -> str:
        return f"http://{LOOPBACK}:{self.dashboard_port}/"


def location_refusal(root: Path, home: Path | None = None) -> str | None:
    """Why ``root`` cannot hold a sandbox, or ``None`` when it can."""
    if not root.is_absolute():
        return f"the sandbox directory must be an absolute path: {root}"
    resolved = root.resolve(strict=False)
    user_home = (home or Path.home()).resolve(strict=False)
    if resolved in (user_home, Path(resolved.anchor)) or resolved in user_home.parents:
        return f"the sandbox directory must be a directory of its own, not {resolved}"
    inside = sorted(set(resolved.parts).intersection(HARNESS_HOME_NAMES))
    if inside:
        return (
            f"the sandbox directory {resolved} lies inside a harness configuration directory "
            f"({', '.join(inside)})"
        )
    for folder in resolved.parents:
        if folder == user_home:
            continue
        for name in PROJECT_HARNESS_MARKERS:
            if (folder / name).exists():
                return (
                    f"the sandbox directory {resolved} lies under {folder}, which holds per-project "
                    f"harness configuration ({name}); agents working in the sandbox would inherit it"
                )
    return None


def read_marker(layout: SandboxLayout) -> dict[str, Any] | None:
    """The sandbox's own marker; ``None`` when the directory is not a sandbox this tool built."""
    try:
        marker = json.loads(layout.marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(marker, dict) or marker.get("schema") != SANDBOX_SCHEMA:
        return None
    return marker


def provider_entries(layout: SandboxLayout, eve_env_file: Path | None) -> dict[str, Any]:
    """The provider entries Paseo needs: Hermes and Eve are driven through ACP.

    Eve cannot start a session when a client hands it tool servers, so its entry tells Paseo not
    to. Its launcher and application directory are the sandbox's own; ``eve_env_file`` is the one
    path outside the sandbox, read at launch for the developer's model keys and never copied.
    ``None`` means the developer has no Eve project and the entry is left out.
    """
    providers: dict[str, Any] = {
        "hermes": {"extends": "acp", "label": "Hermes", "command": ["hermes", "acp"]}
    }
    if eve_env_file is not None:
        providers["eve"] = {
            "extends": "acp",
            "label": "Eve",
            "command": [
                layout.eve_launcher.as_posix(),
                "--app",
                layout.eve_app.as_posix(),
                "--env-file",
                eve_env_file.as_posix(),
            ],
            "options": {"supportsMcpServers": False},
        }
    return providers


def embed_entries(layout: SandboxLayout) -> list[dict[str, str]]:
    """Both dashboard origins, each framing the sandbox's own Paseo runtime."""
    return [
        {
            "dashboardOrigin": f"http://{host}:{layout.dashboard_port}",
            "frameBaseUrl": layout.paseo_url,
        }
        for host in (LOOPBACK, "localhost")
    ]


def settings_document(layout: SandboxLayout, eve_env_file: Path | None) -> dict[str, Any]:
    """The settings of the sandbox's dashboard, tool server and Paseo runtime."""
    return {
        "version": 1,
        "coordinationRoot": layout.coordination.as_posix(),
        "workspaceRoot": layout.projects.as_posix(),
        "transcriptRoot": (layout.coordination / "logs" / "mcp").as_posix(),
        "harnessSkillRoot": layout.harness_skills.as_posix(),
        "directExecutionEnabled": False,
        "repositories": {REPOSITORY_ID: {}},
        "providers": {},
        # Named explicitly: left out, the port would default to 8765, the developer's own
        # dashboard service.
        "dashboard": {"autoStart": False, "port": layout.dashboard_port},
        "paseoRuntime": {
            "installPrefix": layout.paseo_prefix.as_posix(),
            "home": layout.paseo_home.as_posix(),
            "listen": layout.paseo_listen,
            "version": PASEO_VERSION,
            "providers": provider_entries(layout, eve_env_file),
            "embed": embed_entries(layout),
        },
    }

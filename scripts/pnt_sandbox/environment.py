"""The environment every process of the sandbox receives (PNT-R11 item 7).

The Paseo daemon keeps the environment of the process that provisioned it and hands it to every
agent it starts, and the dashboard and the tool server read their own. A variable that points one
of them at another AR runtime, coordination root, repository or Paseo home is therefore removed
before anything is started, and so is every variable that ties a process to the harness session
the command was run from. Harness logins and credentials are not touched: session variables are
removed by exact name, never by the ``CLAUDE_``, ``CLAUDE_CODE_`` or ``CODEX_`` prefix.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .layout import SandboxLayout

# Prefix -> why every variable carrying it is removed.
REMOVED_PREFIXES: dict[str, str] = {
    "PASEO_": (
        "select another Paseo home, listen address or daemon (PASEO_HOME, PASEO_LISTEN, PASEO_HOST)"
    ),
    "AR_": (
        "belong to another AR runtime or launch: the seat identity its control plane injects "
        "(AR_SPAWN_*, AR_HOSTED_SESSION_ID), a launch's workspace and capsule (AR_WORKSPACE_ROOT, "
        "AR_BINDING_REF, AR_CAPSULE_*), the Orca runtime the ONT line selects (AR_ORCA_*), the Eve "
        "runtime and state roots (AR_EVE_*), the experiment switch (AR_EXPERIMENT), the settings "
        "file of a reloading dashboard (AR_DASHBOARD_DEV_*) and the Dagger authority root "
        "(AR_DAGGER_*)"
    ),
    "AGENTS_REMEMBER_": "select another AR source tree or settings file",
    "ORCA_": (
        "select the ONT line's Orca runtime, profile and pairing (ORCA_USER_DATA_PATH, "
        "ORCA_PAIRING_CODE, ORCA_REMOTE_PAIRING, ORCA_ENVIRONMENT)"
    ),
}

# Exact name -> why it is removed.
REMOVED_VARIABLES: dict[str, str] = {
    "PYTHONPATH": "puts another AR source tree in front of the checkout's own (how ONT ran branches)",
    "PYTHONHOME": "replaces the interpreter's own library with another installation",
    "VIRTUAL_ENV": "names another Python environment, which uv would use instead of the checkout's",
    "UV_PROJECT_ENVIRONMENT": "makes uv build the checkout's environment somewhere else",
    "GIT_DIR": "redirects every Git command at one fixed repository",
    "GIT_WORK_TREE": "redirects every Git command at one fixed work tree",
    "GIT_INDEX_FILE": "redirects every Git command at another index",
    "GIT_COMMON_DIR": "redirects every Git command at another repository's common directory",
    "GIT_OBJECT_DIRECTORY": "redirects every Git command at another object store",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "adds another repository's objects to every Git command",
    "GIT_NAMESPACE": "redirects every Git command at another ref namespace",
    "TMUX": "names the terminal multiplexer server of the session that ran the command",
    "TMUX_PANE": "names a pane of the session that ran the command",
    "OLDPWD": "names the directory the caller came from; a child is given a PWD of its own",
    # The calling harness session. An agent started in the sandbox must not join the session the
    # command was run from, nor inherit its effort setting.
    "AI_AGENT": "names the harness of the calling session as the agent every child runs under",
    "CLAUDECODE": "marks a process as running inside the calling Claude Code session",
    "CLAUDE_CODE_CHILD_SESSION": "makes an agent a child of the calling session",
    "CLAUDE_CODE_ENTRYPOINT": "names how the calling session was entered, not how an agent is",
    "CLAUDE_CODE_EXECPATH": "names the Claude Code program of the calling session",
    "CLAUDE_CODE_MESSAGING_SOCKET": "is the calling session's message channel, which an agent must not join",
    "CLAUDE_CODE_MESSAGING_TOKEN": "admits its holder to the calling session's message channel",
    "CLAUDE_CODE_REMOTE_SESSION_ID": "is the remote identity of the calling session",
    "CLAUDE_CODE_SESSION_ATTENDED": "says a person attends the calling session",
    "CLAUDE_CODE_SESSION_ID": "is the calling session's identity, which an agent must not take over",
    "CLAUDE_CODE_SSE_PORT": "is the port of the calling session's own editor connection",
    "CLAUDE_DOC_FOCUS_PATHS": "is the document focus of the calling session (the build removes it too)",
    "CLAUDE_EFFORT": "is the calling session's effort setting; an agent takes the one its launch sets",
    "CLAUDE_JOB_DIR": "is the job directory of the calling session",
    "CLAUDE_PID": "is the process id of the calling session",
    "CODEX_CI": "marks the calling Codex session (the build removes it too)",
    "CODEX_THREAD_ID": "is the thread of the calling Codex session, which an agent must not continue",
}

# What Paseo itself puts into the environment of a supervisor it starts.
PASEO_OWN_VARIABLES = frozenset({"PASEO_HOME"})


def is_removed(name: str) -> bool:
    return name in REMOVED_VARIABLES or name.startswith(tuple(REMOVED_PREFIXES))


def sandbox_variables(layout: SandboxLayout) -> dict[str, str]:
    """What the sandbox sets in every process it starts.

    ``TMUX_TMPDIR`` gives the dashboard's terminal sessions a multiplexer server of the sandbox's
    own instead of the developer's. ``PYTHONPYCACHEPREFIX`` and ``GIT_OPTIONAL_LOCKS=0`` keep the
    checkout unwritten: no bytecode beside its sources, and no index lock when the dashboard asks
    Git whether the build it serves is modified. ``AR_DAGGER_AUTHORITY_ROOT`` puts the registry of
    the build's quality tools inside the sandbox; unset, the build keeps it in the user's home,
    where the installed runtime keeps its own.
    """
    return {
        "TMUX_TMPDIR": layout.tmux_dir.as_posix(),
        "PYTHONPYCACHEPREFIX": layout.pycache.as_posix(),
        "GIT_OPTIONAL_LOCKS": "0",
        "AR_DAGGER_AUTHORITY_ROOT": layout.dagger_authority.as_posix(),
    }


def sandbox_environment(base: Mapping[str, str], layout: SandboxLayout) -> dict[str, str]:
    """``base`` without the selecting variables, with the sandbox's own set."""
    environment = {name: value for name, value in base.items() if not is_removed(name)}
    environment.update(sandbox_variables(layout))
    return environment


def child_environment(environment: Mapping[str, str], cwd: Path) -> dict[str, str]:
    """The environment of one child: its ``PWD`` is the directory it is started in."""
    return {**environment, "PWD": cwd.as_posix()}


def removed_names(base: Iterable[str]) -> list[str]:
    """The names ``sandbox_environment`` drops from ``base``; never their values."""
    return sorted(name for name in base if is_removed(name))


def foreign_variables(environment: Mapping[str, str], layout: SandboxLayout) -> list[str]:
    """The names that show a running process does not carry the sandbox environment.

    They are the variables the scrub would have removed (what Paseo sets itself excepted) and
    the sandbox's own variables that are missing or hold another value.
    """
    own = sandbox_variables(layout)
    carried = [
        name
        for name in environment
        if is_removed(name) and name not in own and name not in PASEO_OWN_VARIABLES
    ]
    differing = [name for name, value in own.items() if environment.get(name) != value]
    return sorted([*carried, *differing])


def launcher_scrub(layout: SandboxLayout) -> dict[str, Any]:
    """The names an env file may not set through the Eve launcher, as the launcher reads them."""
    return {
        "prefixes": sorted(REMOVED_PREFIXES),
        "names": sorted([*REMOVED_VARIABLES, *sandbox_variables(layout), "PWD"]),
    }

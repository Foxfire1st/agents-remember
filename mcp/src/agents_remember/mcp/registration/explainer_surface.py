"""The explainer's four tools on the normal AR task server (MIK-R186 @ v2 rule 3, MIK-R211 v5 rule 3).

There is one server, not two. The normal `agents-remember-task` build serves an explainer seat with AR's
general tools, as a task agent has them, and what adds the explainer's own four (`render`,
`define_category`, `remove_category`, `grade`) is the **launch-owned binding**: the seat launch writes it
into the server process's environment. Nothing else -- a tool argument, repository text, a workspace
marker -- can select it, and an invalid binding refuses service rather than serving an unbound seat.

The tool bodies are not copied here:
:func:`agents_remember.mcp.registration.explainer_tools.register_bound_explainer_tools` registers the
implementations on the instance the launch already built.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.canvas.explainer_binding import (
    EXPLAINER_BINDING_ENV,
    EXPLAINER_CAPABILITY,
    EXPLAINER_SURFACE,
    EXPLAINER_WORKSPACE_ENV,
)
from agents_remember.canvas.explainer_inputs import ExplainerInputs
from agents_remember.canvas.explainer_paths import explainer_task_root
from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.registration.explainer_tools import register_bound_explainer_tools

_REQUIRED = (
    "capability",
    "agent",
    "request",
    "repository",
    "task",
    "leaf",
    "workspace",
    "generation",
)


@dataclass(frozen=True)
class ExplainerBinding:
    """Who this server is, as the launch said it: capability, seat, request, leaf and generation."""

    agent: str
    request: str
    repository: str
    task: str
    leaf: str
    workspace: Path
    generation: int
    seat: str | None = None

    @property
    def label(self) -> str:
        return f"{self.repository}/{self.task}/{self.leaf}"


def _refused(detail: str) -> ExplainerRefused:
    return ExplainerRefused("explainer-binding-invalid", detail)


def read_explainer_binding(config: McpRuntimeConfig | None = None) -> ExplainerBinding | None:
    """The launch's explainer binding, or ``None`` when this process serves the ordinary roster.

    A binding that is present and wrong is not the same as no binding: it refuses by name, because an
    invalid explainer binding must never become ordinary unrestricted AR access.
    """

    raw = os.environ.get(EXPLAINER_BINDING_ENV)
    if raw is None:
        # No binding at all is the ordinary branch -- but only for a process that is *not* wearing this
        # capability's launch marker. A seat launched as an explainer always carries both; if the binding
        # is missing while the marker is present, the binding was lost, and absence must not be the thing
        # that chooses the roster. That is the fail-open path, and it refuses instead.
        marker = os.environ.get(EXPLAINER_WORKSPACE_ENV)
        if marker and str(marker).strip():
            raise _refused(
                f"{EXPLAINER_BINDING_ENV} is missing on a process launched as an explainer "
                f"({EXPLAINER_WORKSPACE_ENV} names {marker}); a lost binding is not the ordinary roster"
            )
        return None
    if not raw.strip():
        raise _refused(
            f"{EXPLAINER_BINDING_ENV} is empty; an unset or empty binding is not a launch"
        )
    return _checked(_parsed(raw), config)


def _parsed(raw: str) -> dict[str, Any]:
    """The binding's own JSON, or a refusal naming what arrived instead."""

    try:
        given = json.loads(raw)
    except ValueError as error:
        raise _refused(f"{EXPLAINER_BINDING_ENV} is not JSON: {error}") from error
    if not isinstance(given, dict):
        raise _refused(f"{EXPLAINER_BINDING_ENV} is not an object")
    return given


def _checked(given: dict[str, Any], config: McpRuntimeConfig | None) -> ExplainerBinding:
    """One binding, validated as a whole: capability, generation and the leaf it claims to be."""

    missing = [key for key in _REQUIRED if not given.get(key)]
    if missing:
        raise _refused(f"the launch binding is missing {', '.join(missing)}")
    if given["capability"] != EXPLAINER_CAPABILITY:
        raise _refused(
            f"capability {given['capability']!r} has no surface here; this build serves "
            f"{EXPLAINER_CAPABILITY!r} on this server"
        )
    workspace = _workspace_of(given)
    if config is not None:
        _task_root_of(config, given)
    return ExplainerBinding(
        agent=str(given["agent"]),
        request=str(given["request"]),
        repository=str(given["repository"]),
        task=str(given["task"]),
        leaf=str(given["leaf"]),
        workspace=workspace,
        generation=_generation(given["generation"]),
        seat=str(given["seat"]) if given.get("seat") else None,
    )


def _task_root_of(config: McpRuntimeConfig, given: dict[str, Any]) -> Path:
    """The leaf's task folder, or a refusal -- a binding naming a root this build cannot serve is invalid.

    ``explainer_task_root`` raises the authority error a wider build would raise; at the launch boundary
    that is the same fact as an invalid binding, so it is answered with the explainer's own name.
    """

    try:
        task_root = explainer_task_root(config, str(given["repository"]), str(given["task"]))
    except Exception as error:
        raise _refused(f"the binding names a root this build cannot serve: {error}") from error
    if not task_root.is_dir():
        raise _refused(f"the leaf's task folder {task_root} does not exist")
    return task_root


def _generation(value: Any) -> int:
    """The seat generation: a count from one upward, never a string that could sort as one."""

    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise _refused(f"generation {value!r} is not a count from one upward")
    return value


def _workspace_of(given: dict[str, Any]) -> Path:
    """The environment the binding names, checked against the leaf it claims.

    The launch must describe the leaf whose environment it created. A disagreement is a forged or stale
    binding, and either way it does not become authority over somebody else's leaf.
    """

    workspace = Path(str(given["workspace"]))
    if (workspace.name, workspace.parent.name) != (str(given["leaf"]), str(given["task"])):
        raise _refused(
            f"workspace {workspace} is not the environment of {given['task']}/{given['leaf']}"
        )
    if workspace.parent.parent.name != str(given["repository"]):
        raise _refused(f"workspace {workspace} is not under repository {given['repository']!r}")
    return workspace


def binds_explainer() -> bool:
    """Whether this process was launched as an explainer seat, without parsing it twice."""

    return bool(os.environ.get(EXPLAINER_BINDING_ENV, "").strip())


def register_explainer_surface(
    server: FastMCP, config: McpRuntimeConfig, binding: ExplainerBinding
) -> None:
    """Register the explainer's four tools on the normal server, for this leaf and no other."""

    coordination_root = Path(config.coordination_root)
    canonical_task = coordination_root / "tasks" / binding.repository / binding.task
    resolved_task_root = (
        canonical_task
        if canonical_task.is_dir()
        else explainer_task_root(config, binding.repository, binding.task)
    )
    register_bound_explainer_tools(
        server,
        workspace=binding.workspace,
        seat=binding.seat,
        inputs=ExplainerInputs(task_root=resolved_task_root),
    )


__all__ = [
    "EXPLAINER_BINDING_ENV",
    "EXPLAINER_CAPABILITY",
    "EXPLAINER_SURFACE",
    "ExplainerBinding",
    "binds_explainer",
    "read_explainer_binding",
    "register_explainer_surface",
]

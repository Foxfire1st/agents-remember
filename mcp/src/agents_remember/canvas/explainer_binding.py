"""The launch's explainer binding, as a word and a roster -- no imports, so both sides may name it.

The server selects its explainer surface from the binding (MIK-R186 @ v2 rule 3) and the seat launch
writes it; keeping the identifiers in this leaf module is what lets the launch and the registration share
one spelling without importing each other's packages.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agents_remember.canvas.explainer_paths import (
    EXPLAINER_DIRECTORY,
    explainer_task_root,
    guard_explainer_path,
    seat_counter_path,
)
from agents_remember.canvas.explainer_state import (
    AGENT_MARKER,
    ExplainerRefused,
    ExplainerStateError,
    generation,
    seat_for_workspace,
    seat_ids_for,
)
from agents_remember.canvas.paths import PathRefused, path_segment

if TYPE_CHECKING:  # annotation only: the validator names the runtime config, never reads it here
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

#: The launch writes this into the server process's environment; its value is the binding as JSON.
#: An environment entry is launch-owned in a way no file in the workspace can be: a seat cannot rewrite
#: its own parent's environment, and the renderer child inherits the same identity rather than inventing one.
EXPLAINER_BINDING_ENV = "AR_EXPLAINER_BINDING"

#: The launch's cleanup marker, and the independent witness that this process *is* an explainer seat:
#: a seat always carries it, so its presence with no binding means the binding was lost, not that the
#: seat is an ordinary one.
EXPLAINER_WORKSPACE_ENV = "AR_EXPLAINER_WORKSPACE"

#: Exactly the tools an explainer binding may discover or call (MIK-R186 @ v2 rule 3). Discovery and
#: dispatch both check this list, so a direct call by name is refused as well as omitted.
#: The capability word a binding must carry to be an explainer binding at all.
EXPLAINER_CAPABILITY = "explainer"

#: The explainer's own four tools (MIK-R211 v5 rule 3, D603, D607): what no harness tool can do. A
#: bound seat also has AR's general tools, as a task agent has them; nothing else is refused.
EXPLAINER_SURFACE: tuple[str, ...] = (
    "define_category",
    "grade",
    "remove_category",
    "render",
)

_REQUIRED_FIELDS = (
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
class ExplainerLaunchBinding:
    """Who an explainer process is: capability, seat, request, leaf, workspace and generation."""

    agent: str
    request: str
    repository: str
    task: str
    leaf: str
    workspace: Path
    generation: int
    seat: str

    @property
    def label(self) -> str:
        return f"{self.repository}/{self.task}/{self.leaf}"


class LaunchedSeat(Protocol):
    """What the dispatch guard needs of a launch binding: the seat, its workspace and its generation.

    The server's registration reader carries its own binding dataclass and this one is what the launch
    validator returns; both satisfy this shape, so the guard compares against one contract rather than
    importing either caller's type back into `canvas`.
    """

    agent: str
    workspace: Path
    generation: int
    leaf: str


def _refused(detail: str) -> ExplainerRefused:
    return ExplainerRefused("explainer-binding-invalid", detail)


def _check_generation(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise _refused(f"generation {value!r} is not a positive integer")
    return value


def _check_seat_identity(given: Mapping[str, Any]) -> tuple[str, str]:
    raw_agent = given.get("agent") if "agent" in given else given.get("agentId")
    agent = str(raw_agent or "").strip()
    if not agent:
        raise _refused("the launch binding is missing agent")
    if "seat" in given and str(given.get("seat") or "").strip() != agent:
        raise _refused(f"binding agent {agent!r} does not match seat {given.get('seat')!r}")
    if "seatId" in given and str(given.get("seatId") or "").strip() != agent:
        raise _refused(f"binding agent {agent!r} does not match seatId {given.get('seatId')!r}")
    if "agentId" in given and str(given.get("agentId") or "").strip() != agent:
        raise _refused(f"binding agent {agent!r} does not match agentId {given.get('agentId')!r}")
    return agent, agent


def _check_workspace(
    binding: ExplainerLaunchBinding,
    *,
    coordination_root: Path | None,
    config: McpRuntimeConfig | None,
) -> Path:
    workspace = binding.workspace
    repository, task, leaf = binding.repository, binding.task, binding.leaf
    if (workspace.name, workspace.parent.name) != (leaf, task):
        raise _refused(f"workspace {workspace} is not the environment of {task}/{leaf}")
    if workspace.parent.parent.name != repository:
        raise _refused(f"workspace {workspace} is not under repository {repository!r}")
    if workspace.parent.parent.parent.name != EXPLAINER_DIRECTORY:
        raise _refused(f"workspace {workspace} is not under {EXPLAINER_DIRECTORY!r}")
    coord_root = (
        Path(config.coordination_root)
        if config is not None and getattr(config, "coordination_root", None) is not None
        else (Path(coordination_root) if coordination_root is not None else workspace.parents[3])
    )
    try:
        expected = (
            coord_root
            / EXPLAINER_DIRECTORY
            / path_segment(repository, name="repository")
            / path_segment(task, name="task")
            / path_segment(leaf, name="leaf")
        )
        if workspace != expected:
            raise _refused(
                f"workspace {workspace} does not belong to coordination root {coord_root}"
            )
        if config is not None:
            task_root = explainer_task_root(config, repository, task)
            if not task_root.is_dir():
                raise _refused(f"the leaf's task folder {task_root} does not exist")
        guard_explainer_path(workspace, root=coord_root, label="explainer workspace")
    except (PathRefused, ValueError, KeyError) as error:
        if isinstance(error, ExplainerRefused):
            raise
        raise _refused(f"the binding names a root this build cannot serve: {error}") from error
    if not workspace.is_dir() or workspace.is_symlink():
        raise _refused(f"the explainer workspace {workspace} does not exist on disk")
    return workspace


def _check_disk_records(binding: ExplainerLaunchBinding) -> None:
    workspace = binding.workspace
    gen_num = binding.generation
    counter_file = seat_counter_path(workspace)
    gen_json_file = workspace / "generation.json"
    binding_file = workspace / "binding.json"
    has_counter = counter_file.exists() or counter_file.is_symlink()
    has_gen_json = gen_json_file.exists() or gen_json_file.is_symlink()
    if has_counter:
        try:
            guard_explainer_path(
                counter_file, root=workspace.parents[3], label="explainer generation"
            )
            disk_gen = generation(counter_file)
        except (PathRefused, ExplainerStateError) as error:
            raise _refused(f"the workspace generation counter is invalid: {error}") from error
        if disk_gen != gen_num:
            raise _refused(
                f"binding generation {gen_num} does not match current generation {disk_gen}"
            )
    if has_gen_json:
        _check_json_record(gen_json_file, binding, require_agent=False)
    if gen_num > 1 and not has_counter and not has_gen_json:
        raise _refused(
            f"binding claims generation {gen_num}, but no generation record exists on disk"
        )
    if binding_file.exists() or binding_file.is_symlink():
        _check_json_record(binding_file, binding, require_agent=has_counter)


def _check_json_fields(payload: dict[str, Any], name: str, binding: ExplainerLaunchBinding) -> None:
    checks = (
        ("repository", binding.repository),
        ("leaf", binding.leaf),
        ("master", binding.task),
        ("task", binding.task),
        ("agentId", binding.agent),
        ("seatId", binding.agent),
        ("agent", binding.agent),
        ("seat", binding.agent),
    )
    for key, expected in checks:
        if key in payload and str(payload.get(key)) != expected:
            raise _refused(f"{name} {key} {payload.get(key)!r} != {expected!r}")
    if "generation" in payload and payload.get("generation") != binding.generation:
        raise _refused(f"{name} generation {payload.get('generation')!r} != {binding.generation!r}")


def _check_json_record(path: Path, binding: ExplainerLaunchBinding, *, require_agent: bool) -> None:
    try:
        guard_explainer_path(path, root=path.parents[3], label=path.name)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (PathRefused, OSError, UnicodeDecodeError, ValueError) as error:
        raise _refused(f"{path.name} cannot be verified: {error}") from error
    if not isinstance(payload, dict):
        raise _refused(f"{path.name} is not a JSON object")
    _check_json_fields(payload, path.name, binding)
    nested = payload.get("binding")
    if isinstance(nested, dict):
        if "leaf_id" in nested and str(nested.get("leaf_id")) != binding.leaf:
            raise _refused(f"binding.json leaf_id {nested.get('leaf_id')!r} != {binding.leaf!r}")
        if "task_id" in nested and str(nested.get("task_id")) != binding.task:
            raise _refused(f"binding.json task_id {nested.get('task_id')!r} != {binding.task!r}")
    if require_agent and not any(k in payload for k in ("agentId", "seatId", "agent", "seat")):
        # Any id this generation named is its seat: the un-attempted one a binding written before
        # attempts existed carries, or the attempt that made the seat since (MIK-R211 v2 rule 6).
        named = seat_ids_for(path.parent, binding.generation)
        if binding.agent not in named:
            raise _refused(
                f"binding agent {binding.agent!r} does not match a seat this generation named {named[0]!r}"
            )


def validate_explainer_binding(
    given: Mapping[str, Any],
    *,
    coordination_root: Path | None = None,
    config: McpRuntimeConfig | None = None,
) -> ExplainerLaunchBinding:
    """Validate an explainer binding and its physical workspace association (MIK-R186 @ v2 rule 3)."""

    if not isinstance(given, Mapping):
        raise _refused("the launch binding is not an object")
    missing = [key for key in _REQUIRED_FIELDS if not given.get(key)]
    if missing:
        raise _refused(f"the launch binding is missing {', '.join(missing)}")
    if given["capability"] != EXPLAINER_CAPABILITY:
        raise _refused(
            f"capability {given['capability']!r} has no surface here; this build serves "
            f"{EXPLAINER_CAPABILITY!r} on this server"
        )
    gen_num = _check_generation(given["generation"])
    agent, seat = _check_seat_identity(given)
    candidate = ExplainerLaunchBinding(
        agent=agent,
        request=str(given["request"]),
        repository=str(given["repository"]).strip(),
        task=str(given["task"]).strip(),
        leaf=str(given["leaf"]).strip(),
        workspace=Path(str(given["workspace"])),
        generation=gen_num,
        seat=seat,
    )
    _check_workspace(candidate, coordination_root=coordination_root, config=config)
    _check_disk_records(candidate)
    return candidate


def require_current_seat(workspace: Path, seat: str | None) -> str:
    """The identity one process carries must be this environment's current seat (rule 5).

    The seat's id is derived from the workspace, generation and recorded attempt, so a process
    presenting anything else is not this leaf's explainer -- a stale generation, a marker pointing at
    another workspace, or no identity at all.
    """

    marker = os.environ.get(EXPLAINER_WORKSPACE_ENV)
    if marker and Path(marker) != workspace:
        raise ExplainerRefused(
            "foreign-workspace", f"this server serves {workspace}, not the marked {marker}"
        )
    if not seat:
        raise ExplainerRefused(
            "not-this-explainer",
            "the server was built without a caller identity; only the leaf's explainer may call "
            "this tool, and its id comes from its own process environment",
        )
    counter = seat_counter_path(workspace)
    try:
        current = generation(counter)
        expected = seat_for_workspace(workspace, current)
    except ExplainerStateError as error:
        raise ExplainerRefused("seat-unreadable", str(error)) from error
    if seat != expected:
        raise ExplainerRefused(
            "not-this-explainer",
            f"the caller {seat} is not this leaf's explainer ({expected}); a grade or a report is "
            "the seat's own act",
        )
    return seat


def require_launched_seat(binding: LaunchedSeat) -> str:
    """Every call of a bound server must come from the seat that server was launched for (rule 3).

    The launch owns the identity twice: the binding in the process environment names the seat, and
    the harness puts the same id in ``PASEO_AGENT_ID`` for every process of that seat. A tool
    argument, repository text or a workspace marker cannot select another seat or widen the binding,
    so the check compares what the calling process is with what the binding names, and then names the
    leaf's current seat: a resume that made a new seat replaces this one, and an old server refuses
    rather than serving into the new seat's environment.
    """

    caller = (os.environ.get(AGENT_MARKER) or "").strip()
    if caller and caller != binding.agent:
        raise ExplainerRefused(
            "not-this-explainer",
            f"this server was launched for {binding.agent} and the calling process is {caller}; "
            "no argument or marker widens a binding",
        )
    marker = (os.environ.get(EXPLAINER_WORKSPACE_ENV) or "").strip()
    if marker and Path(marker) != binding.workspace:
        raise ExplainerRefused(
            "foreign-workspace",
            f"this server serves {binding.workspace}, not the marked {marker}",
        )
    counter = seat_counter_path(binding.workspace)
    try:
        current = generation(counter)
        expected = seat_for_workspace(binding.workspace, current) if current else None
    except ExplainerStateError as error:
        raise ExplainerRefused("seat-unreadable", str(error)) from error
    if current and current != binding.generation:
        raise ExplainerRefused(
            "not-this-explainer",
            f"generation {current} is this leaf's seat and this server was launched for generation "
            f"{binding.generation} ({binding.agent}); the leaf's current seat is another process",
        )
    if current and expected != binding.agent:
        raise ExplainerRefused(
            "not-this-explainer",
            f"generation {current} derives {expected} and this server "
            f"was launched for {binding.agent}",
        )
    return binding.agent


def read_explainer_binding(
    config: McpRuntimeConfig | None = None, *, coordination_root: Path | None = None
) -> ExplainerLaunchBinding | None:
    """Read and validate the launch's explainer binding from the process environment."""

    raw = os.environ.get(EXPLAINER_BINDING_ENV)
    if raw is None:
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
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise _refused(f"{EXPLAINER_BINDING_ENV} is not JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise _refused(f"{EXPLAINER_BINDING_ENV} is not an object")
    return validate_explainer_binding(parsed, coordination_root=coordination_root, config=config)


__all__ = [
    "EXPLAINER_BINDING_ENV",
    "EXPLAINER_CAPABILITY",
    "EXPLAINER_SURFACE",
    "EXPLAINER_WORKSPACE_ENV",
    "ExplainerLaunchBinding",
    "LaunchedSeat",
    "read_explainer_binding",
    "require_current_seat",
    "require_launched_seat",
    "validate_explainer_binding",
]

#!/usr/bin/env python3
"""Resolve every root a PNT build would use for one settings file, with the build's own code.

Run with the build's own interpreter (``<checkout>/mcp/.venv/bin/python``) and the environment
the sandbox gives its processes. The settings file is loaded through the build's configuration
loader after declaring the process the way the dashboard and the tool server declare themselves.
Every path the loaded configuration holds is reported, whatever key it sits under, and derived
roots come from the build's own functions, so a root the build fills in by default is reported
exactly as the build would use it.

Prints one JSON document: ``{"packageRoot", "roots": {key: path or null}, "expected": [key],
"values": {...}, "errors": {key: message}}``. ``expected`` names every root this script must
have reported for the configuration it loaded. A root that could not be resolved is ``null`` and
carries its error; the caller treats that, and a missing expected key, as a failure (fail closed).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

MODES = ("dashboard", "mcp")
# Fields of the loaded configuration that must hold a path.
CONFIGURATION_ROOTS = (
    "configPath",
    "coordinationRoot",
    "workspaceRoot",
    "transcriptRoot",
    "harnessSkillRoot",
    "paseoRuntime.home",
    "paseoRuntime.installPrefix",
)
# Roots the build derives; each has one resolver below.
DERIVED_ROOTS = (
    "agenticSettings",
    "observerRoot",
    "dashboardDaemonDir",
    "receipts.taskless",
    "receipts.messageBindings",
    "reports.taskless",
    "daggerAuthorityRoot",
)
REPOSITORY_ROOTS = (
    "path",
    "memoryRoot",
    "taskRoot (receipts and reports of task-bound roles)",
    "leafEnclosures",
    "resolver.code_repository_root",
    "resolver.memory_root",
    "resolver.onboarding_root",
    "resolver.task_root",
    "resolver.temp_root",
)
PROVIDER_ROOTS = ("runtimeRoot", "logRoot")


class _Roots:
    """Collects resolved roots; one unresolved root never hides the others."""

    def __init__(self) -> None:
        self.roots: dict[str, str | None] = {}
        self.errors: dict[str, str] = {}

    def add(self, key: str, resolve: Callable[[], Path | None]) -> None:
        try:
            path = resolve()
            if path is None:
                raise LookupError("the build resolves no path for this root")
        except Exception as error:  # every failure is one unresolved root, by name
            self.roots[key] = None
            self.errors[key] = f"{type(error).__name__}: {error}"
            return
        self.roots[key] = Path(path).resolve(strict=False).as_posix()


def _settings_name(field: str) -> str:
    """A configuration field under the spelling of its settings key."""
    head, *rest = field.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _configuration_paths(value: Any, key: str) -> Iterator[tuple[str, Path]]:
    """Every absolute path the loaded configuration holds, with the key it sits under.

    A path that is relative names a file inside a repository, not a root.
    """
    if isinstance(value, Path):
        if value.is_absolute():
            yield key, value
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            name = _settings_name(field.name)
            yield from _configuration_paths(getattr(value, field.name), f"{key}.{name}".strip("."))
    elif isinstance(value, Mapping):
        for name, item in value.items():
            yield from _configuration_paths(item, f"{key}.{name}".strip("."))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            yield from _configuration_paths(item, f"{key}[{index}]")


def expected_roots(repositories: list[str], providers: list[str]) -> list[str]:
    """The keys a complete resolution reports for this configuration."""
    keys = [*CONFIGURATION_ROOTS, *DERIVED_ROOTS]
    for repo_id in repositories:
        keys.extend(f"repositories.{repo_id}.{name}" for name in REPOSITORY_ROOTS)
    for provider_id in providers:
        keys.extend(f"providers.{provider_id}.{name}" for name in PROVIDER_ROOTS)
    return keys


def resolve_roots(config_path: str) -> dict[str, Any]:
    """Every root of the dashboard and tool server configured by ``config_path``.

    The caller declares the process first (see ``main``); a pytest process has declared itself
    already. Imports are local so that a build whose modules moved is reported root by root.
    """
    import agents_remember  # noqa: PLC0415
    from agents_remember.kernel.primitives.runtime_config import load_config  # noqa: PLC0415

    found = _Roots()
    report: dict[str, Any] = {
        "packageRoot": Path(agents_remember.__file__).resolve().parent.as_posix(),
        "roots": found.roots,
        "expected": list(CONFIGURATION_ROOTS),
        "values": {},
        "errors": found.errors,
    }
    try:
        config = load_config(config_path)
    except Exception as error:
        found.roots["configPath"] = None
        found.errors["configPath"] = f"{type(error).__name__}: {error}"
        return report

    for key, path in _configuration_paths(config, ""):
        found.add(key, lambda path=path: path)
    _coordination_roots(found, config)
    _launch_roots(found, config)
    for repo_id in sorted(config.repositories):
        _repository_roots(found, config, repo_id)
    paseo = config.paseo_runtime
    report["expected"] = expected_roots(sorted(config.repositories), sorted(config.providers))
    report["values"] = {
        "dashboard.port": config.dashboard.port,
        "dashboard.autoStart": config.dashboard.auto_start,
        "paseoRuntime.listen": paseo.listen if paseo else None,
        "paseoRuntime.version": paseo.version if paseo else None,
        "paseoRuntime.embed": paseo.embed_payload() if paseo else None,
        "repositories": sorted(config.repositories),
    }
    return report


def _coordination_roots(found: _Roots, config: Any) -> None:
    def dagger_authority() -> Path:
        from agents_remember.worktrees.modules.quality.dagger_authority import (  # noqa: PLC0415
            default_registry_root,
        )

        # Read from this process's environment, which is the one the sandbox's processes get.
        return default_registry_root()

    def agentic_settings() -> Path:
        from agents_remember.kernel.agentic_settings import (  # noqa: PLC0415
            agentic_settings_path,
        )

        return agentic_settings_path(config.coordination_root)

    def observer() -> Path:
        from agents_remember.observer import observer_root  # noqa: PLC0415

        return observer_root(config)

    def dashboard_daemon() -> Path:
        from agents_remember.serving.daemon import daemon_dir  # noqa: PLC0415

        return daemon_dir(config)

    found.add("agenticSettings", agentic_settings)
    found.add("observerRoot", observer)
    found.add("dashboardDaemonDir", dashboard_daemon)
    found.add("daggerAuthorityRoot", dagger_authority)


def _launch_roots(found: _Roots, config: Any) -> None:
    """Where launch receipts and the reports of taskless roles are written."""

    def taskless_receipts() -> Path:
        from agents_remember.cli.orca_task_receipts import (  # noqa: PLC0415
            _taskless_session_directory,
        )

        return _taskless_session_directory(config, "architect")

    def message_bindings() -> Path:
        from agents_remember.cli.orca_task_receipts import (  # noqa: PLC0415
            _message_binding_projection_path,
        )

        return _message_binding_projection_path(config, uuid.UUID(int=0)).parent

    found.add("receipts.taskless", taskless_receipts)
    found.add("receipts.messageBindings", message_bindings)
    # The launcher writes a taskless role's report under the Projects folder; it computes the
    # path inline while preparing a launch, so the same expression is repeated here.
    found.add("reports.taskless", lambda: config.workspace_root / ".agents-remember" / "reports")


def _repository_roots(found: _Roots, config: Any, repo_id: str) -> None:
    """One repository's task and enclosure roots and what the build's resolver answers for it.

    Its code and memory roots are fields of the configuration and are reported with it. Receipts
    and reports of task-bound roles are written below the selected task document's own folder
    (``notes/reports``), so the task root covers them.
    """
    prefix = f"repositories.{repo_id}"

    def task_root() -> Path:
        from agents_remember.worktrees.task_resolver import task_root_for  # noqa: PLC0415

        return task_root_for(config.coordination_root, repo_id, "probe").parent

    def enclosures() -> Path:
        from agents_remember.worktrees.worktree_contract import (  # noqa: PLC0415
            worktree_group_for,
        )

        return worktree_group_for(config.coordination_root, repo_id, "probe").parent

    found.add(f"{prefix}.taskRoot (receipts and reports of task-bound roles)", task_root)
    found.add(f"{prefix}.leafEnclosures", enclosures)
    context: dict[str, Any] = {}

    def resolved(key: str) -> Callable[[], Path | None]:
        def read() -> Path | None:
            if not context:
                from agents_remember.application.coordination_tools import (  # noqa: PLC0415
                    resolve_context_tool,
                )
                from agents_remember.application.task_docs.task_ref import (  # noqa: PLC0415
                    TaskRef,
                )

                context.update(resolve_context_tool(config, TaskRef(repo_id=repo_id))["context"])
            value = context.get(key)
            return Path(value) if isinstance(value, str) and value else None

        return read

    for key in ("code_repository_root", "memory_root", "onboarding_root", "task_root", "temp_root"):
        found.add(f"{prefix}.resolver.{key}", resolved(key))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=MODES)
    args = parser.parse_args()
    from agents_remember.controlplane.durable_store import declare_process_role  # noqa: PLC0415

    # The dashboard and the tool server declare themselves before they load their settings;
    # an undeclared process loaded from a checkout gets a different configuration.
    declare_process_role(args.mode)
    json.dump(resolve_roots(args.config), sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Resolve every root a PNT build would use for one settings file, with the build's own code.

Run with the build's own interpreter (``<checkout>/mcp/.venv/bin/python``). The settings file is
loaded through the build's configuration loader after declaring the process the way the
dashboard and the tool server declare themselves, and derived roots come from the build's own
functions, so a root the build fills in by default is reported exactly as the build would use it.

Prints one JSON document: ``{"packageRoot", "roots": {key: path or null}, "values": {...},
"errors": {key: message}}``. A root that could not be resolved is ``null`` and carries its error;
the caller treats that as a failure (fail closed).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

MODES = ("dashboard", "mcp")


class _Roots:
    """Collects resolved roots; one unresolved root never hides the others."""

    def __init__(self) -> None:
        self.roots: dict[str, str | None] = {}
        self.errors: dict[str, str] = {}

    def add(self, key: str, resolve: Callable[[], Path | None]) -> None:
        try:
            path = resolve()
        except Exception as error:  # every failure is one unresolved root, by name
            self.roots[key] = None
            self.errors[key] = f"{type(error).__name__}: {error}"
            return
        if path is None:
            self.roots[key] = None
            self.errors[key] = "the build resolves no path for this root"
            return
        self.roots[key] = Path(path).resolve(strict=False).as_posix()


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
        "values": {},
        "errors": found.errors,
    }
    try:
        config = load_config(config_path)
    except Exception as error:
        found.roots["settings"] = None
        found.errors["settings"] = f"{type(error).__name__}: {error}"
        return report

    found.add("settings", lambda: config.config_path)
    found.add("coordinationRoot", lambda: config.coordination_root)
    found.add("workspaceRoot (Projects folder)", lambda: config.workspace_root)
    found.add("transcriptRoot", lambda: config.transcript_root)
    found.add("harnessSkillRoot", lambda: config.harness_skill_root)
    _coordination_roots(found, config)
    _launch_roots(found, config)
    for repo_id, repository in sorted(config.repositories.items()):
        _repository_roots(found, config, repo_id, repository)
    for provider_id, provider in sorted(config.providers.items()):
        found.add(f"providers.{provider_id}.runtimeRoot", lambda p=provider: p.runtime_root)
        found.add(f"providers.{provider_id}.logRoot", lambda p=provider: p.log_root)
    paseo = config.paseo_runtime
    found.add("paseoRuntime.home", lambda: paseo.home if paseo else None)
    found.add("paseoRuntime.installPrefix", lambda: paseo.install_prefix if paseo else None)
    report["values"] = {
        "dashboard.port": config.dashboard.port,
        "dashboard.autoStart": config.dashboard.auto_start,
        "paseoRuntime.listen": paseo.listen if paseo else None,
        "paseoRuntime.version": paseo.version if paseo else None,
        "repositories": sorted(config.repositories),
    }
    return report


def _coordination_roots(found: _Roots, config: Any) -> None:
    from agents_remember.kernel.agentic_settings import agentic_settings_path  # noqa: PLC0415
    from agents_remember.observer import observer_root  # noqa: PLC0415
    from agents_remember.serving.daemon import daemon_dir  # noqa: PLC0415

    found.add("agenticSettings", lambda: agentic_settings_path(config.coordination_root))
    found.add("observerRoot", lambda: observer_root(config))
    found.add("dashboardDaemonDir", lambda: daemon_dir(config))


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


def _repository_roots(found: _Roots, config: Any, repo_id: str, repository: Any) -> None:
    """One repository's code, memory, task and enclosure roots.

    Receipts and reports of task-bound roles are written below the selected task document's own
    folder (``notes/reports``), so the task root covers them.
    """
    prefix = f"repositories.{repo_id}"
    found.add(f"{prefix}.path", lambda: repository.path)
    found.add(f"{prefix}.memoryRoot", lambda: repository.memory_root)

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

"""The closed register and test isolation owners of product process-wide state."""

from __future__ import annotations

import inspect
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Literal

from agents_remember.kernel.primitives import checkout_coordination

from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until


@dataclass(frozen=True)
class OwnedMutableState:
    name: str
    snapshot: Any
    restore: Any


@dataclass(frozen=True)
class ProcessMutableState:
    module: str
    attribute: str
    treatment: Literal["restored", "reset", "constant for tests"]
    reason: str
    reset: Callable[[ModuleType, str], None] | None = None

    @property
    def name(self) -> str:
        return f"{self.module}.{self.attribute}"


@dataclass
class _PytestProcessState:
    snapshot: dict[str, Any] | None = None


def _clear(module: ModuleType, attribute: str) -> None:
    getattr(module, attribute).clear()


def _clear_cache(module: ModuleType, attribute: str) -> None:
    getattr(module, attribute).cache_clear()


def _clear_binding(module: ModuleType, attribute: str) -> None:
    del attribute
    module.reset_worktree_services()


def _reset_ambient(module: ModuleType, attribute: str) -> None:
    del attribute
    current = module.ambient()
    ticker = current._ticker if current is not None else None
    module.reset_ambient()
    if ticker is not None:
        ticker.join(timeout=HANG_GUARD_SECONDS)
        if ticker.is_alive():
            raise RuntimeError("ambient lifecycle heartbeat did not stop during module reset")


def _stop_spawned(module: ModuleType, attribute: str) -> None:
    processes = getattr(module, attribute)
    for process in tuple(processes):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=HANG_GUARD_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=HANG_GUARD_SECONDS)
    processes.clear()


def _finish_quality_runs(module: ModuleType, attribute: str) -> None:
    def finished() -> bool:
        with module._lock:
            return all(run.status != "running" for run in getattr(module, attribute).values())

    wait_until(finished, "memory-quality module workers completed before reset")
    with module._lock:
        getattr(module, attribute).clear()


def _reset_compact_content(module: ModuleType, attribute: str) -> None:
    if getattr(module, attribute):
        wrapper = module._fm._convert_to_content
        module._fm._convert_to_content = inspect.getclosurevars(wrapper).nonlocals["_orig"]
        setattr(module, attribute, False)


def _reset_extension(module: ModuleType, attribute: str) -> None:
    if getattr(module, attribute):
        wrapper = module.ServerSession.__init__
        module.ServerSession.__init__ = inspect.getclosurevars(wrapper).nonlocals["original"]
        setattr(module, attribute, False)


def _row(
    module: str,
    attribute: str,
    reason: str,
    reset: Callable[[ModuleType, str], None] | None = _clear,
    *,
    treatment: Literal["restored", "reset", "constant for tests"] = "reset",
) -> ProcessMutableState:
    return ProcessMutableState(f"agents_remember.{module}", attribute, treatment, reason, reset)


# A reset never imports its product module. Tables whose tests deliberately register a
# temporary row are restored per test; import-only tables must survive deferred imports.
# Every discovered state has exactly one row; test_suite_load_independence checks both ways.
PROCESS_MUTABLE_STATES = (
    _row(
        "application.knowledge_gate.predicates",
        "GATE_PREDICATES",
        "Written only by the module's import-time predicate registrations.",
        None,
        treatment="constant for tests",
    ),
    _row(
        "application.knowledge_reader.timeline",
        "_converted_range",
        "Forget converted-history memoization between test modules.",
        _clear_cache,
    ),
    _row(
        "application.knowledge_worklist.registry",
        "ITEM_KINDS",
        "Tests register temporary item kinds and must restore the imported table.",
        None,
        treatment="restored",
    ),
    _row(
        "application.memory_quality.runs",
        "_registry",
        "Complete the module's owned workers before forgetting retained quality runs.",
        _finish_quality_runs,
    ),
    _row(
        "application.task_projection.statuses",
        "UNREACHABLE_STATUSES",
        "The empty status vocabulary is written only at import and tests only read it.",
        None,
        treatment="constant for tests",
    ),
    _row(
        "cli.paseo_catalog",
        "_CATALOGS",
        "Forget runtime discovery so another module cannot inherit a launcher catalog.",
    ),
    _row(
        "kernel.file_lock",
        "_thread_mutexes",
        "Forget per-path mutexes after the test module's contenders have ended.",
    ),
    _row(
        "kernel.file_lock",
        "_verified_lock_paths",
        "Every test module must establish its own filesystem exclusion capability.",
    ),
    _row(
        "kernel.harnesses",
        "RUNTIME_PROBES",
        "Written only when readiness owner modules are imported, including deferred imports.",
        None,
        treatment="constant for tests",
    ),
    _row(
        "kernel.primitives.checkout_coordination",
        "_declared",
        "The execution-mode declaration is restored and a leaking test is named.",
        None,
        treatment="restored",
    ),
    _row(
        "mcp.compact_content",
        "_installed",
        "Undo the installed converter together with its installation guard.",
        _reset_compact_content,
    ),
    _row(
        "mcp.registration.capsule_serving",
        "_DECLARED",
        "Forget declarations belonging to the module's own server objects.",
    ),
    _row(
        "mcp.registration.capsule_serving",
        "_REGISTERED",
        "Forget registrations belonging to the module's own server objects.",
    ),
    _row(
        "mcp.registration.skills_extension",
        "_DISPATCH_INSTALLED",
        "Forget dispatchers belonging to the module's own server objects.",
    ),
    _row(
        "mcp.registration.skills_extension",
        "_INSTALLED",
        "Undo the session initializer together with its installation guard.",
        _reset_extension,
    ),
    _row(
        "memory.conversion.base",
        "_cache",
        "Forget comparison bases so every module establishes its own conversion inputs.",
    ),
    _row(
        "memory_quality.knowledge_validator.registry",
        "_REGISTRY",
        "Tests register temporary validation rules and must restore the imported table.",
        None,
        treatment="restored",
    ),
    _row(
        "memory_quality.knowledge_validator.rules_census",
        "_FINDINGS",
        "Forget weak validation-context findings between test modules.",
    ),
    _row(
        "memory_quality.style.citations.grammars",
        "language",
        "Forget loaded grammar memoization between test modules.",
        _clear_cache,
    ),
    _row(
        "memory_quality.style.citations.grammars",
        "typescript_anchor_identifier",
        "Forget parsed TypeScript anchor memoization between test modules.",
        _clear_cache,
    ),
    _row(
        "observer.ambient",
        "_AmbientRegistry.instance",
        "Stop and join the heartbeat before clearing the ambient singleton.",
        _reset_ambient,
    ),
    _row(
        "serving.build_info",
        "process_serving_build",
        "Recompute serving identity so another module cannot inherit a patched build resolver.",
        _clear_cache,
    ),
    _row(
        "serving.conversation.active.service",
        "_SERVICES",
        "Forget services derived for the module's own conversation runtimes.",
    ),
    _row(
        "serving.conversation.control.service",
        "_SERVICES",
        "Forget controls derived for the module's own conversation runtimes.",
    ),
    _row(
        "serving.conversation.library.factories",
        "_shared_by_runtime",
        "Forget library authorities derived for the module's own runtimes.",
    ),
    _row(
        "serving.daemon",
        "_spawned",
        "Terminate and reap only children tracked as spawned by this process.",
        _stop_spawned,
    ),
    _row(
        "serving.files",
        "_repo_catalog_cache",
        "Forget repository catalog TTL results between test modules.",
    ),
    _row(
        "serving.pane_signals",
        "_HARNESS_MID_TURN_PATTERNS",
        "The empty harness vocabulary is assigned only at import and tests only read it.",
        None,
        treatment="constant for tests",
    ),
    _row(
        "serving.projections.projection_store",
        "_last_task_payload_warn",
        "A preceding module cannot suppress a task-payload warning.",
    ),
    _row(
        "serving.projections.projection_store",
        "_lifecycle_log_cache",
        "Forget lifecycle filesystem memoization between test modules.",
    ),
    _row(
        "serving.projections.projection_store",
        "_repo_surface_cache",
        "Forget repository-surface TTL results between test modules.",
    ),
    _row(
        "serving.projections.snapshots_impl._common",
        "_status_payload_cache",
        "Forget status TTL results between test modules.",
    ),
    _row(
        "serving.structural_dispatch",
        "_PROCESS_LOCKS",
        "Forget seat lock bookkeeping after the module's contenders have ended.",
    ),
    _row(
        "serving.terminal_tmux",
        "_tmux_version",
        "Each module resolves tmux against its own subprocess environment.",
        _clear_cache,
    ),
    _row(
        "serving.turn_state",
        "_HARNESS_AWAITING_INPUT_PATTERNS",
        "The empty harness vocabulary is assigned only at import and tests only read it.",
        None,
        treatment="constant for tests",
    ),
    _row(
        "serving.turn_state",
        "_HARNESS_TURN_ENDED_PATTERNS",
        "The empty harness vocabulary is assigned only at import and tests only read it.",
        None,
        treatment="constant for tests",
    ),
    _row(
        "serving.turn_state",
        "_HARNESS_WORKING_PATTERNS",
        "The empty harness vocabulary is assigned only at import and tests only read it.",
        None,
        treatment="constant for tests",
    ),
    _row(
        "worktrees.integration.atomic_series_terminal",
        "_THREAD_ACTIVE",
        "Forget terminal permits after the module's publications have completed.",
    ),
    _row(
        "worktrees.services",
        "_current",
        "Clear the module's application composition binding.",
        _clear_binding,
    ),
)


def reset_process_mutable_state() -> None:
    """Reset only already-imported owners at each module boundary."""
    for state in PROCESS_MUTABLE_STATES:
        module = sys.modules.get(state.module)
        if module is not None and state.reset is not None:
            state.reset(module, state.attribute)


def _mapping_snapshot(module: str, attribute: str) -> dict[str, Any]:
    return dict(getattr(sys.modules[module], attribute))


def _mapping_restore(module: str, attribute: str, snapshot: dict[str, Any]) -> None:
    mapping = getattr(sys.modules[module], attribute)
    mapping.clear()
    mapping.update(snapshot)


OWNED_MUTABLE_STATES = tuple(
    OwnedMutableState(
        name=state.name,
        snapshot=lambda state=state: _mapping_snapshot(state.module, state.attribute),
        restore=lambda previous, state=state: _mapping_restore(
            state.module, state.attribute, previous
        ),
    )
    for state in PROCESS_MUTABLE_STATES
    if state.treatment == "restored"
)
_PYTEST_PROCESS_STATE = _PytestProcessState()


def snapshot_owned_mutable_state() -> dict[str, Any]:
    return {
        state.name: state.snapshot()
        for state in OWNED_MUTABLE_STATES
        if state.name.rpartition(".")[0] in sys.modules
    }


def restore_owned_mutable_state(previous: dict[str, Any]) -> list[str]:
    """Restore every loaded owned global, returning the complete list that changed."""
    changed: list[str] = []
    for state in OWNED_MUTABLE_STATES:
        if state.name not in previous:
            continue
        after = state.snapshot()
        state.restore(previous[state.name])
        if after != previous[state.name]:
            changed.append(f"{state.name}: before={previous[state.name]!r}, after={after!r}")
    return changed


def begin_pytest_process() -> None:
    """Declare the process before production collection imports, once per session."""
    if _PYTEST_PROCESS_STATE.snapshot is None:
        _PYTEST_PROCESS_STATE.snapshot = snapshot_owned_mutable_state()
    checkout_coordination.declare_test_process()


def end_pytest_process() -> None:
    """Restore the execution-mode registry after every pytest exit path."""
    if _PYTEST_PROCESS_STATE.snapshot is not None:
        restore_owned_mutable_state(_PYTEST_PROCESS_STATE.snapshot)
        _PYTEST_PROCESS_STATE.snapshot = None


@contextmanager
def preserve_owned_mutable_state() -> Iterator[None]:
    """Contain a production entry point whose contract is to set process state."""
    previous = snapshot_owned_mutable_state()
    try:
        yield
    finally:
        restore_owned_mutable_state(previous)

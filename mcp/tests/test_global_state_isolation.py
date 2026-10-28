"""The suite identifies the test that leaks an explicitly-owned mutable global."""

from __future__ import annotations

import subprocess
import sys
import threading
import unittest
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

import conftest
import pytest
from agents_remember.controlplane.durable_store import (
    declare_process_role,
    declared_process_role,
)
from agents_remember.serving import background_accounting
from agents_remember_test_support.testing.global_state import (
    OWNED_MUTABLE_STATES,
    PROCESS_MUTABLE_STATES,
    ProcessMutableState,
    reset_process_mutable_state,
    restore_owned_mutable_state,
    snapshot_owned_mutable_state,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


def _owned_cleared(module: ModuleType, state: ProcessMutableState) -> Any:
    return {"previous module": object()}


def _owned_cached(module: ModuleType, state: ProcessMutableState) -> Any:
    return mock.Mock()


def _owned_quality(module: ModuleType, state: ProcessMutableState) -> Any:
    module.__dict__["_lock"] = threading.Lock()
    return {"completed run": SimpleNamespace(status="completed")}


def _owned_spawned(module: ModuleType, state: ProcessMutableState) -> Any:
    process = mock.Mock()
    process.poll.return_value = None
    return [process]


def _owned_delegated_sweep(module: ModuleType, state: ProcessMutableState) -> Any:
    # The wrapper delegates to the owner's own resetter: the fake owner carries a
    # mock of it, and the test asserts the delegation below.
    module.__dict__["_reset_sweep_cursor"] = mock.Mock()
    return {"previous module": object()}


def _owned_delegated_writer(module: ModuleType, state: ProcessMutableState) -> Any:
    module.__dict__["reset_tool_writer"] = mock.Mock()
    return {"previous module": object()}


def _owned_background_pool(module: ModuleType, state: ProcessMutableState) -> Any:
    executor = mock.Mock()
    module.__dict__["_BACKGROUND_POOL_LOCK"] = threading.Lock()
    module.__dict__["_BackgroundPool"] = SimpleNamespace(executor=executor)
    return executor


def _owned_binding(module: ModuleType, state: ProcessMutableState) -> Any:
    module.__dict__["reset_worktree_services"] = lambda: setattr(module, state.attribute, None)
    return object()


def _owned_plain(module: ModuleType, state: ProcessMutableState) -> Any:
    value = True

    def original() -> None:
        pass

    if state.reset is not None and state.reset.__name__ == "_reset_compact_content":
        _orig = original

        def wrapper() -> None:
            _orig()

        module.__dict__["_fm"] = SimpleNamespace(_convert_to_content=wrapper)
    else:

        def wrapper() -> None:
            original()

        module.__dict__["ServerSession"] = SimpleNamespace(__init__=wrapper)
    module.__dict__["original_callable"] = original
    return value


def _owned_watch(module: ModuleType, state: ProcessMutableState) -> Any:
    del state
    watch = mock.Mock()
    module.__dict__["held_watch"] = watch  # the reset empties the dict in place
    return {"process": watch}


_OWNED_STATE_BUILDERS = {
    "_reset_process_worktree_watch": _owned_watch,
    "_clear": _owned_cleared,
    "_clear_cache": _owned_cached,
    "_finish_quality_runs": _owned_quality,
    "_stop_spawned": _owned_spawned,
    "_reset_sweep_cursor": _owned_delegated_sweep,
    "_reset_tool_writer": _owned_delegated_writer,
    "_clear_binding": _owned_binding,
    "_reset_background_pool": _owned_background_pool,
}


def _module_with_owned_state(state: ProcessMutableState) -> tuple[ModuleType, Any]:
    module = ModuleType(state.module)
    assert state.reset is not None
    action = state.reset.__name__
    value = _OWNED_STATE_BUILDERS.get(action, _owned_plain)(module, state)
    setattr(module, state.attribute, value)
    return module, tuple(value) if action == "_stop_spawned" else value


def _assert_cleanup(
    test: unittest.TestCase,
    state: ProcessMutableState,
    module: ModuleType,
    previous: Any,
    value: Any,
) -> None:
    """One reset row's declared cleanup, observed on its loaded owner."""
    assert state.reset is not None
    action = state.reset.__name__
    if action == "_clear_cache":
        value.cache_clear.assert_called_once_with()
    elif action == "_stop_spawned":
        previous[0].terminate.assert_called_once_with()
        previous[0].wait.assert_called_once()
        test.assertEqual(value, [])
    elif action == "_reset_compact_content":
        test.assertIs(value, False)
        test.assertIs(
            sys.modules[state.module]._fm._convert_to_content,
            module.original_callable,
        )
    elif action == "_reset_extension":
        test.assertIs(value, False)
        test.assertIs(
            sys.modules[state.module].ServerSession.__init__,
            module.original_callable,
        )
    elif action == "_reset_background_pool":
        previous.shutdown.assert_called_once_with(wait=True)
        test.assertIsNone(sys.modules[state.module]._BackgroundPool.executor)
    elif action == "_clear_binding":
        test.assertIsNone(value)
    elif action == "_reset_build_watchdog":
        # The watchdog row's contract is the reference, not a container: a loaded
        # owner may hold a thread or a placeholder, and either way it is cleared.
        test.assertIsNone(value)
    elif action in {"_reset_sweep_cursor", "_reset_tool_writer"}:
        resetter = {
            "_reset_sweep_cursor": "_reset_sweep_cursor",
            "_reset_tool_writer": "reset_tool_writer",
        }[action]
        getattr(module, resetter).assert_called_once_with()
    else:
        test.assertEqual(value, {})


class GlobalStateLeakDetectionTests(unittest.TestCase):
    def test_a_deliberate_process_role_leak_is_reported_after_being_restored(self) -> None:
        previous = snapshot_owned_mutable_state()
        declare_process_role("dashboard")

        changed = restore_owned_mutable_state(previous)

        self.assertEqual(
            changed,
            [
                "agents_remember.kernel.primitives.checkout_coordination._declared: "
                "before={'mode': 'test'}, after={'mode': 'dashboard'}"
            ],
        )
        self.assertIsNone(
            declared_process_role(),
            "restoration must happen before the leak becomes a test failure, so later tests "
            "cannot inherit the role",
        )

    def test_only_restored_rows_are_in_the_per_test_snapshot(self) -> None:
        self.assertEqual(
            {state.name for state in OWNED_MUTABLE_STATES},
            {
                "agents_remember.kernel.primitives.checkout_coordination._declared",
                "agents_remember.application.knowledge_worklist.registry.ITEM_KINDS",
                "agents_remember.memory_quality.knowledge_validator.registry._REGISTRY",
            },
        )

    def test_resets_touch_only_loaded_modules_and_forget_a_later_loaded_cache(self) -> None:
        owner = "agents_remember.application.tool_answer_log"
        fake = ModuleType(owner)
        fake.__dict__["_FOLDER_BUDGETS"] = {"previous module": object()}
        with mock.patch.dict(sys.modules):
            sys.modules.pop(owner, None)
            before = set(sys.modules)
            reset_process_mutable_state()
            self.assertEqual(set(sys.modules), before)
            sys.modules[owner] = fake
            reset_process_mutable_state()
            self.assertEqual(fake._FOLDER_BUDGETS, {})

    def test_the_process_watch_reset_shuts_its_watch_down_and_forgets_it(self) -> None:
        (state,) = [one for one in PROCESS_MUTABLE_STATES if one.name.endswith("._PROCESS_WATCH")]
        module, _ = _module_with_owned_state(state)
        with mock.patch.dict(sys.modules, {state.module: module}):
            reset_process_mutable_state()
        self.assertEqual(getattr(module, state.attribute), {})
        module.held_watch.shutdown.assert_called_once_with()

    def test_every_reset_row_runs_its_declared_cleanup_for_a_loaded_owner(self) -> None:
        # Ambient's actual heartbeat is exercised separately; all other registered reset
        # owners get deterministic local state so every row participates in this proof.
        states = [state for state in PROCESS_MUTABLE_STATES if state.reset is not None]
        modules = {state.name: _module_with_owned_state(state) for state in states}
        with mock.patch.dict(
            sys.modules, {module.__name__: module for module, _ in modules.values()}
        ):
            # Multiple states share a module: give its one loaded owner every attribute.
            for state in states:
                module, _ = modules[state.name]
                loaded = sys.modules[state.module]
                loaded.__dict__.update(module.__dict__)
            reset_process_mutable_state()
            for state in states:
                with self.subTest(state=state.name):
                    module, previous = modules[state.name]
                    value = getattr(sys.modules[state.module], state.attribute)
                    assert state.reset is not None
                    _assert_cleanup(self, state, module, previous, value)

    def test_background_pool_reset_closes_the_old_executor_and_allows_a_fresh_one(self) -> None:
        state = next(
            row
            for row in PROCESS_MUTABLE_STATES
            if row.name == "agents_remember.serving.background_accounting._BackgroundPool.executor"
        )
        assert state.reset is not None
        previous = background_accounting._background_pool()
        self.assertEqual(previous.submit(lambda: "before").result(HANG_GUARD_SECONDS), "before")
        state.reset(background_accounting, state.attribute)
        self.assertIsNone(background_accounting._BackgroundPool.executor)
        with self.assertRaises(RuntimeError):
            previous.submit(lambda: "closed")
        replacement = background_accounting._background_pool()
        self.assertIsNot(replacement, previous)
        self.assertEqual(replacement.submit(lambda: "after").result(HANG_GUARD_SECONDS), "after")

    def test_each_restored_row_names_the_leak_and_restores_its_loaded_table(self) -> None:
        states = [state for state in PROCESS_MUTABLE_STATES if state.treatment == "restored"]
        owners = {state.module: ModuleType(state.module) for state in states}
        for state in states:
            setattr(owners[state.module], state.attribute, {"imported row": "kept"})
        with mock.patch.dict(sys.modules, owners):
            previous = snapshot_owned_mutable_state()
            for state in states:
                getattr(owners[state.module], state.attribute)["leaked row"] = "removed"
            changed = restore_owned_mutable_state(previous)
            self.assertEqual(
                [entry.split(": before=", 1)[0] for entry in changed],
                [state.name for state in states],
            )
            for state in states:
                self.assertEqual(
                    getattr(owners[state.module], state.attribute), {"imported row": "kept"}
                )


def test_tmux_cleanup_failures_do_not_skip_existing_pytest_teardown(
    pytestconfig: pytest.Config,
) -> None:
    for failure in (
        subprocess.TimeoutExpired(["tmux", "kill-server"], HANG_GUARD_SECONDS),
        OSError("tmux could not start"),
    ):
        cleanup: list[str] = []
        with (
            mock.patch.object(conftest.shutil, "which", return_value="/usr/bin/tmux"),
            mock.patch.object(conftest.subprocess, "run", side_effect=failure) as kill,
            mock.patch.object(
                conftest._ISOLATED_ENVIRONMENT,
                "stop",
                side_effect=lambda cleanup=cleanup: cleanup.append("environment"),
            ),
            mock.patch.object(
                conftest._ENVIRONMENT_LEASE,
                "close",
                side_effect=lambda cleanup=cleanup: cleanup.append("lease"),
            ),
            mock.patch.object(
                conftest._TEMPORARY,
                "cleanup",
                side_effect=lambda cleanup=cleanup: cleanup.append("temporary"),
            ),
        ):
            assert conftest.os.environ["TMUX_TMPDIR"] == str(conftest._ISOLATED_ROOT / "tmux")
            with pytest.raises(type(failure)) as raised:
                conftest.pytest_unconfigure(pytestconfig)
            assert raised.value is failure
            kill.assert_called_once_with(
                ["tmux", "kill-server"],
                capture_output=True,
                check=False,
                timeout=HANG_GUARD_SECONDS,
            )
            assert cleanup == ["environment", "lease", "temporary"]


if __name__ == "__main__":
    unittest.main()

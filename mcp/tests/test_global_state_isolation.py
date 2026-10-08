"""The suite identifies the test that leaks an explicitly-owned mutable global."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

import conftest
import pytest
from agents_remember.controlplane.durable_store import (
    declare_process_role,
    declared_process_role,
)
from agents_remember.observer.ambient import AmbientLifecycle, ambient, install_ambient
from agents_remember.observer.store import EventStore
from agents_remember_test_support.testing.global_state import (
    OWNED_MUTABLE_STATES,
    PROCESS_MUTABLE_STATES,
    ProcessMutableState,
    reset_process_mutable_state,
    restore_owned_mutable_state,
    snapshot_owned_mutable_state,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


def _module_with_owned_state(state: ProcessMutableState) -> tuple[ModuleType, Any]:
    module = ModuleType(state.module)
    assert state.reset is not None
    action = state.reset.__name__
    if action == "_clear":
        value = {"previous module": object()}
    elif action == "_clear_cache":
        value = mock.Mock()
    elif action == "_finish_quality_runs":
        module.__dict__["_lock"] = threading.Lock()
        value = {"completed run": SimpleNamespace(status="completed")}
    elif action == "_stop_spawned":
        process = mock.Mock()
        process.poll.return_value = None
        value = [process]
    elif action == "_clear_binding":
        value = object()
        module.__dict__["reset_worktree_services"] = lambda: setattr(module, state.attribute, None)
    else:
        value = True

        def original() -> None:
            pass

        if action == "_reset_compact_content":
            _orig = original

            def wrapper() -> None:
                _orig()

            module.__dict__["_fm"] = SimpleNamespace(_convert_to_content=wrapper)
        else:

            def wrapper() -> None:
                original()

            module.__dict__["ServerSession"] = SimpleNamespace(__init__=wrapper)
        module.__dict__["original_callable"] = original
    setattr(module, state.attribute, value)
    return module, tuple(value) if action == "_stop_spawned" else value


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
        owner = "agents_remember.cli.paseo_catalog"
        fake = ModuleType(owner)
        fake.__dict__["_CATALOGS"] = {"previous module": object()}
        with mock.patch.dict(sys.modules):
            sys.modules.pop(owner, None)
            before = set(sys.modules)
            reset_process_mutable_state()
            self.assertEqual(set(sys.modules), before)
            sys.modules[owner] = fake
            reset_process_mutable_state()
            self.assertEqual(fake._CATALOGS, {})

    def test_reset_stops_the_ambient_heartbeat_and_clears_the_singleton(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lifecycle = AmbientLifecycle(EventStore(Path(temporary)))
            install_ambient(lifecycle)
            lifecycle.start(ticker_wait=lambda stop, interval: stop.wait(interval))
            ticker = lifecycle._ticker
            assert ticker is not None
            self.assertTrue(ticker.is_alive())
            reset_process_mutable_state()
            self.assertIsNone(ambient())
            self.assertFalse(ticker.is_alive())

    def test_every_reset_row_runs_its_declared_cleanup_for_a_loaded_owner(self) -> None:
        # Ambient's actual heartbeat is exercised separately; all other registered reset
        # owners get deterministic local state so every row participates in this proof.
        states = [
            state
            for state in PROCESS_MUTABLE_STATES
            if state.reset is not None and state.reset.__name__ != "_reset_ambient"
        ]
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
                    action = state.reset.__name__
                    if action == "_clear_cache":
                        value.cache_clear.assert_called_once_with()
                    elif action == "_stop_spawned":
                        previous[0].terminate.assert_called_once_with()
                        previous[0].wait.assert_called_once()
                        self.assertEqual(value, [])
                    elif action == "_reset_compact_content":
                        self.assertIs(value, False)
                        self.assertIs(
                            sys.modules[state.module]._fm._convert_to_content,
                            module.original_callable,
                        )
                    elif action == "_reset_extension":
                        self.assertIs(value, False)
                        self.assertIs(
                            sys.modules[state.module].ServerSession.__init__,
                            module.original_callable,
                        )
                    elif action == "_clear_binding":
                        self.assertIsNone(value)
                    else:
                        self.assertEqual(value, {})

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

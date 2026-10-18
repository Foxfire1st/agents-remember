"""Regression coverage for leaf-local checkout coordination isolation."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.cli.dashboard import _resolve_settings
from agents_remember.controlplane.durable_store import (
    GATE_OWNERSHIP,
    append_line,
    declared_process_role,
    exclusive_access,
    rewrite_lines,
)
from agents_remember.kernel.primitives import checkout_coordination
from agents_remember.kernel.primitives.checkout_coordination import (
    CheckoutCoordinationError,
    declare_test_process,
    resolve_checkout_location,
)
from agents_remember.kernel.primitives.runtime_config import ConfigError, load_config
from agents_remember.mcp.server import create_server
from agents_remember.serving.app import LiveProjectionInputs, create_app
from agents_remember_test_support.testing.global_state import preserve_owned_mutable_state
from fastapi.testclient import TestClient
from tool_entry_point_world import EntryPointWorld


class CheckoutCoordinationIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def _checkout(self, *, linked: bool = True) -> tuple[Path, Path]:
        checkout = self.root / "leaf-enclosure" / "candidate-checkout"
        source = (
            checkout
            / "mcp"
            / "src"
            / "agents_remember"
            / "kernel"
            / "primitives"
            / "checkout_coordination.py"
        )
        source.parent.mkdir(parents=True)
        source.touch()
        (checkout / "mcp" / "pyproject.toml").touch()
        marker = checkout / ".git"
        if linked:
            marker.write_text("gitdir: /tmp/example\n", encoding="utf-8")
        else:
            marker.mkdir()
        return checkout, source

    def _settings(self, path: Path) -> None:
        payload = {
            "coordinationRoot": str(self.root / "live-ar-coordination"),
            "workspaceRoot": str(self.root / "workspace"),
            "repositories": {"agents-remember": {}},
            "providers": {},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _undeclared_checkout(self, source: Path):
        state = preserve_owned_mutable_state()
        state.__enter__()
        self.addCleanup(state.__exit__, None, None, None)
        checkout_coordination._declared.clear()
        source_patch = patch.object(checkout_coordination, "_PACKAGE_SOURCE", source)
        source_patch.start()
        self.addCleanup(source_patch.stop)

    def test_linked_checkout_is_derived_from_loaded_source_not_cwd(self) -> None:
        checkout, source = self._checkout()

        location = resolve_checkout_location(source)

        self.assertIsNotNone(location)
        assert location is not None
        self.assertEqual(location.kind, "linked")
        self.assertEqual(location.checkout_root, checkout)
        self.assertEqual(
            location.coordination_root,
            checkout.parent / "provider-runtime" / "dev-ar-coordination",
        )

    def test_checkout_config_ignores_live_authority_and_uses_only_dummy_root(self) -> None:
        checkout, source = self._checkout()
        self._undeclared_checkout(source)
        live_settings = self.root / "live-settings.json"
        live_settings.write_text("not valid JSON", encoding="utf-8")

        config = load_config(live_settings)

        expected_root = checkout.parent / "provider-runtime" / "dev-ar-coordination"
        self.assertEqual(config.coordination_root, expected_root)
        self.assertEqual(config.repositories["agents-remember"].path, checkout)
        self.assertEqual(
            config.repositories["agents-remember"].memory_root,
            expected_root / "memory-repos" / "ar-agents-remember",
        )
        self.assertEqual(config.providers, {})
        self.assertFalse(config.dashboard.auto_start)
        self.assertFalse(config.benchmarks_enabled)
        self.assertFalse(live_settings.parent.joinpath("live-ar-coordination").exists())

    def test_checkout_guard_is_named_by_cli_registered_server_and_dashboard(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        checkout, source = self._checkout()
        self._undeclared_checkout(source)
        settings = self.root / "live-settings.json"
        self._settings(settings)
        stderr = io.StringIO()

        with patch("sys.stderr", stderr):
            resolved = _resolve_settings(argparse.Namespace(config=str(settings)))

        self.assertIsNotNone(resolved)
        assert resolved is not None
        _path, config = resolved
        disposable = checkout.parent / "provider-runtime" / "dev-ar-coordination"
        configured = self.root / "live-ar-coordination"
        (line,) = stderr.getvalue().splitlines()
        for part in (
            str(checkout),
            str(disposable),
            str(configured),
            "is not used",
            "--config does not change the binding",
            "Run the installed command",
            "declare the test mode",
            "scratch work",
        ):
            self.assertIn(part, line)
        self.assertEqual(config.coordination_root, disposable)
        # Only the fixture's subsequent transport and HTTP work is a declared test process.
        # The CLI above ran undeclared and produced the binding being tested.
        declare_test_process()
        expected = {
            "state": "disposable-coordination-root",
            "worktree": str(checkout),
            "coordinationRoot": str(disposable),
            "configuredRoot": str(configured),
        }
        world.server = create_server(config)
        outcome, server = world.call("server_info", {})
        self.assertEqual(outcome, "RETURNED", server)
        assert server is not None
        with TestClient(
            create_app(
                config,
                live_inputs=LiveProjectionInputs(
                    provider_state=False, landing_state=False, change_watch=False
                ),
            )
        ) as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200, response.text)
        dashboard = response.json()
        for payload in (server, dashboard):
            binding = payload["checkoutBinding"]
            for field, value in expected.items():
                self.assertEqual(binding[field], value, payload)
            self.assertIn("run the installed command", binding["remedy"])
            self.assertIn("declare the test mode", binding["remedy"])
            self.assertIn("--config does not change the binding", binding["remedy"])
        self.assertFalse(configured.exists())

    @pytest.mark.integration
    @pytest.mark.usefixtures("worktree_services")
    def test_incident_shaped_store_write_lands_only_in_leaf_dummy_root(self) -> None:
        _checkout, source = self._checkout()
        self._undeclared_checkout(source)
        settings = self.root / "live-settings.json"
        self._settings(settings)
        config = load_config(settings)
        gates = config.coordination_root / "observer" / "workspace" / "gates.jsonl"

        with exclusive_access(gates, GATE_OWNERSHIP):
            append_line(gates, '{"candidateField":"unpublished"}')

        self.assertTrue(gates.is_file())
        self.assertEqual(
            gates.read_text(encoding="utf-8"),
            '{"candidateField":"unpublished"}\n',
        )
        self.assertFalse((self.root / "live-ar-coordination").exists())

    @pytest.mark.integration
    @pytest.mark.usefixtures("worktree_services")
    def test_store_guard_refuses_escape_before_creating_lock_or_parent(self) -> None:
        _checkout, source = self._checkout()
        self._undeclared_checkout(source)
        escaped = self.root / "live-ar-coordination" / "gates.jsonl"

        guard = exclusive_access(escaped, GATE_OWNERSHIP)
        with self.assertRaisesRegex(CheckoutCoordinationError, "leaf-local"):
            guard.__enter__()

        self.assertFalse(escaped.parent.exists())
        self.assertFalse(escaped.with_name(f"{escaped.name}.lock").exists())

    def test_enclosure_report_write_is_allowed_without_opening_coordination_escape(self) -> None:
        checkout, source = self._checkout()
        self._undeclared_checkout(source)
        report = checkout.parent / "reports" / "closeout-operation.json"

        with exclusive_access(report, GATE_OWNERSHIP):
            rewrite_lines(report, ['{"status":"running"}'], GATE_OWNERSHIP)

        self.assertEqual(report.read_text(encoding="utf-8"), '{"status":"running"}\n')
        self.assertFalse((checkout.parent / "gates.jsonl").exists())

    def test_rewrite_guard_refuses_a_manually_constructed_live_target(self) -> None:
        _checkout, source = self._checkout()
        self._undeclared_checkout(source)
        escaped = self.root / "live-ar-coordination" / "gates.jsonl"

        with self.assertRaisesRegex(CheckoutCoordinationError, "refused target"):
            rewrite_lines(escaped, ["unsafe"], GATE_OWNERSHIP)

        self.assertFalse(escaped.parent.exists())

    def test_primary_checkout_undeclared_config_access_fails_closed(self) -> None:
        _checkout, source = self._checkout(linked=False)
        self._undeclared_checkout(source)
        settings = self.root / "live-settings.json"
        self._settings(settings)

        with self.assertRaisesRegex(ConfigError, "primary checkout is refused"):
            load_config(settings)

    def test_explicit_test_mode_preserves_temporary_store_writes(self) -> None:
        _checkout, source = self._checkout()
        self._undeclared_checkout(source)
        target = self.root / "pytest-temp-root" / "records.jsonl"
        declare_test_process()

        append_line(target, "test-row")

        self.assertEqual(target.read_text(encoding="utf-8"), "test-row\n")

    def test_any_mode_declaration_lifts_the_primary_refusal_and_confinement(self) -> None:
        """PIN (260918-TSIP-L3, `T30`): the boundary asks *was any mode declared*, never *who*.

        This case asserts today's behaviour on purpose, and it is the only case here that does.
        `declare_execution_mode` is one assignment (`kernel/primitives/checkout_coordination.py:59-61`):
        no caller identity, no PID or parent check, no environment proof, no signature, and no
        operation record. The two predicates that decide everything test `is not None`
        (`:118` `checkout_cli_location`, `:142-143` `require_durable_write_target`), and
        `runtime_config._config_for_execution` (`:757-769`) then loads the LIVE authority settings
        instead of the synthetic non-authority config.

        Arm A is the plane's real refusal and is the positive control: without it, a case that
        "passes" after the declaration could be passing because the refusal never fired at all.
        Arm B is the finding. If arm B ever fails, someone has made the declaration
        authenticated, which is a product and security decision this leaf deliberately did not
        take -- so read `notes/defect-index.md` (`T28`/`T30`/`T41`) and the developer's ruling
        before repairing this case rather than deleting it.
        """
        _checkout, source = self._checkout(linked=False)
        self._undeclared_checkout(source)
        settings = self.root / "live-settings.json"
        self._settings(settings)
        outside = self.root / "outside-any-checkout" / "rows.jsonl"

        with self.assertRaisesRegex(ConfigError, "primary checkout is refused"):
            load_config(settings)
        with self.assertRaises(CheckoutCoordinationError):
            checkout_coordination.require_durable_write_target(outside)

        checkout_coordination.declare_execution_mode("mcp")

        config = load_config(settings)
        self.assertEqual(config.coordination_root, self.root / "live-ar-coordination")
        self.assertFalse(hasattr(config, "direct_execution_enabled"))
        checkout_coordination.require_durable_write_target(outside)

    def test_the_reserved_lifecycle_operation_mode_is_declarable_by_any_caller(self) -> None:
        """PIN (260918-TSIP-L3, `T30`): a mode the plane reserves in words is nobody's to grant.

        `declare_lifecycle_operation_process` (`checkout_coordination.py:69-76`) says the worker
        "is launched only from a durable plane-owned operation record" -- and it has **zero
        callers** in `mcp/src` or `mcp/tests`, so nothing in the product ever proves that claim.
        The call is a bare `declare_execution_mode("lifecycle-operation")`, and
        `durable_store.declared_process_role` (`:91-95`) reports it as a declared writer of every
        store `mcp` owns -- including the gate log (`:144-147`).

        Asserting today's behaviour here turns an unexamined invariant into a stated one: the
        entry point that is *supposed* to be reachable only from a plane-owned operation record is
        reachable from anywhere. This case fails the day that becomes true, which is the point.
        """
        _checkout, source = self._checkout(linked=False)
        self._undeclared_checkout(source)

        checkout_coordination.declare_lifecycle_operation_process()

        self.assertEqual(checkout_coordination.declared_execution_mode(), "lifecycle-operation")
        self.assertEqual(declared_process_role(), "lifecycle-operation")
        self.assertIsNone(checkout_coordination.checkout_cli_location())

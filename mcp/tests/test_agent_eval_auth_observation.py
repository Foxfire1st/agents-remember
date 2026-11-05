"""Explicit in-memory auth comparisons; fake files only, no credential store access."""

from __future__ import annotations

import contextlib
import io
import json
import pickle
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from agent_evals.adapters.execution import HERMES_ACTOR, HERMES_GPT_ACTOR, NativeBinding
from agent_evals.adapters.models import NativeCapture
from agent_evals.adapters.paseo import PaseoConnection, PaseoNativeAdapter
from agent_evals.auth_observation import AuthObservation, AuthObservationError
from agent_evals.trial_credentials import record_auth_comparison
from agent_evals.trials import _finish_trial, _TrialRuntime
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig


def test_gpt_auth_difference_is_unknown_and_a_finding(tmp_path):

    source = tmp_path / "fake-auth.json"
    source.write_bytes(b"synthetic-before")
    report = {"unknowns": []}
    runtime = SimpleNamespace(context=SimpleNamespace(profile=HERMES_GPT_ACTOR), report=report)
    with AuthObservation(source) as observation:
        source.write_bytes(b"synthetic-after!")
        assert not record_auth_comparison(runtime.report, runtime.context.profile, observation)
    assert report["authContentComparison"] == "differs"
    assert report["unknown_class"] == "binding-unavailable"
    assert report["authContentFinding"] == "real Hermes auth content differs"
    assert report["unknowns"] == ["binding-unavailable: real Hermes auth content differs"]
    assert "synthetic" not in json.dumps(report)


def test_gpt_auth_equal_and_existing_arm_difference_keep_their_meaning(tmp_path):

    source = tmp_path / "fake-auth.json"
    for profile, change in ((HERMES_GPT_ACTOR, False), (HERMES_ACTOR, True)):
        source.write_bytes(b"synthetic-before")
        report = {"unknowns": []}
        runtime = SimpleNamespace(context=SimpleNamespace(profile=profile), report=report)
        with AuthObservation(source) as observation:
            if change:
                source.write_bytes(b"synthetic-after!")
            assert record_auth_comparison(runtime.report, runtime.context.profile, observation)
        assert report["authContentComparison"] == ("differs" if change else "equal")
        assert not report["unknowns"]
        assert "authContentFinding" not in report


def test_gpt_auth_difference_overrides_a_captured_right_outcome(tmp_path):
    source = tmp_path / "fake-auth.json"
    source.write_bytes(b"synthetic-before")
    report = {"unknowns": [], "after": {}}
    runtime = SimpleNamespace(
        context=SimpleNamespace(profile=HERMES_GPT_ACTOR),
        provisioned=SimpleNamespace(cleanup=Mock()),
        evidence=tmp_path,
        report=report,
    )
    with AuthObservation(source) as observation, patch("agent_evals.trials._save_setup"):
        source.write_bytes(b"synthetic-after!")
        assert (
            _finish_trial(cast(_TrialRuntime, runtime), "right", observation)
            == "infrastructure_failure"
        )
    saved = json.loads((tmp_path / "attempt.json").read_text())
    assert saved["status"] == "infrastructure_failure"
    assert saved["authContentComparison"] == "differs"
    assert saved["authContentFinding"] == "real Hermes auth content differs"
    assert "synthetic" not in json.dumps(saved)


@pytest.mark.parametrize("end", [102.0, 110.0])
def test_gpt_expected_end_includes_host_creation_time(tmp_path, end):

    clock = {"wall": 100.0}
    sent = []

    def bridge(command, payload):
        if command == "agents-live":
            return {"complete": True, "agentIds": []}
        if command == "workspace-open":
            clock["wall"] += 1
            return {"workspace": {"id": "w"}}
        if command == "agent-create":
            clock["wall"] += 1
            sent.append(payload)
            return {"existing": "prompt" in payload, "agent": {"id": payload["agentId"]}}
        return {}

    binding = NativeBinding(
        tmp_path, tmp_path, tmp_path / "settings", "build", (), HERMES_GPT_ACTOR
    )
    config = McpRuntimeConfig(binding.settings_path, tmp_path, tmp_path, tmp_path)
    adapter = PaseoNativeAdapter(
        PaseoConnection(config, binding, tmp_path, {}, actor_deadline_epoch=end), call=bridge
    )
    with (
        patch.object(adapter, "probe", return_value={}),
        patch.object(adapter, "_check_applied"),
        patch.object(adapter, "_wait") as wait,
        patch.object(adapter, "_settle_native_stop"),
        patch("agent_evals.adapters.paseo.time.time", side_effect=lambda: clock["wall"]),
        patch("agent_evals.adapters.paseo.time.monotonic", return_value=50.0),
    ):
        if end == 102:
            with pytest.raises(AuthorityError, match="expected end"):
                adapter.run(
                    instructions="bound text",
                    prompt="case",
                    capture=lambda _: NativeCapture("hermes", "s", (), (), (), ()),
                )
            assert not any("prompt" in payload for payload in sent)
        else:
            adapter.run(
                instructions="bound text",
                prompt="case",
                capture=lambda _: NativeCapture("hermes", "s", (), (), (), ()),
                timeout_seconds=60,
            )
            assert wait.call_args.args[0].deadline == 57.0


class AuthObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "fake-auth.json"
        self.path.write_bytes(b"fake-secret-before")

    def test_exact_unchanged_bytes_are_equal(self):
        with AuthObservation(self.path) as observation:
            self.assertEqual(observation.compare(), "equal")

    def test_same_size_changed_bytes_differ(self):
        self.assertEqual(len(b"fake-secret-before"), len(b"fake-secret-after!"))
        with AuthObservation(self.path) as observation:
            self.path.write_bytes(b"fake-secret-after!")
            self.assertEqual(observation.compare(), "differs")

    def test_changed_size_differs(self):
        with AuthObservation(self.path) as observation:
            self.path.write_bytes(b"different-sized-fake-secret")
            self.assertEqual(observation.compare(), "differs")

    def test_empty_file_is_a_real_comparable_baseline(self):
        self.path.write_bytes(b"")
        with AuthObservation(self.path) as observation:
            self.assertEqual(observation.compare(), "equal")

    def test_missing_before_file_has_named_failure(self):
        self.path.unlink()
        with self.assertRaisesRegex(AuthObservationError, "before.*FileNotFoundError"):
            AuthObservation(self.path)

    def test_missing_after_file_fails_and_discards_baseline(self):
        with AuthObservation(self.path) as observation:
            self.path.unlink()
            with self.assertRaisesRegex(AuthObservationError, "after.*FileNotFoundError"):
                observation.compare()
            with self.assertRaisesRegex(AuthObservationError, "discarded"):
                observation.compare()

    def test_unreadable_before_and_after_never_quote_error_payload(self):
        for phase in ("before", "after"):
            with self.subTest(phase=phase):
                if phase == "after":
                    observation = AuthObservation(self.path)
                with (
                    patch.object(
                        Path,
                        "read_bytes",
                        side_effect=PermissionError(13, "fake-secret-error-payload"),
                    ),
                    self.assertRaises(AuthObservationError) as raised,
                ):
                    if phase == "before":
                        AuthObservation(self.path)
                    else:
                        observation.compare()
                self.assertIn(phase, str(raised.exception))
                self.assertIn("PermissionError", str(raised.exception))
                self.assertNotIn("fake-secret", repr(raised.exception))
                self.assertIsNone(raised.exception.__context__)
                if phase == "after":
                    with self.assertRaisesRegex(AuthObservationError, "discarded"):
                        observation.compare()

    def test_consumed_baseline_cannot_be_reused(self):
        with AuthObservation(self.path) as observation:
            self.assertEqual(observation.compare(), "equal")
            with self.assertRaisesRegex(AuthObservationError, "discarded"):
                observation.compare()

    def test_context_exception_discards_before_without_reading_after(self):
        observation = AuthObservation(self.path)
        with patch.object(Path, "read_bytes") as read:
            with self.assertRaisesRegex(RuntimeError, "interrupted"), observation:
                raise RuntimeError("interrupted")
            with self.assertRaisesRegex(AuthObservationError, "discarded"):
                observation.compare()
        read.assert_not_called()

    def test_repr_serialization_and_output_do_not_expose_content(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            AuthObservation(self.path) as observation,
        ):
            self.assertEqual(repr(observation), "<AuthObservation>")
            self.assertEqual(str(observation), "<AuthObservation>")
            self.assertFalse(hasattr(observation, "__dict__"))
            with self.assertRaisesRegex(TypeError, "cannot be serialized"):
                pickle.dumps(observation)
            with self.assertRaises(TypeError):
                json.dumps(observation)
            self.assertEqual(observation.compare(), "equal")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("fake-secret", repr(observation))

    def test_comparison_reads_exact_bytes_without_hash_or_stat(self):
        with (
            patch("hashlib.sha256", side_effect=AssertionError("no digest")),
            patch.object(Path, "stat", side_effect=AssertionError("no metadata")),
            patch.object(Path, "read_bytes", side_effect=[b"fake-one", b"fake-two"]) as read,
            AuthObservation(self.path) as observation,
        ):
            self.assertEqual(observation.compare(), "differs")
        self.assertEqual(read.call_count, 2)

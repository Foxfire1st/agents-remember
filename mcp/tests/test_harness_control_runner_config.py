"""Payload-level tests for ``parse_runner_config`` and the helpers it delegates to.

``mcp/tests/test_harness_control_runner.py`` exercises the happy round trip and three
malformed tokens; this module owns the arms that round trip cannot reach -- every
rejection message the parser can raise, the additive-field defaults, and the two
resolved-launch agreement refusals that keep a contradictory payload from ever reaching
a vendor process. Each case asserts the exact parsed value or the exact refusal text,
because the message is the evidence a hosted session surfaces when a launch is refused.
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
)
from agents_remember.serving.harness_control_runner import parse_runner_config
from agents_remember.serving.harness_launch import ResolvedLaunch

NOW = "2026-07-31T10:00:00+00:00"


def _encode(payload: object) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "identity": {
            "arSessionId": "ar-session-1",
            "tmuxName": "ar-ar-session-1",
            "createdAt": NOW,
        },
        "harnessId": "claude",
        "cwd": "/workspace",
        "argv": ["claude"],
        "endpointRoot": "/runtime/control",
        "sessionCommands": [],
        "resolvedLaunch": None,
        "resumeThreadId": None,
    }
    payload.update(overrides)
    return payload


def _launch(*, harness_id: str = "claude", workspace: str = "/workspace") -> dict[str, str]:
    return {
        "harnessId": harness_id,
        "modelKey": "claude-fable-5",
        "effort": "max",
        "workspace": workspace,
    }


class RunnerConfigRejectionTests(unittest.TestCase):
    def _refusal(self, payload: object) -> str:
        with self.assertRaises(HarnessControlError) as caught:
            parse_runner_config(_encode(payload))
        return str(caught.exception)

    def test_undecodable_and_non_object_tokens_name_their_own_defect(self) -> None:
        with self.assertRaises(HarnessControlError) as caught:
            parse_runner_config("not-base64")
        self.assertEqual(str(caught.exception), "hosted control runner config is malformed")

        # Valid base64 whose bytes are not JSON, and valid JSON that is not an object.
        with self.assertRaises(HarnessControlError) as caught:
            parse_runner_config(base64.urlsafe_b64encode(b"{nope").decode("ascii"))
        self.assertEqual(str(caught.exception), "hosted control runner config is malformed")
        for not_an_object in ([], "text", 7, None):
            with self.subTest(payload=not_an_object):
                self.assertEqual(
                    self._refusal(not_an_object),
                    "hosted control runner config must be an object",
                )

    def test_identity_argv_and_session_commands_share_one_shape_refusal(self) -> None:
        cases: tuple[dict[str, object], ...] = (
            {"identity": "ar-session-1"},
            {"identity": ["ar-session-1"]},
            {"argv": "claude"},
            {"argv": ["claude", ""]},
            {"argv": ["claude", 7]},
            {"argv": [None]},
            {"sessionCommands": "/color blue"},
            {"sessionCommands": ["/color blue", ""]},
            {"sessionCommands": [3]},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertEqual(
                    self._refusal(_payload(**overrides)),
                    "hosted control runner requires identity and argv",
                )

    def test_each_required_text_field_names_itself(self) -> None:
        for key in ("harnessId", "cwd", "endpointRoot"):
            for value in ("", 7, None, ["/workspace"]):
                with self.subTest(key=key, value=value):
                    self.assertEqual(
                        self._refusal(_payload(**{key: value})),
                        f"hosted control runner requires non-empty {key}",
                    )
        missing = _payload()
        del missing["harnessId"]
        self.assertEqual(
            self._refusal(missing), "hosted control runner requires non-empty harnessId"
        )

    def test_untrimmed_or_empty_resume_thread_id_is_refused(self) -> None:
        expected = "hosted control runner resumeThreadId must be non-empty trimmed text or null"
        for bad in ("", "   ", " thread-9", "thread-9 ", "\tthread-9\n", 7, ["thread-9"]):
            with self.subTest(bad=bad):
                self.assertEqual(self._refusal(_payload(resumeThreadId=bad)), expected)

    def test_malformed_resolved_launch_is_refused_by_its_own_reader(self) -> None:
        self.assertEqual(
            self._refusal(_payload(resolvedLaunch="claude")),
            "resolved launch must be an object",
        )
        launch = _launch()
        del launch["effort"]
        self.assertEqual(
            self._refusal(_payload(resolvedLaunch=launch)),
            "resolved launch requires non-empty effort",
        )

    def test_resolved_launch_harness_must_match_the_harness_being_launched(self) -> None:
        self.assertEqual(
            self._refusal(_payload(resolvedLaunch=_launch(harness_id="codex"))),
            "runner resolved launch harness does not match harnessId",
        )

    def test_resolved_launch_workspace_must_match_the_runner_cwd(self) -> None:
        self.assertEqual(
            self._refusal(_payload(resolvedLaunch=_launch(workspace="/elsewhere"))),
            "runner resolved launch workspace does not match cwd",
        )
        # A launch whose harness matches but whose workspace does not is still refused, so
        # the workspace check cannot be satisfied by the harness check passing.
        self.assertEqual(
            self._refusal(
                _payload(
                    cwd="/workspace/sub",
                    resolvedLaunch=_launch(workspace="/workspace"),
                )
            ),
            "runner resolved launch workspace does not match cwd",
        )

    def test_an_agreeing_resolved_launch_is_kept_verbatim(self) -> None:
        config = parse_runner_config(_encode(_payload(resolvedLaunch=_launch())))
        self.assertEqual(
            config.resolved_launch,
            ResolvedLaunch("claude", "claude-fable-5", "max", Path("/workspace")),
        )
        self.assertEqual(config.harness_id, "claude")
        self.assertEqual(config.cwd, Path("/workspace"))


class RunnerConfigDefaultTests(unittest.TestCase):
    def test_absent_additive_fields_parse_to_their_documented_defaults(self) -> None:
        payload = _payload()
        for key in ("sessionCommands", "resolvedLaunch", "resumeThreadId"):
            del payload[key]
        config = parse_runner_config(_encode(payload))
        self.assertEqual(config.session_commands, ())
        self.assertIsNone(config.resolved_launch)
        self.assertIsNone(config.resume_thread_id)
        self.assertEqual(config.identity, ControlIdentity("ar-session-1", "ar-ar-session-1", NOW))
        self.assertEqual(config.argv, ("claude",))
        self.assertEqual(config.endpoint_root, Path("/runtime/control"))

    def test_present_additive_fields_are_carried_through_as_read(self) -> None:
        config = parse_runner_config(
            _encode(
                _payload(
                    sessionCommands=["/color blue", "/model sonnet"],
                    resumeThreadId="thread-9",
                    argv=["claude", "--verbose"],
                )
            )
        )
        self.assertEqual(config.session_commands, ("/color blue", "/model sonnet"))
        self.assertEqual(config.resume_thread_id, "thread-9")
        self.assertEqual(config.argv, ("claude", "--verbose"))


if __name__ == "__main__":
    unittest.main()

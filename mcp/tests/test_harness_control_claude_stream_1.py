from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import _interaction_questions
from agents_remember.serving.harness_control_models import (
    InteractionResponse,
    pending_interaction_json,
)
from agents_remember.serving.harness_launch import ResolvedLaunch
from test_harness_control_claude import (
    NOW,
    SESSION_ID,
    _adapter,
    _FakeClaudeTransport,
    _identity,
    _launch,
    _load_fixture,
    _replay,
    _result,
    _settle,
    _wait_for_activity,
    _wire_text,
)


class ClaudeStreamJsonAdapterTests1(unittest.IsolatedAsyncioTestCase):
    async def test_discover_uses_only_token_free_bootstrap_and_list_models(self) -> None:
        fixture_frames = _load_fixture("initialization.jsonl")
        transport = _FakeClaudeTransport(fixture_frames)
        adapter = _adapter(transport)

        advertised = await adapter.discover(_launch())

        self.assertEqual(advertised.selected_model_key, "sonnet")
        user_frames = [frame for frame in transport.writes if frame["type"] == "user"]
        self.assertEqual(len(user_frames), 1)
        self.assertEqual(user_frames[0]["shouldQuery"], False)
        bootstrap_result = next(frame for frame in fixture_frames if frame["type"] == "result")
        self.assertEqual(bootstrap_result["num_turns"], 0)
        self.assertEqual(bootstrap_result["total_cost_usd"], 0)
        assert transport.argv is not None
        self.assertEqual(transport.argv[:9], _launch().argv)
        self.assertEqual(
            transport.argv[9:12],
            ("--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config"),
        )
        self.assertEqual(transport.stop_modes, ["forced"])

    async def test_discover_replaces_all_installed_mcp_selector_spellings(self) -> None:
        executable = "/opt/claude"
        empty = ("--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config")
        # Live 2.1.210 startup accepts the exact strict flag below, but rejects
        # --no-strict-mcp-config and the =true/=false boolean forms; those are not its grammar.
        cases = (
            (
                "separate-single",
                (
                    executable,
                    "--model",
                    "sonnet",
                    "--mcp-config",
                    '{"mcpServers":{"touch":{}}}',
                    "--effort",
                    "high",
                ),
                (executable, "--model", "sonnet", "--effort", "high", *empty),
            ),
            (
                "separate-multiple-and-repeated",
                (
                    executable,
                    "--mcp-config",
                    "/tmp/one.json",
                    '{"mcpServers":{"two":{}}}',
                    "/tmp/three.json",
                    "--settings",
                    "/tmp/settings.json",
                    "--mcp-config",
                    "/tmp/four.json",
                    "/tmp/five.json",
                    "--resume",
                    SESSION_ID,
                ),
                (
                    executable,
                    "--settings",
                    "/tmp/settings.json",
                    "--resume",
                    SESSION_ID,
                    *empty,
                ),
            ),
            (
                "equals-attached-preserves-following-positionals",
                (
                    executable,
                    "--append-system-prompt",
                    "keep-before",
                    "--mcp-config=/tmp/attached.json",
                    "literal-positional",
                    "--strict-mcp-config",
                    '--mcp-config={"mcpServers":{"other":{}}}',
                    "--append-system-prompt",
                    "keep-after",
                ),
                (
                    executable,
                    "--append-system-prompt",
                    "keep-before",
                    "literal-positional",
                    "--append-system-prompt",
                    "keep-after",
                    *empty,
                ),
            ),
            (
                "end-of-options-preserves-positional-suffix",
                (
                    executable,
                    "--model",
                    "sonnet",
                    "--mcp-config=/tmp/attached.json",
                    "--",
                    "--mcp-config",
                    "literal-positional",
                    "--strict-mcp-config",
                ),
                (
                    executable,
                    "--model",
                    "sonnet",
                    *empty,
                    "--",
                    "--mcp-config",
                    "literal-positional",
                    "--strict-mcp-config",
                ),
            ),
        )
        for label, argv, expected_prefix in cases:
            with self.subTest(label=label):
                transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
                adapter = _adapter(transport)

                await adapter.discover(_launch(argv=argv))

                assert transport.argv is not None
                self.assertEqual(transport.argv[: len(expected_prefix)], expected_prefix)
                discovery_prefix = transport.argv[: len(expected_prefix)]
                separator = (
                    discovery_prefix.index("--")
                    if "--" in discovery_prefix
                    else len(discovery_prefix)
                )
                parsed_options = discovery_prefix[:separator]
                self.assertEqual(parsed_options.count("--mcp-config"), 1)
                self.assertEqual(parsed_options.count("--strict-mcp-config"), 1)
                self.assertFalse(
                    any(argument.startswith("--mcp-config=") for argument in parsed_options)
                )
                self.assertEqual(transport.stop_modes, ["forced"])

    async def test_normal_start_preserves_existing_mcp_selectors_byte_for_byte(self) -> None:
        argv = (
            "/opt/claude",
            "--model",
            "sonnet",
            "--mcp-config",
            "/tmp/one.json",
            "/tmp/two.json",
            "--strict-mcp-config",
            "--settings",
            "/tmp/settings.json",
        )
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)

        await adapter.start(_launch(argv=argv))
        try:
            assert transport.argv is not None
            self.assertEqual(transport.argv[: len(argv)], argv)
        finally:
            await adapter.stop("forced")

    async def test_launch_preserves_arguments_environment_and_requires_structured_init(
        self,
    ) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)

        handshake = await adapter.start(_launch())
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(handshake.snapshot.vendor_session_id, SESSION_ID)
            self.assertEqual(handshake.raw["claudeCodeVersion"], "2.1.210")
            assert transport.argv is not None
            self.assertEqual(
                transport.argv[:9],
                _launch().argv,
            )
            for required in (
                "-p",
                "--input-format",
                "--output-format",
                "--verbose",
                "--replay-user-messages",
                "--permission-prompt-tool",
            ):
                self.assertIn(required, transport.argv)
            # 2.1.210 is below the probed floor, so the flag
            # is omitted fail-closed — one launch, no re-launch, and the exact reason.
            self.assertNotIn("--forward-subagent-text", transport.argv)
            self.assertEqual(len(transport.start_argvs), 1)
            note = str(handshake.snapshot.raw["subagentTextForwarding"])
            self.assertIn("unverified", note)
            self.assertIn("2.1.210", note)
            self.assertIn("2.1.220", note)
            self.assertNotIn("--mcp-config", transport.argv)
            self.assertNotIn("--strict-mcp-config", transport.argv)
            self.assertEqual(transport.env, _launch().env)
            self.assertNotIn("AUTH_TOKEN_FOR_TEST", json.dumps(handshake.raw))
            self.assertEqual(
                [frame["type"] for frame in transport.writes],
                ["control_request", "user", "control_request"],
            )
            self.assertEqual(transport.writes[1]["shouldQuery"], False)
            list_request = transport.writes[2]
            self.assertEqual(list_request["request"], {"subtype": "list_models"})
            advertised = adapter.advertise()
            self.assertEqual(advertised.selected_model_key, "sonnet")
            self.assertIsNone(advertised.selected_effort)
            self.assertEqual(
                [option.key for option in advertised.models[0].effort_options],
                ["low", "medium", "high", "xhigh", "max"],
            )
            self.assertEqual(advertised.models[1].effort_options, ())
            self.assertFalse(advertised.models[2].selectable)
            self.assertEqual(len(transport.writes), 3)
        finally:
            await adapter.stop("forced")

    async def test_forward_subagent_text_relaunches_with_the_flag_at_or_above_the_floor(
        self,
    ) -> None:
        """The floor-proven install probes
        WITHOUT the flag, then re-launches WITH it behind the system/init capture."""

        frames = _load_fixture("initialization.jsonl")
        frames[1]["claude_code_version"] = "2.1.220"
        transport = _FakeClaudeTransport(frames)
        transport.restart_frames = _load_fixture("initialization.jsonl")
        assert transport.restart_frames is not None
        transport.restart_frames[1]["claude_code_version"] = "2.1.220"
        adapter = _adapter(transport)

        handshake = await adapter.start(_launch())
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            # The probe launch omitted the flag; the proven re-launch carries it.
            self.assertEqual(len(transport.start_argvs), 2)
            self.assertNotIn("--forward-subagent-text", transport.start_argvs[0])
            self.assertIn("--forward-subagent-text", transport.start_argvs[1])
            note = str(handshake.snapshot.raw["subagentTextForwarding"])
            self.assertIn("enabled", note)
            self.assertIn("2.1.220", note)
        finally:
            await adapter.stop("forced")

    async def test_forward_subagent_text_stays_fail_closed_on_an_unparseable_version(
        self,
    ) -> None:
        frames = _load_fixture("initialization.jsonl")
        frames[1]["claude_code_version"] = "dev-build"
        transport = _FakeClaudeTransport(frames)
        adapter = _adapter(transport)

        handshake = await adapter.start(_launch())
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(len(transport.start_argvs), 1)
            self.assertNotIn("--forward-subagent-text", transport.start_argvs[0])
            self.assertIn("unverified", str(handshake.snapshot.raw["subagentTextForwarding"]))
        finally:
            await adapter.stop("forced")

    async def test_current_initialize_without_models_or_account_is_accepted(self) -> None:
        frames = _load_fixture("initialization.jsonl")
        response = frames[0]["response"]
        assert isinstance(response, dict)
        payload = response["response"]
        assert isinstance(payload, dict)
        self.assertNotIn("models", payload)
        self.assertNotIn("account", payload)
        transport = _FakeClaudeTransport(frames)
        adapter = _adapter(transport)

        handshake = await adapter.start(_launch())
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(adapter.advertise().models[0].key, "sonnet")
        finally:
            await adapter.stop("forced")

    async def test_malformed_or_rejected_list_models_fails_loud_without_fallback(self) -> None:
        duplicate_frames = _load_fixture("initialization.jsonl")
        response = duplicate_frames[-1]["response"]
        assert isinstance(response, dict)
        payload = response["response"]
        assert isinstance(payload, dict)
        models = payload["models"]
        assert isinstance(models, list)
        models.append(dict(models[0]))
        duplicate_transport = _FakeClaudeTransport(duplicate_frames)
        duplicate = _adapter(duplicate_transport)

        duplicate_handshake = await duplicate.start(_launch())

        self.assertEqual(duplicate_handshake.snapshot.control, "unsupported")
        self.assertIn("repeated model value", str(duplicate_handshake.raw["detail"]))
        with self.assertRaisesRegex(HarnessControlError, "repeated model value"):
            duplicate.advertise()

        rejected_frames = _load_fixture("initialization.jsonl")
        rejected_response = rejected_frames[-1]["response"]
        assert isinstance(rejected_response, dict)
        rejected_response["subtype"] = "error"
        rejected_transport = _FakeClaudeTransport(rejected_frames)
        rejected = _adapter(rejected_transport)

        rejected_handshake = await rejected.start(_launch())

        self.assertEqual(rejected_handshake.snapshot.control, "unsupported")
        self.assertIn(
            "list_models control request was rejected",
            str(rejected_handshake.raw["detail"]),
        )

    async def test_compatible_patch_version_is_accepted_after_structured_negotiation(self) -> None:
        frames = _load_fixture("initialization.jsonl")
        frames[1]["claude_code_version"] = "2.1.209"
        transport = _FakeClaudeTransport(frames)
        adapter = _adapter(transport)

        handshake = await adapter.start(_launch())
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(handshake.adapter_id, "claude-stream-json:2.1.209")
            self.assertEqual(handshake.raw["claudeCodeVersion"], "2.1.209")
            self.assertTrue(transport.started)
        finally:
            await adapter.stop("forced")

    async def test_missing_protocol_capability_fails_loudly(self) -> None:
        frames = _load_fixture("initialization.jsonl")
        response = frames[0]["response"]
        assert isinstance(response, dict)
        payload = response["response"]
        assert isinstance(payload, dict)
        payload.pop("commands")
        incompatible_transport = _FakeClaudeTransport(frames)
        incompatible = _adapter(incompatible_transport)
        handshake = await incompatible.start(_launch())
        self.assertEqual(handshake.snapshot.control, "unsupported")
        self.assertEqual(incompatible_transport.stop_modes, ["forced"])
        self.assertIn("command capabilities", str(handshake.raw["detail"]))

    async def test_expected_launch_model_mismatch_closes_and_propagates_as_failure(self) -> None:
        frames = _load_fixture("initialization.jsonl")
        system_init = frames[1]
        system_init["model"] = "haiku"
        transport = _FakeClaudeTransport(frames)
        adapter = _adapter(
            transport,
            expected_launch=ResolvedLaunch("claude", "sonnet", "high", Path("/workspace")),
        )

        with self.assertRaisesRegex(
            HarnessControlError,
            "selected model 'sonnet'.*running harness reported 'haiku'",
        ):
            await adapter.start(_launch())

        self.assertEqual(transport.stop_modes, ["forced"])

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_claude_stream_1.py:391).
    async def test_correlated_acceptance_retry_activity_and_terminal_result_are_distinct(  # pragma: no cover
        self,
    ) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        submission = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("first prompt", source="durable", request_id="request-1")
            )
        )
        try:
            await transport.wait_for_writes(4)
            turn = _load_fixture("turn.jsonl")
            transport.feed(turn[0])
            receipt = await asyncio.wait_for(submission, timeout=1.0)
            self.assertEqual(receipt.acceptance, "immediate")
            self.assertEqual(bridge.snapshot().activity, "running")
            self.assertFalse(
                any(entry.terminal_result is not None for entry in bridge.transcript())
            )

            transport.feed(turn[1])
            transport.feed(turn[2])
            await _settle()
            self.assertEqual(bridge.snapshot().activity, "settling")

            transport.feed(turn[3])
            await _settle()
            self.assertEqual(bridge.snapshot().activity, "idle")
            transcript = bridge.transcript()
            self.assertEqual([entry.role for entry in transcript], ["user", "assistant", "result"])
            self.assertEqual(transcript[0].text, "first prompt")
            self.assertEqual(transcript[-1].request_id, "request-1")
            assert transcript[-1].terminal_result is not None
            self.assertEqual(transcript[-1].terminal_result.outcome, "completed")
        finally:
            if not submission.done():
                submission.cancel()
                await asyncio.gather(submission, return_exceptions=True)
            await bridge.stop("forced")

    async def test_multiple_messages_use_only_the_authoritative_bridge_queue(self) -> None:
        correlations = [
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ]
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport, correlations=correlations)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            first_task = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("first", source="terminal", request_id="first")
                )
            )
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            first = await asyncio.wait_for(first_task, timeout=1.0)

            second = await bridge.submissions().submit(
                bridge.prompt("second", source="durable", request_id="second")
            )
            self.assertEqual((first.acceptance, second.acceptance), ("immediate", "queued"))
            self.assertEqual(len(transport.writes), 4)
            self.assertIn("\n\nfirst", _wire_text(transport.writes[3]))

            transport.feed(_result("first done"))
            await transport.wait_for_writes(5)
            self.assertIn("\n\nsecond", _wire_text(transport.writes[4]))
            transport.feed(_replay(transport.writes[4]))
            transport.feed(_result("second done"))
            await _settle()
            results = [entry for entry in bridge.transcript() if entry.role == "result"]
            self.assertEqual([entry.request_id for entry in results], ["first", "second"])
        finally:
            await bridge.stop("forced")

    async def test_permissions_and_ask_user_question_use_durable_interaction_response(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        permission, question = _load_fixture("interactions.jsonl")
        try:
            active = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("interaction turn", source="terminal", request_id="interaction")
                )
            )
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            self.assertEqual((await active).acceptance, "immediate")
            transport.feed(permission)
            await _settle()
            pending = bridge.snapshot().pending_interaction
            assert pending is not None
            self.assertEqual((pending.kind, pending.choices), ("permission", ("allow", "deny")))
            await bridge.submissions().respond(InteractionResponse("permission-1", "allow", NOW))
            permission_response = transport.writes[-1]["response"]
            assert isinstance(permission_response, dict)
            result = permission_response["response"]
            assert isinstance(result, dict)
            self.assertEqual(result["behavior"], "allow")
            self.assertEqual(bridge.snapshot().activity, "settling")

            transport.feed(question)
            await _settle()
            pending = bridge.snapshot().pending_interaction
            assert pending is not None
            self.assertEqual(pending.kind, "user-input")
            self.assertIn("Which mode", pending.prompt)
            await bridge.submissions().respond(
                InteractionResponse(
                    "question-1",
                    json.dumps({"Which mode should be used?": "Safe"}),
                    NOW,
                )
            )
            question_response = transport.writes[-1]["response"]
            assert isinstance(question_response, dict)
            result = question_response["response"]
            assert isinstance(result, dict)
            updated = result["updatedInput"]
            assert isinstance(updated, dict)
            self.assertEqual(updated["answers"], {"Which mode should be used?": "Safe"})
            transport.feed(_result("interaction done"))
            await _wait_for_activity(adapter, "idle")
        finally:
            await bridge.stop("forced")

    async def test_multi_question_normalization_keeps_structured_pages(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        (question,) = _load_fixture("interactions-multi.jsonl")
        try:
            active = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("interaction turn", source="terminal", request_id="interaction")
                )
            )
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            self.assertEqual((await active).acceptance, "immediate")
            transport.feed(question)
            await _settle()
            pending = bridge.snapshot().pending_interaction
            assert pending is not None
            self.assertEqual(pending.kind, "user-input")
            # The legacy flattened rendering stays for old surfaces...
            self.assertEqual(
                pending.prompt,
                "Mode: Which mode should be used?\nFeatures: Which features should be enabled?",
            )
            self.assertEqual(pending.choices, ("Safe", "Fast", "Logs", "Traces"))
            # ...while the structured pages carry per-question options and multiSelect.
            self.assertEqual(len(pending.questions), 2)
            mode, features = pending.questions
            self.assertEqual(
                (mode.text, mode.header, mode.multi_select),
                ("Which mode should be used?", "Mode", False),
            )
            self.assertEqual(
                [(option.label, option.description) for option in mode.options],
                [("Safe", "Use safe mode"), ("Fast", "Use fast mode")],
            )
            self.assertEqual(
                (features.text, features.header, features.multi_select),
                ("Which features should be enabled?", "Features", True),
            )
            self.assertEqual(
                [(option.label, option.description) for option in features.options],
                [("Logs", None), ("Traces", "Enable distributed traces")],
            )
            # The structured shape survives the catalog-row/gate-packet serialization.
            wire = pending_interaction_json(pending)
            assert wire is not None
            self.assertEqual(_interaction_questions(wire["questions"]), pending.questions)

            await bridge.submissions().respond(
                InteractionResponse(
                    "question-multi",
                    json.dumps(
                        {
                            "Which mode should be used?": "Safe",
                            "Which features should be enabled?": "Logs",
                        }
                    ),
                    NOW,
                )
            )
            question_response = transport.writes[-1]["response"]
            assert isinstance(question_response, dict)
            result = question_response["response"]
            assert isinstance(result, dict)
            updated = result["updatedInput"]
            assert isinstance(updated, dict)
            self.assertEqual(
                updated["answers"],
                {
                    "Which mode should be used?": "Safe",
                    "Which features should be enabled?": "Logs",
                },
            )
        finally:
            await bridge.stop("forced")

    async def test_multi_question_answer_map_must_answer_every_question(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        (question,) = _load_fixture("interactions-multi.jsonl")
        try:
            active = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("interaction turn", source="terminal", request_id="interaction")
                )
            )
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            self.assertEqual((await active).acceptance, "immediate")
            transport.feed(question)
            await _settle()

            with self.assertRaisesRegex(HarnessControlError, "answer every question"):
                await bridge.submissions().respond(
                    InteractionResponse(
                        "question-multi",
                        json.dumps({"Which mode should be used?": "Safe"}),
                        NOW,
                    )
                )
            with self.assertRaisesRegex(HarnessControlError, "JSON object"):
                await bridge.submissions().respond(
                    InteractionResponse("question-multi", "Safe", NOW)
                )
            # The failed responds never reached the transport; the interaction stays pending.
            self.assertIsNotNone(bridge.snapshot().pending_interaction)
            self.assertEqual(len(transport.writes), 4)
        finally:
            await bridge.stop("forced")

"""L1 explicit-message boundary for branch-addressed direct landing."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.application.direct_landing import direct_landing_tool
from agents_remember.worktrees.closeout_input import CloseoutInputError
from agents_remember.worktrees.direct_landing import (
    DirectLandingRequest,
    direct_landing,
)
from test_direct_landing import _series_fixture


class DirectLandingInputBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_enabled_message_matrix_refuses_before_lane_authority_or_git(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        contract_path = fixture["contract"].contract_path.as_posix()
        observations = ((None, "omitted"), ("", "empty"), (" \n ", "whitespace-only"))
        for field in ("memory_commit_message", "ledger_commit_message"):
            for supplied, observation in observations:
                with (
                    self.subTest(field=field, observation=observation),
                    mock.patch(
                        "agents_remember.worktrees.direct_landing.integration_authority_lock"
                    ) as authority,
                    mock.patch(
                        "agents_remember.worktrees.direct_landing._verify_code_commit"
                    ) as verify,
                    self.assertRaises(CloseoutInputError) as raised,
                ):
                    direct_landing(
                        fixture["config"],
                        DirectLandingRequest(
                            contract_path=contract_path,
                            code_commit=fixture["code_head"],
                            memory_commit_message=(
                                supplied
                                if field == "memory_commit_message"
                                else "explicit sibling message"
                            ),
                            ledger_commit_message=(
                                supplied
                                if field == "ledger_commit_message"
                                else "explicit sibling message"
                            ),
                            intent_note="approve",
                        ),
                    )
                error = raised.exception
                self.assertEqual(error.invalid_fields[0].observation, observation)
                self.assertEqual(error.invalid_fields[0].field, field)
                self.assertEqual(error.resolved_plan.code.state, "not-applicable")
                self.assertEqual(error.resolved_plan.memory.state, "enabled")
                self.assertEqual(error.resolved_plan.ledger.state, "enabled")
                authority.assert_not_called()
                verify.assert_not_called()

    def test_preview_and_apply_receive_the_same_stripped_effective_input(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        contract_path = fixture["contract"].contract_path.as_posix()
        with (
            mock.patch(
                "agents_remember.worktrees.direct_landing.integration_authority_lock",
                return_value=nullcontext(),
            ),
            mock.patch(
                "agents_remember.worktrees.direct_landing._direct_landing_preview",
                return_value={"state": "would-land"},
            ) as preview,
            mock.patch(
                "agents_remember.worktrees.direct_landing._direct_landing_apply",
                return_value={"state": "landed"},
            ) as apply,
        ):
            for dry_run in (True, False):
                direct_landing(
                    fixture["config"],
                    DirectLandingRequest(
                        contract_path=contract_path,
                        code_commit=fixture["code_head"],
                        memory_commit_message="  explicit memory  ",
                        ledger_commit_message="  explicit ledger  ",
                        intent_note="approve",
                        dry_run=dry_run,
                    ),
                )

        preview_input = preview.call_args.args[2]
        apply_input = apply.call_args.args[2]
        self.assertEqual(preview_input, apply_input)
        self.assertEqual(preview_input.code.state, "not-applicable")
        self.assertEqual(preview_input.message_for("memory"), "explicit memory")
        self.assertEqual(preview_input.message_for("ledger"), "explicit ledger")

    def test_public_boundary_returns_the_structured_input_refusal(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        with mock.patch(
            "agents_remember.worktrees.direct_landing.integration_authority_lock"
        ) as authority:
            refused = direct_landing_tool(
                fixture["config"],
                DirectLandingRequest(
                    contract_path=fixture["contract"].contract_path.as_posix(),
                    code_commit=fixture["code_head"],
                    memory_commit_message=" ",
                    ledger_commit_message="ledger",
                    intent_note="approve",
                ),
            )

        self.assertEqual(refused["status"], "closeout-input-invalid")
        invalid_fields = cast(list[dict[str, object]], refused["invalidFields"])
        resolved_plan = cast(dict[str, dict[str, object]], refused["resolvedPlan"])
        corrected_call = cast(dict[str, dict[str, object]], refused["correctedCall"])
        self.assertEqual(invalid_fields[0]["field"], "memory_commit_message")
        self.assertEqual(resolved_plan["code"]["state"], "not-applicable")
        self.assertEqual(
            corrected_call["arguments"]["memory_commit_message"],
            "<nonblank memory commit message>",
        )
        authority.assert_not_called()

    def test_dry_run_input_refusal_preserves_dry_run_in_corrected_call(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        refused = direct_landing_tool(
            fixture["config"],
            DirectLandingRequest(
                contract_path=fixture["contract"].contract_path.as_posix(),
                code_commit=fixture["code_head"],
                memory_commit_message=None,
                ledger_commit_message=None,
                intent_note="review only",
                dry_run=True,
            ),
        )

        self.assertEqual(refused["status"], "closeout-input-invalid")
        corrected_call = cast(dict[str, object], refused["correctedCall"])
        arguments = cast(dict[str, object], corrected_call["arguments"])
        self.assertIs(arguments["dry_run"], True)

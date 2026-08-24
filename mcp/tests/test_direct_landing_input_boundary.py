"""L1 explicit-message boundary for branch-addressed direct landing."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from typing import Any, cast
from unittest import mock

from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractRefused,
)
from agents_remember.application.lifecycle.direct_landing import direct_landing_tool
from agents_remember.application.lifecycle.lifecycle_operation_location import (
    ContractReadOperationObservation,
)
from agents_remember.mcp.tools.direct_landing import direct_landing_payload
from agents_remember.models.direct_landing import DirectLandingResponse
from agents_remember.worktrees import direct_landing as direct_domain
from agents_remember.worktrees.closeout_input import CloseoutInputError
from agents_remember.worktrees.direct_landing import (
    DirectLandingError,
    DirectLandingRequest,
    direct_landing,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationSchemaError,
)
from pydantic import ValidationError
from test_direct_landing import _byte_tree, _scratch_config, _series_fixture


class DirectLandingInputBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_public_state_schema_is_the_exact_three_outcome_vocabulary(self) -> None:
        for state in ("landed", "would-land", "refused"):
            response = DirectLandingResponse(ok=state != "refused", state=state)
            self.assertEqual(response.state, state)
        with self.assertRaises(ValidationError):
            DirectLandingResponse.model_validate({"ok": False, "state": "unknown"})
        self.assertEqual(
            DirectLandingResponse.model_json_schema()["properties"]["state"]["enum"],
            ["landed", "would-land", "refused"],
        )

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
                        fixture["contract"],
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
                "agents_remember.worktrees.direct_landing._start_or_observe_direct_landing",
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
                    fixture["contract"],
                )

        preview_input = preview.call_args.args[2]
        apply_input = apply.call_args.args[3]
        self.assertEqual(preview_input, apply_input)
        self.assertEqual(preview_input.code.state, "not-applicable")
        self.assertEqual(preview_input.message_for("memory"), "explicit memory")
        self.assertEqual(preview_input.message_for("ledger"), "explicit ledger")

    def test_domain_uses_admitted_contract_not_the_raw_request_address(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        contract = fixture["contract"]
        request = DirectLandingRequest(
            contract_path=(root.parent / "PRIVATE_UNADMITTED_ADDRESS.md").as_posix(),
            code_commit=fixture["code_head"],
            memory_commit_message="direct memory",
            ledger_commit_message="direct ledger",
            intent_note="approve",
            dry_run=True,
        )
        with (
            mock.patch.object(
                direct_domain,
                "contract_lifecycle_lease",
                return_value=nullcontext(),
            ),
            mock.patch.object(
                direct_domain,
                "integration_authority_lock",
                return_value=nullcontext(),
            ),
            mock.patch.object(
                direct_domain,
                "reread_configured_contract",
                return_value=(contract, mock.sentinel.location),
            ) as reread,
            mock.patch.object(
                direct_domain,
                "_direct_landing_preview",
                return_value={"state": "would-land"},
            ) as preview,
        ):
            result = direct_landing(fixture["config"], request, contract)

        self.assertEqual(result, {"state": "would-land"})
        reread.assert_called_once_with(contract, fixture["config"].config_path.as_posix())
        preview.assert_called_once()
        source = inspect.getsource(direct_domain._direct_landing_after_policy)
        self.assertNotIn("request.contract_path", source)
        self.assertNotIn("Path(request.contract_path)", source)

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
        self.assertEqual(DirectLandingResponse.model_validate(refused).state, "refused")
        authority.assert_not_called()

    def test_public_boundary_normalizes_location_and_publication_refusals(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        request = DirectLandingRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            code_commit=fixture["code_head"],
            memory_commit_message="memory",
            ledger_commit_message="ledger",
            intent_note="approved",
        )
        publication_decision: dict[str, Any] = {
            "ok": False,
            "state": "operation-contract-publication-lost",
            "status": "operation-contract-publication-lost",
            "summary": "published contract lost",
            "detail": "published contract lost",
            "expected": {"state": "readable"},
            "observed": {"state": "missing"},
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": "published contract lost",
        }
        refusals = (
            ConfiguredContractRefused(
                reason="location-invalid",
                status="lifecycle-operation-location-invalid",
                detail="location invalid",
                expected={"state": "addressable"},
                observed={"state": "missing"},
            ),
            ConfiguredContractRefused(
                reason="contract-unreadable",
                status="configured-contract-unreadable",
                detail="contract unreadable",
                expected={"state": "readable"},
                observed={"state": "missing"},
                contract_path=fixture["contract"].contract_path,
                location=cast(Any, mock.sentinel.location),
            ),
        )
        for refusal in refusals:
            observation = (
                ContractReadOperationObservation(
                    (),
                    cast(Any, publication_decision),
                )
                if refusal.reason == "contract-unreadable"
                else None
            )
            observation_patch = (
                mock.patch(
                    "agents_remember.application.lifecycle.configured_contract_admission."
                    "observe_contract_read_failure",
                    return_value=observation,
                )
                if observation is not None
                else nullcontext()
            )
            with (
                self.subTest(reason=refusal.reason),
                mock.patch(
                    "agents_remember.application.lifecycle.direct_landing."
                    "admit_configured_contract",
                    return_value=refusal,
                ),
                observation_patch,
            ):
                payload = direct_landing_payload(fixture["config"], request)

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["state"], "refused")
            expected_status = (
                "operation-contract-publication-lost"
                if refusal.reason == "contract-unreadable"
                else refusal.status
            )
            self.assertEqual(payload["status"], expected_status)
            self.assertEqual(DirectLandingResponse.model_validate(payload).state, "refused")

    def test_public_boundary_normalizes_mutation_time_configured_refusal(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        request = DirectLandingRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            code_commit=fixture["code_head"],
            memory_commit_message="memory",
            ledger_commit_message="ledger",
            intent_note="approved",
        )
        refusal = ConfiguredContractRefused(
            reason="location-invalid",
            status="lifecycle-operation-location-invalid",
            detail="location invalid after admission",
            expected={"state": "addressable"},
            observed={"state": "missing"},
        )
        with (
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing.admit_configured_contract",
                return_value=mock.sentinel.accepted_contract,
            ),
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing."
                "execute_configured_contract_operation",
                return_value=refusal,
            ),
        ):
            payload = direct_landing_payload(fixture["config"], request)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], "refused")
        self.assertEqual(payload["status"], refusal.status)
        self.assertEqual(DirectLandingResponse.model_validate(payload).state, "refused")

    def test_recovery_journal_state_remains_nested_in_public_refusal(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        request = DirectLandingRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            code_commit=fixture["code_head"],
            memory_commit_message="memory",
            ledger_commit_message="ledger",
            intent_note="approved",
        )
        store = mock.Mock()
        store.read.side_effect = LifecycleOperationSchemaError(root / "direct-landing.json", "1.0")
        with (
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing.admit_configured_contract",
                return_value=mock.sentinel.accepted_contract,
            ),
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing."
                "execute_configured_contract_operation",
                side_effect=DirectLandingError("direct-landing-failed", "landing failed"),
            ),
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing.require_within_coordination",
                return_value=fixture["contract"].contract_path,
            ),
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing.load_contract",
                return_value=fixture["contract"],
            ),
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing.direct_landing_store",
                return_value=store,
            ),
        ):
            payload = direct_landing_payload(fixture["config"], request)

        self.assertEqual(payload["state"], "refused")
        self.assertEqual(payload["status"], "direct-landing-failed")
        lifecycle = cast(dict[str, Any], payload["lifecycleOperation"])
        self.assertEqual(lifecycle["status"], "unreadable")
        self.assertEqual(
            cast(dict[str, Any], lifecycle["result"])["state"],
            "direct-landing-lifecycle-journal-unreadable",
        )
        self.assertEqual(DirectLandingResponse.model_validate(payload).state, "refused")

    def test_policy_disabled_public_refusal_precedes_hostile_recovery_surfaces(self) -> None:
        root = Path(self.temp.name)
        fixture = _series_fixture(root / "fx")
        config = _scratch_config(
            root / "fx",
            fixture["code"],
            fixture["memory"],
            direct_execution_enabled=False,
        )
        private_sentinel = "PRIVATE_POLICY_RECOVERY_SENTINEL"
        hostile_contract_paths = (
            (root.parent / f"outside-{private_sentinel}.md").as_posix(),
            (root / "fx" / f"missing-{private_sentinel}.md").as_posix(),
            (root / "fx" / f"unreadable-{private_sentinel}.md").as_posix(),
            fixture["contract"].contract_path.as_posix(),
        )
        before = _byte_tree(root)
        expected = {
            "ok": False,
            "operation": "direct_landing",
            "state": "refused",
            "status": "direct-landing-policy-disabled",
            "detail": (
                "direct landing is disabled by policy; enable directExecutionEnabled "
                "in the MCP authority settings for sanctioned direct execution"
            ),
            "expected": {},
            "observed": {},
        }
        with (
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing.admit_configured_contract",
                side_effect=AssertionError(private_sentinel),
            ) as path_authority,
            mock.patch(
                "agents_remember.worktrees.integration.configured_contract_authority."
                "reread_configured_contract",
                side_effect=AssertionError(private_sentinel),
            ) as contract_read,
            mock.patch(
                "agents_remember.worktrees.direct_landing.integration_authority_lock",
                side_effect=AssertionError(private_sentinel),
            ) as mutation_authority,
            mock.patch(
                "agents_remember.worktrees.direct_landing._verify_code_commit",
                side_effect=AssertionError(private_sentinel),
            ) as git_inspection,
            mock.patch(
                "agents_remember.application.lifecycle.direct_landing._direct_recovery_action",
                side_effect=AssertionError(private_sentinel),
            ) as recovery,
        ):
            for contract_path in hostile_contract_paths:
                with self.subTest(contract_path=contract_path):
                    refused = direct_landing_tool(
                        config,
                        DirectLandingRequest(
                            contract_path=contract_path,
                            code_commit=private_sentinel,
                            memory_commit_message=private_sentinel,
                            ledger_commit_message=private_sentinel,
                            intent_note=private_sentinel,
                        ),
                    )
                    self.assertEqual(refused, expected)
                    self.assertNotIn(private_sentinel, str(refused))

        path_authority.assert_not_called()
        contract_read.assert_not_called()
        mutation_authority.assert_not_called()
        git_inspection.assert_not_called()
        recovery.assert_not_called()
        self.assertEqual(_byte_tree(root), before)

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

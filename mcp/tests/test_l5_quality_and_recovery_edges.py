from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import test_organizational_completion_integration as fixture_mod
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees import integration_quality as quality
from agents_remember.worktrees import integration_ref_transaction as ref_transaction
from agents_remember.worktrees import organizational_completion_integration as completion
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules import clean_quality_executor, code_quality_gate
from agents_remember.worktrees.series_closeout import atomic_series_ledger_prefix


class L5QualityAndRecoveryEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = fixture_mod.OrganizationalCompletionIntegrationTests(
            "test_nonfinal_leaf_reuses_targeted_closeout_without_full_gate"
        )
        self.owner.setUp()

    def tearDown(self) -> None:
        try:
            self.owner.doCleanups()
        finally:
            self.owner.tearDown()

    def test_published_quality_attestation_and_result_failure_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            reports = root / "reports"
            export.mkdir()
            result_path = export / "clean-quality-results.json"
            result_path.write_text("not json\n", encoding="utf-8")
            clean_quality_executor._publish_reports(export, reports, attestation={"id": "one"})
            target = code_quality_gate.QualityGateTarget(
                code_worktree=root,
                worktree_group=root,
            )
            plan = code_quality_gate.QualityGatePlan(mode="full", executor="dagger")
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                code_quality_gate.recover_strict_code_quality_gate(
                    target,
                    diff_base="a" * 40,
                    plan=plan,
                    attestation={"id": "one"},
                )

            result_path.write_text(
                json.dumps({"status": "failed", "exitCode": 1}) + "\n",
                encoding="utf-8",
            )
            clean_quality_executor._publish_reports(export, reports, attestation={"id": "one"})
            self.assertIsNone(
                code_quality_gate.recover_strict_code_quality_gate(
                    target,
                    diff_base="a" * 40,
                    plan=plan,
                    attestation={"id": "one"},
                )
            )

            manifest_path = reports / clean_quality_executor.REPORT_SET_MANIFEST
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("attestation")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertIsNone(clean_quality_executor.published_quality_attestation(reports))
            manifest["attestation"] = "invalid"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "attestation is invalid"):
                clean_quality_executor.published_quality_attestation(reports)

    def test_organizational_gate_returns_a_certificate_without_a_sink(self) -> None:
        contract = self.owner._certified_contract(final=True)
        plan = completion.preview_organizational_completion(contract)
        assert plan is not None
        with mock.patch.object(
            quality,
            "run_strict_code_quality_gate",
            return_value=fixture_mod._full_gate(contract),
        ) as gate:
            outcome = quality.run_integration_quality_gate(contract, completion=plan)
        gate.assert_called_once()
        self.assertIsNotNone(outcome.certification)

    def test_public_ledger_and_series_prefix_preconditions_refuse_wrong_contracts(self) -> None:
        contract = self.owner._certified_contract(final=True)
        with self.assertRaisesRegex(RuntimeError, "leaf or series"):
            ref_transaction.require_integrated_ledger_mapping(
                replace(contract, kind="invalid"),  # type: ignore[arg-type]
                ref_transaction.IntegratedCommits("a" * 40, "b" * 40, "c" * 40),
                memory_source_commit="d" * 40,
            )
        with self.assertRaisesRegex(RuntimeError, "external-memory series"):
            atomic_series_ledger_prefix(replace(contract, kind="leaf"))

    def test_closeout_wal_cannot_claim_an_integration_quality_failure(self) -> None:
        contract = self.owner._certified_contract(final=True)
        closeout = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, "closeout")
        ).read()
        assert closeout is not None
        with self.assertRaisesRegex(ValueError, "belongs to integration only"):
            LifecycleOperationRecord.model_validate(
                {
                    **closeout.model_dump(mode="json"),
                    "result": {"state": "organizational-completion-gate-failed"},
                }
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

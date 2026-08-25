"""Pure contracts for owner preflights, causal edges, and retry evidence."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from agents_remember.code_quality.causal_preflight import (
    PREFLIGHTS,
    PreflightSpec,
    evaluate_preflights,
)
from agents_remember.code_quality.dependency_ownership import (
    OwnedTest,
    SelectionReason,
    SelectionReasonKind,
)
from agents_remember.code_quality.dependency_ownership import (
    TestImpact as DependencyTestImpact,
)
from agents_remember.testing.causal_failures import (
    CAUSAL_REPORT_SCHEMA,
    FailureClass,
    execution_profile,
    load_causal_report,
    runtime_failure_record,
    write_causal_report,
)

OWNER = Path("mcp/src/agents_remember/application/lifecycle/owner.py")
CANDIDATE: dict[str, object] = {
    "tree": "a" * 40,
    "environmentId": "b" * 64,
    "attemptNonceSha256": "c" * 64,
}


class _Graph:
    def __init__(self, impact: DependencyTestImpact) -> None:
        self.impact = impact

    def resolve(self, changed: Sequence[Path]) -> DependencyTestImpact:
        assert len(changed) == 1
        return self.impact


class _RuntimeReport:
    nodeid = "mcp/tests/test_socket_case.py::test_disconnect"
    outcome = "failed"
    when = "call"
    duration = 1.25
    start = 10.0
    stop = 11.25
    worker_id = "gw3"

    def __init__(self) -> None:
        self.user_properties: list[tuple[str, object]] = [
            ("arFailureClass", FailureClass.PROCESS_ENVIRONMENT.value),
            ("arFailureFamilies", "async-concurrency,socket-service"),
            (
                "arRetrySemantics",
                "repeat-exact-node-with-seed-worker-timing-and-process-topology",
            ),
            ("arRandomOrderSeed", "260824"),
        ]


class CausalFailureLocalizationTests(unittest.TestCase):
    def test_real_lifecycle_owner_preflight_is_compatible(self) -> None:
        PREFLIGHTS[0].validator()

    def test_failed_owner_blocks_only_proven_import_or_declared_edges(self) -> None:
        def fail() -> None:
            raise RuntimeError("schema incompatible")

        spec = PreflightSpec(
            cause_id="schema:fixture:v1",
            owner=OWNER,
            evidence_altitude="shared-fixture-schema",
            corrective_owner=OWNER,
            validator=fail,
        )
        import_reason = SelectionReason(
            SelectionReasonKind.IMPORT_CONSUMER,
            OWNER,
            "agents_remember.owner",
        )
        declared_reason = SelectionReason(
            SelectionReasonKind.DECLARED_CONSUMER,
            OWNER,
            "catalog",
        )
        heuristic = SelectionReason(
            SelectionReasonKind.NAME_HEURISTIC,
            OWNER,
            "owner",
        )
        impact = DependencyTestImpact(
            tests=(
                Path("mcp/tests/test_import.py"),
                Path("mcp/tests/test_declared.py"),
                Path("mcp/tests/test_owner.py"),
            ),
            ownership=(
                OwnedTest(Path("mcp/tests/test_import.py"), (import_reason,)),
                OwnedTest(Path("mcp/tests/test_declared.py"), (declared_reason,)),
                OwnedTest(Path("mcp/tests/test_owner.py"), (heuristic,)),
            ),
            complete=True,
            global_invalidation=False,
        )

        payload = evaluate_preflights((spec,), _Graph(impact), candidate=CANDIDATE)

        self.assertEqual(payload["schemaVersion"], CAUSAL_REPORT_SCHEMA)
        self.assertEqual(payload["status"], "failed")
        self.assertFalse(payload["acceptanceEligible"])
        self.assertEqual(payload["firstCausalFailure"], "schema:fixture:v1")
        blocked = cast(list[dict[str, object]], payload["blockedGroups"])
        self.assertEqual(
            {row["testPath"] for row in blocked},
            {"mcp/tests/test_import.py", "mcp/tests/test_declared.py"},
        )
        self.assertTrue(all(row["correctiveOwner"] == OWNER.as_posix() for row in blocked))

    def test_incomplete_ownership_never_becomes_blanket_suppression(self) -> None:
        def fail() -> None:
            raise RuntimeError("owner failed")

        spec = PreflightSpec("schema:fixture:v1", OWNER, "schema", OWNER, fail)
        fallback = SelectionReason(
            SelectionReasonKind.SAFE_FULL,
            OWNER,
            "ownership-incomplete",
        )
        impact = DependencyTestImpact(
            tests=(Path("mcp/tests/test_independent.py"),),
            ownership=(OwnedTest(Path("mcp/tests/test_independent.py"), (fallback,)),),
            complete=False,
            global_invalidation=True,
            fallback=fallback,
        )

        payload = evaluate_preflights((spec,), _Graph(impact), candidate=CANDIDATE)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["blockedGroups"], [])

    def test_machine_and_human_artifacts_render_from_one_payload(self) -> None:
        payload = evaluate_preflights(PREFLIGHTS, _Graph(_empty_impact()), candidate=CANDIDATE)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "causal-failures.json"
            write_causal_report(path, payload)

            self.assertEqual(load_causal_report(path), payload)
            rendered = path.with_suffix(".md").read_text(encoding="utf-8")

        self.assertIn(PREFLIGHTS[0].cause_id, rendered)
        self.assertIn(PREFLIGHTS[0].corrective_owner.as_posix(), rendered)
        self.assertIn("not granted by this artifact", rendered)

    def test_process_sensitive_failures_retain_exact_retry_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sensitive = root / "sensitive.py"
            sensitive.write_text("import asyncio\nimport socket\n", encoding="utf-8")
            deterministic = root / "deterministic.py"
            deterministic.write_text("import json\n", encoding="utf-8")

            sensitive_profile = execution_profile(sensitive)
            deterministic_profile = execution_profile(deterministic)

        self.assertEqual(sensitive_profile.failure_class, FailureClass.PROCESS_ENVIRONMENT)
        self.assertEqual(
            sensitive_profile.families,
            ("async-concurrency", "socket-service"),
        )
        self.assertEqual(deterministic_profile.failure_class, FailureClass.INDEPENDENT)

        record = runtime_failure_record(_RuntimeReport())
        self.assertEqual(record["workerId"], "gw3")
        self.assertEqual(record["randomOrderSeed"], "260824")
        self.assertEqual(record["durationSeconds"], 1.25)
        self.assertEqual(
            record["retrySemantics"],
            "repeat-exact-node-with-seed-worker-timing-and-process-topology",
        )


def _empty_impact() -> DependencyTestImpact:
    return DependencyTestImpact((), (), True, False)


if __name__ == "__main__":
    unittest.main()

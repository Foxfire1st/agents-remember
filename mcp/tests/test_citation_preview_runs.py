"""MIK-R213: previews share check runs, retain scope guards, and never write."""

from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

import test_citation_fix_documents as fixtures
import test_memory_quality_runs as quality_fixtures
from agents_remember.application import memory_tools
from agents_remember.application.memory_quality import citation_runs, controller, runs
from agents_remember.application.memory_scope import MemoryScope, MemoryScopeIdentity
from agents_remember.errors import MemoryCandidatePairError, MemoryCandidatePairFailure
from agents_remember.mcp.tools import memory
from agents_remember.models.memory import MemoryQualityPollRequest
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


class CitationPreviewRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(runs._registry.clear)
        self.scope = MemoryScope(
            repo_id="demo",
            identity=MemoryScopeIdentity(
                authority="official",
                authority_path="/memory",
                code_root="/code",
                onboarding_root="/memory/onboarding",
            ),
            code_root=Path("/code"),
            onboarding_root=Path("/memory/onboarding"),
            context=mock.Mock(),
        )
        self.enterContext(
            mock.patch.object(citation_runs, "_preview_scope", return_value=self.scope)
        )
        self.enterContext(
            mock.patch.object(
                citation_runs, "revalidate_memory_candidate_scope", return_value=self.scope
            )
        )
        self.enterContext(
            mock.patch.object(
                controller, "require_repo", return_value=SimpleNamespace(repo_id="demo")
            )
        )
        self.answers: list[dict[str, Any] | None] = []

        def payload(_name, whole, **kwargs):
            self.answers.append(kwargs.get("answer"))
            return whole

        self.enterContext(mock.patch.object(memory, "_tool_payload", side_effect=payload))

    def _start(self, **kwargs):
        return memory.citation_fix_payload(
            mock.Mock(), "demo", mode="start", dry_run=True, **kwargs
        )

    def _poll(self, run_id: str, **kwargs):
        return memory.citation_fix_payload(
            mock.Mock(), "demo", mode="poll", run_id=run_id, **kwargs
        )

    def _settled(self, run_id: str, **kwargs):
        deadline = time.monotonic() + HANG_GUARD_SECONDS
        while time.monotonic() < deadline:
            result = self._poll(run_id, **kwargs)
            if result["status"] != "running":
                return result
            time.sleep(0.01)
        self.fail("background preview did not settle")

    def test_completed_preview_equals_sync_and_leaves_memory_bytes_unchanged(self) -> None:
        fixture = fixtures.ConvertedListFixTests()
        with TemporaryDirectory() as raw:
            code, root = fixture._stale_converted_tree(Path(raw))
            leaf = SimpleNamespace(
                repo_id="demo", onboarding_root=root / "onboarding", code_root=code.root
            )
            before = fixture._tree_bytes(root)
            selectors = memory_tools.CitationOperationScope(
                documents=(f"{fixtures.APP}.md", "missing.md")
            )
            with (
                mock.patch.object(memory_tools, "_leaf_memory_writer_scope", return_value=leaf),
                mock.patch.object(controller, "_poll_scope_mismatch", return_value=None),
            ):
                synchronous = memory.citation_fix_payload(
                    mock.Mock(),
                    "demo",
                    contract_path="contract.md",
                    operation_scope=selectors,
                    dry_run=True,
                )
                synchronous_answer = self.answers[-1]
                # Pair guards are proved separately; this fixture keeps the real preview computation.
                started = self._start(operation_scope=selectors, contract_path="contract.md")
                completed = self._settled(started["runId"], contract_path="contract.md")
                completed_answer = self.answers[-1]
                assert completed_answer is not None
                self.assertEqual(
                    {k: v for k, v in completed_answer.items() if k not in {"status", "runId"}},
                    synchronous_answer,
                )
            self.assertEqual(
                {k: v for k, v in completed.items() if k not in {"status", "runId"}},
                {k: v for k, v in synchronous.items() if k not in {"status", "runId"}},
            )
            self.assertEqual(fixture._tree_bytes(root), before)
            self.assertFalse(completed["ok"])
            self.assertGreater(completed["refusedCount"], 0)
            self.assertEqual(
                memory._citation_fix_answer(completed), memory._citation_fix_answer(synchronous)
            )

    def test_shared_capacity_identity_kind_and_responsive_poll(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            self.assertTrue(release.wait(HANG_GUARD_SECONDS))
            return {"ok": True, "nested": {"rows": ["original"]}}

        self.addCleanup(release.set)
        with (
            mock.patch.object(memory, "_citation_fix_whole", side_effect=blocked),
            mock.patch.object(runs, "MAX_QUALITY_RUNS", 2),
        ):
            first = self._start(
                operation_scope=memory_tools.CitationOperationScope(document="a.md")
            )
            self.assertTrue(entered.wait(HANG_GUARD_SECONDS))
            duplicate = self._start(
                operation_scope=memory_tools.CitationOperationScope(document="a.md")
            )
            self.assertEqual(duplicate["runId"], first["runId"])
            # Poll must answer while preview work is still blocked; the shared bound
            # only guards a deadlock, not a machine-dependent response time.
            with ThreadPoolExecutor(max_workers=1) as pool:
                poll = pool.submit(self._poll, first["runId"])
                try:
                    self.assertEqual(poll.result(HANG_GUARD_SECONDS)["status"], "running")
                    self.assertFalse(release.is_set())
                finally:
                    if not poll.done():
                        release.set()
            snapshot = runs.poll_quality_run("demo", first["runId"])
            assert snapshot is not None
            check_identity = replace(
                snapshot.identity, operation="memory_quality_check", document=None
            )
            admitted = runs.start_quality_run(check_identity, blocked)
            self.assertEqual(admitted.state, "started")
            assert admitted.run_id is not None
            refused = self._start(
                operation_scope=memory_tools.CitationOperationScope(document="b.md")
            )
            self.assertEqual(refused["status"], "capacity-reached")
            request = MemoryQualityPollRequest(mode="poll", repo_id="demo", run_id=first["runId"])
            self.assertEqual(
                controller._poll_memory_quality_request(mock.Mock(), request)["status"],
                "run-not-found",
            )
            self.assertEqual(self._poll(admitted.run_id)["status"], "run-not-found")
            release.set()
            settled = self._settled(first["runId"])
            settled["nested"]["rows"].append("mutated")
            self.assertEqual(self._poll(first["runId"])["nested"]["rows"], ["original"])

    def test_shared_refusals_failure_and_scope_change(self) -> None:
        self.assertEqual(self._poll("unknown")["status"], "run-not-found")
        with mock.patch.object(
            memory, "_citation_fix_whole", side_effect=RuntimeError("fixture failure")
        ):
            started = self._start()
            failed = self._settled(started["runId"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("fixture failure", failed["error"])
        self.assertEqual(
            self._poll(started["runId"], contract_path="other")["code"],
            "memory-candidate-pair-poll-scope-mismatch",
        )
        with mock.patch.object(
            citation_runs,
            "_preview_scope",
            side_effect=MemoryCandidatePairError(
                "memory-candidate-pair-stale",
                "changed",
                failure=MemoryCandidatePairFailure(
                    field="pairIdentity", contract_path="/contract", next_action="worktree_sync"
                ),
            ),
        ):
            refused = self._start()
        self.assertEqual(refused["status"], "scope-refused")
        self.assertEqual(refused["code"], "memory-candidate-pair-stale")

    def test_candidate_scope_refusal_is_shared_before_and_after_execution(self) -> None:
        pair = quality_fixtures._pair()
        identity = runs.QualityRunIdentity(
            repo_id="demo",
            scope=replace(self.scope.identity, pair_identity=pair),
            checks=(),
            detail_limit=0,
            publish_curator_report=False,
            operation="citation_fix",
        )
        with mock.patch.object(
            controller,
            "resolve_memory_candidate_scope",
            side_effect=MemoryCandidatePairError(
                "memory-candidate-pair-stale",
                "changed",
                failure=MemoryCandidatePairFailure(
                    field="pairIdentity", contract_path="/contract", next_action="worktree_sync"
                ),
            ),
        ):
            admission = runs.start_quality_run(identity, lambda: {"ok": True})
            assert admission.run_id is not None
            refusal = self._poll(admission.run_id, contract_path=pair.contractPath)
        self.assertEqual(refusal["status"], "scope-refused")
        self.assertEqual(refusal["code"], "memory-candidate-pair-stale")
        scoped = {
            "ok": False,
            "status": "scope-refused",
            "code": "memory-candidate-pair-stale",
            "operation": "citation_fix",
        }
        with mock.patch.object(memory, "_citation_fix_whole", return_value=scoped):
            admission = self._start()
            refusal = self._settled(admission["runId"])
        self.assertEqual(refusal, {**scoped, "runId": admission["runId"]})

    def test_background_writes_and_ambiguous_arguments_refused(self) -> None:
        with mock.patch.object(memory, "_citation_fix_whole") as execute:
            cases: tuple[dict[str, Any], ...] = (
                {"mode": "start"},
                {"mode": "start", "dry_run": True, "run_id": "run"},
                {"mode": "poll"},
                {
                    "mode": "poll",
                    "run_id": "run",
                    "operation_scope": memory_tools.CitationOperationScope(document="a.md"),
                },
                {"run_id": "run"},
            )
            for arguments in cases:
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    memory.citation_fix_payload(mock.Mock(), "demo", **arguments)
            execute.assert_not_called()

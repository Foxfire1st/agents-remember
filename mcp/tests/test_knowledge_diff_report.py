"""``knowledge_diff`` writes its comparison as a report (MIK-R109 rule 3).

Through the registered payload builder, on hermetic converted git repos:
the answer holds the two trees compared, the count of changes by kind, the
changed records in decision order up to the list bound, and the report's
location; the report holds what the answer held before (every changed file
with its patch). The comparison behind both is the reviewer's unchanged
computation. A report that cannot be written refuses the call with the
reason, leaving nothing partial behind.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.mcp.tools.knowledge import DiffToolRequest, knowledge_diff_payload
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _repo(root: Path, name: str) -> Path:
    repo = root / name
    (repo / "knowledge").mkdir(parents=True)
    (repo / "knowledge" / "layout.json").write_text('{"version": 1}\n', encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "diff-report@example.invalid")
    _git(repo, "config", "user.name", "Diff Report")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _record(repo: Path, stem: str, text: str) -> None:
    records = repo / "knowledge" / "invariants"
    records.mkdir(parents=True, exist_ok=True)
    (records / f"{stem}.json").write_text(text, encoding="utf-8")


class KnowledgeDiffReportTests(unittest.TestCase):
    maxDiff = None

    def test_compared_names_trees_counts_records_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = _repo(root, "memory")
            _record(
                repo,
                "INV-TSTA01-first",
                '{"schema": "ar-invariant/v1", "id": "INV-TSTA01", "statement": "first"}\n',
            )
            _record(
                repo,
                "INV-TSTB02-second",
                '{"schema": "ar-invariant/v1", "id": "INV-TSTB02", "statement": "second"}\n',
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "two records")
            _record(
                repo,
                "INV-TSTA01-first",
                '{"schema": "ar-invariant/v1", "id": "INV-TSTA01", "statement": "first, revised"}\n',
            )
            (repo / "knowledge" / "invariants" / "INV-TSTB02-second.json").unlink()
            _record(
                repo,
                "INV-TSTC03-third",
                '{"schema": "ar-invariant/v1", "id": "INV-TSTC03", "statement": "third"}\n',
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "change")

            out = knowledge_diff_payload(
                DiffToolRequest(memory_root=str(repo), before="HEAD~1", after="HEAD"),
                coordination_root=str(root),
            )
            TOOL_RESPONSE_MODELS["knowledge_diff"].model_validate(out)
            self.assertEqual(out["state"], "compared", out)
            self.assertEqual(out["beforeRevision"], "HEAD~1")
            self.assertEqual(out["afterRevision"], "HEAD")
            self.assertTrue(out["beforeTree"] and out["afterTree"])
            self.assertNotEqual(out["beforeTree"], out["afterTree"])
            counts = out["counts"]
            self.assertEqual(counts["changedFiles"], 3, counts)
            self.assertEqual(counts["records"], 3, counts)
            self.assertEqual(counts["added"], 1, counts)
            self.assertEqual(counts["deleted"], 1, counts)
            self.assertEqual(counts["modified"], 1, counts)
            changes = {entry["recordId"]: entry["change"] for entry in out["decisionRecords"]}
            self.assertEqual(
                changes,
                {
                    "INV-TSTA01": "modified",
                    "INV-TSTB02": "deleted",
                    "INV-TSTC03": "added",
                },
                out,
            )
            self.assertEqual(out["decisionRecordCount"], 3)
            for entry in out["decisionRecords"]:
                with self.subTest(record=entry["recordId"]):
                    # Decision paths are memory-root-relative (joined with
                    # the answered memoryRoot); the join opens live records,
                    # and stays absent for deleted ones.
                    found = Path(str(out["memoryRoot"])) / str(entry["path"])
                    if entry["change"] == "deleted":
                        self.assertFalse(found.is_file(), entry)
                    else:
                        self.assertTrue(found.is_file(), entry)
            whole = json.dumps(out)
            self.assertNotIn("first, revised", whole)
            report = Path(str(out["report"]))
            self.assertTrue(report.is_file(), out)
            reported = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(reported["changedFiles"], 3, reported)
            self.assertEqual(len(reported["records"]), 3, reported)
            patches = [
                change["patch"] for group in reported["records"] for change in group["files"]
            ]
            self.assertTrue(any("first, revised" in patch for patch in patches), reported)

    def test_decision_order_puts_added_and_deleted_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = _repo(root, "memory")
            _record(
                repo,
                "INV-AAAM01-v1",
                '{"schema": "ar-invariant/v1", "id": "INV-AAAM01", "statement": "v1"}\n',
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "one record")
            _record(
                repo,
                "INV-AAAM01-v1",
                '{"schema": "ar-invariant/v1", "id": "INV-AAAM01", "statement": "v2"}\n',
            )
            _record(
                repo,
                "INV-ZZZA02-new",
                '{"schema": "ar-invariant/v1", "id": "INV-ZZZA02", "statement": "new"}\n',
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "change")

            out = knowledge_diff_payload(
                DiffToolRequest(memory_root=str(repo), before="HEAD~1", after="HEAD"),
                coordination_root=str(root),
            )
            self.assertEqual(out["state"], "compared", out)
            self.assertEqual(
                [entry["recordId"] for entry in out["decisionRecords"]],
                ["INV-ZZZA02", "INV-AAAM01"],
                out,
            )

    def test_an_empty_comparison_answers_zero_with_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = _repo(root, "memory")
            out = knowledge_diff_payload(
                DiffToolRequest(memory_root=str(repo), before="HEAD", after="HEAD"),
                coordination_root=str(root),
            )
            self.assertEqual(out["state"], "compared", out)
            self.assertEqual(out["counts"]["changedFiles"], 0, out)
            self.assertEqual(out["decisionRecords"], [], out)
            self.assertEqual(out["decisionRecordCount"], 0, out)
            self.assertTrue(Path(str(out["report"])).is_file(), out)

    def test_an_unwritable_report_refuses_leaving_nothing_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = _repo(root, "memory")
            _record(
                repo,
                "INV-TSTA01-first",
                '{"schema": "ar-invariant/v1", "id": "INV-TSTA01"}\n',
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "change")
            out = knowledge_diff_payload(
                DiffToolRequest(memory_root=str(repo), before="HEAD~1", after="HEAD"),
                coordination_root=None,
            )
            self.assertEqual(out["state"], "refused", out)
            self.assertEqual(out["refusalCode"], "report-unwritable", out)
            self.assertIn("nothing partial was left behind", out["refusalDetail"])
            leftovers = [
                path for path in root.rglob("*") if path.is_file() and path.suffix == ".part"
            ]
            self.assertEqual(leftovers, [])

    def test_a_failed_replace_leaves_no_partial_and_keeps_the_prior_report(
        self,
    ) -> None:
        from unittest import mock  # noqa: PLC0415

        from agents_remember.application import knowledge_reports  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            first = knowledge_reports.write_report(
                "knowledge-diff",
                {"operation": "knowledge_diff", "rows": [1]},
                coordination_root=root,
            )
            self.assertTrue(first.is_file())
            first_bytes = first.read_bytes()
            with (
                mock.patch.object(knowledge_reports.os, "replace", side_effect=OSError("denied")),
                self.assertRaises(knowledge_reports.ReportWriteError) as raised,
            ):
                knowledge_reports.write_report(
                    "knowledge-diff",
                    {"operation": "knowledge_diff", "rows": [2]},
                    coordination_root=root,
                )
            self.assertIsNone(raised.exception.residual)
            # Byte equality: the pre-existing final artifact is preserved
            # exactly, not merely present.
            self.assertEqual(first.read_bytes(), first_bytes)
            leftovers = [
                path for path in root.rglob("*") if path.is_file() and path.suffix == ".part"
            ]
            self.assertEqual(leftovers, [])

    def test_a_mid_stream_write_failure_cleans_the_partial_and_keeps_the_prior(
        self,
    ) -> None:
        from unittest import mock  # noqa: PLC0415

        from agents_remember.application import knowledge_reports  # noqa: PLC0415

        class _FailingHandle:
            """A write handle that lands partial bytes, then fails the disk."""

            def __init__(self, handle) -> None:
                self._handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return self._handle.__exit__(*exc)

            def write(self, text: str):
                self._handle.write(text[:5])
                raise OSError("injected disk-full mid-stream")

        real_open = Path.open

        def _open(self, mode="r", *args, **kwargs):
            handle = real_open(self, mode, *args, **kwargs)
            if "w" in str(mode) and self.suffix == ".part":
                return _FailingHandle(handle)
            return handle

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            first = knowledge_reports.write_report(
                "knowledge-diff",
                {"operation": "knowledge_diff", "rows": [1]},
                coordination_root=root,
            )
            first_bytes = first.read_bytes()
            with (
                mock.patch.object(Path, "open", _open),
                self.assertRaises(knowledge_reports.ReportWriteError) as raised,
            ):
                knowledge_reports.write_report(
                    "knowledge-diff",
                    {"operation": "knowledge_diff", "rows": [2]},
                    coordination_root=root,
                )
            # Supported failure: no partial file remains, and the
            # pre-existing final artifact is byte-identical.
            self.assertIsNone(raised.exception.residual)
            leftovers = [
                path for path in root.rglob("*") if path.is_file() and path.suffix == ".part"
            ]
            self.assertEqual(leftovers, [])
            self.assertEqual(first.read_bytes(), first_bytes)

    def test_a_failed_cleanup_names_the_residual(self) -> None:
        from unittest import mock  # noqa: PLC0415

        from agents_remember.application import knowledge_reports  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            with (
                mock.patch.object(knowledge_reports.os, "replace", side_effect=OSError("denied")),
                mock.patch.object(Path, "unlink", side_effect=OSError("locked")),
                self.assertRaises(knowledge_reports.ReportWriteError) as raised,
            ):
                knowledge_reports.write_report(
                    "knowledge-diff",
                    {"operation": "knowledge_diff", "rows": [2]},
                    coordination_root=root,
                )
            self.assertIsNotNone(raised.exception.residual)
            self.assertIn("remains at", raised.exception.reason)
            self.assertNotIn("nothing partial was left behind", raised.exception.reason)
            residual = Path(str(raised.exception.residual))
            self.assertTrue(residual.is_file(), "effect readback: the residual is real")
            # Architect ruling 941a9521: a residual never stands under a final
            # report's name — it is always the beside-the-final ".part" file.
            self.assertEqual(residual.suffix, ".part")
            final = residual.with_name(residual.name[: -len(".part")])
            self.assertNotEqual(residual, final)
            self.assertFalse(final.is_file(), "no partial stands at the final name")

    def test_failed_cleanup_carry_through_names_the_residual_to_the_caller(
        self,
    ) -> None:
        from unittest import mock  # noqa: PLC0415

        from agents_remember.application import knowledge_reports  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = _repo(root, "memory")
            _record(
                repo,
                "INV-TSTA01-first",
                '{"schema": "ar-invariant/v1", "id": "INV-TSTA01"}\n',
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "change")
            with (
                mock.patch.object(knowledge_reports.os, "replace", side_effect=OSError("denied")),
                mock.patch.object(Path, "unlink", side_effect=OSError("locked")),
            ):
                out = knowledge_diff_payload(
                    DiffToolRequest(memory_root=str(repo), before="HEAD~1", after="HEAD"),
                    coordination_root=str(root),
                )
            # Carry-through: the caller learns cleanup impossibility (the
            # residual path and its removal), not an absence assertion.
            self.assertEqual(out["state"], "refused", out)
            self.assertEqual(out["refusalCode"], "report-unwritable", out)
            self.assertIn("remains at", out["refusalDetail"], out)
            self.assertIn("remove it before retrying", out["refusalDetail"], out)
            self.assertNotIn("nothing partial was left behind", out["refusalDetail"], out)

    def test_reports_are_admitted_at_two_sizes_and_refused_over_budget(self) -> None:
        from agents_remember.application import knowledge_reports  # noqa: PLC0415
        from agents_remember.kernel.primitives.runtime_config import (  # noqa: PLC0415
            McpRuntimeConfig,
            ToolLogSettings,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            small = {"operation": "knowledge_diff", "rows": [{"id": "INV-1"}]}
            large = {
                "operation": "knowledge_diff",
                "rows": [{"id": f"INV-{index:05d}", "patch": "x" * 200} for index in range(200)],
            }
            small_path = knowledge_reports.write_report(
                "knowledge-diff", small, coordination_root=root
            )
            large_path = knowledge_reports.write_report(
                "knowledge-diff", large, coordination_root=root
            )
            small_size = small_path.stat().st_size
            large_size = large_path.stat().st_size
            self.assertGreater(large_size, small_size * 10)
            # A budget between the two admits the small report and refuses
            # the large one before any file is created -- never truncated.
            # The budgeted writes go to a fresh directory, so usage starts at zero.
            budgeted = root / "budgeted"
            budgeted.mkdir()
            budget = small_size + (large_size - small_size) // 2
            config = McpRuntimeConfig(
                config_path=budgeted / "settings.json",
                coordination_root=budgeted,
                workspace_root=budgeted,
                transcript_root=budgeted / "logs" / "mcp",
                tool_log=ToolLogSettings(max_bytes=budget),
            )
            admitted = knowledge_reports.write_report("knowledge-diff", small, config=config)
            self.assertTrue(admitted.is_file())
            before = {path.name for path in admitted.parent.iterdir()}
            with self.assertRaises(knowledge_reports.ReportWriteError) as raised:
                knowledge_reports.write_report("knowledge-diff", large, config=config)
            self.assertIn("budget", raised.exception.reason)
            after = {path.name for path in admitted.parent.iterdir()}
            self.assertEqual(before, after, "an over-budget report creates no file")

    def test_zero_input_writes_a_whole_small_report(self) -> None:
        from agents_remember.application import knowledge_reports  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = knowledge_reports.write_report("knowledge-diff", {}, coordination_root=root)
            self.assertTrue(path.is_file(), path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {})


class ReportResourceTests(unittest.TestCase):
    """Bounded operation growth and reclamation at two sizes (MIK-R109 rule 3).

    Round-3 F15: reports are admitted whole or refused with the reason;
    a refused report leaves nothing behind so a smaller one still
    succeeds (reclamation); growth across corpus sizes stays bounded;
    live-enclosure reports land under the enclosure with its budget.
    """

    def _corpus(self, root: Path, count: int) -> Path:
        from agents_remember.models.knowledge_files import (  # noqa: PLC0415
            derived_record_id,
        )
        from knowledge_index_test_support import (  # noqa: PLC0415
            write_document,
        )

        repo = _repo(root, f"memory-{count}")
        for index in range(count):
            inv = derived_record_id("invariant", f"resource-{index}")
            write_document(
                repo,
                f"knowledge/invariants/{inv}-item.json",
                {
                    "schema": "ar-invariant/v1",
                    "id": inv,
                    "revision": 1,
                    "status": "accepted",
                    "statement": f"Resource item {index} holds.",
                    "applicability": f"Every resource {index}.",
                    "conditions": [],
                    "exclusions": [],
                    "supersedes": [],
                    "admission": "legacy-unassessed",
                    "origin": {
                        "task": "260928-MIK",
                        "leaf": "260928-MIK-L109",
                        "legacyId": f"legacy-resource-{index}",
                    },
                },
            )
        write_document(
            repo,
            "knowledge/layout.json",
            {"schema": "ar-memory-layout/v2", "conversion": "fixture"},
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "resource corpus")
        return repo

    def _diff(self, repo: Path, root: Path) -> dict:
        out = knowledge_diff_payload(
            DiffToolRequest(memory_root=str(repo), before="HEAD~1", after="HEAD"),
            coordination_root=str(root),
        )
        self.assertEqual(out["state"], "compared", out)
        return out

    def test_repeated_comparison_is_stable_and_reclaims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = self._corpus(root, 10)
            first = self._diff(repo, root)
            second = self._diff(repo, root)
            first_report = Path(str(first["report"]))
            second_report = Path(str(second["report"]))
            self.assertTrue(first_report.is_file())
            self.assertTrue(second_report.is_file())
            # Idempotent content: the repeated comparison reproduces the
            # report bytes, and no partial file remains behind either write.
            self.assertEqual(
                first_report.read_bytes(),
                second_report.read_bytes(),
            )
            leftovers = [
                path for path in root.rglob("*") if path.is_file() and path.suffix == ".part"
            ]
            self.assertEqual(leftovers, [])

    def test_growth_across_two_sizes_stays_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            small_repo = self._corpus(root, 3)
            large_repo = self._corpus(root, 30)
            small_report = Path(str(self._diff(small_repo, root)["report"]))
            large_report = Path(str(self._diff(large_repo, root)["report"]))
            small_size = small_report.stat().st_size
            large_size = large_report.stat().st_size
            # Ten times the records: bounded, roughly linear growth -- well
            # under a quadratic blowup, and both admitted whole.
            self.assertLess(large_size, small_size * 20)
            self.assertGreater(large_size, small_size)

    def test_unrelated_corpus_reads_stay_small(self) -> None:
        from agents_remember.mcp.tools.knowledge import (  # noqa: PLC0415
            ReadToolRequest,
            knowledge_read_payload,
        )
        from agents_remember.models.tokens import (  # noqa: PLC0415
            count_response_tokens,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            # An unrelated small corpus: reads against it stay small no
            # matter what the compared corpus holds (per-answer caches do
            # not leak across calls).
            other = self._corpus(root, 2)
            read = knowledge_read_payload(
                ReadToolRequest(memory_root=str(other), view="invariant"),
                coordination_root=str(root),
            )
            self.assertEqual(read["state"], "view", read)
            self.assertEqual(len(read["payload"]["rows"]), 2, read)
            self.assertLessEqual(count_response_tokens(read), 4000)

    def test_sidecar_walks_complete_with_bounded_operation_growth(self) -> None:
        import hashlib  # noqa: PLC0415

        from agents_remember.mcp.tools.knowledge import (  # noqa: PLC0415
            ReadToolRequest,
            knowledge_read_payload,
        )
        from agents_remember.models.knowledge_files import (  # noqa: PLC0415
            derived_record_id,
        )
        from agents_remember.models.tokens import (  # noqa: PLC0415
            count_response_tokens,
        )
        from knowledge_index_test_support import (  # noqa: PLC0415
            write_document,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            # One sidecar with 25 realizations: the multi-page walk
            # completes with bounded operation growth (every entry once,
            # total tokens within pages times threshold).
            repo = _repo(root, "memory-walk")
            write_document(
                repo,
                "knowledge/layout.json",
                {"schema": "ar-memory-layout/v2", "conversion": "fixture"},
            )
            code = root / "code-walk"
            code.mkdir()
            (code / "batch.py").write_text("x = 1\n", encoding="utf-8")
            _git(code, "init", "-q")
            _git(code, "config", "user.email", "walk@example.invalid")
            _git(code, "config", "user.name", "Walk")
            _git(code, "add", "-A")
            _git(code, "commit", "-qm", "batch")
            blob = _git(code, "hash-object", "batch.py")
            digest = "sha256:" + hashlib.sha256((code / "batch.py").read_bytes()).hexdigest()
            anchor = {"locator": {"kind": "file"}, "blob": blob, "content": digest}
            realizes = []
            padding = " walk padding" * 400
            for index in range(25):
                inv = derived_record_id("invariant", f"walk-{index}")
                (repo / "knowledge" / "invariants").mkdir(parents=True, exist_ok=True)
                (repo / "knowledge" / "invariants" / f"{inv}-item.json").write_text(
                    '{"schema": "ar-invariant/v1", '
                    f'"id": "{inv}", "revision": 1, "status": "accepted", '
                    f'"statement": "Walk item {index} holds.", '
                    f'"applicability": "Every walk {index}.", "conditions": [], '
                    '"exclusions": [], "supersedes": [], '
                    '"admission": "legacy-unassessed", "origin": {"task": "260928-MIK", '
                    '"leaf": "260928-MIK-L109", '
                    f'"legacyId": "legacy-walk-{index}"' + "}}\n",
                    encoding="utf-8",
                )
                realizes.append(
                    {
                        "id": f"RLZ-W{index:05d}",
                        "invariant": inv,
                        "anchor": dict(anchor),
                        "role": "primary-authority",
                        "rationale": f"Walk item {index} lands.{padding}",
                    }
                )
            onb = repo / "onboarding"
            (onb / "pkg").mkdir(parents=True, exist_ok=True)
            (onb / "pkg" / "batch.py.md").write_text("# Batch\n", encoding="utf-8")
            (onb / "pkg" / "batch.py.json").write_text(
                json.dumps(
                    {
                        "schema": "ar-onboarding-file/v1",
                        "path": "pkg/batch.py",
                        "references": {},
                        "realizes": realizes,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "walk corpus")
            pages = []
            response = knowledge_read_payload(
                ReadToolRequest(
                    memory_root=str(repo),
                    view="source_context",
                    source_path="pkg/batch.py",
                ),
                coordination_root=str(root),
            )
            while True:
                self.assertEqual(response["state"], "page", response)
                pages.append(response)
                continuation = response.get("continuation")
                if continuation is None:
                    break
                response = knowledge_read_payload(
                    ReadToolRequest(
                        memory_root=str(repo),
                        view="source_context",
                        source_path="pkg/batch.py",
                        continuation=continuation,
                    ),
                    coordination_root=str(root),
                )
            names = [
                str(row["id"])
                for page in pages
                for row in page["payload"]["rows"]
                if isinstance(row, dict) and row.get("kind") == "realization"
            ]
            self.assertGreaterEqual(len(pages), 2, "the walk never paged")
            self.assertEqual(len(names), 25, names)
            self.assertEqual(len(set(names)), 25, names)
            total_tokens = sum(count_response_tokens(page) for page in pages)
            self.assertLessEqual(total_tokens, len(pages) * 8000)

    def test_live_enclosure_reports_land_under_the_enclosure(self) -> None:
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world = EntryPointWorld()
        try:
            world.build()
            with tempfile.TemporaryDirectory() as tmp_dir:
                repo = self._corpus(Path(tmp_dir), 3)
                outcome, out = world.call(
                    "knowledge_diff",
                    {
                        "memoryRoot": str(repo),
                        "before": "HEAD~1",
                        "after": "HEAD",
                    },
                )
                self.assertEqual(outcome, "RETURNED", out)
                assert isinstance(out, dict)
                self.assertEqual(out["state"], "compared", out)
                report = Path(str(out["report"]))
                # The live-enclosure policy: ambient leaf calls write the
                # caller-readable report under the enclosure reports,
                # whole and parseable.
                self.assertTrue(report.is_file(), out)
                self.assertIn("reports", report.parts, out)
                parsed = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(parsed["operation"], "knowledge_diff")
        finally:
            world.close()

    def test_declaration_caches_parse_each_sidecar_and_file_once(self) -> None:
        from agents_remember.application.knowledge_names import (  # noqa: PLC0415
            NameResolver,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            realizes = [
                {
                    "id": f"RLZ-C{i:05d}",
                    "invariant": f"INV-C{i:05d}",
                    "anchor": {"kind": "file", "path": "pkg/batch.py"},
                    "role": "primary-authority",
                    "rationale": f"Cache item {i} lands.",
                }
                for i in range(25)
            ]
            (root / "pkg").mkdir(parents=True, exist_ok=True)
            (root / "pkg" / "batch.py.json").write_text(
                json.dumps(
                    {
                        "schema": "ar-onboarding-file/v1",
                        "path": "pkg/batch.py",
                        "references": {},
                        "realizes": realizes,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
            (root / "pkg" / "batch.py.md").write_text(
                "# Batch\n\n## First\n\n- first [1]\n", encoding="utf-8"
            )
            resolver = NameResolver(
                memory_root=root,
                index_path=root / "unused-index",
                tree_key="cache-probe",
            )
            for entry in realizes:
                self.assertTrue(
                    resolver.declares_entry("pkg/batch.py.json", entry["id"]),
                    entry["id"],
                )
            lines = {
                entry["id"]: resolver.line_of("pkg/batch.py.json", entry["id"])
                for entry in realizes
            }
            for entry_id, line in lines.items():
                self.assertIsNotNone(line, entry_id)
                assert line is not None
                self.assertGreater(line, 1, entry_id)
            # Twenty-five declaration checks against one sidecar parse it
            # once; every line lookup splits the file once.
            self.assertEqual(resolver.sidecar_parses, 1, resolver.sidecar_parses)
            self.assertEqual(resolver.file_reads, 1, resolver.file_reads)

    def test_locator_line_work_grows_with_the_file_not_per_mark_scans(self) -> None:
        """Retained R5 probe: per-file batched declaration-line indexing.

        The Reviewer's ``L109-R5-locator-work-probe-source.txt`` shape kept
        verbatim (same sidecars, sizes, counting, 20x contrast): every
        requested mark's ``line_of`` used to scan from line 1, so 30 entries
        cost 97.75x the 3-entry line visits. The file's declaration lines
        now index once per file; later marks are map lookups. Declaration
        visits AND line visits are both recorded at both sizes.
        """

        from unittest import mock  # noqa: PLC0415

        from agents_remember.application import knowledge_names  # noqa: PLC0415
        from agents_remember.application.knowledge_names import (  # noqa: PLC0415
            NameResolver,
        )

        original_declares = knowledge_names._declares_in
        measured = []

        class CountedLines(list):
            def __init__(self, values, visits):
                super().__init__(values)
                self.visits = visits

            def __iter__(self):
                for value in super().__iter__():
                    self.visits[0] += 1
                    yield value

        for size in (3, 30):
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                entries = [
                    {
                        "id": f"RLZ-P{index:05d}",
                        "invariant": f"INV-P{index:05d}",
                        "anchor": {
                            "locator": {"kind": "file"},
                            "blob": "a" * 40,
                            "content": "sha256:" + "b" * 64,
                        },
                        "role": "primary-authority",
                        "rationale": "Prepared locator work count, not a live-session receipt.",
                    }
                    for index in range(size)
                ]
                (root / "batch.json").write_text(
                    json.dumps(
                        {
                            "schema": "ar-onboarding-file/v1",
                            "path": "pkg/batch.py",
                            "references": {},
                            "realizes": entries,
                        },
                        indent=1,
                    ),
                    encoding="utf-8",
                )
                resolver = NameResolver(
                    memory_root=root,
                    index_path=root / "unused-index",
                    tree_key="prepared-locator-work-counter",
                )
                first_lines = resolver.file_lines("batch.json")
                self.assertIsNotNone(first_lines)
                assert first_lines is not None
                declaration_visits = [0]

                def _count_declaration(
                    value: object, entry_id: str, _visits: list[int] = declaration_visits
                ) -> bool:
                    _visits[0] += 1
                    return original_declares(value, entry_id)

                line_visits = [0]
                counted = CountedLines(first_lines, line_visits)
                with (
                    mock.patch.object(
                        knowledge_names, "_declares_in", side_effect=_count_declaration
                    ),
                    mock.patch.object(NameResolver, "file_lines", return_value=counted),
                ):
                    for entry in entries:
                        self.assertTrue(
                            resolver.declares_entry("batch.json", entry["id"]), entry["id"]
                        )
                        number = resolver.line_of("batch.json", entry["id"])
                        self.assertIsNotNone(number, entry["id"])
                        assert number is not None
                        self.assertIn('"id"', first_lines[number - 1], entry["id"])
                        self.assertIn(entry["id"], first_lines[number - 1], entry["id"])
                measured.append(
                    {
                        "entries": size,
                        "fileLines": len(first_lines),
                        "declarationVisits": declaration_visits[0],
                        "actualLineVisits": line_visits[0],
                        "sidecarParses": resolver.sidecar_parses,
                        "fileReads": resolver.file_reads,
                    }
                )
        self.assertEqual(measured[0]["sidecarParses"], 1, measured)
        self.assertEqual(measured[0]["fileReads"], 1, measured)
        self.assertEqual(measured[1]["sidecarParses"], 1, measured)
        self.assertEqual(measured[1]["fileReads"], 1, measured)
        # Declaration membership is one indexed walk per sidecar: a handful
        # of visits for 3 entries, growing with the document -- far under
        # the 20x contrast the recursive per-entry walks showed.
        self.assertGreater(measured[0]["declarationVisits"], 0, measured)
        self.assertLess(
            measured[1]["declarationVisits"],
            measured[0]["declarationVisits"] * 20,
            measured,
        )
        # Ten times the entries: one indexing pass per file, so line work
        # grows with the file -- well under the 20x contrast the repeated
        # per-mark scans showed (60/5865, 97.75x).
        self.assertLess(
            measured[1]["actualLineVisits"], measured[0]["actualLineVisits"] * 20, measured
        )
        print(f"F15 LOCATOR WORK COUNTS {json.dumps(measured)}")

    def test_declaration_work_grows_with_the_document_not_its_square(self) -> None:
        from unittest import mock  # noqa: PLC0415

        from agents_remember.application import knowledge_names  # noqa: PLC0415
        from agents_remember.application.knowledge_names import (  # noqa: PLC0415
            NameResolver,
        )

        measured = []
        original = knowledge_names._declares_in
        for size in (3, 30):
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                entries = [
                    {
                        "id": f"RLZ-P{index:05d}",
                        "invariant": f"INV-P{index:05d}",
                        "anchor": {
                            "locator": {"kind": "file"},
                            "blob": "a" * 40,
                            "content": "sha256:" + "b" * 64,
                        },
                        "role": "primary-authority",
                        "rationale": "Prepared declaration counter.",
                    }
                    for index in range(size)
                ]
                (root / "batch.json").write_text(
                    json.dumps(
                        {
                            "schema": "ar-onboarding-file/v1",
                            "path": "pkg/batch.py",
                            "references": {},
                            "realizes": entries,
                        }
                    ),
                    encoding="utf-8",
                )
                resolver = NameResolver(
                    memory_root=root,
                    index_path=root / "unused-index",
                    tree_key="prepared-operation-counter",
                )
                visits = [0]

                def _count(value: object, entry_id: str, _visits: list[int] = visits) -> bool:
                    _visits[0] += 1
                    return original(value, entry_id)

                with mock.patch.object(knowledge_names, "_declares_in", side_effect=_count):
                    for entry in entries:
                        self.assertTrue(resolver.declares_entry("batch.json", entry["id"]))
                self.assertEqual(resolver.sidecar_parses, 1)
                measured.append({"entries": size, "visits": visits[0]})
        self.assertGreater(measured[0]["visits"], 0, measured)
        # Ten times the entries: the indexed document is walked once, so
        # declaration work grows with the document -- well under the
        # 20x contrast the repeated per-entry scans showed.
        self.assertLess(measured[1]["visits"], measured[0]["visits"] * 20, measured)

    def test_enclosure_family_charges_reports_beside_logs(self) -> None:
        from unittest import mock  # noqa: PLC0415

        from agents_remember.application import knowledge_reports  # noqa: PLC0415
        from agents_remember.kernel.primitives.runtime_config import (  # noqa: PLC0415
            McpRuntimeConfig,
            ToolLogSettings,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            budget = 500
            config = McpRuntimeConfig(
                config_path=root / "settings.json",
                coordination_root=root,
                workspace_root=root,
                transcript_root=root / "logs" / "mcp",
                tool_log=ToolLogSettings(max_bytes=budget),
            )
            directory = knowledge_reports.resolve_reports_dir(config, "knowledge-diff")
            assert directory is not None
            directory.mkdir(parents=True, exist_ok=True)
            # An existing report beside the logs: 900 bytes the budget
            # must charge before admitting another write.
            (directory / "old-report.json").write_bytes(b"r" * 900)
            logs = directory.parent / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "tool.log").write_bytes(b"l" * 100)
            dest = mock.Mock()
            dest.enclosure_dir = logs
            dest.coord_log_dir = None
            dest.max_bytes = budget
            dest.enclosure_max_bytes = budget
            dest.keep_days = 30
            with mock.patch(
                "agents_remember.application.tool_answer_log.resolve_log_roots",
                return_value=dest,
            ) as resolved:
                family = knowledge_reports._budget_family(config, "knowledge-diff", directory)
                self.assertEqual(set(family), {directory, logs}, family)
                with self.assertRaises(knowledge_reports.ReportWriteError) as raised:
                    knowledge_reports.write_report(
                        "knowledge-diff", {"operation": "knowledge-diff"}, config=config
                    )
            self.assertTrue(resolved.called, "the family must resolve the log roots")
            # Logs alone (100 bytes) would fit the 500-byte budget; the
            # existing 900 report bytes beside them must refuse.
            self.assertIn("budget", raised.exception.reason)

    def test_unmeasurable_census_refuses_instead_of_undercounting(self) -> None:
        from unittest import mock  # noqa: PLC0415

        from agents_remember.application import knowledge_reports  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            small = {"operation": "knowledge_diff", "rows": [{"id": "INV-1"}]}
            directory = knowledge_reports.resolve_reports_dir(
                None, "knowledge-diff", coordination_root=root
            )
            assert directory is not None
            directory.mkdir(parents=True, exist_ok=True)
            # A seed report the census must measure: the flaky stat below
            # fails exactly on report files, so admission refuses instead
            # of admitting against an undercount.
            (directory / "old-report.json").write_bytes(b"r" * 100)
            real_stat = Path.stat
            calls: list[str] = []

            def _flaky_stat(self: Path) -> object:
                calls.append(self.name)
                if self.suffix == ".json" and "report" in self.name:
                    raise OSError("unreadable during census")
                return real_stat(self)

            with (
                mock.patch.object(Path, "stat", autospec=True, side_effect=_flaky_stat),
                self.assertRaises(knowledge_reports.ReportWriteError) as raised,
            ):
                knowledge_reports.write_report("knowledge-diff", small, coordination_root=root)
            self.assertTrue(calls, "the census must reach the file stats")
            self.assertIn("cannot be checked", raised.exception.reason)
            leftovers = [
                path for path in root.rglob("*") if path.is_file() and path.suffix == ".part"
            ]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()

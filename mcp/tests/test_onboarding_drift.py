from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.memory_quality.integrity.onboarding_drift_check import drift
from agents_remember.memory_quality.integrity.onboarding_drift_check.inline import (
    classify_inline_source,
    compute_inline_source_digest,
    extract_inline_onboarding_block,
)
from test_memory_quality import initialize_clean_memory_fixture

INLINE_TEMPLATE = "\n".join(
    [
        "x = 1",
        '"""',
        "@ar-onboarding",
        "verifiedAt: 2026-05-29T12:00+02:00",
        "sourceDigest: sha256:{digest}",
        "@ar-onboarding-end",
        '"""',
        "y = 2",
        "",
    ]
)
INLINE_NO_DIGEST = "\n".join(
    [
        "x = 1",
        '"""',
        "@ar-onboarding",
        "verifiedAt: 2026-05-29T12:00+02:00",
        "@ar-onboarding-end",
        '"""',
        "y = 2",
        "",
    ]
)


def write_up_to_date_inline(path: Path) -> str:
    """Write an inline source whose recorded sourceDigest matches its body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(INLINE_TEMPLATE.format(digest="placeholder"), encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    block = extract_inline_onboarding_block(text)
    assert block is not None
    digest = compute_inline_source_digest(text, block)
    path.write_text(INLINE_TEMPLATE.format(digest=digest), encoding="utf-8")
    return digest


class InlineOnboardingTests(unittest.TestCase):
    def test_extract_block_parses_metadata_and_expands_delimiters(self) -> None:
        block = extract_inline_onboarding_block(INLINE_TEMPLATE.format(digest="abc"))
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(block.metadata.get("verifiedAt"), "2026-05-29T12:00+02:00")
        self.assertEqual(block.metadata.get("sourceDigest"), "sha256:abc")
        self.assertIn('"""', block.raw_text)

    def test_extract_block_absent_returns_none(self) -> None:
        self.assertIsNone(extract_inline_onboarding_block("just_code = 1\n"))

    def test_digest_depends_only_on_source_outside_block(self) -> None:
        text_a = INLINE_TEMPLATE.format(digest="aaaa")
        text_b = INLINE_TEMPLATE.format(digest="bbbb")
        block_a = extract_inline_onboarding_block(text_a)
        block_b = extract_inline_onboarding_block(text_b)
        assert block_a is not None and block_b is not None
        self.assertEqual(
            compute_inline_source_digest(text_a, block_a),
            compute_inline_source_digest(text_b, block_b),
        )

    def test_classify_inline_up_to_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            write_up_to_date_inline(repo / "pkg" / "mod.py")
            row = classify_inline_source("pkg/mod.py", repo)
            self.assertEqual(row.classification, "up to date")
            self.assertEqual(row.storage_mode, "inline")
            self.assertEqual(row.trust, "high")

    def test_classify_inline_drifted_when_body_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            path = repo / "pkg" / "mod.py"
            write_up_to_date_inline(path)
            path.write_text(path.read_text(encoding="utf-8") + "z = 3\n", encoding="utf-8")
            row = classify_inline_source("pkg/mod.py", repo)
            self.assertEqual(row.classification, "drifted")

    def test_classify_inline_missing_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
            row = classify_inline_source("mod.py", repo)
            self.assertEqual(row.classification, "missing")

    def test_classify_inline_missing_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            (repo / "mod.py").write_text(INLINE_NO_DIGEST, encoding="utf-8")
            row = classify_inline_source("mod.py", repo)
            self.assertEqual(row.classification, "missing verification")

    def test_classify_inline_orphaned_when_source_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            row = classify_inline_source("does/not/exist.py", repo)
            self.assertEqual(row.classification, "orphaned")


class DriftMainCliTests(unittest.TestCase):
    def test_main_runs_clean_repo_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_clean_memory_fixture(root)
            repo = root / "workspace" / "agents-remember"
            coordination = root / "ar-coordination"
            onboarding = coordination / "memory-repos" / "ar-agents-remember" / "onboarding"

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = drift.main(
                    [
                        "--code-repository-root",
                        str(repo),
                        "--onboarding-root",
                        str(onboarding),
                        "--coordination-root",
                        str(coordination),
                        "--topology",
                        "external",
                        "--format",
                        "json",
                    ]
                )

            self.assertEqual(code, 0)
            report = drift.default_report_path(coordination / "temp", repo)
            self.assertTrue(report.exists())
            self.assertIn("README.md", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()

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

from agents_remember.mcp.config import load_config
from agents_remember.mcp.tools import memory_quality_check_payload
from agents_remember.memory_quality.check import run_memory_quality_check
from agents_remember.memory_quality.style.update_history import history_order_fix
from agents_remember.memory_quality.style.update_history.history_order import (
    check_onboarding_root,
)
from test_config import settings_payload


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_onboarding(path: Path, history: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Example",
                "",
                "## Purpose",
                "",
                "Example onboarding.",
                "",
                "## Update History",
                "",
                history,
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_file_level_onboarding(
    path: Path,
    *,
    source_path: str,
    commit_hash: str,
    commit_date: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_path}",
                "",
                "| Field                  | Value                                      |",
                "| ---------------------- | ------------------------------------------ |",
                "| repository             | agents-remember-md                         |",
                f"| path                   | `{source_path}` |",
                "| doc_type               | `file-level-onboarding`                    |",
                f"| lastVerifiedCommitHash | `{commit_hash}` |",
                f"| lastVerifiedCommitDate | {commit_date} |",
                "",
                "## Purpose",
                "",
                "Fixture onboarding.",
                "",
                "## Update History",
                "",
                "- 2026-05-24T00:37+02:00: Created fixture onboarding.",
                "",
            ]
        ),
        encoding="utf-8",
    )


class MemoryQualityTests(unittest.TestCase):
    def test_update_history_accepts_newest_first_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_onboarding(
                root / "onboarding" / "example.md",
                "\n".join(
                    [
                        "- 2026-05-24T00:37+02:00: Newest entry.",
                        "  Continuation text stays with the newest entry.",
                        "- 2026-05-23T22:10+02:00: Older entry.",
                    ]
                ),
            )

            result = check_onboarding_root(root / "onboarding")

            self.assertTrue(result["ok"])
            self.assertEqual(result["findingCount"], 0)
            self.assertEqual(result["filesChecked"], 1)

    def test_update_history_flags_entries_inserted_below_older_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_onboarding(
                root / "onboarding" / "example.md",
                "\n".join(
                    [
                        "- 2026-05-23T22:10+02:00: Older entry.",
                        "- 2026-05-24T00:37+02:00: Newer entry inserted in the middle.",
                    ]
                ),
            )

            result = check_onboarding_root(root / "onboarding")

            self.assertFalse(result["ok"])
            self.assertEqual(result["findingCount"], 1)
            finding = result["findings"][0]
            self.assertEqual(finding["code"], "update_history_not_newest_first")
            self.assertEqual(finding["path"], "example.md")
            self.assertEqual(finding["line"], 10)
            self.assertEqual(finding["timestamp"], "2026-05-24T00:37+02:00")
            self.assertEqual(finding["previousTimestamp"], "2026-05-23T22:10+02:00")

    def test_update_history_flags_missing_timestamp_on_bullet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_onboarding(root / "onboarding" / "example.md", "- Missing timestamp.")

            result = check_onboarding_root(root / "onboarding")

            self.assertFalse(result["ok"])
            self.assertEqual(result["findings"][0]["code"], "update_history_timestamp_missing")

    def test_history_order_fix_reorders_update_history_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "onboarding" / "example.md"
            write_onboarding(
                path,
                "\n".join(
                    [
                        "- 2026-05-23T22:10+02:00: Older entry.",
                        "  Older continuation.",
                        "- 2026-05-24T00:37+02:00: Newer entry.",
                    ]
                ),
            )

            result = history_order_fix.fix_onboarding_root(root / "onboarding")
            check_result = run_memory_quality_check(root / "onboarding")

            self.assertTrue(result["ok"])
            self.assertEqual(result["changedFiles"], ["example.md"])
            self.assertEqual(result["skippedFiles"], [])
            self.assertTrue(check_result["ok"])
            text = path.read_text(encoding="utf-8")
            self.assertLess(text.index("Newer entry"), text.index("Older entry"))
            self.assertLess(text.index("Older entry"), text.index("Older continuation"))

    def test_history_order_fix_skips_missing_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "onboarding" / "example.md"
            write_onboarding(
                path,
                "\n".join(
                    [
                        "- Missing timestamp.",
                        "- 2026-05-24T00:37+02:00: Timestamped entry.",
                    ]
                ),
            )

            result = history_order_fix.fix_onboarding_root(root / "onboarding")
            check_result = run_memory_quality_check(root / "onboarding")

            self.assertFalse(result["ok"])
            self.assertEqual(result["changedFiles"], [])
            self.assertEqual(result["skippedFiles"], ["example.md"])
            self.assertFalse(check_result["ok"])
            self.assertEqual(
                check_result["findings"][0]["code"], "update_history_timestamp_missing"
            )

    def test_memory_quality_check_wrapper_defaults_to_update_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_onboarding(root / "onboarding" / "example.md", "- 2026-05-24T00:37: Clean.")

            result = run_memory_quality_check(root / "onboarding")

            self.assertTrue(result["ok"])
            self.assertEqual(result["findingCount"], 0)
            self.assertIn("style.update_history.history_order", result["checks"])

    def test_memory_quality_check_payload_can_run_style_check_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            onboarding = (
                root / "ar-coordination" / "memory-repos" / "ar-agents-remember-md" / "onboarding"
            )
            write_onboarding(
                onboarding / "example.md",
                "\n".join(
                    [
                        "- 2026-05-23T22:10+02:00: Older entry.",
                        "- 2026-05-24T00:37+02:00: Newer entry inserted below it.",
                    ]
                ),
            )
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = memory_quality_check_payload(
                config,
                "agents-remember-md",
                checks=["style.update_history.history_order"],
            )

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["operation"], "memory_quality_check")
            self.assertEqual(payload["repoId"], "agents-remember-md")
            self.assertEqual(payload["findingCount"], 1)
            self.assertEqual(
                payload["findings"][0]["check"],
                "style.update_history.history_order",
            )

    def test_memory_quality_check_payload_runs_drift_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_clean_memory_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = memory_quality_check_payload(config, "agents-remember-md")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["findingCount"], 0)
            self.assertIn("integrity.onboarding_drift_check.summary", payload["checks"])
            self.assertIn("style.update_history.history_order", payload["checks"])
            drift_result = payload["checks"]["integrity.onboarding_drift_check.summary"]
            self.assertEqual(drift_result["status"], "checked")
            self.assertEqual(drift_result["findingCount"], 0)


def initialize_clean_memory_fixture(root: Path) -> None:
    repo = root / "workspace" / "agents-remember-md"
    memory = root / "ar-coordination" / "memory-repos" / "ar-agents-remember-md"
    (memory / "system").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, ["init"])
    run_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    run_git(repo, ["config", "user.name", "Agents Remember"])
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    run_git(repo, ["add", "README.md"])
    run_git(repo, ["commit", "-m", "init"])
    write_file_level_onboarding(
        memory / "onboarding" / "README.md.md",
        source_path="README.md",
        commit_hash=git_output(repo, ["rev-parse", "HEAD"]),
        commit_date=git_output(repo, ["show", "-s", "--format=%cI", "HEAD"]),
    )


def git_output(repo: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def run_git(repo: Path, args: list[str]) -> None:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()

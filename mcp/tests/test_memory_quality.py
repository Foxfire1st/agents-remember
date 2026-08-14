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

from agents_remember.kernel.primitives.runtime_config import (
    load_config,
)
from agents_remember.mcp.tools import memory_quality_check_payload
from agents_remember.memory_quality.check import (
    BEFORE_METADATA_REFRESH_CHECKS,
    run_memory_quality_check,
)
from agents_remember.memory_quality.style.document_shape import entity_catalog_alignment
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
                "| repository             | agents-remember                         |",
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


def write_entity_catalog(path: Path, *, inventory: list[str], fingerprints: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    inventory_lines = [line for name in inventory for line in (f"### `{name}`", "", "Fixture.", "")]
    fingerprint_lines = [
        f"| `{name}` | `git-blob-set-sha256-v1` | `abc` | `README.md` |" for name in fingerprints
    ]
    path.write_text(
        "\n".join(
            [
                "# Repository Entity Catalog",
                "",
                "## Entity Inventory",
                "",
                *inventory_lines,
                "## Entity Fingerprints",
                "",
                "| Entity | Algorithm | Fingerprint | Evidence Paths |",
                "| --- | --- | --- | --- |",
                *fingerprint_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )


class MemoryQualityTests(unittest.TestCase):
    def test_entity_catalog_alignment_rejects_orphaned_fingerprint_before_code_rails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            onboarding = Path(tmp_dir) / "onboarding"
            write_entity_catalog(
                onboarding / "entities.md",
                inventory=["Present"],
                fingerprints=["Present", "Orphan"],
            )

            result = run_memory_quality_check(
                onboarding, checks=[entity_catalog_alignment.CHECK_NAME]
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["findingCount"], 1)
            self.assertEqual(result["findings"][0]["code"], "entity_fingerprint_without_inventory")
            self.assertEqual(BEFORE_METADATA_REFRESH_CHECKS[0], entity_catalog_alignment.CHECK_NAME)

    def test_entity_catalog_alignment_rejects_inventory_without_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            onboarding = Path(tmp_dir) / "onboarding"
            write_entity_catalog(onboarding / "entities.md", inventory=["Missing"], fingerprints=[])

            result = run_memory_quality_check(
                onboarding, checks=[entity_catalog_alignment.CHECK_NAME]
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["findings"][0]["code"], "entity_inventory_without_fingerprint")

    def test_entity_catalog_alignment_accepts_one_fingerprint_per_inventory_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            onboarding = Path(tmp_dir) / "onboarding"
            write_entity_catalog(
                onboarding / "entities.md", inventory=["Aligned"], fingerprints=["Aligned"]
            )

            result = run_memory_quality_check(onboarding)

            self.assertTrue(result["ok"])
            self.assertIn(entity_catalog_alignment.CHECK_NAME, result["checks"])

    def test_entity_catalog_alignment_rejects_missing_sections_and_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            onboarding = Path(tmp_dir) / "onboarding"
            catalog = onboarding / "entities.md"
            catalog.parent.mkdir(parents=True)
            catalog.write_text("# Repository Entity Catalog\n", encoding="utf-8")

            missing = run_memory_quality_check(
                onboarding, checks=[entity_catalog_alignment.CHECK_NAME]
            )

            self.assertEqual(
                {finding["code"] for finding in missing["findings"]},
                {"entity_inventory_section_missing", "entity_fingerprint_section_missing"},
            )
            self.assertEqual(
                entity_catalog_alignment._entity_line(
                    ["# Catalog", "", "## Entity Inventory"],
                    entity_catalog_alignment.INVENTORY_HEADING,
                    "Absent",
                ),
                3,
            )

            write_entity_catalog(
                catalog, inventory=["Duplicate"], fingerprints=["Duplicate", "Duplicate"]
            )
            duplicate = run_memory_quality_check(
                onboarding, checks=[entity_catalog_alignment.CHECK_NAME]
            )
            self.assertEqual(duplicate["findings"][0]["code"], "entity_fingerprint_duplicate")

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
            write_onboarding(root / "onboarding" / "example.md", "- 2026-05-24T00:37+02:00: Clean.")

            result = run_memory_quality_check(root / "onboarding")

            self.assertTrue(result["ok"])
            self.assertEqual(result["findingCount"], 0)
            self.assertIn("style.update_history.history_order", result["checks"])

    def test_memory_quality_check_payload_can_run_style_check_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            onboarding = (
                root / "ar-coordination" / "memory-repos" / "ar-agents-remember" / "onboarding"
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
                "agents-remember",
                checks=["style.update_history.history_order"],
            )

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["operation"], "memory_quality_check")
            self.assertEqual(payload["repoId"], "agents-remember")
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

            payload = memory_quality_check_payload(config, "agents-remember")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["findingCount"], 0)
            self.assertIn("integrity.onboarding_drift_check.summary", payload["checks"])
            self.assertIn("style.update_history.history_order", payload["checks"])
            drift_result = payload["checks"]["integrity.onboarding_drift_check.summary"]
            self.assertEqual(drift_result["status"], "checked")
            self.assertEqual(drift_result["findingCount"], 0)


def initialize_clean_memory_fixture(root: Path) -> None:
    repo = root / "workspace" / "agents-remember"
    memory = root / "ar-coordination" / "memory-repos" / "ar-agents-remember"
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

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.kernel.coordination_context_resolver import StorageSettings
from agents_remember.memory_quality.check import (
    BEFORE_METADATA_REFRESH_CHECKS,
    run_memory_quality_check,
)
from agents_remember.memory_quality.memory_census import _Census, _Tree
from agents_remember.memory_quality.style.document_shape import entity_catalog_alignment
from agents_remember.worktrees.integration.closeout.memory_census_scope import MemoryCensusScope


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    def test_census_accepts_repaired_current_onboarding_metadata(self) -> None:
        source = "mcp/src/agents_remember/application/memory_quality/census.py"
        path = f"onboarding/{source}.md"

        def run_case(
            historical: dict[str, str] | None,
            current: dict[str, str] | None,
            *,
            before_present: bool = True,
            after_present: bool = True,
            required: bool = False,
        ) -> _Census:
            with tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                before = SimpleNamespace(
                    root=root,
                    members={path: "100644"} if before_present else {},
                )
                after = SimpleNamespace(
                    root=root,
                    members={path: "100644"} if after_present else {},
                )
                scope = SimpleNamespace(memory_paths=(path,))
                census = _Census(
                    cast(MemoryCensusScope, scope),
                    cast(StorageSettings, None),
                    cast(_Tree, before),
                    cast(_Tree, after),
                    "onboarding",
                )
                documents = (
                    {path: historical} if historical is not None else {},
                    {path: current} if current is not None else {},
                )
                census.sidecars(source, documents, required=required)
                census.edited_documents(documents)
                return census

        canonical = {
            "doc_type": "file-level-onboarding",
            "path": source,
            "lastVerifiedCommitHash": "",
            "lastVerifiedCommitDate": "",
        }
        self.assertEqual(
            run_case({}, canonical).blockers,
            [],
        )
        self.assertEqual(
            run_case(
                {"doc_type": "repo-overview", "sourceRoute": "mcp"},
                canonical,
            ).blockers,
            [],
        )
        self.assertEqual(
            run_case(
                {"doc_type": "file-level-onboarding", "path": "mcp/src/old.py"},
                canonical,
            ).blockers,
            [],
        )
        malformed = run_case(
            {},
            {"doc_type": "file-level-onboarding", "path": "mcp/src/other.py"},
        )
        self.assertTrue(malformed.blockers)
        self.assertEqual(run_case({}, canonical).blockers, [])
        new_card = run_case(
            None,
            canonical,
            before_present=False,
            required=True,
        )
        self.assertEqual(new_card.blockers, [])
        self.assertIn(path, {row.identity.memoryRootRelativePath for row in new_card.rows.values()})
        missing_new_card = run_case(
            None,
            None,
            before_present=False,
            after_present=False,
            required=True,
        )
        self.assertIn("missing", {blocker.code for blocker in missing_new_card.blockers})

        entity_path = "onboarding/entities.md"
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            tree = SimpleNamespace(root=root, members={entity_path: "100644"})
            scope = SimpleNamespace(memory_paths=(entity_path,))
            census = _Census(
                cast(MemoryCensusScope, scope),
                cast(StorageSettings, None),
                cast(_Tree, tree),
                cast(_Tree, tree),
                "onboarding",
            )
            entity_documents = (
                {entity_path: {"doc_type": "repo-entity-catalog"}},
                {entity_path: {"doc_type": "repo-entity-catalog"}},
            )
            census.edited_documents(entity_documents)
            self.assertEqual(census.blockers, [])

    def test_census_repaired_old_association_does_not_veto_current_mapping(self) -> None:
        source = "mcp/src/agents_remember/application/memory_quality/census.py"
        path = f"onboarding/{source}.md"
        old_source = "mcp/src/agents_remember/application/memory_quality/old.py"
        canonical = {"doc_type": "file-level-onboarding", "path": source}
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            tree = SimpleNamespace(root=root, members={path: "100644"})
            scope = SimpleNamespace(memory_paths=(path,))
            census = _Census(
                cast(MemoryCensusScope, scope),
                cast(StorageSettings, None),
                cast(_Tree, tree),
                cast(_Tree, tree),
                "onboarding",
            )
            repaired_documents = (
                {path: {"doc_type": "file-level-onboarding", "path": old_source}},
                {path: canonical},
            )
            census.sidecars(old_source, repaired_documents, required=False)
            census.sidecars(source, repaired_documents, required=False)
            census.edited_documents(repaired_documents)
            self.assertEqual(census.blockers, [])

    def test_census_retains_historical_removal_row_without_mapping_validation(self) -> None:
        source = "mcp/src/agents_remember/application/memory_quality/removed.py"
        path = f"onboarding/{source}.md"
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            before = SimpleNamespace(root=root, members={path: "100644"})
            after = SimpleNamespace(root=root, members={})
            scope = SimpleNamespace(memory_paths=(path,))
            census = _Census(
                cast(MemoryCensusScope, scope),
                cast(StorageSettings, None),
                cast(_Tree, before),
                cast(_Tree, after),
                "onboarding",
            )
            removed_documents = (
                {path: {"doc_type": "file-level-onboarding", "path": source}},
                {},
            )
            census.sidecars(source, removed_documents, required=False)
            census.edited_documents(removed_documents)
            self.assertNotIn(
                "removal-disposition-required",
                {blocker.code for blocker in census.blockers},
            )
            self.assertEqual(
                [row.expectedFinalPresence for row in census.rows.values()],
                ["absent"],
            )

    def test_census_move_retains_old_and_new_rows(self) -> None:
        old_source = "mcp/src/agents_remember/application/memory_quality/old.py"
        new_source = "mcp/src/agents_remember/application/memory_quality/new.py"
        old_path = f"onboarding/{old_source}.md"
        new_path = f"onboarding/{new_source}.md"
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            before = SimpleNamespace(root=root, members={old_path: "100644"})
            after = SimpleNamespace(root=root, members={new_path: "100644"})
            scope = SimpleNamespace(memory_paths=(old_path, new_path))
            census = _Census(
                cast(MemoryCensusScope, scope),
                cast(StorageSettings, None),
                cast(_Tree, before),
                cast(_Tree, after),
                "onboarding",
            )
            moved_documents = (
                {old_path: {"doc_type": "file-level-onboarding", "path": old_source}},
                {new_path: {"doc_type": "file-level-onboarding", "path": new_source}},
            )
            census.sidecars(old_source, moved_documents, required=False)
            census.sidecars(new_source, moved_documents, required=False)
            census.edited_documents(moved_documents)
            self.assertEqual(census.blockers, [])
            rows = {row.identity.memoryRootRelativePath: row for row in census.rows.values()}
            self.assertEqual(rows[old_path].expectedFinalPresence, "absent")
            self.assertEqual(rows[new_path].expectedFinalPresence, "present")

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

    def test_entity_catalog_alignment_accepts_one_fingerprint_per_inventory_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            onboarding = Path(tmp_dir) / "onboarding"
            write_entity_catalog(
                onboarding / "entities.md", inventory=["Aligned"], fingerprints=["Aligned"]
            )

            result = run_memory_quality_check(onboarding)

            self.assertTrue(result["ok"])
            self.assertIn(entity_catalog_alignment.CHECK_NAME, result["checks"])


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

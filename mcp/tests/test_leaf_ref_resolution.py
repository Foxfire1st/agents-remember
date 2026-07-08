from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.leaf_refs import LeafRefResolutionError, resolve_leaf_ref
from agents_remember.worktrees.worktree_contract import (
    contract_to_text,
    default_contract,
    load_contract,
)


def _write_leaf(
    root: Path,
    *,
    repo: str = "repo-a",
    master: str = "260700_master",
    doc_id: str = "260700-L1",
    slug: str = "01_legacy-leaf",
) -> None:
    task_root = root / "tasks" / repo / master
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": master.upper(),
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": repo,
                "createdAt": "2026-07-07T10:00",
                "subTasks": [
                    {
                        "number": doc_id,
                        "name": "Leaf",
                        "file": f"{slug}.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": doc_id,
                "slug": slug,
                "title": "Leaf",
                "kind": "subTask",
                "repo": repo,
                "createdAt": "2026-07-07T10:01",
                "master": "task.md",
            }
        ),
    )


class LeafRefResolutionTests(unittest.TestCase):
    def test_resolves_qualified_doc_id_doc_id_and_legacy_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_leaf(root)

            qualified = resolve_leaf_ref(root, "repo-a", "repo-a/260700_master/260700-L1")
            by_doc_id = resolve_leaf_ref(root, "repo-a", "260700-l1")
            by_slug = resolve_leaf_ref(root, "repo-a", "01_legacy-leaf")

            self.assertEqual(qualified.qualified_id, "repo-a/260700_master/260700-L1")
            self.assertEqual(by_doc_id.qualified_id, qualified.qualified_id)
            self.assertEqual(by_slug.qualified_id, qualified.qualified_id)

    def test_rejects_unmatchable_with_expected_form_and_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_leaf(root)

            with self.assertRaises(LeafRefResolutionError) as ctx:
                resolve_leaf_ref(root, "repo-a", "260700-L9")

            message = str(ctx.exception)
            self.assertEqual(ctx.exception.status, "leaf-ref-not-found")
            self.assertIn("<repo>/<master-folder>/<doc-id>", message)
            self.assertIn("repo-a/260700_master/260700-L1", message)

    def test_rejects_ambiguous_legacy_slug_with_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_leaf(root, master="master-a", doc_id="A-L1", slug="01_shared")
            _write_leaf(root, master="master-b", doc_id="B-L1", slug="01_shared")

            with self.assertRaises(LeafRefResolutionError) as ctx:
                resolve_leaf_ref(root, "repo-a", "01_shared")

            self.assertEqual(ctx.exception.status, "leaf-ref-ambiguous")
            self.assertIn("repo-a/master-a/A-L1", str(ctx.exception))
            self.assertIn("repo-a/master-b/B-L1", str(ctx.exception))

    def test_rejects_qualified_ref_outside_requested_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_leaf(root, master="requested-master", doc_id="A-L1", slug="01_first")
            _write_leaf(root, master="other-master", doc_id="B-L1", slug="01_second")

            with self.assertRaises(LeafRefResolutionError) as ctx:
                resolve_leaf_ref(
                    root,
                    "repo-a",
                    "repo-a/other-master/B-L1",
                    task_name="requested master",
                )

            self.assertEqual(ctx.exception.status, "leaf-ref-not-found")

    def test_missing_optional_master_doc_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_root = root / "tasks" / "repo-a" / "260700_master"
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "260700-L1",
                        "slug": "01_legacy-leaf",
                        "title": "Leaf",
                        "kind": "subTask",
                        "repo": "repo-a",
                        "createdAt": "2026-07-07T10:01",
                        "master": "task.md",
                    }
                ),
            )

            resolved = resolve_leaf_ref(
                root,
                "repo-a",
                "01_legacy-leaf",
                task_name="260700_master",
            )

            self.assertEqual(resolved.qualified_id, "repo-a/260700_master/260700-L1")

    def test_marker_bearing_malformed_leaf_doc_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_leaf(root)
            task_root = root / "tasks" / "repo-a" / "260700_master"
            (task_root / "bad.json").write_text(
                '{"schema":"ar-task-document/v1","id":"bad"}',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                resolve_leaf_ref(root, "repo-a", "260700-L1")

    def test_sibling_artifact_json_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_leaf(root)
            task_root = root / "tasks" / "repo-a" / "260700_master"
            (task_root / "lint-inventory-baseline.json").write_text(
                '{"summary":{"findings":14},"items":[{"path":"a.py","line":1}]}',
                encoding="utf-8",
            )

            resolved = resolve_leaf_ref(root, "repo-a", "01_legacy-leaf")

            self.assertEqual(resolved.qualified_id, "repo-a/260700_master/260700-L1")

    def test_malformed_sibling_artifact_json_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_leaf(root)
            task_root = root / "tasks" / "repo-a" / "260700_master"
            (task_root / "lint-inventory-baseline.json").write_text(
                '{"summary":',
                encoding="utf-8",
            )

            resolved = resolve_leaf_ref(root, "repo-a", "01_legacy-leaf")

            self.assertEqual(resolved.qualified_id, "repo-a/260700_master/260700-L1")

    def test_load_contract_ignores_sibling_artifact_json_while_normalizing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_leaf(root)
            task_root = root / "tasks" / "repo-a" / "260700_master"
            (task_root / "lint-inventory-baseline.json").write_text(
                '{"summary":{"findings":14},"items":[{"path":"a.py","line":1}]}',
                encoding="utf-8",
            )
            contract = default_contract(
                task_name="260700_master",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="disabled",
                coordination_root=root,
                code_repo_path=root / "repo-a",
                code_source_branch="main",
                code_work_branch="ar/legacy",
                code_base_commit="abc123",
                worktree_name="legacy",
                leaf_id="01_legacy-leaf",
            )
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            contract.contract_path.write_text(contract_to_text(contract), encoding="utf-8")

            loaded = load_contract(contract.contract_path)

            self.assertEqual(loaded.leaf_id, "260700-L1")

    def test_load_contract_keeps_leaf_id_when_task_resolution_is_unproven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_root = root / "tasks" / "repo-a" / "duplicate"
            task_root.mkdir(parents=True)
            contract = default_contract(
                task_name="duplicate",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="disabled",
                coordination_root=root,
                code_repo_path=root / "repo-a",
                code_source_branch="main",
                code_work_branch="ar/legacy",
                code_base_commit="abc123",
                worktree_name="legacy",
                leaf_id="legacy-id",
            )
            contract = replace(contract, leaf_id="legacy-id")
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            contract.contract_path.write_text(contract_to_text(contract), encoding="utf-8")
            for parent in ("parent-a", "parent-b"):
                active = root / "tasks" / "repo-a" / parent / "duplicate"
                active.mkdir(parents=True)
                (active / "series-contract.md").write_text("---\n---\n", encoding="utf-8")

            loaded = load_contract(contract.contract_path)

            self.assertEqual(loaded.leaf_id, "legacy-id")

    def test_light_task_doc_is_indexed_as_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_root = root / "tasks" / "repo-a" / "fix-thing"
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "260707-T1",
                        "slug": "fix-thing",
                        "title": "Fix Thing",
                        "kind": "light",
                        "repo": "repo-a",
                        "createdAt": "2026-07-07T10:01",
                        "enclosures": [
                            {
                                "leafId": "legacy-light",
                                "enclosurePath": "enclosures/legacy-light/series-contract.md",
                            }
                        ],
                    }
                ),
            )

            by_doc_id = resolve_leaf_ref(root, "repo-a", "260707-T1", task_name="fix thing")
            by_folder = resolve_leaf_ref(root, "repo-a", "fix-thing", task_name="fix thing")
            by_enclosure = resolve_leaf_ref(root, "repo-a", "legacy-light", task_name="fix thing")

            self.assertEqual(by_doc_id.qualified_id, "repo-a/fix-thing/260707-T1")
            self.assertEqual(by_folder.qualified_id, by_doc_id.qualified_id)
            self.assertEqual(by_enclosure.qualified_id, by_doc_id.qualified_id)


if __name__ == "__main__":
    unittest.main()

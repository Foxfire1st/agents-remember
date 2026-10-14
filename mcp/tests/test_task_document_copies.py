from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from agents_remember.application.knowledge_reader import selection as reader_selection
from agents_remember.application.role_launch_context import resolve_role_launch_context
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.cli import role_launch_preparation as preparation
from agents_remember.cli.role_launch_routes import _missing_copies_receipt_warning
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.role_launcher import LauncherRole, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving import scope as scope_module
from agents_remember.serving.projections.contract_snapshot import ContractSnapshotCache
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.task_paths import (
    enclosure_repository_tasks,
    is_foreign_enclosure_contract,
    iter_leaf_enclosure_contracts,
)
from agents_remember.worktrees import task_document_copies as copies
from agents_remember.worktrees.modules.closeout import closeout_changed_paths
from agents_remember.worktrees.role_reports import remove_group_role_folders
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    heal_contract_leaf_ids,
    write_contract,
)


class TaskDocumentCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = self.root / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "coordinationRoot": str(self.root / "coord"),
                    "workspaceRoot": str(self.root / "projects"),
                    "repositories": {"demo": {"path": str(self.root / "code")}},
                }
            )
        )
        self.config = load_config(settings)
        self.repo_tasks = self.config.coordination_root / "tasks" / "demo"
        self.refs = []
        for folder, slug, extra in (
            ("sprint", "sprint", {"kind": "master", "orchestrates": ["master", "other"]}),
            (
                "master",
                "master",
                {
                    "kind": "master",
                    "subTasks": [
                        {"number": "LEAF", "name": "leaf", "file": "leaf.md"},
                        {"number": "SIBLING", "name": "sibling", "file": "sibling.md"},
                    ],
                },
            ),
            ("master", "leaf", {"kind": "subTask", "master": "task.md"}),
            ("master", "sibling", {"kind": "subTask", "master": "task.md"}),
            (
                "other",
                "other",
                {
                    "kind": "master",
                    "subTasks": [{"number": "FOREIGN", "name": "foreign", "file": "foreign.md"}],
                },
            ),
            ("other", "foreign", {"kind": "subTask", "master": "task.md"}),
        ):
            doc = TaskDocument.model_validate(
                {
                    "id": slug.upper(),
                    "slug": slug,
                    "title": slug,
                    "repo": "demo",
                    "createdAt": "2026-10-08T00:00:00Z",
                    **extra,
                }
            )
            path, _ = write_task_doc(self.repo_tasks / folder, doc)
            self.refs.append(
                TaskDocumentRef(
                    repository="demo", path=path.relative_to(self.repo_tasks).as_posix()
                )
            )
        self.contracts = {}
        self.groups = []
        for n, leaf in enumerate((self.refs[2], self.refs[3], self.refs[5], self.refs[2])):
            group = self.root / f"group{n}"
            group.mkdir()
            code, memory = group / "code", group / "memory"
            for repo in (code, memory):
                repo.mkdir()
                subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            self.groups.append(group)
            self.contracts[self.root / f"contract{n}.md"] = SimpleNamespace(
                worktree_group=group,
                code_worktree=code,
                memory_worktree=memory,
                task_artifact=self.repo_tasks / leaf.path,
                task_root=(self.repo_tasks / leaf.path).parent,
            )

    def selection(self, role: LauncherRole) -> RoleSelection:
        return RoleSelection(
            role=role,
            sprintDocumentRef=self.refs[0],
            masterDocumentRef=self.refs[1],
            **(
                {"taskDocumentRef": self.refs[2]}
                if role in {"worker", "reviewer", "curator"}
                else {}
            ),
        )

    def test_each_leaf_role_publishes_real_leaf_before_handover_compilation(self) -> None:
        group = self.groups[0]
        workspace = {
            "path": str(group),
            "contractPath": str(self.root / "contract0.md"),
            "codeRoot": str(group / "code"),
            "memoryRoot": str(group / "memory"),
        }
        for role in ("worker", "reviewer", "curator"):
            context = resolve_role_launch_context(self.config, self.selection(role))

            def compiled(request):
                index = json.loads((group / "task-documents/INDEX.json").read_text())
                self.assertEqual(len(index["documents"]), 1)
                for row in index["documents"]:
                    source = self.repo_tasks / row["reference"]["path"]
                    copy = group / "task-documents" / row["path"]
                    self.assertTrue(copy.is_file())
                    self.assertFalse(copy.is_symlink())
                    self.assertEqual(copy.read_bytes(), source.with_suffix(".md").read_bytes())
                    self.assertEqual(
                        row["canonicalJsonSha256"], hashlib.sha256(source.read_bytes()).hexdigest()
                    )
                    self.assertEqual(
                        row["copySha256"], hashlib.sha256(copy.read_bytes()).hexdigest()
                    )
                self.assertEqual(
                    index["documents"][0]["reference"], self.refs[2].model_dump(mode="json")
                )
                self.assertFalse((group / "task-documents/sprint/task.md").exists())
                self.assertFalse((group / "task-documents/master/task.md").exists())
                return {}

            with (
                patch.object(preparation, "_role_defaults", return_value=({}, [])),
                patch.object(preparation, "resolve_agent_selection", return_value=("codex", {})),
                patch.object(preparation, "_resolve_workspace", return_value=workspace),
                patch.object(preparation, "_prepare_role_folders"),
                patch.object(preparation, "_current_role_context", return_value=context),
                patch.object(preparation, "_ar_mcp_context", return_value={}),
                patch.object(preparation, "_compile_handover", side_effect=compiled),
            ):
                preparation.prepare_role_handover(
                    self.config, context, agent_override=None, request_id=uuid.uuid4()
                )
        for repo in (group / "code", group / "memory"):
            self.assertEqual(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=repo), b""
            )
            self.assertEqual(
                subprocess.check_output(
                    ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo
                ),
                b"",
            )
        code = group / "code"
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--quiet",
                "--allow-empty",
                "-m",
                "fixture base",
            ],
            cwd=code,
            check=True,
        )
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=code).decode().strip()
        self.assertEqual(
            closeout_changed_paths(
                SimpleNamespace(
                    kind="leaf", code_worktree=code, code_base_commit=base, code_commit=""
                )
            ),
            {"all": [], "working": [], "committed": []},
        )

    def test_public_writes_refresh_only_own_leaf_in_every_existing_enclosure(self) -> None:
        for group, ref in zip(
            self.groups, (self.refs[2], self.refs[3], self.refs[5], self.refs[2]), strict=True
        ):
            copies.prepare_task_document_copies(self.config.coordination_root, group, ref)
        with (
            patch.object(
                copies, "iter_leaf_enclosure_contracts", return_value=list(self.contracts)
            ),
            patch.object(copies, "load_contract", side_effect=self.contracts.__getitem__),
        ):
            for ref, expected in (
                (self.refs[2], {0, 3}),
                (self.refs[1], set()),
                (self.refs[0], set()),
                (self.refs[5], {2}),
            ):
                before = [self.snapshot(group) for group in self.groups]
                path = Path(ref.path)
                task_doc_tool(
                    self.config,
                    TaskDocTarget(repo_id="demo", task_name=path.parent.name, slug=path.stem),
                    operation="set_field",
                    edit=TaskDocEdit(
                        fields={"objective": "changed " + path.parent.name + path.stem}
                    ),
                )
                for n, group in enumerate(self.groups):
                    after = self.snapshot(group)
                    self.assertEqual(after != before[n], n in expected, (ref.path, n))
                    if n in expected:
                        copy = group / "task-documents" / path.with_suffix(".md")
                        self.assertEqual(
                            copy.read_bytes(),
                            (self.repo_tasks / path).with_suffix(".md").read_bytes(),
                        )

    def test_leaf_title_updates_canonical_master_but_copies_only_the_leaf(self) -> None:
        with (
            patch.object(
                copies, "iter_leaf_enclosure_contracts", return_value=list(self.contracts)
            ),
            patch.object(copies, "load_contract", side_effect=self.contracts.__getitem__),
        ):
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", task_name="master", slug="leaf"),
                operation="set_field",
                edit=TaskDocEdit(fields={"title": "renamed leaf"}),
            )
        for n in (0, 3):
            self.assertEqual(
                (self.groups[n] / "task-documents/master/leaf.md").read_bytes(),
                (self.repo_tasks / "master/leaf.md").read_bytes(),
            )
            self.assertFalse((self.groups[n] / "task-documents/master/task.md").exists())
        self.assertIn(b"renamed leaf", (self.repo_tasks / "master/task.json").read_bytes())
        for n in (1, 2):
            self.assertFalse((self.groups[n] / "task-documents").exists())

    def test_failed_copy_publication_restores_every_copy_and_canonical_source(self) -> None:
        for group, ref in zip(
            self.groups, (self.refs[2], self.refs[3], self.refs[5], self.refs[2]), strict=True
        ):
            copies.prepare_task_document_copies(self.config.coordination_root, group, ref)
        canonical = {p: p.read_bytes() for p in self.repo_tasks.rglob("*") if p.is_file()}
        before = [self.snapshot(group) for group in self.groups]
        real_write = copies.atomic_write_bytes
        failed = False

        def fail_once(path, raw):
            nonlocal failed
            if path == self.groups[3] / "task-documents/INDEX.json" and not failed:
                failed = True
                raise OSError("injected derived index publication failure")
            real_write(path, raw)

        with (
            patch.object(
                copies, "iter_leaf_enclosure_contracts", return_value=list(self.contracts)
            ),
            patch.object(copies, "load_contract", side_effect=self.contracts.__getitem__),
            patch.object(copies, "atomic_write_bytes", side_effect=fail_once),
            self.assertRaisesRegex(OSError, "derived index"),
        ):
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", task_name="master", slug="leaf"),
                operation="set_field",
                edit=TaskDocEdit(fields={"title": "renamed leaf before failed copy"}),
            )
        self.assertTrue(failed)
        self.assertEqual(
            {p: p.read_bytes() for p in self.repo_tasks.rglob("*") if p.is_file()}, canonical
        )
        self.assertEqual([self.snapshot(group) for group in self.groups], before)

    def test_leaf_and_controller_handover_paths_and_no_enclosure_boundary(self) -> None:
        for role in ("worker", "manager"):
            context = resolve_role_launch_context(self.config, self.selection(role))
            rows = [
                {"taskDocumentRef": doc.ref.model_dump(mode="json")}
                for doc in (context.sprint, context.master, context.task)
                if doc
            ]
            metadata, first = preparation._task_document_read_metadata(
                self.config, context, {"path": str(self.groups[0])}, rows
            )
            self.assertIn("harness's own file tools", first)
            self.assertIn("task_doc only", first)
            self.assertEqual(
                metadata["state"],
                "inside-working-folder"
                if role == "worker"
                else "controller-document-outside-working-folder",
            )
            self.assertEqual(len(metadata["paths"]), 1)
        with self.assertRaisesRegex(ValueError, "no enclosure"):
            preparation._prepare_task_document_files(
                self.config,
                resolve_role_launch_context(self.config, self.selection("worker")),
                {"path": str(self.groups[0])},
            )

    def test_edits_to_copy_are_not_read_back_and_next_leaf_write_replaces_them(self) -> None:
        group = self.groups[0]
        copies.prepare_task_document_copies(self.config.coordination_root, group, self.refs[2])
        copy = group / "task-documents/master/leaf.md"
        copy.write_text("unauthorized derived-file edit")
        with (
            patch.object(
                copies, "iter_leaf_enclosure_contracts", return_value=list(self.contracts)
            ),
            patch.object(copies, "load_contract", side_effect=self.contracts.__getitem__),
        ):
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", task_name="master", slug="leaf"),
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "canonical edit"}),
            )
        self.assertEqual(copy.read_bytes(), (self.repo_tasks / "master/leaf.md").read_bytes())
        self.assertNotIn(b"unauthorized", (self.repo_tasks / "master/leaf.json").read_bytes())

    def test_cleanup_removes_copies_and_external_links_are_refused(self) -> None:
        group = self.groups[0]
        copies.prepare_task_document_copies(self.config.coordination_root, group, self.refs[2])
        remove_group_role_folders(next(iter(self.contracts.values())), dry_run=False)
        self.assertFalse((group / "task-documents").exists())
        external = self.root / "outside"
        external.mkdir()
        (group / "task-documents").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real files"):
            copies.prepare_task_document_copies(self.config.coordination_root, group, self.refs[2])
        self.assertEqual(list(external.iterdir()), [])

    @staticmethod
    def snapshot(group: Path) -> dict[str, bytes]:
        return {
            p.relative_to(group).as_posix(): p.read_bytes()
            for p in (group / "task-documents").rglob("*")
            if p.is_file()
        }

    def _write_requirement_fixture(self) -> None:
        (self.repo_tasks / "master" / "requirements").mkdir(exist_ok=True)
        (self.repo_tasks / "master" / "requirements" / "REQ-1.md").write_text(
            "# REQ-1\n", encoding="utf-8"
        )
        leaf_json = self.repo_tasks / "master" / "leaf.json"
        data = json.loads(leaf_json.read_text(encoding="utf-8"))
        data["requirements"] = [
            "[REQ-1](requirements/REQ-1.md) \u2014 the rule.",
            "[Missing](requirements/NOPE.md) \u2014 absent.",
        ]
        data["references"] = ["[Master](task.md)"]
        leaf_json.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def test_requirement_files_are_copied_beside_the_leaf_and_nothing_else(self) -> None:
        self._write_requirement_fixture()
        group = self.groups[0]
        rows = copies.prepare_task_document_copies(
            self.config.coordination_root, group, self.refs[2]
        )
        self.assertTrue((group / "task-documents/master/leaf.md").is_file())
        requirement = group / "task-documents/master/requirements/REQ-1.md"
        self.assertTrue(requirement.is_file())
        self.assertFalse(requirement.is_symlink())
        self.assertEqual(
            requirement.read_bytes(),
            (self.repo_tasks / "master/requirements/REQ-1.md").read_bytes(),
        )
        self.assertFalse((group / "task-documents/master/task.md").exists())
        self.assertFalse((group / "task-documents/sprint").exists())
        self.assertFalse((group / "task-documents/other").exists())
        index = json.loads((group / "task-documents/INDEX.json").read_text())
        self.assertEqual(len(index["documents"]), 3)
        by_path = {row["path"]: row for row in index["documents"]}
        self.assertEqual(
            set(by_path),
            {"master/leaf.md", "master/requirements/REQ-1.md", "master/requirements/NOPE.md"},
        )
        requirement_row = by_path["master/requirements/REQ-1.md"]
        self.assertEqual(requirement_row["kind"], "requirement")
        self.assertEqual(
            requirement_row["copySha256"], hashlib.sha256(requirement.read_bytes()).hexdigest()
        )
        missing_row = by_path["master/requirements/NOPE.md"]
        self.assertTrue(missing_row["missing"])
        self.assertEqual(missing_row["reference"], "master/requirements/NOPE.md")
        self.assertEqual(
            sorted(
                Path(row["path"]).relative_to(group / "task-documents").as_posix()
                for row in rows
                if row.get("kind") == "requirement"
            ),
            ["master/requirements/NOPE.md", "master/requirements/REQ-1.md"],
        )

    def test_missing_requirement_is_named_in_handover_lists_and_receipt_warning(self) -> None:
        self._write_requirement_fixture()
        group = self.groups[0]
        copies.prepare_task_document_copies(self.config.coordination_root, group, self.refs[2])
        context = resolve_role_launch_context(self.config, self.selection("worker"))
        rows = [
            {"taskDocumentRef": doc.ref.model_dump(mode="json")}
            for doc in (context.sprint, context.master, context.task)
            if doc
        ]
        metadata, _first = preparation._task_document_read_metadata(
            self.config, context, {"path": str(group)}, rows
        )
        self.assertIn(
            (group / "task-documents/master/requirements/REQ-1.md").as_posix(),
            metadata["copies"],
        )
        self.assertEqual(metadata["missingCopies"], ["master/requirements/NOPE.md"])

        warning = _missing_copies_receipt_warning(metadata)
        self.assertIn("master/requirements/NOPE.md", warning["warning"])

    def test_write_adding_a_requirement_adds_its_copy(self) -> None:
        group = self.groups[0]
        copies.prepare_task_document_copies(self.config.coordination_root, group, self.refs[2])
        (self.repo_tasks / "master" / "requirements").mkdir(exist_ok=True)
        (self.repo_tasks / "master" / "requirements" / "REQ-2.md").write_text(
            "# REQ-2\n", encoding="utf-8"
        )
        with (
            patch.object(
                copies, "iter_leaf_enclosure_contracts", return_value=list(self.contracts)
            ),
            patch.object(copies, "load_contract", side_effect=self.contracts.__getitem__),
        ):
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", task_name="master", slug="leaf"),
                operation="set_field",
                edit=TaskDocEdit(fields={"requirements": ["[REQ-2](requirements/REQ-2.md)"]}),
            )
        added = group / "task-documents/master/requirements/REQ-2.md"
        self.assertEqual(
            added.read_bytes(), (self.repo_tasks / "master/requirements/REQ-2.md").read_bytes()
        )

    def test_changed_source_is_named_and_next_write_refreshes(self) -> None:
        self._write_requirement_fixture()
        group = self.groups[0]
        copies.prepare_task_document_copies(self.config.coordination_root, group, self.refs[2])
        self.assertIsNone(copies.stale_copy_line(self.config.coordination_root, group))
        (self.repo_tasks / "master" / "requirements" / "REQ-1.md").write_text(
            "# REQ-1 amended\n", encoding="utf-8"
        )
        line = copies.stale_copy_line(self.config.coordination_root, group)
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("master/requirements/REQ-1.md", line)
        self.assertNotIn("\n", line)
        with (
            patch.object(
                copies, "iter_leaf_enclosure_contracts", return_value=list(self.contracts)
            ),
            patch.object(copies, "load_contract", side_effect=self.contracts.__getitem__),
        ):
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", task_name="master", slug="leaf"),
                operation="set_field",
                edit=TaskDocEdit(fields={"title": "leaf amended"}),
            )
        self.assertIsNone(copies.stale_copy_line(self.config.coordination_root, group))
        self.assertEqual(
            (group / "task-documents/master/requirements/REQ-1.md").read_bytes(),
            (self.repo_tasks / "master/requirements/REQ-1.md").read_bytes(),
        )

    def test_cleanup_removes_requirement_copies(self) -> None:
        self._write_requirement_fixture()
        group = self.groups[0]
        copies.prepare_task_document_copies(self.config.coordination_root, group, self.refs[2])
        self.assertTrue((group / "task-documents/master/requirements/REQ-1.md").is_file())
        remove_group_role_folders(next(iter(self.contracts.values())), dry_run=False)
        self.assertFalse((group / "task-documents").exists())


class TaskDocumentCopyScopeTests(unittest.TestCase):
    """MIK-R134: a written task document refreshes its own copies and nothing else.

    Real enclosure contracts on disk and the real enclosure walk: the incident
    this repairs (2026-10-11, every ``task_doc`` write failed for 26 minutes)
    came from a sandbox contract copy kept under a master's ``notes`` whose
    worktree group still existed.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = self.root / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "coordinationRoot": str(self.root / "coord"),
                    "workspaceRoot": str(self.root / "projects"),
                    "repositories": {"demo": {"path": str(self.root / "code")}},
                }
            )
        )
        self.config = load_config(settings)
        self.repo_tasks = self.config.coordination_root / "tasks" / "demo"

    def _write_leaf(self, task_name: str, slug: str = "doc") -> tuple[Path, Path]:
        task_dir = self.repo_tasks / task_name
        write_task_doc(
            task_dir,
            TaskDocument.model_validate(
                {
                    "id": task_name.upper(),
                    "slug": task_name,
                    "title": task_name,
                    "repo": "demo",
                    "createdAt": "2026-10-08T00:00:00Z",
                    "kind": "master",
                    "subTasks": [{"number": "LEAF", "name": slug, "file": f"{slug}.md"}],
                }
            ),
        )
        path, _markdown = write_task_doc(
            task_dir,
            TaskDocument.model_validate(
                {
                    "id": slug.upper(),
                    "slug": slug,
                    "title": slug,
                    "repo": "demo",
                    "createdAt": "2026-10-08T00:00:00Z",
                    "kind": "subTask",
                    "master": "task.md",
                }
            ),
        )
        return task_dir, path

    def _write_enclosure(
        self,
        task_dir: Path,
        leaf_name: str,
        task_artifact: Path,
        *,
        contract_dir: Path | None = None,
        task_root: Path | None = None,
    ) -> tuple[Path, Path]:
        group = self.root / f"group-{task_dir.name}-{leaf_name}-{uuid.uuid4().hex[:8]}"
        group.mkdir(parents=True)
        code = group / "code"
        code.mkdir()
        contract_path = (contract_dir or task_dir / "enclosures" / leaf_name).joinpath(
            "series-contract.md"
        )
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(
            contract_path,
            WorktreeContract(
                task_id=f"{task_dir.name}-{leaf_name}",
                task_name=task_dir.name,
                repo_name="demo",
                workflow_kind="light-task",
                memory_mode="disabled",
                coordination_root=self.config.coordination_root,
                task_root=task_root or task_dir,
                contract_path=contract_path,
                task_artifact=task_artifact,
                worktree_group=group,
                code_repo_path=self.root / "code",
                code_source_branch="main",
                code_work_branch=f"ar/{leaf_name}",
                code_base_commit="a" * 40,
                code_worktree=code,
                kind="leaf",
                leaf_id=f"{task_dir.name}-{leaf_name}",
            ),
        )
        return contract_path, group

    def _write_foreign_enclosure(
        self, contract_path: Path, leaf_name: str = "sbx", *, repo_name: str = "sandbox-app"
    ) -> tuple[Path, Path]:
        outside = self.root / "outside" / "tasks" / "sandbox-app" / "sbx"
        group = self.root / f"foreign-group-{uuid.uuid4().hex[:8]}"
        group.mkdir(parents=True)
        (group / "code").mkdir()
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(
            contract_path,
            WorktreeContract(
                task_id=f"sandbox-{leaf_name}",
                task_name="sbx",
                repo_name=repo_name,
                workflow_kind="light-task",
                memory_mode="disabled",
                coordination_root=self.config.coordination_root,
                task_root=outside,
                contract_path=contract_path,
                task_artifact=outside / "task.json",
                worktree_group=group,
                code_repo_path=self.root / "code",
                code_source_branch="main",
                code_work_branch=f"ar/{leaf_name}",
                code_base_commit="b" * 40,
                code_worktree=group / "code",
                kind="leaf",
                leaf_id=f"sandbox-{leaf_name}",
            ),
        )
        return contract_path, group

    def _write_real_task(self, task_name: str = "master") -> tuple[Path, Path, Path]:
        task_dir, _json = self._write_leaf(task_name)
        contract_path, group = self._write_enclosure(task_dir, "real", task_dir / "doc.json")
        return task_dir, contract_path, group

    def test_write_succeeds_with_foreign_contract_below_notes(self) -> None:
        """Evidence 1: a foreign contract under a master's notes stops no write."""
        task_dir, _contract_path, group = self._write_real_task()
        _foreign_path, foreign_group = self._write_foreign_enclosure(
            task_dir
            / "notes"
            / "evidence"
            / "tasks"
            / "sandbox-app"
            / "sbx"
            / "enclosures"
            / "sbx"
            / "series-contract.md"
        )
        task_doc_tool(
            self.config,
            TaskDocTarget(repo_id="demo", task_name="master", slug="doc"),
            operation="set_field",
            edit=TaskDocEdit(fields={"objective": "changed master doc"}),
        )
        self.assertEqual(
            (group / "task-documents/master/doc.md").read_bytes(),
            (task_dir / "doc.md").read_bytes(),
        )
        self.assertFalse((foreign_group / "task-documents").exists())

    def test_foreign_contract_in_own_enclosures_is_skipped_and_named(self) -> None:
        """Rule 2: a foreign contract in the changed task's enclosures is named."""
        task_dir, contract_path, group = self._write_real_task()
        foreign_path, foreign_group = self._write_foreign_enclosure(
            task_dir / "enclosures" / "foreign" / "series-contract.md"
        )
        with self.assertLogs(
            "agents_remember.worktrees.task_document_copies", level="WARNING"
        ) as logs:
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", slug="doc", contract_path=str(contract_path)),
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "changed master doc"}),
            )
        self.assertIn(foreign_path.as_posix(), "\n".join(logs.output))
        self.assertEqual(
            (group / "task-documents/master/doc.md").read_bytes(),
            (task_dir / "doc.md").read_bytes(),
        )
        self.assertFalse((foreign_group / "task-documents").exists())

    def test_refresh_loads_only_the_changed_tasks_contracts(self) -> None:
        """Evidence 2: one write loads the changed task's contracts only."""
        task_dir, _json = self._write_leaf("changed")
        wanted = [
            self._write_enclosure(task_dir, f"c{n}", task_dir / "doc.json")[0] for n in range(2)
        ]
        for n in range(10):
            other = self.repo_tasks / f"other{n}"
            self._write_enclosure(other, "c", other / "doc.json")
        real_load = copies.load_contract
        calls: list[Path] = []

        def counting(path: Path):  # type: ignore[no-untyped-def]
            calls.append(path)
            return real_load(path)

        with patch.object(copies, "load_contract", side_effect=counting):
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", slug="doc", contract_path=str(wanted[0])),
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "changed changed doc"}),
            )
        self.assertEqual(
            sorted(path.as_posix() for path in calls),
            sorted(path.as_posix() for path in wanted),
        )

    def test_walk_yields_task_enclosures_and_nothing_below_notes(self) -> None:
        """Rule 3: the walk yields task enclosures and none below ``notes``."""
        keep = self.repo_tasks / "live" / "enclosures" / "a" / "series-contract.md"
        keep.parent.mkdir(parents=True)
        keep.write_text("contract")
        archived = self.repo_tasks / "0_archive" / "old" / "enclosures" / "b" / "series-contract.md"
        archived.parent.mkdir(parents=True)
        archived.write_text("contract")
        below_notes = (
            self.repo_tasks
            / "live"
            / "notes"
            / "evidence"
            / "tasks"
            / "x"
            / "y"
            / "enclosures"
            / "c"
            / "series-contract.md"
        )
        below_notes.parent.mkdir(parents=True)
        below_notes.write_text("contract")
        notes_direct = (
            self.repo_tasks / "live" / "notes" / "enclosures" / "d" / "series-contract.md"
        )
        notes_direct.parent.mkdir(parents=True)
        notes_direct.write_text("contract")
        leaf_named_notes = self.repo_tasks / "live" / "enclosures" / "notes" / "series-contract.md"
        leaf_named_notes.parent.mkdir(parents=True)
        leaf_named_notes.write_text("contract")
        self.assertEqual(
            set(iter_leaf_enclosure_contracts(self.repo_tasks)),
            {keep, leaf_named_notes},
        )


class TaskDocumentCopyMembershipTests(TaskDocumentCopyScopeTests):
    """MIK-R134 round 2: exact-enclosure selection and shared foreign guard.

    Sealed IDs L134-C1-01 (same-master siblings are never loaded) and
    L134-C1-02 (foreign contracts are rejected and named across consumers).
    """

    def test_sibling_leaf_contract_is_never_loaded(self) -> None:
        task_dir, own, _group = self._write_real_task()
        sibling, _sibling_group = self._write_enclosure(
            task_dir, "sibling", task_dir / "sibling.json"
        )
        real_load = copies.load_contract
        loaded: list[Path] = []

        def counting(path: Path):  # type: ignore[no-untyped-def]
            loaded.append(path)
            return real_load(path)

        with patch.object(copies, "load_contract", side_effect=counting):
            copies.refresh_task_document_copies(
                self.config.coordination_root,
                "demo",
                [(task_dir / "doc.json", task_dir / "doc.md")],
            )
        self.assertEqual(loaded, [own], f"sibling contract loaded: {sibling}")

    def test_broken_sibling_contract_does_not_stop_real_write(self) -> None:
        task_dir, own, group = self._write_real_task()
        sibling = task_dir / "enclosures" / "sibling" / "series-contract.md"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("broken sibling contract")
        before = (task_dir / "doc.json").read_bytes()
        try:
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", contract_path=str(own), slug="doc"),
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "changed only own leaf"}),
            )
        except Exception as exc:
            self.assertEqual(before, (task_dir / "doc.json").read_bytes())
            self.fail(f"unrelated sibling prevented publication: {exc}")
        self.assertEqual(
            (group / "task-documents/master/doc.md").read_bytes(),
            (task_dir / "doc.md").read_bytes(),
        )

    def test_destroyed_own_contract_fails_write_and_restores_sources(self) -> None:
        task_dir, own, group = self._write_real_task()
        own.write_text("destroyed own contract")
        before = {path: path.read_bytes() for path in (task_dir / "doc.json", task_dir / "doc.md")}
        with self.assertRaises(Exception) as raised:
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", contract_path=str(own), slug="doc"),
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "changed own leaf"}),
            )
        self.assertIn("series-contract.md", str(raised.exception))
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((group / "task-documents").exists())

    def test_master_write_with_enclosures_present_succeeds_without_copies(self) -> None:
        _task_dir, _own, group = self._write_real_task()
        task_doc_tool(
            self.config,
            TaskDocTarget(repo_id="demo", task_name="master", slug="task"),
            operation="set_field",
            edit=TaskDocEdit(fields={"objective": "changed master"}),
        )
        self.assertFalse((group / "task-documents").exists())

    def test_foreign_contract_is_not_a_snapshot_enclosure(self) -> None:
        task_dir, own, _group = self._write_real_task()
        foreign, _foreign_group = self._write_foreign_enclosure(
            task_dir / "enclosures" / "foreign" / "series-contract.md"
        )
        snapshot = ContractSnapshotCache().build(self.repo_tasks)
        self.assertIn(own, snapshot.contracts)
        self.assertNotIn(foreign, snapshot.contracts)
        self.assertIn(foreign, snapshot.skipped)

    def test_dotdot_alias_escaping_repo_tasks_is_still_foreign(self) -> None:
        task_dir, own, _group = self._write_real_task()
        foreign, _foreign_group = self._write_foreign_enclosure(
            task_dir / "enclosures" / "foreign" / "series-contract.md"
        )
        real = copies.load_contract(foreign)
        alias = self.repo_tasks / "../../../outside/tasks/sandbox-app/sbx/task.json"
        self.assertEqual(alias.resolve(), real.task_artifact.resolve())
        self.assertFalse(alias.resolve().is_relative_to(self.repo_tasks.resolve()))
        write_contract(foreign, replace(real, task_artifact=alias))
        self.assertTrue(is_foreign_enclosure_contract(foreign, alias))
        snapshot = ContractSnapshotCache().build(self.repo_tasks)
        self.assertIn(own, snapshot.contracts)
        self.assertNotIn(foreign, snapshot.contracts)
        self.assertIn(foreign, snapshot.skipped)

    def test_foreign_gone_group_is_still_named_in_refresh(self) -> None:
        task_dir, _own, _group = self._write_real_task()
        foreign, foreign_group = self._write_foreign_enclosure(
            task_dir / "enclosures" / "foreign" / "series-contract.md"
        )
        (foreign_group / "code").rmdir()
        foreign_group.rmdir()
        with self.assertLogs(
            "agents_remember.worktrees.task_document_copies", level="WARNING"
        ) as logs:
            copies.refresh_task_document_copies(
                self.config.coordination_root,
                "demo",
                [(task_dir / "doc.json", task_dir / "doc.md")],
            )
        self.assertIn(foreign.as_posix(), "\n".join(logs.output))

    def test_heal_lists_foreign_contract_as_skipped(self) -> None:
        task_dir, _own, _group = self._write_real_task()
        foreign, _foreign_group = self._write_foreign_enclosure(
            task_dir / "enclosures" / "foreign" / "series-contract.md"
        )
        foreign_bytes = foreign.read_bytes()
        report = heal_contract_leaf_ids(self.config.coordination_root, dry_run=True)
        skipped = cast("list[dict[str, str]]", report["skipped"])
        self.assertIn(
            foreign.as_posix(),
            [row["contract"] for row in skipped],
        )
        self.assertEqual(foreign.read_bytes(), foreign_bytes)

    def test_scope_and_selection_exclude_foreign_contract(self) -> None:
        _task_dir, _own, _group = self._write_real_task()
        foreign, _foreign_group = self._write_foreign_enclosure(
            self.repo_tasks / "master" / "enclosures" / "foreign2" / "series-contract.md",
            repo_name="demo",
        )
        with self.assertLogs(level="WARNING") as logs:
            scopes = list(scope_module._iter_active_contracts(self.config))
            leaves = reader_selection._leaves(self.config, "demo")
        self.assertNotIn(
            foreign.as_posix(),
            [contract.contract_path.as_posix() for contract in scopes],
        )
        leaf_ids = [leaf["leafId"] for leaf in leaves]
        self.assertIn("master-real", leaf_ids)
        self.assertNotIn("master-foreign2", leaf_ids)
        self.assertIn(foreign.as_posix(), "\n".join(logs.output))

    def test_foreign_membership_is_positional(self) -> None:
        task_dir, own, _group = self._write_real_task()
        foreign, _foreign_group = self._write_foreign_enclosure(
            task_dir / "enclosures" / "foreign" / "series-contract.md"
        )
        self.assertEqual(enclosure_repository_tasks(own), self.repo_tasks)
        self.assertEqual(enclosure_repository_tasks(foreign), self.repo_tasks)
        self.assertFalse(is_foreign_enclosure_contract(own, task_dir / "doc.json"))
        self.assertTrue(
            is_foreign_enclosure_contract(
                foreign, self.root / "outside" / "tasks" / "sandbox-app" / "sbx" / "task.json"
            )
        )

    def test_unreadable_file_beside_refreshed_own_is_named_not_raised(self) -> None:
        """Architect ruling C1-01 clause 1: unreadable beside refreshed own."""
        task_dir, own, group = self._write_real_task()
        blocked = task_dir / "enclosures" / "baddir" / "series-contract.md"
        blocked.parent.mkdir(parents=True)
        blocked.mkdir()
        with self.assertLogs(
            "agents_remember.worktrees.task_document_copies", level="WARNING"
        ) as logs:
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", contract_path=str(own), slug="doc"),
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "changed only own leaf"}),
            )
        self.assertIn(blocked.as_posix(), "\n".join(logs.output))
        self.assertEqual(
            (group / "task-documents/master/doc.md").read_bytes(),
            (task_dir / "doc.md").read_bytes(),
        )

    def test_unreadable_only_enclosure_fails_and_names_the_file(self) -> None:
        """Architect ruling C1-01 clause 2: nothing own refreshed, fail closed."""
        task_dir, own, group = self._write_real_task()
        own.unlink()
        own.mkdir()
        before = {path: path.read_bytes() for path in (task_dir / "doc.json", task_dir / "doc.md")}
        with self.assertRaises(Exception) as raised:
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="demo", contract_path=str(own), slug="doc"),
                operation="set_field",
                edit=TaskDocEdit(fields={"objective": "changed own leaf"}),
            )
        self.assertIn(own.as_posix(), str(raised.exception))
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((group / "task-documents").exists())

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents_remember.application.role_launch_context import resolve_role_launch_context
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.cli import role_launch_preparation as preparation
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.role_launcher import LauncherRole, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees import task_document_copies as copies
from agents_remember.worktrees.modules.closeout import closeout_changed_paths
from agents_remember.worktrees.role_reports import remove_group_role_folders


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

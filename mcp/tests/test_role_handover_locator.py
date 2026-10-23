"""The bounded locator a launch names its hand-over artifact through (MIK-R208).

The artifact keeps its own name and bytes beside the report; the agent, the recovery note and the
first message's first line name it through one product-owned directory alias per request, whose
length does not depend on how deep the report folder is. The alias belongs to its request, is
reused by a repeat, rebuilt when lost, refused when it would lead elsewhere, and removed alone
when the seat can no longer resume.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from agents_remember.cli.role_handover_artifacts import (
    artifact_line,
    first_message,
    write_handover_artifact,
)
from agents_remember.cli.role_handover_file import handover_file_reference
from agents_remember.cli.role_handover_publication import publish_handover_files
from agents_remember.worktrees import role_reports
from agents_remember.worktrees.role_handover_locator import (
    ensure_locator,
    is_locator,
    locator_directory,
    locator_root,
    remove_locator,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

REQUEST_ID = "00000000-0000-4000-8000-000000000000"
OTHER_ID = "11111111-1111-4111-8111-111111111111"
HANDOVER = '{"schema": "ar-role-handover/v1"}\n'


class LocatorTestCase(unittest.TestCase):
    """A home of the product's own, and report folders at two very different depths."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        home = self.root / "home"
        home.mkdir()
        environment = patch.dict(os.environ, {"HOME": home.as_posix()})
        environment.start()
        self.addCleanup(environment.stop)

    def folder(self, *parts: str) -> Path:
        folder = self.root.joinpath(*parts)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def shallow(self) -> Path:
        """The report folder of a checkout at the root of a drive."""

        return self.folder("shallow", "role-launch")

    def deep(self) -> Path:
        """A report folder whose path is longer than 318 characters, as a deep enclosure gives it."""

        return self.folder("deep", *(("d" * 45 + str(index)) for index in range(6)), "role-launch")

    def report(self, folder: Path, name: str = "260928-MIK-L199-worker.md") -> str:
        return (folder / name).as_posix()

    def publish(self, folder: Path, *, request_id: str = REQUEST_ID) -> dict[str, Any]:
        report = self.report(folder)
        artifact, _handover_file = publish_handover_files(
            report,
            "Compiled capsule and handover.",
            HANDOVER,
            expected=handover_file_reference(report, HANDOVER),
            request_id=request_id,
        )
        return artifact


class LocatorLinkTests(LocatorTestCase):
    def test_the_locator_is_one_link_of_the_report_folder_and_its_request_reuses_it(self) -> None:
        target = self.shallow()
        locator = locator_directory(REQUEST_ID)
        self.assertFalse(locator.exists() or locator.is_symlink())
        self.assertTrue(is_locator(locator))

        ensure_locator(locator, target)

        self.assertTrue(locator.is_symlink())
        self.assertEqual(locator.resolve(), target.resolve())
        # A repeat of the same request finds the link it made and does not rewrite it.
        created = locator.lstat().st_ino
        ensure_locator(locator, target)
        self.assertEqual(locator.lstat().st_ino, created)

    def test_a_locator_that_would_lead_elsewhere_is_refused_before_anything_else_happens(
        self,
    ) -> None:
        locator = locator_directory(REQUEST_ID)
        shallow, deep = self.shallow(), self.deep()
        ensure_locator(locator, shallow)
        with self.assertRaisesRegex(ValueError, "does not lead to this request's report folder"):
            ensure_locator(locator, deep)
        self.assertEqual(locator.resolve(), shallow.resolve())
        # A real directory at the locator's name is never written through either, and is kept.
        occupied = locator_directory(OTHER_ID)
        occupied.mkdir(parents=True)
        (occupied / "own.txt").write_text("kept", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not lead to this request's report folder"):
            ensure_locator(occupied, shallow)
        self.assertEqual((occupied / "own.txt").read_text(encoding="utf-8"), "kept")

    def test_a_lost_locator_is_rebuilt_at_the_same_place_over_the_same_bytes(self) -> None:
        target = self.deep()
        locator = locator_directory(REQUEST_ID)
        ensure_locator(locator, target)
        content = "Compiled capsule and handover."
        reference = write_handover_artifact(self.report(target), content, locator=locator)
        stored = Path(reference["canonicalPath"])
        written = stored.stat()
        self.assertEqual(Path(reference["path"]).read_text(encoding="utf-8"), content)

        locator.unlink()
        self.assertFalse(locator.exists() or locator.is_symlink())
        ensure_locator(locator, target)

        self.assertEqual(locator.resolve(), target.resolve())
        self.assertEqual(Path(reference["path"]).read_text(encoding="utf-8"), content)
        self.assertEqual(
            (stored.stat().st_ino, stored.stat().st_mtime_ns),
            (written.st_ino, written.st_mtime_ns),
        )


class PublishedArtifactTests(LocatorTestCase):
    def test_a_deep_report_folder_publishes_one_bounded_locator_that_opens_its_bytes(self) -> None:
        target = self.deep()
        report = self.report(target)
        # The pin the requirement states: a 318-character artifact path at least.
        self.assertGreaterEqual(len(Path(report).with_suffix(".handover.txt").as_posix()), 318)

        artifact = self.publish(target)

        self.assertTrue(is_locator(Path(artifact["path"]).parent))
        self.assertEqual(Path(artifact["canonicalPath"]), Path(report).with_suffix(".handover.txt"))
        stored = Path(artifact["canonicalPath"])
        body = stored.read_bytes()
        self.assertEqual(hashlib.sha256(body).hexdigest(), artifact["sha256"])
        # The locator opens exactly those bytes, and the artifact is still written once, read-only.
        self.assertEqual(Path(artifact["path"]).read_bytes(), body)
        self.assertEqual(stat.S_IMODE(stored.stat().st_mode), 0o444)
        line = artifact_line(artifact)
        self.assertTrue(line.startswith(f"AR handover artifact: {artifact['path']} "))
        self.assertEqual(first_message(artifact, body.decode("utf-8")).partition("\n")[0], line)

    def test_the_locator_is_as_long_at_any_folder_depth(self) -> None:
        shallow = self.publish(self.shallow(), request_id=REQUEST_ID)
        deep = self.publish(self.deep(), request_id=OTHER_ID)
        self.assertGreater(
            len(deep["canonicalPath"]) - len(shallow["canonicalPath"]),
            150,
            "the two report folders must differ widely in depth for this comparison to mean anything",
        )
        self.assertEqual(len(deep["path"]), len(shallow["path"]))
        # The locator is the product's root, the request's own id, and the artifact's own name.
        name = Path(deep["canonicalPath"]).name
        self.assertEqual(deep["path"], (locator_root() / OTHER_ID / name).as_posix())

    def test_an_artifact_name_link_is_still_never_followed_through_a_locator(self) -> None:
        target = self.folder("linked", "role-launch")
        locator = locator_directory(REQUEST_ID)
        ensure_locator(locator, target)
        outside = self.root / "outside.txt"
        (target / "260928-MIK-L199-worker.handover.txt").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            write_handover_artifact(self.report(target), "content", locator=locator)
        self.assertFalse(outside.exists())


class LocatorLifeTests(LocatorTestCase):
    def test_the_end_of_resumability_removes_the_alias_and_never_the_reports(self) -> None:
        group = self.shallow().parent
        target = self.shallow()
        kept = target / "260928-MIK-L199-worker.md"
        kept.write_text("the report", encoding="utf-8")
        locator = locator_directory(REQUEST_ID)
        ensure_locator(locator, target)

        preview = remove_locator(locator, within=group, dry_run=True)
        self.assertTrue(preview["would_remove"])
        self.assertTrue(locator.is_symlink())

        outcome = remove_locator(locator, within=group)
        self.assertTrue(outcome["removed"])
        self.assertFalse(locator.is_symlink())
        self.assertEqual(kept.read_text(encoding="utf-8"), "the report")
        self.assertFalse((locator_root() / REQUEST_ID).is_symlink())
        self.assertEqual(remove_locator(locator, within=group)["reason"], "no-locator")

    def test_a_locator_that_leads_outside_the_reclaimed_folders_is_left_standing(self) -> None:
        target = self.folder("elsewhere", "role-launch")
        group = self.folder("group")
        locator = locator_directory(REQUEST_ID)
        ensure_locator(locator, target)
        outcome = remove_locator(locator, within=group)
        self.assertFalse(outcome["removed"])
        self.assertEqual(outcome["reason"], "leads-outside")
        self.assertTrue(locator.is_symlink())

    def test_closing_an_execution_removes_its_locator_and_keeps_its_reports(self) -> None:
        workspace = self.folder("workspace")
        execution = workspace / ".agents-remember" / "role-executions" / REQUEST_ID
        source = execution / "role-reports"
        target = source / "role-launch"
        target.mkdir(parents=True)
        (execution / "scratch").mkdir()
        canonical = self.folder("durable")
        report = Path(self.report(target))
        report.write_text("the report", encoding="utf-8")
        artifact = self.publish(target)
        locator = Path(artifact["path"]).parent
        receipt = {
            "requestId": REQUEST_ID,
            "workspace": {
                "path": workspace.as_posix(),
                "taskReportAccessRoot": source.as_posix(),
                "taskReportRoot": canonical.as_posix(),
                "scratchRoot": (execution / "scratch").as_posix(),
            },
        }

        role_reports.close_execution_role_folders(receipt)

        self.assertFalse(execution.exists())
        self.assertFalse(locator.is_symlink())
        # Every report was collected to its durable place before the folder went.
        self.assertEqual(
            (canonical / "role-launch" / report.name).read_text(encoding="utf-8"), "the report"
        )
        self.assertEqual(
            (canonical / "role-launch" / Path(artifact["canonicalPath"]).name).read_text(
                encoding="utf-8"
            ),
            "Compiled capsule and handover.",
        )

    def test_a_groups_cleanup_removes_its_launches_locators_and_keeps_the_collected_reports(
        self,
    ) -> None:
        group = self.folder("group")
        target = self.folder("group", "role-reports", "worker", REQUEST_ID, "role-launch")
        task_root = self.folder("task")
        (group / "scratch").mkdir()
        Path(self.report(target)).write_text("the report", encoding="utf-8")
        artifact = self.publish(target)
        locator = Path(artifact["path"]).parent
        contract = cast(
            WorktreeContract, SimpleNamespace(worktree_group=group, task_root=task_root)
        )

        preview = role_reports.remove_group_role_folders(contract, dry_run=True)
        self.assertTrue(preview["handover-locators"]["would_remove"])
        self.assertTrue(locator.is_symlink())

        role_reports.remove_group_role_folders(contract, dry_run=False)

        self.assertFalse(locator.is_symlink())
        self.assertFalse((group / "role-reports").exists())
        collected = task_root / "notes" / "reports" / "worker" / REQUEST_ID / "role-launch"
        self.assertTrue((collected / "260928-MIK-L199-worker.md").is_file())
        self.assertTrue((collected / "260928-MIK-L199-worker.handover.txt").is_file())
        self.assertEqual(
            (collected / "260928-MIK-L199-worker.handover.txt").read_text(encoding="utf-8"),
            "Compiled capsule and handover.",
        )


class LocatorHousekeepingTests(LocatorTestCase):
    def test_a_name_that_cannot_be_a_request_id_names_no_locator(self) -> None:
        for name in ("", ".", "..", "a/b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                locator_directory(name)

    def test_the_report_folder_is_collected_before_its_locator_is_removed(self) -> None:
        group = self.folder("group")
        target = self.folder("group", "role-reports", "worker", REQUEST_ID, "role-launch")
        canonical = self.folder("task", "notes", "reports")
        artifact = self.publish(target)
        locator = Path(artifact["path"]).parent
        contract = cast(
            WorktreeContract,
            SimpleNamespace(worktree_group=group, task_root=canonical.parent.parent),
        )
        with (
            patch.object(role_reports, "atomic_replace", side_effect=OSError("disk full")),
            self.assertRaisesRegex(role_reports.RoleReportCollectionError, "disk full"),
        ):
            role_reports.remove_group_role_folders(contract, dry_run=False)
        # No failed collection admits the removal of the aliases either.
        self.assertTrue(locator.is_symlink())
        self.assertTrue(target.is_dir())

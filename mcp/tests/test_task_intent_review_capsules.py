"""Intent-review role capsule sync, enclosure custody, agreed-revision resolver, and master order tests."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import agents_remember
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.cli import role_launch_workspace
from agents_remember.cli.role_launch_workspace import _resolve_workspace
from agents_remember.errors import RolePreparationError
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document import (
    INTENT_REVIEW_REFUSAL,
    resolve_agreed_revision,
)
from agents_remember.tasks.master_order import derive_master_order, load_master_children
from agents_remember.tasks.render import render_markdown
from agents_remember.worktrees.task_leaf_binding import TaskLeafBindingError
from agents_remember.worktrees.task_stage import plan_task_stage
from test_task_document import _config
from test_task_intent_review import (
    GO,
    LEAF_ID,
    LEAF_SLUG,
    LEAF_TASK_ID,
    OTHER_ID,
    OTHER_SLUG,
    PENDING,
    _context,
    _fixture,
    _leaf_contract,
    _leaf_doc,
    _rewrite_leaf,
)


def _found_status(contract: Any, *, preparation: str = "found") -> dict[str, Any]:
    return {
        "ok": True,
        "worktree_group": Path(contract.worktree_group).as_posix(),
        "code_worktree": Path(contract.code_worktree).as_posix(),
        "memory_worktree": Path(contract.memory_worktree).as_posix(),
        "enclosurePreparation": preparation,
    }


class RoleCapsuleSyncTests(unittest.TestCase):
    def test_role_capsule_sentences_and_departures_are_present_and_synced(self) -> None:
        checkout_root = Path(agents_remember.__file__).resolve().parents[3]
        skills_root = checkout_root / "skills" / "l-01-agent-lifecycles"
        packaged_root = (
            Path(agents_remember.__file__).resolve().parent
            / "package_data"
            / "runtime"
            / "skills"
            / "l-01-agent-lifecycles"
        )
        planning = (skills_root / "operations" / "planning.md").read_text(encoding="utf-8")
        architect = (skills_root / "roles" / "architect.md").read_text(encoding="utf-8")
        coordination = (skills_root / "operations" / "coordination.md").read_text(encoding="utf-8")
        # Sentence A + both up-front-workspace departures + preserved approval recovery.
        self.assertIn("go, or review first?", planning)
        self.assertIn("planned comparison", planning)
        self.assertIn("without a task workspace", planning)
        self.assertIn("explainer workspace creates on first Explain", planning)
        self.assertIn("intentReview", planning)
        self.assertIn("intent-review-open", planning)
        self.assertIn("without re-asking", planning)
        # Sentence B (copied developer SHA-256 match) and Sentence C (claim).
        self.assertIn("From explainer ·", architect)
        self.assertIn("SHA-256", architect)
        self.assertIn("developer-messages/", architect)
        self.assertIn("explainer_control", architect)
        self.assertIn("claim", architect)
        # Sentence D (Manager waits on intent-review-open and never retries).
        self.assertIn("intent-review-open", coordination)
        self.assertIn(
            "never relay handovers or retry `intent-review-open` before Architect `go`",
            coordination,
        )
        for rel in (
            "operations/planning.md",
            "roles/architect.md",
            "operations/coordination.md",
        ):
            self.assertEqual(
                (skills_root / rel).read_bytes(),
                (packaged_root / rel).read_bytes(),
            )


class FoundEnclosureMoveTests(unittest.TestCase):
    """MIK-R189 v2: the found live enclosure makes the move as the last act of the resolution."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))
        self.contract = _leaf_contract(self.fixture)

    def _resolve(self, *, preparation: str = "found") -> dict[str, str]:
        status = _found_status(self.contract, preparation=preparation)
        with patch.object(
            role_launch_workspace,
            "_ensure_leaf_enclosure",
            return_value=(
                self.contract.contract_path,
                status,
            ),
        ):
            return _resolve_workspace(self.fixture.config, _context(self.fixture))

    def test_a_found_live_enclosure_moves_the_leaf_and_its_master_row(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=GO))
        workspace = self._resolve()
        self.assertEqual(workspace["enclosurePreparation"], "found")
        leaf = read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json")
        self.assertEqual(leaf.status, "inProgress")
        # The found contract's identity is written onto the leaf with the stage.
        self.assertEqual(
            [item.enclosurePath for item in leaf.enclosures],
            [self.contract.contract_path.as_posix()],
        )
        master = read_task_doc(self.fixture.task_root / "task.json")
        self.assertEqual(
            [row.status for row in master.subTasks if row.number == LEAF_ID], ["inProgress"]
        )

    def test_a_refused_start_at_a_found_enclosure_moves_nothing(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=PENDING))
        leaf_before = (self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes()
        master_before = (self.fixture.task_root / "task.json").read_bytes()
        with self.assertRaises(RolePreparationError) as raised:
            self._resolve()
        self.assertEqual(raised.exception.status, INTENT_REVIEW_REFUSAL)
        self.assertEqual((self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes(), leaf_before)
        self.assertEqual((self.fixture.task_root / "task.json").read_bytes(), master_before)

    def test_a_created_enclosure_and_an_already_started_leaf_are_untouched(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=GO))
        leaf_before = (self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes()
        self._resolve(preparation="created")
        self.assertEqual((self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes(), leaf_before)
        # A leaf already inProgress writes nothing on a repeat start.
        plan_task_stage(self.contract, "inProgress").publish()
        started = (self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes()
        master_started = (self.fixture.task_root / "task.json").read_bytes()
        self._resolve()
        self.assertEqual((self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes(), started)
        self.assertEqual((self.fixture.task_root / "task.json").read_bytes(), master_started)

    def test_a_move_that_cannot_be_written_is_a_named_refusal(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=GO))
        failure = TaskLeafBindingError(
            "the leaf enclosure binding is missing", status="task-enclosure-binding-missing"
        )
        with (
            patch.object(role_launch_workspace, "publish_start_task_facts", side_effect=failure),
            self.assertRaises(RolePreparationError) as raised,
        ):
            self._resolve()
        self.assertEqual(raised.exception.status, "task-enclosure-binding-missing")
        self.assertIn("No seat was made", raised.exception.next_action)

    def test_enclosure_copies_reference_folder_and_agreed_revision_excluding_binding_and_warns_on_error(
        self,
    ) -> None:
        ref_dir = self.fixture.task_root / "notes" / "reference" / LEAF_ID
        (ref_dir / "nested").mkdir(parents=True)
        (ref_dir / "mockup.md").write_text("# Mockup\n", encoding="utf-8")
        (ref_dir / "nested" / "spec.txt").write_text("nested spec\n", encoding="utf-8")
        (ref_dir / "binding.json").write_text('{"secret": true}\n', encoding="utf-8")
        rev_dir = (
            self.fixture.task_root
            / "notes"
            / "reports"
            / "review-frames"
            / LEAF_ID.lower()
            / "explainer"
            / "revisions"
            / "7"
        )
        (rev_dir / "assets" / "sub").mkdir(parents=True)
        manifest_bytes = b'{"entrypoint": {"path": "index.html"}, "revision": 7}\n'
        (rev_dir / "manifest.json").write_bytes(manifest_bytes)
        (rev_dir / "binding.json").write_text('{"env": "excluded"}\n', encoding="utf-8")
        (rev_dir / "assets" / "index.html").write_text("<html>agreed</html>\n", encoding="utf-8")
        (rev_dir / "assets" / "sub" / "app.css").write_text("body {}\n", encoding="utf-8")
        _rewrite_leaf(
            self.fixture,
            _leaf_doc(
                intent_review={
                    **GO,
                    "agreedRevision": {
                        "revisionId": "rev-7",
                        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                        "packetVersions": ["MIK-R189@v2"],
                    },
                }
            ),
        )
        workspace = self._resolve(preparation="created")
        out_ref = Path(workspace["referenceRoot"])
        self.assertNotIn("referenceWarning", workspace)
        self.assertEqual((out_ref / "mockup.md").read_text(encoding="utf-8"), "# Mockup\n")
        self.assertEqual(
            (out_ref / "nested" / "spec.txt").read_text(encoding="utf-8"), "nested spec\n"
        )
        self.assertEqual((out_ref / "manifest.json").read_bytes(), manifest_bytes)
        self.assertEqual(
            (out_ref / "index.html").read_text(encoding="utf-8"), "<html>agreed</html>\n"
        )
        self.assertEqual(
            (out_ref / "assets" / "sub" / "app.css").read_text(encoding="utf-8"), "body {}\n"
        )
        self.assertFalse((out_ref / "binding.json").exists())
        # Retry preserves sealed custody even if source files change afterwards.
        (ref_dir / "mockup.md").write_text("# Mutated after creation\n", encoding="utf-8")
        self._resolve(preparation="found")
        self.assertEqual((out_ref / "mockup.md").read_text(encoding="utf-8"), "# Mockup\n")
        # If custody is unsealed and agreed revision is missing, start continues with referenceWarning.
        (Path(self.contract.worktree_group) / ".ar-product" / "reference-custody.json").unlink()
        _rewrite_leaf(
            self.fixture,
            _leaf_doc(
                intent_review={
                    **GO,
                    "agreedRevision": {
                        "revisionId": "rev-99",
                        "sha256": "c" * 64,
                        "packetVersions": ["MIK-R189@v2"],
                    },
                }
            ),
        )
        warned = self._resolve(preparation="created")
        self.assertIn("referenceWarning", warned)
        self.assertIn("agreed-revision-missing", warned["referenceWarning"])


class AgreedRevisionResolverTests(unittest.TestCase):
    """I13: one canonical resolver, from the strict native fields to the exact retained revision.

    The go is recorded through the real ``task_doc`` route; the retained revisions live in the
    leaf's frame store. The same validated agreement identifies the revision, seals its manifest
    bytes with the recorded digest and supplies that exact manifest -- the yardstick the removal
    pin protects and later receipts cite.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.coord = Path(self._tmp.name) / "coord"
        self.coord.mkdir()
        self.config = _config(self.coord)
        self.target = TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x")
        self.master_target = TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="task")
        task_doc_tool(
            self.config,
            self.master_target,
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "series",
                    "slug": "series",
                    "title": "Series",
                    "kind": "master",
                    "repo": "agents-remember",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
                }
            ),
        )
        task_doc_tool(
            self.config,
            self.master_target,
            operation="set_subtask",
            edit=TaskDocEdit(
                subtask={
                    "number": LEAF_TASK_ID,
                    "name": "Smoke",
                    "file": "03c_x.md",
                    "status": "planning",
                }
            ),
        )
        self.fields = {
            "id": LEAF_TASK_ID,
            "slug": "03c_x",
            "title": "Smoke",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-01-01T00:00:00Z",
        }
        task_doc_tool(
            self.config,
            self.target,
            operation="create",
            edit=TaskDocEdit(fields=dict(self.fields)),
        )
        self.task_root = self.coord / "tasks" / "agents-remember" / "3c-x"

    def _retain(self, number: int, *, content: str = "") -> tuple[Path, bytes]:
        """Retain one revision in the leaf's explainer frame store and return its manifest."""

        rev_dir = (
            self.task_root
            / "notes"
            / "reports"
            / "review-frames"
            / "testdoc-leaf"
            / "explainer"
            / "revisions"
            / str(number)
        )
        rev_dir.mkdir(parents=True, exist_ok=True)
        manifest_bytes = f'{{"revision": {number}, "content": {json.dumps(content)}}}\n'.encode()
        (rev_dir / "manifest.json").write_bytes(manifest_bytes)
        return rev_dir, manifest_bytes

    def _go(self, revision: dict[str, Any]) -> None:
        # The native route: the architect records the developer's answer through task_doc.
        task_doc_tool(
            self.config,
            self.target,
            operation="set_field",
            edit=TaskDocEdit(fields={"intentReview": {"state": "go", **revision}}),
            call=TaskDocCall(),
        )

    def test_the_native_go_resolves_to_the_exact_retained_revision_manifest_and_digest(
        self,
    ) -> None:
        _rev_dir, manifest_bytes = self._retain(7)
        self._retain(8)
        self._go(
            {
                "decisionRef": "D369",
                "agreedRevision": {
                    "revisionId": "rev-7",
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "packetVersions": ["MIK-R189@v2", "MIK-R106@v2"],
                },
            }
        )
        document = read_task_doc(self.task_root / "03c_x.json")
        resolved = resolve_agreed_revision(self.task_root, document)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.revision, 7)
        self.assertEqual(resolved.revision_id, "rev-7")
        self.assertEqual(resolved.packet_versions, ("MIK-R189@v2", "MIK-R106@v2"))
        self.assertEqual(resolved.manifest_path.parent.name, "7")
        self.assertEqual(resolved.manifest_bytes, manifest_bytes)
        self.assertTrue(resolved.digest_verified)
        # The pin names revision 7 alone: the other retained revision is not the yardstick.
        self.assertNotEqual(resolved.manifest_path, self._retain(8)[0] / "manifest.json")

    def test_a_tampered_manifest_resolves_unverified_and_a_missing_one_resolves_nothing(
        self,
    ) -> None:
        _rev_dir, manifest_bytes = self._retain(7)
        self._go(
            {
                "agreedRevision": {
                    "revisionId": "rev-7",
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "packetVersions": ["MIK-R189@v2"],
                }
            }
        )
        document = read_task_doc(self.task_root / "03c_x.json")
        rev_dir = self._retain(7)[0]
        rev_dir.joinpath("manifest.json").write_bytes(b"tampered bytes\n")
        resolved = resolve_agreed_revision(self.task_root, document)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        # The revision is still identified -- removal refuses instead of guessing -- but the
        # recorded digest no longer seals its bytes.
        self.assertEqual(resolved.revision, 7)
        self.assertFalse(resolved.digest_verified)
        # A revision the record does not name resolves to nothing at all.
        task_doc_tool(
            self.config,
            self.target,
            operation="set_field",
            edit=TaskDocEdit(
                fields={
                    "intentReview": {
                        "state": "go",
                        "agreedRevision": {
                            "revisionId": "rev-99",
                            "sha256": "a" * 64,
                            "packetVersions": ["MIK-R189@v2"],
                        },
                    }
                }
            ),
            call=TaskDocCall(),
        )
        self.assertIsNone(
            resolve_agreed_revision(self.task_root, read_task_doc(self.task_root / "03c_x.json"))
        )

    def test_loose_records_do_not_resolve_and_a_symlink_on_the_way_is_refused(self) -> None:
        _rev_dir, manifest_bytes = self._retain(7)
        self._go(
            {
                "agreedRevision": {
                    "revisionId": "rev-7",
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "packetVersions": ["MIK-R189@v2"],
                }
            }
        )
        # A loose record without the strict revisionId is not the native agreement.
        loose = {
            "intentReview": {
                "state": "go",
                "agreedRevision": {
                    "revision": 7,
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                },
            }
        }
        self.assertIsNone(resolve_agreed_revision(self.task_root, loose, leaf_id=LEAF_TASK_ID))
        # A digest that is not a SHA-256 fails the strict schema too.
        self.assertIsNone(
            resolve_agreed_revision(
                self.task_root,
                {
                    "intentReview": {
                        "state": "go",
                        "agreedRevision": {"revisionId": "rev-7", "sha256": "not-a-digest"},
                    }
                },
                leaf_id=LEAF_TASK_ID,
            )
        )
        # A symlink on the way is refused by name, never followed.
        foreign = Path(self._tmp.name) / "foreign"
        foreign.mkdir()
        (foreign / "notes").mkdir()
        (foreign / "notes" / "reports").symlink_to(
            self.task_root / "notes" / "reports", target_is_directory=True
        )
        with self.assertRaisesRegex(ValueError, "agreed-revision-symlink-refused"):
            resolve_agreed_revision(foreign, read_task_doc(self.task_root / "03c_x.json"))


class MasterOrderStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))
        # A declared waitsFor makes the master carry a structured order.
        write_task_doc(
            self.fixture.task_root,
            _leaf_doc(OTHER_ID, OTHER_SLUG, intent_review=GO, waits_for=[]),
        )
        write_task_doc(
            self.fixture.task_root,
            _leaf_doc(LEAF_ID, LEAF_SLUG, intent_review=PENDING, waits_for=[OTHER_ID]),
        )

    def test_a_pending_leaf_is_left_out_of_ready_and_the_order_shows_it(self) -> None:
        master = read_task_doc(self.fixture.task_root / "task.json")
        children = load_master_children(self.fixture.task_root, master)
        order = derive_master_order(master, children)
        assert order is not None
        self.assertIn(OTHER_ID, order.ready_ids)
        self.assertNotIn(LEAF_ID, order.ready_ids)
        rows = {task.id: task for task in order.tasks}
        self.assertEqual(rows[LEAF_ID].intent_review, "pending")
        self.assertTrue(rows[LEAF_ID].intent_review_open)
        self.assertEqual(rows[OTHER_ID].intent_review, "go")
        rendered = render_markdown(master, master_order=order)
        self.assertIn(
            f"| [{LEAF_ID} — Leaf {LEAF_ID}]({LEAF_SLUG}.md) | planning · intent review open |",
            rendered,
        )
        self.assertIn(f"| [{OTHER_ID} — Leaf {OTHER_ID}]({OTHER_SLUG}.md) | planning |", rendered)


if __name__ == "__main__":
    unittest.main()

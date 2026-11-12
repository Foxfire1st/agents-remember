"""MIK-R198 rule 6a: a reference's note is revised in place, and nothing else moves.

Evidence item 8a, each behavior with the test that proves it:

- **the fails-before case.** On a converted scratch pair whose source moved, a table row naming ``[n]``
  re-resolves that reference's targets: the recorded anchor changes. The note-only revision changes the
  note and leaves every target, anchor and other reference byte-identical, and mints no reference number.
- a missing key, a stale input (a base digest that is not the sidecar's current bytes), an empty note, an
  absent card and an absent sidecar are refused, and the sidecar is left exactly as it was;
- the leaf the contract encloses is recorded as the author, and a dry run writes nothing;
- **mechanical non-rewrite:** the shipped fixer re-points a moved anchor and leaves its note unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application import memory_tools
from agents_remember.application.memory_tools import CitationOperationScope
from agents_remember.memory.conversion import card_authoring
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json
from knowledge_conversion_test_support import (
    APP,
    APP_CARD,
    APP_SIDECAR,
    APP_SOURCE,
    CodeFixture,
    code_repository,
    memory_repository,
)

FIXED_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}

LEAF = "260928-MIK-L191"


def _frozen_git() -> mock._patch_dict:
    return mock.patch.dict(os.environ, FIXED_GIT_ENV)


def _converted(root: Path) -> tuple[CodeFixture, Path]:
    code = code_repository(root / "code")
    memory = root / "memory"
    memory_repository(memory, code)
    outcome = convert_memory(
        memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
    )
    write_changed(memory, outcome.changed)
    return code, memory


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _references(memory: Path) -> dict[str, Any]:
    return parse_json((memory / APP_SIDECAR).read_text(encoding="utf-8"))["references"]


def _without_note(document: dict[str, Any], key: str) -> str:
    stripped = json.loads(json.dumps(document))
    stripped["references"][key].pop("note", None)
    return canonical_text(stripped)


def _leaf_scope(memory: Path, code: CodeFixture) -> SimpleNamespace:
    return SimpleNamespace(
        repo_id="demo",
        onboarding_root=memory / "onboarding",
        code_root=code.root,
        contract=SimpleNamespace(leaf_id=LEAF, worktree_name="l191"),
    )


def _revise(  # noqa: PLR0913 -- the tests state the revision's named inputs
    memory: Path,
    *,
    card: str,
    number: int,
    note: str,
    base_digest: str,
    author: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """The revision API as the tests state it: one request object, one answer."""

    return card_authoring.revise_reference_note(
        card_authoring.NoteRevision(
            memory_root=memory,
            card=card,
            number=number,
            note=note,
            base_digest=base_digest,
            author=author,
        ),
        dry_run=dry_run,
    )


class NoteRevisionTests(unittest.TestCase):
    """Rule 6a's revision: one note changes, nothing else does."""

    def test_a_table_row_re_resolves_where_the_revision_keeps_the_anchor(self) -> None:
        """The fails-before case: the installed re-authoring route moves the anchor, the revision cannot."""

        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            code, memory = _converted(Path(raw))
            sidecar = memory / APP_SIDECAR
            card = memory / APP_CARD
            card_bytes, sidecar_bytes = card.read_bytes(), sidecar.read_bytes()
            recorded = _references(memory)["4"]["targets"]
            # The source moves: line 5 no longer holds what reference 4 recorded.
            (code.root / APP).write_text("# moved\n" + APP_SOURCE, encoding="utf-8")
            # A citation row naming [4] is the installed way to re-author that reference: the curator
            # deletes the evidence line and writes the row in its place.
            original = card_bytes.decode("utf-8")
            cited = "- A quoted literal keeps its range. [4]\n"
            self.assertIn(cited, original)
            card.write_text(
                original.replace(cited, "")
                + "\n| Finding | Anchor | Source |\n| --- | --- | --- |\n"
                + "| A quoted literal keeps its range. [4] | `alpha` | src/pkg/app.py:5-5 |\n",
                encoding="utf-8",
            )
            card_authoring.author_card_references(memory, code.root, only="src/pkg/app.py.md")
            reauthored = _references(memory)["4"]["targets"]
            self.assertNotEqual(
                recorded,
                reauthored,
                "the table route left the recorded anchor alone, so this test no longer "
                "demonstrates the gap rule 6a closes",
            )
            # Same pair, restored: the revision keeps exactly what the table route moved.
            card.write_bytes(card_bytes)
            sidecar.write_bytes(sidecar_bytes)
            result = _revise(
                memory,
                card=APP_CARD,
                number=4,
                note="A quoted literal keeps its range, at the line it was read from.",
                base_digest=_digest(sidecar),
                author=LEAF,
            )
            revised = _references(memory)
            self.assertEqual(revised["4"]["targets"], recorded)
            self.assertEqual(
                revised["4"]["note"],
                "A quoted literal keeps its range, at the line it was read from.",
            )
            self.assertEqual(set(revised), set(_references(memory)))
            self.assertEqual(result["reference"], "4")
            self.assertEqual(result["author"], LEAF)
            self.assertTrue(result["changed"])
            self.assertTrue(result["dryRun"] is False)
            self.assertEqual(result["digestAfter"], _digest(sidecar))

    def test_every_other_reference_and_the_card_are_untouched(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            _code, memory = _converted(Path(raw))
            sidecar = memory / APP_SIDECAR
            before = parse_json(sidecar.read_text(encoding="utf-8"))
            before_card = (memory / APP_CARD).read_bytes()
            _revise(
                memory,
                card=APP_CARD,
                number=2,
                note="alpha and beta compute the total, read at the file's first commit.",
                base_digest=_digest(sidecar),
                author=LEAF,
            )
            after = parse_json(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(_without_note(after, "2"), _without_note(before, "2"))
            self.assertEqual(set(after["references"]), set(before["references"]))
            self.assertEqual(
                sorted(after["references"]),
                sorted(before["references"], key=int),
                "a number was minted",
            )
            self.assertEqual((memory / APP_CARD).read_bytes(), before_card)

    def test_a_missing_key_is_refused_and_the_sidecar_is_unchanged(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            _code, memory = _converted(Path(raw))
            sidecar = memory / APP_SIDECAR
            before = sidecar.read_bytes()
            with self.assertRaises(card_authoring.NoteRevisionRefused) as caught:
                _revise(
                    memory,
                    card=APP_CARD,
                    number=99,
                    note="a note for nothing",
                    base_digest=_digest(sidecar),
                    author=LEAF,
                )
            self.assertEqual(caught.exception.code, "reference-missing")
            self.assertIn("99", caught.exception.detail)
            self.assertEqual(sidecar.read_bytes(), before)

    def test_a_stale_input_is_refused_and_the_sidecar_is_unchanged(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            _code, memory = _converted(Path(raw))
            sidecar = memory / APP_SIDECAR
            before = sidecar.read_bytes()
            with self.assertRaises(card_authoring.NoteRevisionRefused) as caught:
                _revise(
                    memory,
                    card=APP_CARD,
                    number=2,
                    note="a note written against other bytes",
                    base_digest="0" * 64,
                    author=LEAF,
                )
            self.assertEqual(caught.exception.code, "stale-input")
            self.assertEqual(sidecar.read_bytes(), before)

    def test_an_empty_note_is_refused(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            _code, memory = _converted(Path(raw))
            with self.assertRaises(card_authoring.NoteRevisionRefused) as caught:
                _revise(
                    memory,
                    card=APP_CARD,
                    number=2,
                    note="   \n ",
                    base_digest=_digest(memory / APP_SIDECAR),
                    author=LEAF,
                )
            self.assertEqual(caught.exception.code, "note-empty")

    def test_an_absent_card_and_an_absent_sidecar_are_refused_by_name(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            _code, memory = _converted(Path(raw))
            with self.assertRaises(card_authoring.NoteRevisionRefused) as absent:
                _revise(
                    memory,
                    card="onboarding/src/pkg/nothing.py.md",
                    number=1,
                    note="a note for a card that is not there",
                    base_digest="0" * 64,
                    author=LEAF,
                )
            self.assertEqual(absent.exception.code, "card-absent")
            untracked = memory / "onboarding/src/pkg/manual.md"
            untracked.write_text("# manual\n\nNothing anchored here.\n", encoding="utf-8")
            with self.assertRaises(card_authoring.NoteRevisionRefused) as no_sidecar:
                _revise(
                    memory,
                    card="onboarding/src/pkg/manual.md",
                    number=1,
                    note="a note for a card with no sidecar",
                    base_digest="0" * 64,
                    author=LEAF,
                )
            self.assertEqual(no_sidecar.exception.code, "sidecar-absent")

    def test_an_absent_author_is_refused(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            _code, memory = _converted(Path(raw))
            with self.assertRaises(card_authoring.NoteRevisionRefused) as caught:
                _revise(
                    memory,
                    card=APP_CARD,
                    number=2,
                    note="a note nobody owns",
                    base_digest=_digest(memory / APP_SIDECAR),
                    author=" ",
                )
            self.assertEqual(caught.exception.code, "author-absent")

    def test_a_card_operand_cannot_leave_the_admitted_memory_tree(self) -> None:
        """Scope admission names the tree; the operand is confined to it, before any read is trusted.

        A sibling memory tree next to this one is a real, readable, converted tree holding the very card
        name asked for. The scope was admitted for this tree alone, so a ``..`` operand, an absolute
        operand and a symlinked operand are each refused by name, and the sibling's bytes are unchanged.
        """

        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _code, memory = _converted(root)
            sibling = root / "sibling-memory"
            sibling.mkdir()
            for name in ("onboarding", "knowledge"):
                (sibling / name).mkdir()
            (sibling / "onboarding" / "app.py.md").write_text("# a card of another tree\n", "utf-8")
            sibling_sidecar = sibling / APP_SIDECAR
            sibling_sidecar.parent.mkdir(parents=True, exist_ok=True)
            sibling_sidecar.write_text(
                json.dumps(
                    {
                        "schema": "ar-file-sidecar/v1",
                        "path": APP_SOURCE,
                        "references": {"1": {"note": "the sibling's own claim", "targets": []}},
                    }
                )
                + "\n",
                "utf-8",
            )
            before = _digest(sibling_sidecar)
            sibling_card = sibling / APP_CARD
            for card, code in (
                (f"../sibling-memory/{APP_CARD}", "card-escapes-memory"),
                (str(sibling_card), "card-escapes-memory"),
            ):
                with self.subTest(card=card):
                    with self.assertRaises(card_authoring.NoteRevisionRefused) as refused:
                        _revise(
                            memory,
                            card=card,
                            number=1,
                            note="a claim written into another tree",
                            base_digest=before,
                            author=LEAF,
                        )
                    self.assertEqual(refused.exception.code, code)
                    self.assertEqual(_digest(sibling_sidecar), before)
            link = memory / "onboarding" / "linked"
            link.mkdir(exist_ok=True)
            (link / "app.py.md").symlink_to(sibling_card)
            with self.assertRaises(card_authoring.NoteRevisionRefused) as refused_link:
                _revise(
                    memory,
                    card="onboarding/linked/app.py.md",
                    number=1,
                    note="a claim written through a symlink",
                    base_digest=before,
                    author=LEAF,
                )
            self.assertEqual(refused_link.exception.code, "card-escapes-memory")
            self.assertEqual(_digest(sibling_sidecar), before)

    def test_a_dry_run_writes_nothing_and_previews_the_digest(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            _code, memory = _converted(Path(raw))
            sidecar = memory / APP_SIDECAR
            before = sidecar.read_bytes()
            result = _revise(
                memory,
                card=APP_CARD,
                number=3,
                note="The method sits in the holder, as read in this file.",
                base_digest=_digest(sidecar),
                author=LEAF,
                dry_run=True,
            )
            self.assertTrue(result["dryRun"])
            self.assertEqual(sidecar.read_bytes(), before)
            self.assertNotEqual(result["digestAfter"], result["digestBefore"])


class NoteRevisionToolTests(unittest.TestCase):
    """The application route: the leaf's own memory worktree, the leaf recorded as author."""

    def test_the_tool_revises_one_note_and_records_the_contracts_leaf(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            code, memory = _converted(Path(raw))
            sidecar = memory / APP_SIDECAR
            with mock.patch.object(
                memory_tools, "_leaf_memory_writer_scope", return_value=_leaf_scope(memory, code)
            ):
                result = memory_tools.citation_note_revision_tool(
                    cast(Any, None),
                    repo_id="demo",
                    contract_path="contract.md",
                    card=APP_CARD,
                    reference=7,
                    note="The test proves alpha, at the test file's first commit.",
                    base_digest=_digest(sidecar),
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["operation"], "citation_note_revision")
            self.assertEqual(result["author"], LEAF)
            self.assertEqual(result["reference"], "7")
            self.assertEqual(
                parse_json(sidecar.read_text(encoding="utf-8"))["references"]["7"]["note"],
                "The test proves alpha, at the test file's first commit.",
            )

    def test_the_tool_refuses_a_stale_input_with_a_named_code(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            code, memory = _converted(Path(raw))
            with mock.patch.object(
                memory_tools, "_leaf_memory_writer_scope", return_value=_leaf_scope(memory, code)
            ):
                result = memory_tools.citation_note_revision_tool(
                    cast(Any, None),
                    repo_id="demo",
                    contract_path="contract.md",
                    card=APP_CARD,
                    reference=2,
                    note="a note against other bytes",
                    base_digest="f" * 64,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "stale-input")
            self.assertEqual(result["nextAction"], "citation_note_revision")

    def test_unconverted_memory_is_refused_with_the_legacy_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "onboarding").mkdir()
            scope = SimpleNamespace(
                repo_id="demo",
                onboarding_root=root / "onboarding",
                code_root=root / "code",
                contract=SimpleNamespace(leaf_id=LEAF, worktree_name="l191"),
            )
            with mock.patch.object(memory_tools, "_leaf_memory_writer_scope", return_value=scope):
                result = memory_tools.citation_note_revision_tool(
                    cast(Any, None),
                    repo_id="demo",
                    contract_path="contract.md",
                    card=APP_CARD,
                    reference=1,
                    note="a note for a tree that is not converted",
                    base_digest="0" * 64,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], memory_tools.LEGACY_FORMAT_CODE)


class MechanicalNonRewriteTests(unittest.TestCase):
    """Rule 6a: re-pointing an anchor leaves its note exactly as it was."""

    def test_a_repoint_changes_the_anchor_and_not_the_note(self) -> None:
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            code, memory = _converted(Path(raw))
            before = _references(memory)
            note = before["3"]["note"]
            anchor = before["3"]["targets"][0]["anchor"]["blob"]
            # A pure move: the blob changes, the symbol's bytes do not.
            (code.root / APP).write_text("# moved\n" + APP_SOURCE, encoding="utf-8")
            with mock.patch.object(
                memory_tools, "_leaf_memory_writer_scope", return_value=_leaf_scope(memory, code)
            ):
                result = memory_tools.citation_fix_tool(
                    cast(Any, None),
                    repo_id="demo",
                    contract_path="contract.md",
                    operation_scope=CitationOperationScope(document="src/pkg/app.py.md"),
                )
            self.assertTrue(result.get("ok", True), result)
            after = _references(memory)
            self.assertNotEqual(after["3"]["targets"][0]["anchor"]["blob"], anchor)
            self.assertEqual(after["3"]["note"], note)


if __name__ == "__main__":
    unittest.main()

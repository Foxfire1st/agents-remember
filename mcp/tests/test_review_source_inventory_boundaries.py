"""The exact source-change inventory (ICR-R02) at its real boundaries.

Moved unchanged out of ``test_knowledge_diff_boundaries.py`` along its own section boundary, so that
module stays well under the file-size limit. These cases need a real Git repository with a tab and a
newline in a name, a binary file, a symlink, a type change, a mode-only change, a submodule pointer
and a pathname that is not valid text, so they live in the integration lane.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from agents_remember.application.review_source_inventory import (
    byte_form,
    review_inventory,
    tree_difference_observation,
)
from agents_remember.memory.knowledge.diff_display import TreePaths, TreeSide
from diff_scope_test_support import (
    _git,
    independent_changed_records,
)

pytestmark = pytest.mark.integration

# --- the exact source-change inventory (ICR-R02) -----------------------------------------------
#
# The inventory is measured from two exact Git objects and must list *every* changed path of that
# pair, with the status Git reported, whatever the path is called and whatever its content is. These
# cases use a real repository whose base and candidate trees carry the packet's own boundary cases --
# a tab and a newline inside a name, a binary file, a symlink whose target moved, a file that changed
# type, a mode-only change, a submodule pointer and an ordinary edit -- and every one of them is
# compared against an independent Git observation of the same pair rather than against a list
# written by hand.

TAB_NEWLINE_PATH = "src/tab\tnewline\nname.py"
BINARY_PATH = "assets/blob.dat"
SYMLINK_PATH = "links/current"
MODE_PATH = "scripts/run.sh"
TYPED_PATH = "src/typed"
SUBMODULE_PATH = "vendor/module"
PLAIN_PATH = "src/plain.py"
DELETED_PATH = "src/gone.py"
BASE_GITLINK = "1" * 40
CANDIDATE_GITLINK = "2" * 40

# A filename whose bytes are not valid UTF-8. This is the one case a ``str`` path cannot create --
# Python encodes a ``str`` path to the filesystem encoding -- so the fixture writes it through the
# exact bytes, which is also what makes the case a real one rather than a simulated one.
NON_UTF8_NAME = b"src/caf\xe9-latin1.py"
NON_UTF8_BYTE_FORM = "b'src/caf\\xe9-latin1.py'"


# The base commit's files, as ``path -> (kind, content)``. A mapping rather than one call per file
# because the *set* is the fixture's contract: every change class the packet names has to be here.
BASE_FILES: Mapping[str, bytes] = {
    PLAIN_PATH: b"one\n",
    DELETED_PATH: b"removed later\n",
    BINARY_PATH: b"binary\x00base\n",
    MODE_PATH: b"#!/bin/sh\necho base\n",
    TYPED_PATH: b"a regular file\n",
}


@dataclass(frozen=True)
class InventoryFixture:
    """Two real commits whose trees carry every change class the packet names."""

    root: Path
    base_commit: str
    candidate_commit: str

    @property
    def candidate_tree(self) -> str:
        return _git(self.root, ["rev-parse", f"{self.candidate_commit}^{{tree}}"])

    def sides(self) -> tuple[TreeSide, TreeSide]:
        """The bound pair, as the measurement's own sides: the base *commit* against the tree."""

        return (
            TreeSide(tree_id=self.base_commit, root=str(self.root)),
            TreeSide(tree_id=self.candidate_tree, root=str(self.root)),
        )


def build_inventory_fixture(directory: Path, *, non_utf8: bool = False) -> InventoryFixture:
    """Build the real repository: one base commit, one candidate commit, every change class.

    ``non_utf8`` adds the one filename that is not valid UTF-8, which a Python ``str`` path cannot
    even express: it is the case that separates "this surface carries paths" from "this surface
    carries *text*".
    """

    root = directory / "inventory"
    root.mkdir(parents=True)
    _git(root, ["init", "-q", "-b", "main"])
    _git(root, ["config", "user.email", "fixture@example.invalid"])
    _git(root, ["config", "user.name", "fixture"])
    for directory_path in ("src", "assets", "links", "scripts"):
        (root / directory_path).mkdir(parents=True)
    for path, content in BASE_FILES.items():
        (root / path).write_bytes(content)
    (root / SYMLINK_PATH).symlink_to(PLAIN_PATH)
    _git(root, ["add", "-A"])
    _git(root, ["update-index", "--add", "--cacheinfo", f"160000,{BASE_GITLINK},{SUBMODULE_PATH}"])
    _git(root, ["commit", "-qm", "base"])

    (root / PLAIN_PATH).write_text("one\ntwo\n", encoding="utf-8")
    (root / DELETED_PATH).unlink()
    (root / BINARY_PATH).write_bytes(b"binary\x00candidate\n")
    (root / TAB_NEWLINE_PATH).write_text("a name that is not a separator\n", encoding="utf-8")
    (root / MODE_PATH).chmod(0o755)
    (root / TYPED_PATH).unlink()
    (root / TYPED_PATH).symlink_to(PLAIN_PATH)
    (root / SYMLINK_PATH).unlink()
    (root / SYMLINK_PATH).symlink_to(BINARY_PATH)
    if non_utf8:
        with open(os.fsencode(root) + b"/" + NON_UTF8_NAME, "wb") as handle:
            handle.write(b"# a name that is not text\n")
    _git(root, ["add", "-A"])
    _git(
        root,
        ["update-index", "--add", "--cacheinfo", f"160000,{CANDIDATE_GITLINK},{SUBMODULE_PATH}"],
    )
    _git(root, ["commit", "-qm", "candidate"])
    return InventoryFixture(
        root=root,
        base_commit=_git(root, ["rev-parse", "HEAD~1"]),
        candidate_commit=_git(root, ["rev-parse", "HEAD"]),
    )


# Git's own status letters, restated here so the comparison is against the repository's answer and
# not against a helper the implementation also uses.
GIT_STATUS_LETTERS = {"A": "added", "D": "deleted", "M": "modified", "T": "type_changed"}


def test_the_inventory_lists_every_changed_path_of_the_bound_pair_with_gits_own_status(
    tmp_path: Path,
) -> None:
    """Every changed path is listed once, with Git's own status, for two mixed commit/tree objects.

    The base side is the *commit* and the candidate side the captured *tree*, which is the pair a
    live review binds: an implementation that only accepted commits, or only trees, would report a
    different population here than the repository really holds.
    """

    fixture = build_inventory_fixture(tmp_path)
    inventory = review_inventory(*fixture.sides())

    assert inventory.state == "measured", inventory.detail
    assert inventory.partial is False
    observed = independent_changed_records(
        fixture.root, fixture.base_commit, fixture.candidate_tree
    )
    listed = {entry.path: entry.status for entry in inventory.entries}
    assert listed == {
        PLAIN_PATH: "modified",
        DELETED_PATH: "deleted",
        TAB_NEWLINE_PATH: "added",
        BINARY_PATH: "modified",
        SYMLINK_PATH: "modified",
        MODE_PATH: "modified",
        TYPED_PATH: "type_changed",
        SUBMODULE_PATH: "modified",
    }
    # The returned count is the whole population and it is the same population Git reports: not a
    # page, not a filtered view and not a list this test wrote down.
    assert inventory.listed_total == len(inventory.entries) == len(observed)
    assert set(listed) == set(observed)
    assert {path: GIT_STATUS_LETTERS[letter] for path, letter in observed.items()} == listed
    assert inventory.command.endswith(f"{fixture.base_commit} {fixture.candidate_tree}")
    assert inventory.before_code_tree_id == fixture.base_commit
    assert inventory.after_code_tree_id == fixture.candidate_tree


def test_a_tab_and_a_newline_in_a_filename_survive_as_the_address_of_the_change(
    tmp_path: Path,
) -> None:
    """The unusual path is returned verbatim, and the line-parsed interface is shown losing it.

    Git quotes and escapes a pathname containing a tab or a newline when it prints lines, so a reader
    that split lines holds a *different* string from the one the same file is expanded by. Both facts
    are measured here: the inventory carries the exact name, and the line-oriented command's output
    is asserted not to contain it -- which is the defect this interface exists to remove.
    """

    fixture = build_inventory_fixture(tmp_path)
    inventory = review_inventory(*fixture.sides())

    assert TAB_NEWLINE_PATH in [entry.path for entry in inventory.entries]
    assert TAB_NEWLINE_PATH in independent_changed_records(
        fixture.root, fixture.base_commit, fixture.candidate_tree
    )
    # The same file really is at that address, so the identity the inventory published is usable.
    assert (fixture.root / TAB_NEWLINE_PATH).is_file()
    line_oriented = _git(
        fixture.root,
        ["diff", "--name-only", "--no-renames", fixture.base_commit, fixture.candidate_tree],
    )
    assert TAB_NEWLINE_PATH not in line_oriented.splitlines()
    assert "\\t" in line_oriented and "\\n" in line_oriented


def test_a_path_whose_content_cannot_be_rendered_is_still_listed_with_its_own_kind(
    tmp_path: Path,
) -> None:
    """A binary file, a symlink, a submodule pointer and a mode-only change keep their entries.

    Renderability is reported and never used as a filter: each of these paths cannot be shown as
    text (a null byte, a link target, a commit pointer, or bytes that did not move at all), and each
    is listed with the fact that says so.
    """

    fixture = build_inventory_fixture(tmp_path)
    inventory = review_inventory(*fixture.sides())
    by_path = {entry.path: entry for entry in inventory.entries}

    assert by_path[BINARY_PATH].content == "binary"
    assert by_path[SYMLINK_PATH].content == "symlink"
    assert by_path[TAB_NEWLINE_PATH].content == "text"
    assert by_path[PLAIN_PATH].content == "text"
    assert by_path[SUBMODULE_PATH].content == "submodule"
    assert by_path[SUBMODULE_PATH].status == "modified"
    # A mode-only change keeps its bytes and moves its permission word, and both facts are stated.
    assert by_path[MODE_PATH].mode_change is True
    assert by_path[MODE_PATH].status == "modified"
    assert by_path[PLAIN_PATH].mode_change is False
    assert by_path[DELETED_PATH].content == "binary" or by_path[DELETED_PATH].content == "text"
    assert all(entry.detail is None for entry in inventory.entries)


def test_a_measurement_that_could_not_be_made_is_unavailable_and_never_a_measured_empty_set(
    tmp_path: Path,
) -> None:
    """An unobtainable comparison states its reason; the measured empty set is the control.

    The two are the packet's two opposite failure modes: a fabricated empty list reads as "nothing
    changed", and an unavailable measurement that reported zero paths would be exactly that. The
    control is the pair that really has no differences, whose zero must be readable as a
    *measurement*.
    """

    fixture = build_inventory_fixture(tmp_path)
    before, _ = fixture.sides()
    unobtainable = review_inventory(before, TreeSide(tree_id="0" * 40, root=str(fixture.root)))
    assert unobtainable.state == "unavailable"
    assert unobtainable.entries == ()
    assert unobtainable.listed_total == 0
    assert unobtainable.partial is False
    assert fixture.base_commit in unobtainable.detail
    assert "no working tree or HEAD was substituted" in unobtainable.detail

    # The same commit against its own tree: identical content, measured rather than assumed.
    identical = review_inventory(before, TreeSide(tree_id=before.tree_id, root=str(fixture.root)))
    assert identical.state == "measured"
    assert identical.entries == ()
    assert identical.listed_total == 0
    assert "measured empty change set" in identical.detail
    assert identical.detail != unobtainable.detail


def test_an_observation_that_named_paths_without_statuses_is_partial_and_still_lists_them(
    tmp_path: Path,
) -> None:
    """A coarser observation is labelled partial rather than rendered as a complete empty list."""

    fixture = build_inventory_fixture(tmp_path)
    named = (PLAIN_PATH, TAB_NEWLINE_PATH)

    def paths_only(_before: TreeSide, _after: TreeSide) -> TreePaths:
        return TreePaths(available=True, paths=named)

    inventory = review_inventory(*fixture.sides(), probe=paths_only)

    assert inventory.state == "measured"
    assert inventory.partial is True
    assert [entry.path for entry in inventory.entries] == list(named)
    assert all(
        entry.status == "unknown" and entry.content == "unknown" for entry in inventory.entries
    )
    assert all(entry.detail for entry in inventory.entries)


def test_a_pathname_that_is_not_valid_text_is_carried_by_its_bytes_and_never_dropped(
    tmp_path: Path,
) -> None:
    """A non-UTF-8 name is measured, listed by its byte form, and labelled partial -- never a crash.

    ``run_git`` decodes Git's output with ``surrogateescape``, so the change is observed and its path
    arrives as lone surrogates, which the surface's own text fields refuse. The measurement boundary
    is therefore where the fact has to be stated: the observation stays available, the uncarried path
    is kept whole in its byte form, the rest of the change set is still listed, and the response says
    it is partial. Dropping the path would make a partial change set read as a whole one, and raising
    would answer a review request with a crash instead of a reason.
    """

    fixture = build_inventory_fixture(tmp_path, non_utf8=True)
    before, after = fixture.sides()

    observed = tree_difference_observation(before, after)

    assert observed.available is True
    assert observed.partial is True
    assert [byte_form(change.path) for change in observed.unrepresentable] == [NON_UTF8_BYTE_FORM]
    assert NON_UTF8_BYTE_FORM in observed.detail
    # The carried path is not in the text list: it has no text form, and pretending otherwise is the
    # defect this case exists for. The check is written out rather than imported so it states what it
    # means: no path carried as text may contain a surrogate-escaped byte.
    assert all(
        not any(0xDC80 <= ord(character) <= 0xDCFF for character in path) for path in observed.paths
    )

    inventory = review_inventory(before, after)
    observed_count = len(
        independent_changed_records(fixture.root, fixture.base_commit, fixture.candidate_tree)
    )
    assert inventory.state == "measured"
    assert inventory.partial is True
    assert [entry.path_bytes for entry in inventory.unrepresentable_paths] == [NON_UTF8_BYTE_FORM]
    assert inventory.unrepresentable_paths[0].status == "added"
    assert inventory.listed_total == len(inventory.entries) == observed_count - 1
    # The renderable remainder is still listed in full: the odd name costs one entry, not the review.
    assert {PLAIN_PATH, DELETED_PATH, TAB_NEWLINE_PATH, SUBMODULE_PATH} <= {
        entry.path for entry in inventory.entries
    }
    assert NON_UTF8_BYTE_FORM in inventory.detail

    # The control: the same repository without the odd name is a complete measurement, so ``partial``
    # above is caused by that name and not by the fixture.
    clean = build_inventory_fixture(tmp_path / "clean")
    complete = review_inventory(*clean.sides())
    assert complete.partial is False
    assert complete.unrepresentable_paths == ()
    assert complete.listed_total == len(
        independent_changed_records(clean.root, clean.base_commit, clean.candidate_tree)
    )

"""One inventory entry opened into the two bound endpoints' actual content (ICR-R03).

``ICR-R03@v1`` requires every inventory entry to open the exact available before/after source
content *for its bound comparison*, with truthful per-side states for content that cannot be
rendered, and with no silent fallback to a working tree. These cases measure that through the
operations the dashboard really serves: a real leaf enclosure with a real linked worktree, the real
review payload's own inventory (``/api/review/intent``), the real capture owner, and the real
expansion route (``/api/review/intent/source-content``) wired to the real application owner. Nothing
here injects a prebuilt expansion, a fake diff payload or a hand-assembled resolution, and every
assertion reads bytes back out of Git independently of the owner under test.

The load-bearing properties, one case each:

* a modified file opens *both* endpoints' own bytes -- the recorded base tree's and the captured
  candidate tree's -- and the two differ exactly as those objects do;
* an added file opens its entire candidate text with a measured absence on the before side, and a
  deleted file the mirror image: the packet's conforming example;
* binary, symlink, submodule and mode-only entries are opened with their own kind, their exact
  object identity and their exact size, and no text is invented for them;
* the expansion is bound to the *listed* generation: advancing the branch between the listing and the
  expansion leaves the listed content in place, reports the leaf's candidate as superseded, and never
  serves the new bytes under the old generation's name;
* a missing object is `unavailable` on its own side while the other side stays inspectable, and
  nothing reads the working tree to fill the gap; and
* a path outside the measured change set, and a baseline that is not this leaf's recorded base, are
  refused by name rather than read.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import (
    list_knowledge_review_entries,
    read_knowledge_review,
    review_records_for,
)
from agents_remember.application.review_source_content import (
    EXPANSION_TEXT_BYTES,
    SOURCE_CONTENT_REFERENCE,
    read_review_source_content,
)
from agents_remember.serving.review import register_review_routes
from diff_scope_test_support import _git
from fastapi import FastAPI
from fastapi.testclient import TestClient
from read_scope_test_support import AUXILIARY_PATH, MISMATCH_PATH, SYNCHRONIZATION_PATH
from test_knowledge_review_source_endpoints import (
    ELIGIBLE_UNTRACKED_PATH,
    LEAF_ID,
    MODIFIED_PATH,
    STAGED_ADDITION_PATH,
    UNMAPPED_PATH,
    EndpointFixture,
    build_endpoint_fixture,
)

pytestmark = pytest.mark.evidence_unit

# The candidate-only content this leaf adds to the fixture's worktree, one path per content class the
# packet names. They are real filesystem objects -- a real binary blob, a real symlink, a real nested
# repository recorded as a gitlink -- because each class is a measurement of what Git actually
# reports, not a state a test can assert into existence.
BINARY_PATH = "src/asset.bin"
BINARY_BYTES = b"# binary asset\n\x00\x01\x02\xff not text\n"
SYMLINK_PATH = "src/link_to_batch.py"
SYMLINK_TARGET = "batch.py"
SUBMODULE_PATH = "vendor/lib"
SUBMODULE_INNER = "inner.txt"
MODE_ONLY_PATH = AUXILIARY_PATH
TYPE_CHANGE_PATH = MISMATCH_PATH
TYPE_CHANGE_TARGET = "batch.py"
OVERSIZED_PATH = "src/oversized.py"
# A name that is not a line: the inventory keeps it as one address, and opening its content must use
# that same string rather than a re-quoted or re-split spelling of it.
ODD_NAME_PATH = "src/tab\tnewline\nname.py"
ODD_NAME_TEXT = "# a name with a tab and a newline\n"
OVERSIZED_LINE = "# a line of the oversized file\n"
OVERSIZED_LINES = (EXPANSION_TEXT_BYTES // len(OVERSIZED_LINE)) + 64


# The independent observation of one object's bytes: the same question a reader would ask by hand,
# asked through Git directly so the assertion is not a restatement of the owner under test. The bytes
# come back undecoded, so an observation of binary content is byte-exact as well.
def _blob_text(root: Path, revision: str, path: str) -> str:
    """The exact text of ``revision:path``, observed through Git rather than through the owner."""

    raw = _subprocess_git(root, ["show", f"{revision}:{path}"])
    assert raw is not None, f"{revision}:{path} is not in this repository"
    return raw.decode("utf-8")


def _object_id(root: Path, revision: str, path: str) -> str | None:
    """The object ``revision:path`` resolves to, or ``None`` when the path is not there."""

    raw = _subprocess_git(root, ["rev-parse", f"{revision}:{path}"])
    return None if raw is None else raw.decode("ascii").strip()


def _subprocess_git(root: Path, args: list[str]) -> bytes | None:
    """Run one independent Git observation, returning ``None`` when Git answered with an error."""

    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


@dataclass(frozen=True)
class UnusualContent:
    """The identities of the content classes this leaf adds to the endpoint fixture's worktree."""

    submodule_commit: str
    oversized_bytes: int


def _materialize_unusual(fixture: EndpointFixture) -> UnusualContent:
    """Write one real instance of every content class the packet names into the leaf's worktree."""

    worktree = fixture.worktree
    (worktree / BINARY_PATH).write_bytes(BINARY_BYTES)
    link = worktree / SYMLINK_PATH
    if not link.exists():
        os.symlink(SYMLINK_TARGET, link)
    nested = worktree / SUBMODULE_PATH
    nested.mkdir(parents=True, exist_ok=True)
    _git(nested, ["init", "-q", "."])
    (nested / SUBMODULE_INNER).write_text("inner\n", encoding="utf-8")
    _git(nested, ["add", "-A"])
    _git(
        nested,
        [
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=fixture",
            "commit",
            "-qm",
            "inner",
        ],
    )
    # A mode-only change: the bytes at this path are identical on both sides and only the permission
    # word moves, which is the one change a content read must not present as an edit.
    os.chmod(worktree / MODE_ONLY_PATH, 0o755)
    # A type change: a tracked regular file becomes a symlink, so the before side is a document and
    # the after side is a link target.
    replaced = worktree / TYPE_CHANGE_PATH
    replaced.unlink()
    os.symlink(TYPE_CHANGE_TARGET, replaced)
    oversized = worktree / OVERSIZED_PATH
    oversized.write_text(OVERSIZED_LINE * OVERSIZED_LINES, encoding="utf-8")
    (worktree / ODD_NAME_PATH).write_text(ODD_NAME_TEXT, encoding="utf-8")
    return UnusualContent(
        submodule_commit=_git(nested, ["rev-parse", "HEAD"]),
        oversized_bytes=len((OVERSIZED_LINE * OVERSIZED_LINES).encode("utf-8")),
    )


@pytest.fixture
def content_fixture(tmp_path: Path) -> tuple[EndpointFixture, UnusualContent]:
    """One fresh live enclosure, extended with every content class, per case."""

    fixture = build_endpoint_fixture(tmp_path / "content", datasets=False)
    return fixture, _materialize_unusual(fixture)


def _served(fixture: EndpointFixture) -> FastAPI:
    """The real routes over the real application owners, wired as the composition root wires them."""

    app = FastAPI()
    config = fixture.config
    register_review_routes(
        app,
        config,
        lambda request: read_knowledge_review(config, request, review_records_for(config, request)),
        lambda repository_id, master, leaf_id: list_knowledge_review_entries(
            config, repository_id, master, leaf_id
        ),
        lambda request: read_review_source_content(config, request),
    )
    return app


def _listed_inventory(fixture: EndpointFixture) -> dict:
    """The inventory the browser is reading: the real review route's own source pane."""

    with TestClient(_served(fixture)) as client:
        response = client.get(
            "/api/review/intent",
            params={
                "repo": fixture.repository_id,
                "master": fixture.master,
                "leaf": LEAF_ID,
            },
        )
    assert response.status_code == 200, response.text
    return response.json()["payload"]["source"]["inventory"]


def _expand(fixture: EndpointFixture, path: str, *, before: str, after: str) -> dict:
    """One entry opened through the real route, or the served refusal body when it refuses."""

    with TestClient(_served(fixture)) as client:
        response = client.get(
            "/api/review/intent/source-content",
            params={
                "repo": fixture.repository_id,
                "master": fixture.master,
                "leaf": LEAF_ID,
                "path": path,
                "beforeCodeTreeId": before,
                "afterCodeTreeId": after,
            },
        )
    body = response.json()
    body["_status"] = response.status_code
    return body


def _expansion(fixture: EndpointFixture, path: str, *, listed: dict | None = None) -> dict:
    """The expansion of one path at the generation the listing published, or the refusal body."""

    inventory = _listed_inventory(fixture) if listed is None else listed
    return _expand(
        fixture,
        path,
        before=inventory["before_code_tree_id"],
        after=inventory["after_code_tree_id"],
    )


def _side(body: dict, side: str) -> dict:
    assert body["state"] == "content", body
    return body["expansion"][side]


def _unchanged_paths(fixture: EndpointFixture, inventory: dict) -> list[str]:
    """The recorded base tree's paths that the listed change set does not contain.

    They are the falsifiers for path confinement: each one is a real file at the recorded base and in
    no change set, so a route that reads any addressable path serves them and the packet's boundary
    is what forbids it.
    """

    changed = {entry["path"] for entry in inventory["entries"]}
    return sorted(
        path
        for path in _git(
            fixture.contract.code_repo_path,
            ["ls-tree", "-r", "--name-only", inventory["before_code_tree_id"]],
        ).splitlines()
        if path not in changed
    )


# -- the content of each listed entry -----------------------------------------------------------


def test_a_modified_file_opens_both_endpoints_own_bytes(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """The modified path shows the base tree's bytes and the candidate tree's, read independently.

    The before text is measured here against ``git show <base>:<path>`` and the after text against
    ``git show <captured tree>:<path>`` -- two observations the owner under test did not produce --
    so "the expansion carries the content of the two bound objects" is a measurement of this
    fixture rather than a restatement of the read.
    """

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    body = _expand(
        fixture,
        MODIFIED_PATH,
        before=inventory["before_code_tree_id"],
        after=inventory["after_code_tree_id"],
    )

    before, after = _side(body, "before"), _side(body, "after")
    assert before["state"] == "present" and after["state"] == "present"
    assert before["text"] == _blob_text(
        fixture.contract.code_repo_path, inventory["before_code_tree_id"], MODIFIED_PATH
    )
    assert after["text"] == _blob_text(
        fixture.worktree, inventory["after_code_tree_id"], MODIFIED_PATH
    )
    assert before["text"] != after["text"], "the two bound objects hold different bytes here"
    assert before["object_id"] == _object_id(
        fixture.contract.code_repo_path, inventory["before_code_tree_id"], MODIFIED_PATH
    )
    assert after["object_id"] == _object_id(
        fixture.worktree, inventory["after_code_tree_id"], MODIFIED_PATH
    )
    assert body["expansion"]["status"] == "modified"
    assert body["expansion"]["currentness"] == "current"
    assert body["expansion"]["before_code_tree_id"] == inventory["before_code_tree_id"]
    assert body["expansion"]["after_code_tree_id"] == inventory["after_code_tree_id"]
    assert body["expansion"]["language"] == "python"
    # The reproduction names the exact objects the answer rests on, beside the content and not
    # instead of it.
    assert after["object_id"] in body["expansion"]["command"]


def test_an_added_unmapped_file_opens_its_entire_candidate_text(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """The packet's conforming example: an unmapped added file opens its whole candidate text."""

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    listed = {entry["path"]: entry for entry in inventory["entries"]}
    assert listed[UNMAPPED_PATH]["status"] == "added"
    assert listed[STAGED_ADDITION_PATH]["status"] == "added"

    body = _expansion(fixture, UNMAPPED_PATH, listed=inventory)
    before, after = _side(body, "before"), _side(body, "after")

    assert before["state"] == "absent"
    assert "no entry at this path" in before["detail"]
    assert before.get("text") is None
    assert after["state"] == "present"
    assert after["text"] == (fixture.worktree / UNMAPPED_PATH).read_text(encoding="utf-8")
    assert after["text"].count("\n") == 2, "the entire candidate text, not a diff or a path list"
    assert body["expansion"]["status"] == "added"


def test_a_deleted_file_opens_its_entire_base_text_beside_a_measured_absence(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """A deletion is the mirror image: the base text is complete and the after side is absent."""

    fixture, _content = content_fixture
    body = _expansion(fixture, SYNCHRONIZATION_PATH)

    before, after = _side(body, "before"), _side(body, "after")
    assert body["expansion"]["status"] == "deleted"
    assert before["state"] == "present"
    assert before["text"] == _blob_text(
        fixture.contract.code_repo_path,
        body["expansion"]["before_code_tree_id"],
        SYNCHRONIZATION_PATH,
    )
    assert after["state"] == "absent"
    assert after.get("text") is None
    assert not (fixture.worktree / SYNCHRONIZATION_PATH).exists(), "the file really is gone"


def test_a_tab_and_a_newline_in_a_name_is_the_address_its_content_opens_at(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """The listed path is the address: a name with a tab and a newline opens its own object.

    The inventory keeps such a name whole, and this is the other half of that promise -- the same
    string reaches the entry when it is expanded, with no re-quoting, no path-splitting and no
    substituted neighbour whose name happens to match a pattern.
    """

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    listed = {entry["path"]: entry for entry in inventory["entries"]}
    assert ODD_NAME_PATH in listed, sorted(listed)

    body = _expansion(fixture, ODD_NAME_PATH, listed=inventory)
    after = _side(body, "after")

    assert body["expansion"]["path"] == ODD_NAME_PATH
    assert after["state"] == "present"
    assert after["text"] == ODD_NAME_TEXT
    assert after["object_id"] == _object_id(
        fixture.worktree, inventory["after_code_tree_id"], ODD_NAME_PATH
    )


def test_a_binary_entry_states_its_kind_identity_and_size_with_no_text(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """Binary content is listed with its exact object and size, and never as an empty document."""

    fixture, _content = content_fixture
    body = _expansion(fixture, BINARY_PATH)
    after = _side(body, "after")

    assert after["state"] == "binary"
    assert after.get("text") is None
    assert after["byte_length"] == len(BINARY_BYTES)
    assert after["object_id"] == _object_id(
        fixture.worktree, body["expansion"]["after_code_tree_id"], BINARY_PATH
    )
    assert "NUL" in after["detail"]
    assert _side(body, "before")["state"] == "absent"


def test_a_symlink_entry_carries_the_link_target_and_never_a_document(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """A symlink's content is its target, and the state says so rather than presenting file bytes."""

    fixture, _content = content_fixture
    body = _expansion(fixture, SYMLINK_PATH)
    after = _side(body, "after")

    assert after["state"] == "symlink"
    assert after["text"] == SYMLINK_TARGET
    assert after["object_id"] == _object_id(
        fixture.worktree, body["expansion"]["after_code_tree_id"], SYMLINK_PATH
    )
    assert "link target" in after["detail"]


def test_a_submodule_entry_reports_the_recorded_pointer_and_no_file_bytes(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """A gitlink is reported as the commit it records, and no blob is invented for it."""

    fixture, content = content_fixture
    body = _expansion(fixture, SUBMODULE_PATH)
    after = _side(body, "after")

    assert after["state"] == "submodule"
    assert after.get("text") is None
    assert after["object_id"] == content.submodule_commit
    assert after["object_id"] == _object_id(
        fixture.worktree, body["expansion"]["after_code_tree_id"], SUBMODULE_PATH
    )
    assert "submodule pointer" in after["detail"]
    assert _side(body, "before")["state"] == "absent"


def test_a_mode_only_change_shows_identical_bytes_and_names_the_mode(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """Only the permission word moved: both sides hold the same bytes and the mode change is stated."""

    fixture, _content = content_fixture
    body = _expansion(fixture, MODE_ONLY_PATH)
    before, after = _side(body, "before"), _side(body, "after")

    assert body["expansion"]["mode_change"] is True
    assert body["expansion"]["status"] == "modified"
    assert before["state"] == "present" and after["state"] == "present"
    assert before["text"] == after["text"] == "# anchors\n"
    assert before["object_id"] == after["object_id"], (
        "the content is unchanged; only the mode moved"
    )


def test_a_type_change_opens_each_side_by_its_own_kind(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """A file that became a symlink reads as a document on one side and a target on the other."""

    fixture, _content = content_fixture
    body = _expansion(fixture, TYPE_CHANGE_PATH)

    assert body["expansion"]["status"] == "type_changed"
    before, after = _side(body, "before"), _side(body, "after")
    assert before["state"] == "present"
    assert before["text"] == "# timeout\nchanged bytes\n"
    assert after["state"] == "symlink"
    assert after["text"] == TYPE_CHANGE_TARGET


def test_oversized_content_is_a_stated_bounded_expansion(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """Content past the bound is carried as a prefix that says it is one, with the exact size."""

    fixture, content = content_fixture
    body = _expansion(fixture, OVERSIZED_PATH)
    after = _side(body, "after")

    assert after["state"] == "present"
    assert after["truncated"] is True
    assert after["byte_length"] == content.oversized_bytes
    assert len(after["text"]) < content.oversized_bytes
    assert after["text"] == (OVERSIZED_LINE * OVERSIZED_LINES)[: len(after["text"])]
    assert str(content.oversized_bytes) in after["detail"]
    # The whole object stays reachable by the identity carried beside the bounded text.
    assert after["object_id"] in body["expansion"]["command"]


# -- the generation the content is bound to -----------------------------------------------------


def test_the_expansion_stays_bound_when_the_branch_advances_after_the_listing(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """The listed generation's bytes are served after the branch moves, and the move is stated.

    This is the packet's "advance the branch between list and expansion" exercise. The listing is
    taken first; then a real commit lands in the worktree, which changes the file the listing named;
    then the entry is expanded with the *listed* generation's ids. The served text must be the listed
    generation's bytes -- a working-tree or ``HEAD`` read would serve the new ones -- and the answer
    must say the leaf has moved past them. The control at the end shows the route is not merely
    frozen: re-listing yields the new generation, whose expansion carries the new bytes.
    """

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    listed_before = _blob_text(
        fixture.contract.code_repo_path, inventory["before_code_tree_id"], MODIFIED_PATH
    )
    listed_after = _blob_text(fixture.worktree, inventory["after_code_tree_id"], MODIFIED_PATH)

    (fixture.worktree / MODIFIED_PATH).write_text(
        "# batch\ncommitted after the listing was taken\n", encoding="utf-8"
    )
    _git(fixture.worktree, ["add", "-A"])
    _git(
        fixture.worktree,
        [
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=fixture",
            "commit",
            "-qm",
            "advance",
        ],
    )
    advanced = (fixture.worktree / MODIFIED_PATH).read_text(encoding="utf-8")
    assert advanced not in (listed_before, listed_after)

    body = _expand(
        fixture,
        MODIFIED_PATH,
        before=inventory["before_code_tree_id"],
        after=inventory["after_code_tree_id"],
    )

    assert _side(body, "before")["text"] == listed_before
    assert _side(body, "after")["text"] == listed_after
    assert _side(body, "after")["text"] != advanced, "the new bytes must not fill the listed side"
    assert body["expansion"]["currentness"] == "superseded"
    assert body["expansion"]["after_code_tree_id"] == inventory["after_code_tree_id"]
    assert "moved" in body["expansion"]["currentness_detail"]

    relisted = _listed_inventory(fixture)
    assert relisted["after_code_tree_id"] != inventory["after_code_tree_id"]
    current = _expand(
        fixture,
        MODIFIED_PATH,
        before=relisted["before_code_tree_id"],
        after=relisted["after_code_tree_id"],
    )
    assert current["expansion"]["currentness"] == "current"
    assert _side(current, "after")["text"] == advanced


def test_a_missing_object_is_unavailable_on_its_side_while_the_other_stays_inspectable(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """One side's object is gone: that side says so and the other side's text is still readable.

    The requested generation names a candidate tree this repository does not hold, which is the
    state a pruned or never-placed object produces. The before side is still measured from the
    recorded base, the pair's change set is stated as unmeasured rather than empty, and no working
    tree byte is substituted for the side that could not be read.
    """

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    absent_tree = "0" * 40
    body = _expand(
        fixture,
        MODIFIED_PATH,
        before=inventory["before_code_tree_id"],
        after=absent_tree,
    )

    before, after = _side(body, "before"), _side(body, "after")
    assert before["state"] == "present"
    assert before["text"] == _blob_text(
        fixture.contract.code_repo_path, inventory["before_code_tree_id"], MODIFIED_PATH
    )
    assert after["state"] == "unavailable"
    assert after.get("text") is None
    assert absent_tree in after["detail"]
    assert body["expansion"]["status"] == "unknown", "the pair's change set was not measured"
    assert body["expansion"]["after_code_tree_id"] == absent_tree
    assert body["expansion"]["currentness"] == "superseded"
    # The path is still admitted by a *measured* change set: the leaf's own, which is why this read
    # stays a change-set read even when the requested generation cannot be measured.
    assert body["expansion"]["path_bound"] == "leaf_change_set"
    assert "could not be measured" in body["expansion"]["path_bound_detail"]
    assert "the candidate tree it binds now" in body["expansion"]["path_bound_detail"]


def test_a_pruned_base_blob_is_unavailable_on_its_side_while_the_candidate_side_is_served(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """One *blob* is gone while both trees are whole: the affected side says so, the other is served.

    This is the state a prune leaves behind, and it is deliberately not the same fact as an unmeasured
    pair: both trees are there, so the requested generation *is* measured and the entry is admitted by
    its own change set, while the side whose object was pruned reports ``unavailable`` naming the
    object it could not read. No working-tree byte and no other object stands in for it -- which is the
    behaviour the path-confinement fix had to leave untouched.
    """

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    before_oid = _object_id(
        fixture.contract.code_repo_path, inventory["before_code_tree_id"], MODIFIED_PATH
    )
    assert before_oid is not None
    common = Path(
        _git(
            fixture.contract.code_repo_path,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        ).strip()
    )
    loose = common / "objects" / before_oid[:2] / before_oid[2:]
    assert loose.is_file(), f"the fixture's base blob is not loose at {loose}"
    loose.unlink()

    body = _expansion(fixture, MODIFIED_PATH, listed=inventory)
    before, after = _side(body, "before"), _side(body, "after")

    assert before["state"] == "unavailable"
    assert before.get("text") is None
    assert before_oid in before["detail"], "the unreadable object is named"
    assert after["state"] == "present"
    assert after["text"] == _blob_text(
        fixture.worktree, inventory["after_code_tree_id"], MODIFIED_PATH
    )
    assert body["expansion"]["status"] == "modified"
    assert body["expansion"]["path_bound"] == "requested_generation"


def test_an_unmeasured_generation_still_confines_the_path_to_a_measured_change_set(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """The independent verifier's finding: an unmeasured pair must not become an open file reader.

    With an after generation this repository does not hold, the requested pair cannot be measured, and
    the path gate used to be skipped in exactly that state -- any addressable path was then served
    from the recorded base. The path is now bounded by the change set this leaf's review publishes, so
    a path in no change set is refused while a changed path still opens (the case above). Both
    falsifying paths are exercised: a path that is not a change of the pair at all, and the same
    question asked of the repository's own root file.
    """

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    unchanged = _unchanged_paths(fixture, inventory)
    assert unchanged, "the fixture holds a path the pair did not change"
    absent_tree = "0" * 40

    for outside in (unchanged[0], unchanged[-1]):
        body = _expand(
            fixture,
            outside,
            before=inventory["before_code_tree_id"],
            after=absent_tree,
        )
        assert body["_status"] == 400, body
        assert body["state"] == "refused"
        assert body["refusal"]["code"] == "source_content_unresolved"
        assert outside in body["refusal"]["detail"]
        assert "expansion" not in body
        # The refusal states both facts a reader needs: the requested generation was not measurable,
        # and the leaf's own change set is what bounded the request.
        assert absent_tree in body["refusal"]["detail"]
        assert "could not be measured" in body["refusal"]["detail"]
        assert "changed path(s)" in body["refusal"]["detail"]


def test_a_generation_that_names_a_commit_is_refused_rather_than_served(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """The after generation is a *tree* id: a commit this repository holds is refused by name.

    A complete ``HEAD`` commit id can be diffed and looked up, so only the object's own type separates
    it from the generation the listing published -- and serving HEAD's committed bytes under a field
    documented as a tree id would be the substitution this read exists to prevent. The control beside
    it shows the refusal is about the object's kind and not about the endpoint: the listed tree id
    still opens.
    """

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    head_commit = _git(fixture.worktree, ["rev-parse", "HEAD"]).strip()
    assert len(head_commit) == 40 and head_commit != inventory["after_code_tree_id"]
    assert _git(fixture.worktree, ["cat-file", "-t", head_commit]).strip() == "commit"

    body = _expand(
        fixture,
        MODIFIED_PATH,
        before=inventory["before_code_tree_id"],
        after=head_commit,
    )

    assert body["_status"] == 400, body
    assert body["state"] == "refused"
    assert body["refusal"]["code"] == "source_content_unresolved"
    assert head_commit in body["refusal"]["detail"]
    assert "commit" in body["refusal"]["detail"]
    assert "expansion" not in body

    served = _expand(
        fixture,
        MODIFIED_PATH,
        before=inventory["before_code_tree_id"],
        after=inventory["after_code_tree_id"],
    )
    assert served["state"] == "content", served
    assert served["expansion"]["after_code_tree_id"] == inventory["after_code_tree_id"]
    assert served["expansion"]["path_bound"] == "requested_generation"


# -- refusals that keep the route to the inventory's own population -----------------------------


def test_a_path_outside_the_measured_change_set_is_refused_by_name(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """An unchanged path no recorded realization links is refused: with no knowledge bound at all,
    the route reads only the inventory's entries (attributed unchanged context is covered in
    ``test_knowledge_review_attributed_source_content.py``)."""

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    unchanged = _unchanged_paths(fixture, inventory)
    assert unchanged, "the fixture holds a path the pair did not change"
    outside = unchanged[0]

    body = _expand(
        fixture,
        outside,
        before=inventory["before_code_tree_id"],
        after=inventory["after_code_tree_id"],
    )

    assert body["_status"] == 400, body
    assert body["state"] == "refused"
    assert body["refusal"]["code"] == "source_content_unresolved"
    assert outside in body["refusal"]["detail"]
    assert "expansion" not in body


def test_a_baseline_that_is_not_the_recorded_base_is_refused(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """The server reads this leaf's recorded base and substitutes no other generation."""

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    body = _expand(
        fixture,
        MODIFIED_PATH,
        before=inventory["after_code_tree_id"],
        after=inventory["after_code_tree_id"],
    )

    assert body["_status"] == 400, body
    assert body["state"] == "refused"
    assert body["refusal"]["code"] == "source_content_unresolved"
    assert fixture.contract.code_base_commit in body["refusal"]["detail"]
    assert inventory["after_code_tree_id"] in body["refusal"]["offending_input"]


def test_a_query_that_does_not_name_the_generation_is_refused_by_the_transport(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """A missing tree id is a bad request: the server never chooses the generation for a caller."""

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)

    with TestClient(_served(fixture)) as client:
        response = client.get(
            "/api/review/intent/source-content",
            params={
                "repo": fixture.repository_id,
                "master": fixture.master,
                "leaf": LEAF_ID,
                "path": MODIFIED_PATH,
                "beforeCodeTreeId": inventory["before_code_tree_id"],
            },
        )

    assert response.status_code == 400, response.text
    assert response.json()["status"] == "bad-request"
    assert "afterCodeTreeId" in response.json()["expected"]


def test_an_unwired_process_refuses_the_route_by_name(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """A process composed without the owner refuses, rather than serving an empty file."""

    fixture, _content = content_fixture
    app = FastAPI()
    register_review_routes(app, fixture.config, None)

    with TestClient(app) as client:
        response = client.get(
            "/api/review/intent/source-content",
            params={
                "repo": fixture.repository_id,
                "master": fixture.master,
                "leaf": LEAF_ID,
                "path": MODIFIED_PATH,
                "beforeCodeTreeId": "a" * 40,
                "afterCodeTreeId": "b" * 40,
            },
        )

    assert response.status_code == 503, response.text
    assert "no review adapter is wired" in response.json()["detail"]


def test_the_listed_entry_and_its_expansion_describe_the_same_path(
    content_fixture: tuple[EndpointFixture, UnusualContent],
) -> None:
    """The expansion echoes the listed generation and path, so what was opened is what was listed."""

    fixture, _content = content_fixture
    inventory = _listed_inventory(fixture)
    entry = next(
        entry for entry in inventory["entries"] if entry["path"] == ELIGIBLE_UNTRACKED_PATH
    )
    body = _expansion(fixture, ELIGIBLE_UNTRACKED_PATH, listed=inventory)
    expansion = body["expansion"]

    assert expansion["path"] == entry["path"]
    assert expansion["status"] == entry["status"]
    assert expansion["before_code_tree_id"] == inventory["before_code_tree_id"]
    assert expansion["after_code_tree_id"] == inventory["after_code_tree_id"]
    assert expansion["reference"] == SOURCE_CONTENT_REFERENCE
    assert _side(body, "after")["text"].startswith("# retry interval")

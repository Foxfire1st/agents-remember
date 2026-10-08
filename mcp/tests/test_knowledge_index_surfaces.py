"""The index behind the worklist scope and the mounted knowledge tools (MIK-R23 rule 6).

* **Retired records** are never presented as live: the reused reads do not select them, and the
  index answers them with their ``retired`` status.
* **Mounted tools.** ``knowledge_read``, ``knowledge_diff`` and ``knowledge_integrity_check``
  select a converted memory tree by its root directory, read it through the tree's index and name
  the tree and the index state. No tool accepts a database path (MIK-R26 rule 5): an unconverted
  tree or a database file is refused as ``legacy-format``, and nothing is opened.
* **The integrity check** returns the knowledge validator's report of the tree it is handed.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

import apsw
import pytest
from agents_remember.application.knowledge_paging import KNOWLEDGE_PAGE_THRESHOLD_TOKENS
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.mcp.tools import knowledge as knowledge_tools
from agents_remember.mcp.tools.knowledge import (
    DiffToolRequest,
    IntegrityCheckRequest,
    ReadToolRequest,
    knowledge_diff_payload,
    knowledge_integrity_check_payload,
    knowledge_read_payload,
)
from agents_remember.memory.knowledge_index import (
    INDEX_REPOSITORY_ID,
    KnowledgeIndexCache,
    text_uuid,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodeDirectory,
    knowledge_tree_from_directory,
)
from agents_remember.memory_quality.knowledge_validator.validator import validate_tree
from agents_remember.models.knowledge.read import (
    KnowledgeReadBudget,
    KnowledgeReadRequest,
    PathSeed,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from knowledge_index_test_support import (
    FAMILY,
    REVIEW_INVARIANT,
    REVIEW_PATH,
    SIBLING_INVARIANT,
    SIBLING_PATHS,
    TEST_PATH,
    commit_all,
    init_repository,
    write_review_tree,
)
from knowledge_index_test_support import (
    git as _git,
)

# --- the registered scope over the index ------------------------------------------------------


# --- retired records ----------------------------------------------------------------------------


def _retire(root: Path, record_id: str, directory: str) -> None:
    target = next((root / "knowledge" / directory).glob(f"{record_id}-*.json"))
    document = json.loads(target.read_text("utf-8"))
    document["status"] = "retired"
    target.write_text(canonical_text(document), encoding="utf-8")


def test_a_retired_record_is_never_selected_as_live_and_is_answered_as_retired(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    _retire(root, REVIEW_INVARIANT, "invariants")
    commit_all(root)
    cache = KnowledgeIndexCache(tmp_path / "cache")
    with cache.for_directory(root) as index:
        context = open_read_context(index.database_path, index.repository_id)
        page = read_knowledge_scope(
            index.database_path,
            context,
            KnowledgeReadRequest(
                seed=PathSeed(path=REVIEW_PATH), budget=KnowledgeReadBudget(max_items=64)
            ),
        ).page
        answer = index.invariant(REVIEW_INVARIANT).value
        family = index.family(FAMILY).value
        projected = {
            str(row[0])
            for row in apsw.Connection(
                str(index.database_path), flags=apsw.SQLITE_OPEN_READONLY
            ).execute("SELECT id FROM ix_uuid")
        }
    assert page is not None
    selected = {index_text for index_text in projected}
    assert f"{REVIEW_INVARIANT}@1" not in selected and "RLZ-RVW001" not in selected
    assert f"{SIBLING_INVARIANT}@1" in selected and f"{FAMILY}/{REVIEW_INVARIANT}" not in selected
    statements = {item.statement for item in page.items if item.kind == "invariant_revision"}
    assert statements == {"Family context is read from the comparison's own endpoints."}
    # The index still answers the retired record, and says it is retired.
    assert answer.record is not None and answer.record.status == "retired"
    assert [entry.id for entry in answer.realizations] == ["RLZ-RVW001"]
    assert REVIEW_INVARIANT in family.members


# --- the mounted tools ----------------------------------------------------------------------------


def _converted(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    commit_all(root)
    return root


def _read(root: Path, coordination: Path | None, **fields: Any) -> dict[str, Any]:
    return knowledge_read_payload(
        ReadToolRequest(memory_root=str(root), **fields),
        coordination_root=None if coordination is None else str(coordination),
    )


def test_knowledge_read_resolves_a_converted_memory_tree_through_its_index(tmp_path: Path) -> None:
    root = _converted(tmp_path)
    coordination = tmp_path / "coordination"
    family = text_uuid("revision", f"{FAMILY}@1")
    response = _read(root, coordination, view="family", family_revision_id=family)
    assert response["state"] == "view", response
    assert response["memoryTree"]["memoryRoot"] == str(root)
    assert response["memoryTree"]["indexState"] == "complete"
    # The namespace is the index's constant, supplied by the server and stated in the response.
    assert response["repositoryId"] == INDEX_REPOSITORY_ID
    cached = list((coordination / "runtime" / "knowledge-index").glob("*.sqlite"))
    assert [path.stem for path in cached] == [response["memoryTree"]["treeId"]]

    refused = _read(root, None, view="family")
    assert refused["state"] == "refused"
    assert refused["refusalCode"] == "snapshot_unavailable"
    assert "no coordination root" in refused["refusalDetail"]


def test_the_bootstrap_skill_names_only_views_that_list_an_existing_foundation(
    tmp_path: Path,
) -> None:
    """The skill tells a curator how to read a foundation that exists; each view it names lists rows.

    Catches an example that sends the reader to a view which answers nothing on a tree that holds
    records, from which the reader would conclude that no foundation exists.
    """

    skill = Path(__file__).resolve().parents[2] / "skills/c-14-knowledge-bootstrap/SKILL.md"
    (row,) = (
        line
        for line in skill.read_text("utf-8").splitlines()
        if line.startswith("| **Converted, records present**")
    )
    named = re.findall(r'view="([a-z_]+)"', row)
    assert named == ["family", "invariant", "source_context"]
    root = _converted(tmp_path)
    for view in named:
        listed = _read(root, tmp_path / "coordination", view=view)
        assert listed["state"] == "view" and listed["payload"]["rows"], view
    # The view the earlier text named first answers no row on the same tree.
    assert _read(root, tmp_path / "coordination", view="curation_queue")["payload"]["rows"] == []


def _diff(root: Path, before: str, after: str = "HEAD", **fields: Any) -> dict[str, Any]:
    return knowledge_diff_payload(
        DiffToolRequest(memory_root=str(root), before=before, after=after, **fields)
    )


def test_knowledge_diff_serves_the_git_diff_of_the_knowledge_files(tmp_path: Path) -> None:
    root = _converted(tmp_path)
    base = commit_all(root, "base")
    target = next((root / "knowledge" / "invariants").glob(f"{REVIEW_INVARIANT}-*.json"))
    document = json.loads(target.read_text("utf-8"))
    document.update(revision=2, statement="A comparison shows every unchanged realization.")
    target.write_text(canonical_text(document), encoding="utf-8")
    (root / "notes.txt").write_text("not knowledge\n", encoding="utf-8")
    sidecar = root / "onboarding/dashboard/src/data/review.ts.json"
    sidecar_document = json.loads(sidecar.read_text("utf-8"))
    sidecar_document["realizes"][0]["rationale"] = "loadReview refuses a changed context."
    sidecar.write_text(canonical_text(sidecar_document), encoding="utf-8")
    changed = commit_all(root, "change the statement")

    compared = _diff(root, base)  # the after side defaults to HEAD
    assert compared["state"] == "compared", compared
    assert (compared["beforeRevision"], compared["afterRevision"]) == (base, "HEAD")
    diff = compared["diff"]
    assert diff["changed_files"] == 2 and len(diff["before_tree"]) == 40
    (group,) = diff["records"]
    assert group["record_id"] == REVIEW_INVARIANT and group["kind"] == "invariant"
    (change,) = group["files"]
    assert change["status"] == "modified" and change["path"].startswith("knowledge/invariants/")
    assert "A comparison shows every unchanged realization." in change["patch"]
    # The changed entry is named under its record and, with its sidecar, under its source path.
    assert [entry[0] for entry in group["entries"]] == ["RLZ-RVW001"]
    (source,) = diff["sources"]
    assert source["source_path"] == "dashboard/src/data/review.ts"
    assert source["records"] == [REVIEW_INVARIANT]
    assert "refuses a changed context" in source["files"][0]["patch"]
    # The answer carries only the files' own change: no label of any effect is inferred.
    assert "semanticEffectLabels" not in compared
    assert not any("notes.txt" in str(value) for value in diff.values())
    assert _diff(root, base, changed)["diff"] == diff

    # The selectors narrow the same answer; a record or file that did not change answers empty.
    assert _diff(root, base, record_id=REVIEW_INVARIANT)["diff"] == diff
    by_file = _diff(root, base, path=change["path"])["diff"]
    assert by_file["changed_files"] == 1 and by_file["sources"] == []
    assert [file["path"] for file in by_file["records"][0]["files"]] == [change["path"]]
    by_source = _diff(root, base, path="dashboard/src/data/review.ts")["diff"]
    assert by_source["changed_files"] == 1 and by_source["records"][0]["files"] == []
    assert by_source["sources"] == diff["sources"]
    nothing = _diff(root, base, record_id=SIBLING_INVARIANT)["diff"]
    assert (nothing["changed_files"], nothing["records"], nothing["sources"]) == (0, [], [])
    same = _diff(root, changed)["diff"]
    assert same["changed_files"] == 0 and same["records"] == []
    # The whole answer fits one answer's bound, and says so.
    assert compared["complete"] is True and "leftOut" not in compared
    assert compared["threshold"]["tokens"] == KNOWLEDGE_PAGE_THRESHOLD_TOKENS


def test_knowledge_diff_refuses_a_path_that_names_nothing_in_either_tree(tmp_path: Path) -> None:
    """A selector that names nothing would read like "nothing changed", so it is refused by name."""

    root = _converted(tmp_path)
    base = commit_all(root, "base")
    _write(root, f"onboarding/{REVIEW_PATH}.md", "a card\n")
    _write(root, "notes.txt", "not knowledge\n")
    commit_all(root, "a card and a note")
    assert _diff(root, base, path=REVIEW_PATH)["diff"]["changed_files"] == 1
    # An absent knowledge file, an unknown source, and a file of the tree that is not knowledge.
    for absent in ("knowledge/invariants/absent.json", "no/such/source.py", "notes.txt"):
        refused = _diff(root, base, path=absent)
        assert (refused["state"], refused["refusalCode"]) == ("refused", "selector_absent"), absent
        assert repr(absent) in refused["refusalDetail"] and "diff" not in refused
    both = _diff(root, base, record_id=REVIEW_INVARIANT, path="no/such/source.py")
    assert both["refusalCode"] == "selector_absent" and "`path`" in both["refusalDetail"]
    # A file, a source and a route that a tree holds and that did not change answer with no file.
    family = next((root / "knowledge" / "families").glob(f"{FAMILY}-*.json"))
    for held in (family.relative_to(root).as_posix(), SIBLING_PATHS[0], "dashboard/src"):
        quiet = _diff(root, base, path=held)
        assert quiet["state"] == "compared" and quiet["diff"]["changed_files"] == 0, held


def _write(root: Path, path: str, text: str) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text, encoding="utf-8")


def _files(groups: list[dict[str, Any]], key: str) -> dict[str, list[str]]:
    return {group[key]: [change["path"] for change in group["files"]] for group in groups}


def test_knowledge_diff_serves_the_cards_beside_their_sidecars_and_refuses_an_unheld_record(
    tmp_path: Path,
) -> None:
    """An onboarding card is a knowledge file: its change is served under its sidecar's source path.

    A card joins its sidecar's group when both changed; a card whose sidecar did not change is
    grouped by the path that sidecar declares in the tree that holds the card (the before tree for
    a deleted card); a record's prose joins its record; a Markdown file with no sidecar stays under
    ``other``. A card is selectable by its own path, and a record no tree holds is refused by name.
    """

    root = _converted(tmp_path)
    sidecar, card = f"onboarding/{REVIEW_PATH}.json", f"onboarding/{REVIEW_PATH}.md"
    lone = f"onboarding/{SIBLING_PATHS[0]}.md"  # its sidecar does not change
    gone = f"onboarding/{TEST_PATH}.md"  # deleted, and its sidecar stays
    overview = "onboarding/dashboard/src/overview.md"  # the route card beside the route sidecar
    stray = "onboarding/bootstrap/STATE.md"  # no sidecar beside it
    record = next((root / "knowledge" / "invariants").glob(f"{REVIEW_INVARIANT}-*.json"))
    prose = record.with_suffix(".md").relative_to(root).as_posix()
    for path in (card, lone, gone, stray, prose):
        _write(root, path, "first\n")
    base = commit_all(root, "cards")
    for path in (card, lone, overview, stray, prose):
        _write(root, path, "second\n")
    (root / gone).unlink()
    document = json.loads((root / sidecar).read_text("utf-8"))
    document["realizes"][0]["rationale"] = "loadReview refuses a changed context."
    (root / sidecar).write_text(canonical_text(document), encoding="utf-8")
    commit_all(root, "edit the cards")

    diff = _diff(root, base)["diff"]
    assert diff["changed_files"] == 7
    assert _files(diff["sources"], "source_path") == {
        REVIEW_PATH: [sidecar, card],
        SIBLING_PATHS[0]: [lone],
        TEST_PATH: [gone],
        "dashboard/src": [overview],
    }
    assert [change["path"] for change in diff["other"]] == [stray]
    assert _files(diff["records"], "record_id") == {REVIEW_INVARIANT: [prose]}
    assert diff["records"][0]["kind"] == "invariant"
    statuses = {
        change["path"]: change["status"] for group in diff["sources"] for change in group["files"]
    }
    assert (statuses[card], statuses[gone]) == ("modified", "deleted")

    by_card = _diff(root, base, path=card)["diff"]
    assert by_card["changed_files"] == 1 and by_card["records"] == []
    assert _files(by_card["sources"], "source_path") == {REVIEW_PATH: [card]}
    assert "+second" in by_card["sources"][0]["files"][0]["patch"]
    by_source = _diff(root, base, path=REVIEW_PATH)["diff"]
    assert _files(by_source["sources"], "source_path") == {REVIEW_PATH: [sidecar, card]}
    by_record = _diff(root, base, record_id=REVIEW_INVARIANT)["diff"]
    assert _files(by_record["sources"], "source_path") == {REVIEW_PATH: [sidecar, card]}
    assert _files(by_record["records"], "record_id") == {REVIEW_INVARIANT: [prose]}

    unheld = _diff(root, base, record_id="INV-N0SVCH")
    assert (unheld["state"], unheld["refusalCode"]) == ("refused", "selector_absent")
    assert "INV-N0SVCH" in unheld["refusalDetail"] and "diff" not in unheld
    # A record a tree holds and that did not change is still answered, with nothing changed.
    assert _diff(root, base, record_id=SIBLING_INVARIANT)["diff"]["changed_files"] == 0


def test_knowledge_diff_refuses_a_directory_inside_the_memory_repository(tmp_path: Path) -> None:
    """Git reads its pathspecs from the directory it runs in, so a directory below the root would
    be compared as the whole memory tree and answer "nothing changed". It is refused instead, and
    the refusal names the root to pass. A linked worktree is a root of its own."""

    root = _converted(tmp_path)
    base = commit_all(root, "base")
    _write(root, f"onboarding/{REVIEW_PATH}.md", "a card\n")
    commit_all(root, "a card")
    assert _diff(root, base)["diff"]["changed_files"] == 1
    for inside in (root / "onboarding", root / "knowledge" / "families"):
        for refused in (_diff(inside, base), knowledge_diff_payload(DiffToolRequest(str(inside)))):
            assert refused["state"] == "refused", (inside, refused)
            assert refused["refusalCode"] == "selected_input_unavailable"
            assert f"pass {root} as memoryRoot" in refused["refusalDetail"]
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
    assert _diff(linked, base)["diff"]["changed_files"] == 1


def _shown(answer: dict[str, Any]) -> list[str]:
    diff = answer["diff"]
    groups = (
        *diff["records"],
        *diff["sources"],
        {"files": diff["history"]},
        {"files": diff["other"]},
    )
    return [change["path"] for group in groups for change in group["files"]]


def test_knowledge_diff_answers_within_the_read_threshold_and_names_what_it_leaves_out(
    tmp_path: Path,
) -> None:
    """One answer never exceeds the bound ``knowledge_read`` states (MIK-R02's constant).

    The answer holds the patches of as many leading files as fit and names every file it left out,
    each of which is then reached whole by ``path``. A patch longer than an answer is cut and named
    as cut. When not even the names fit, the answer names as many as fit and counts the rest.
    """

    root = _converted(tmp_path)
    cards = [f"onboarding/bound/card_{number:02}.py.md" for number in range(40)]
    long, plain = "onboarding/bound/long.py.md", "onboarding/bound/plain.py.md"
    bulk = [f"onboarding/bulk/file_{number:04}.py.md" for number in range(900)]
    for path in (*cards, long, plain, *bulk):
        _write(root, path, "first\n")
    base = commit_all(root, "short cards")
    for number, path in enumerate(cards):
        _write(root, path, "".join(f"card {number} says thing {line}\n" for line in range(80)))
    many = commit_all(root, "forty long cards")

    answer = _diff(root, base, many)
    left = answer["leftOut"]
    assert answer["state"] == "compared" and answer["complete"] is False
    assert answer["tokens"] <= answer["threshold"]["tokens"] == KNOWLEDGE_PAGE_THRESHOLD_TOKENS
    assert answer["diff"]["changed_files"] == len(cards)
    # Nothing is dropped: the files shown and the files named are the whole change, in order.
    assert _shown(answer) and _shown(answer) + left["paths"] == cards
    assert (left["files"], left["pathsNotNamed"], left["cutPatches"]) == (len(left["paths"]), 0, [])
    assert "as `path`" in left["nextAction"]
    one = _diff(root, base, many, path=left["paths"][-1])
    assert one["complete"] is True and "leftOut" not in one
    assert (
        _shown(one) == [left["paths"][-1]] and "says thing 79" in one["diff"]["other"][0]["patch"]
    )

    # One patch longer than an answer: as much of it as fits, cut at a line end, named as cut.
    digests = [hashlib.sha256(str(line).encode()).hexdigest() for line in range(600)]
    _write(root, long, "".join(f"{digest}\n" for digest in digests))
    # A patch the per-file limit cuts although it fits an answer is named as cut too.
    _write(root, plain, "".join(f"the same plain words on line {line}\n" for line in range(900)))
    longer = commit_all(root, "two long cards")
    cut = _diff(root, many, longer, path=long)
    (change,) = cut["diff"]["other"]
    assert cut["tokens"] <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS and cut["complete"] is False
    assert change["truncated"] is True and change["patch"].endswith("\n")
    assert digests[0] in change["patch"] and digests[-1] not in change["patch"]
    assert (cut["leftOut"]["files"], cut["leftOut"]["cutPatches"]) == (0, [long])
    assert "-- <path>` prints a file's whole patch" in cut["leftOut"]["nextAction"]
    limited = _diff(root, many, longer, path=plain)
    assert limited["complete"] is False and limited["leftOut"]["cutPatches"] == [plain]
    assert limited["diff"]["other"][0]["truncated"] is True

    # More changed files than one answer can name: no patch, the names that fit, the rest counted.
    for path in bulk:
        _write(root, path, "second\n")
    most = commit_all(root, "nine hundred cards")
    crowd = _diff(root, longer, most)
    named = crowd["leftOut"]["paths"]
    assert crowd["tokens"] <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS and _shown(crowd) == []
    assert 0 < len(named) < len(bulk) and named == bulk[: len(named)]
    assert crowd["leftOut"]["files"] == len(bulk) == crowd["diff"]["changed_files"]
    assert crowd["leftOut"]["pathsNotNamed"] == len(bulk) - len(named)
    assert (
        "--name-only -- knowledge onboarding` lists every changed file"
        in (crowd["leftOut"]["nextAction"])
    )


# --- MIK-R26 rule 5: no tool reads unconverted memory or a database file -------------------------


def _legacy(tmp_path: Path) -> Path:
    """An unconverted memory repository that holds a file named like the retired database."""

    legacy = tmp_path / "legacy"
    init_repository(legacy)
    (legacy / "onboarding").mkdir()
    (legacy / "onboarding" / "overview.md").write_text("# root\n", encoding="utf-8")
    (legacy / "knowledge.sqlite").write_bytes(b"SQLite format 3\x00 never opened")
    commit_all(legacy)
    return legacy


def _assert_legacy_format(response: dict[str, Any], *named: str) -> None:
    assert response["state"] == "refused", response
    assert response["refusalCode"] == "legacy-format", response
    detail = response["refusalDetail"]
    # The refusal names the crossing sync, the conversion command and where converted memory is.
    assert "worktree_sync" in detail and "agents-remember knowledge-convert" in detail
    assert "MIK-R26" in detail
    for text in named:
        assert text in detail, (text, detail)
    assert "memoryTree" not in response


def test_every_knowledge_tool_refuses_legacy_memory_and_opens_no_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unconverted tree, a database file in one, and the frozen file inside a converted tree are
    each refused as ``legacy-format`` by all three tools, and no SQLite file is opened or created.

    Catches a fall-through that still opens the path it was handed as a dataset.
    """

    legacy = _legacy(tmp_path)
    converted = _converted(tmp_path)
    (converted / "knowledge.sqlite").write_bytes(b"SQLite format 3\x00 frozen at the cutover")
    coordination = tmp_path / "coordination"
    commit_all(converted, "freeze the database")
    opened: list[str] = []
    real = apsw.Connection

    def spy(filename: str, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(filename))
        return real(filename, *args, **kwargs)

    monkeypatch.setattr(apsw, "Connection", spy)
    selections = (
        (legacy, f"the memory tree {legacy}", "holds no converted memory yet"),
        (legacy / "knowledge.sqlite", "knowledge database file", "holds no converted memory yet"),
        (converted / "knowledge.sqlite", "knowledge database file", f"worktree {converted}"),
    )
    for selection, subject, held in selections:
        read = _read(selection, coordination, view="family")
        _assert_legacy_format(read, "knowledge_read refuses", subject, held)
        assert read["threshold"]["tokens"] > 0  # MIK-R02 rule 1: a refusal states the threshold
        diff = _diff(selection, "HEAD")
        _assert_legacy_format(diff, "knowledge_diff refuses", subject)
        check = knowledge_integrity_check_payload(
            IntegrityCheckRequest(memoryRoot=str(selection), codeRoot=str(tmp_path))
        )
        _assert_legacy_format(check, "knowledge_integrity_check refuses", subject)
    assert opened == []
    assert not coordination.exists()

    absent = _read(tmp_path / "nowhere", coordination, view="family")
    assert (absent["state"], absent["refusalCode"]) == ("refused", "selected_input_unavailable")
    assert "does not exist" in absent["refusalDetail"]


def test_knowledge_diff_with_no_after_revision_compares_with_the_working_tree(
    tmp_path: Path,
) -> None:
    root = _converted(tmp_path)
    base = commit_all(root, "base")
    target = next((root / "knowledge" / "invariants").glob(f"{REVIEW_INVARIANT}-*.json"))
    # Nothing uncommitted yet: the working tree equals HEAD.
    clean = knowledge_diff_payload(DiffToolRequest(memory_root=str(root)))
    assert clean["state"] == "compared" and clean["afterRevision"] == "working tree", clean
    assert clean["diff"]["changed_files"] == 0

    document = json.loads(target.read_text("utf-8"))
    document.update(
        revision=2, statement="An uncommitted statement shows in the working tree diff."
    )
    target.write_text(canonical_text(document), encoding="utf-8")
    (root / "knowledge" / "families" / "FAM-NEWONE-x.json").write_text("{}\n", encoding="utf-8")
    loose, refs = _git(root, "count-objects"), _git(root, "for-each-ref")
    uncommitted = knowledge_diff_payload(DiffToolRequest(memory_root=str(root)))
    assert uncommitted["state"] == "compared", uncommitted
    # The capture writes loose objects that nothing references (the tool's description says so),
    # and a comparison of two named revisions writes none.
    captured = _git(root, "count-objects")
    assert int(captured.split()[0]) > int(loose.split()[0])
    assert _git(root, "for-each-ref") == refs
    _diff(root, base, base)
    assert _git(root, "count-objects") == captured
    diff = uncommitted["diff"]
    assert diff["changed_files"] == 2
    files = {
        change["path"]: change
        for group in (*diff["records"], {"files": diff["other"]})
        for change in group["files"]
    }
    assert any("uncommitted statement" in change["patch"] for change in files.values())
    assert any(change["status"] == "added" for change in files.values())
    # The default before side is HEAD; naming it, or its commit, gives the same answer.
    named = knowledge_diff_payload(DiffToolRequest(memory_root=str(root), before=base))
    assert named["diff"] == uncommitted["diff"] and named["beforeRevision"] == base
    # The working files, the index and the branch are unchanged by the read.
    assert _git(root, "rev-parse", "HEAD") == base
    assert _git(root, "diff", "--cached", "--name-only") == ""
    # With both revisions named the two trees are compared, whatever the working tree holds.
    committed = commit_all(root, "second state")
    pair = _diff(root, base, committed)
    assert pair["afterRevision"] == committed and pair["diff"]["changed_files"] == 2
    assert _diff(root, committed)["diff"]["changed_files"] == 0


def test_knowledge_diff_refuses_an_unconverted_working_tree(tmp_path: Path) -> None:
    legacy = _legacy(tmp_path)
    (legacy / "onboarding" / "overview.md").write_text("# changed\n", encoding="utf-8")
    refused = knowledge_diff_payload(DiffToolRequest(memory_root=str(legacy)))
    _assert_legacy_format(refused, "knowledge_diff refuses", "at HEAD")
    assert refused.get("diff") is None
    # A converted HEAD whose working tree lost its layout marker is refused on the working side.
    converted = _converted(tmp_path / "converted")
    (converted / "knowledge" / "layout.json").unlink()
    gone = knowledge_diff_payload(DiffToolRequest(memory_root=str(converted)))
    _assert_legacy_format(gone, "knowledge_diff refuses", "at working tree")


def test_knowledge_diff_refuses_a_revision_whose_tree_is_unconverted_and_an_unknown_one(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    (root / "onboarding").mkdir(parents=True)
    (root / "onboarding" / "overview.md").write_text("# root\n", encoding="utf-8")
    unconverted = commit_all(root, "before the conversion")
    write_review_tree(root)
    commit_all(root, "converted")

    # Either side may be the unconverted one; the converted pair is compared.
    for before, after in ((unconverted, "HEAD"), ("HEAD", unconverted)):
        refused = _diff(root, before, after)
        _assert_legacy_format(refused, "knowledge_diff refuses", f"the memory tree {root}")
        assert refused.get("diff") is None
    assert _diff(root, "HEAD", "HEAD")["state"] == "compared"

    unknown = _diff(root, "no-such-revision")
    assert (unknown["state"], unknown["refusalCode"]) == ("refused", "selected_input_unavailable")
    assert "no-such-revision" in unknown["refusalDetail"]
    plain = tmp_path / "not-a-repository"
    plain.mkdir()
    assert _diff(plain, "HEAD")["refusalCode"] == "selected_input_unavailable"
    assert _diff(tmp_path / "absent", "HEAD")["refusalCode"] == "selected_input_unavailable"


def test_a_retired_family_is_never_selected_as_live(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    _retire(root, FAMILY, "families")
    commit_all(root)
    with KnowledgeIndexCache(tmp_path / "cache").for_directory(root) as index:
        context = open_read_context(index.database_path, index.repository_id)
        page = read_knowledge_scope(
            index.database_path,
            context,
            KnowledgeReadRequest(
                seed=PathSeed(path=REVIEW_PATH), budget=KnowledgeReadBudget(max_items=64)
            ),
        ).page
        family = index.family(FAMILY).value
        invariant = index.invariant(REVIEW_INVARIANT).value
        assert index.text_id(text_uuid("revision", f"{FAMILY}@1")) is None
    assert page is not None
    assert {item.kind for item in page.items} == {"invariant_revision", "realization_claim"}
    # The index still answers the family and its members, and says it is retired.
    assert family.record is not None and family.record.status == "retired"
    assert family.members == (REVIEW_INVARIANT, SIBLING_INVARIANT)
    assert invariant.families == (FAMILY,)


# --- F1: a partial index is never presented as complete -------------------------------------------


def _partial(tmp_path: Path) -> Path:
    root = _converted(tmp_path)
    (root / "knowledge" / "invariants" / "INV-BRKN01-x.json").write_text("{}", "utf-8")
    return root


def test_the_read_reports_a_partial_index_as_incomplete(tmp_path: Path) -> None:
    root = _partial(tmp_path)
    coordination = tmp_path / "coordination"
    family = text_uuid("revision", f"{FAMILY}@1")
    read = _read(root, coordination, view="family", family_revision_id=family)
    assert read["state"] == "view"
    assert read["completeWithinDeclaredScope"] is False
    assert read["payload"]["completeness"]["complete_within_declared_scope"] is False
    assert read["indexComplete"] is False and read["memoryTree"]["indexState"] == "partial"

    complete_root = _converted(tmp_path / "complete")
    complete = _read(complete_root, coordination, view="family", family_revision_id=family)
    assert complete["completeWithinDeclaredScope"] is True and complete["indexComplete"] is True


# --- F3: an index that cannot be built is a refusal on every surface ------------------------------


def test_an_unbuildable_index_is_refused_not_raised(tmp_path: Path) -> None:
    root = _converted(tmp_path)
    blocked = tmp_path / "coordination-is-a-file"
    blocked.write_text("not a directory", "utf-8")
    read = _read(root, blocked, view="family", family_revision_id=str(uuid.uuid4()))
    assert read["state"] == "refused"
    # The tree was selected; it is its index that could not be built, and the refusal says where.
    assert read["refusalCode"] == "snapshot_unavailable"
    assert "the selected memory tree could not be indexed" in read["refusalDetail"]
    assert str(root) in read["refusalDetail"] and "memoryTree" not in read


def test_a_converted_tree_refuses_a_seed_it_does_not_hold(tmp_path: Path) -> None:
    """L37 ruling (P2 task 4): an unheld seed is refused ``selector_absent``, never answered as an
    empty, complete view."""

    root = _converted(tmp_path)
    coordination = tmp_path / "coordination"
    held = text_uuid("revision", f"{REVIEW_INVARIANT}@1")
    assert (
        _read(root, coordination, view="invariant", invariant_revision_id=held)["state"] == "view"
    )
    for view, seeds in (
        ("invariant", {"invariant_revision_id": str(uuid.uuid4())}),  # a database-era ID
        ("invariant", {"invariant_revision_id": REVIEW_INVARIANT}),  # a bare record ID
        ("family", {"family_revision_id": str(uuid.uuid4())}),
        ("source_context", {"family_revision_id": "FAM-ZZZZZZ"}),
    ):
        refused = _read(root, coordination, view=view, **seeds)
        assert refused["state"] == "refused", (view, seeds, refused)
        assert refused["refusalCode"] == "selector_absent"
        assert "read_ar_files" in refused["refusalDetail"]
    bare = _read(root, coordination, view="invariant", invariant_revision_id=REVIEW_INVARIANT)
    assert f"whose seed is {held}" in bare["refusalDetail"]


# --- knowledge_integrity_check: the validator's report -------------------------------------------


def test_the_integrity_check_runs_the_validator_and_bounds_its_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R26 rule 5: the tool returns the validator's own report for the tree it is handed.

    Catches a tool that reports from anything but ``validate_tree`` over the tree's working files,
    a listing that hides a refusal behind report-only findings, and a call that names too little.
    """

    root = _converted(tmp_path)
    code = tmp_path / "code"
    code.mkdir()

    def check(**fields: Any) -> dict[str, Any]:
        return knowledge_integrity_check_payload(IntegrityCheckRequest(**fields))

    expected = validate_tree(
        knowledge_tree_from_directory(root), code=CodeDirectory(label=code.as_posix(), root=code)
    )
    assert expected.refusals  # the fixture's anchors name files this empty code checkout lacks
    reported = check(memoryRoot=str(root), codeRoot=str(code))
    assert reported["state"] == "reported", reported
    validation = reported["validation"]
    assert (reported["memoryRoot"], reported["codeRoot"]) == (str(root), code.as_posix())
    assert (validation["ok"], validation["refusalCount"], validation["reportCount"]) == (
        False,
        len(expected.refusals),
        len(expected.reports),
    )
    assert sum(one["count"] for one in validation["byRule"]) == len(expected.violations)
    assert validation["violations"] == [
        one.to_document() for one in (*expected.refusals, *expected.reports)
    ]
    assert validation["violationsTruncated"] is False
    assert "worklistState" not in reported  # no leaf was named

    # Against its own commit as the base nothing changed, so no anchor is checked for a path.
    based = check(memoryRoot=str(root), codeRoot=str(code), baseCommits=("HEAD",))
    assert based["bases"] == ["HEAD"]
    assert based["validation"]["refusalCount"] < validation["refusalCount"]

    monkeypatch.setattr(knowledge_tools, "MAX_LISTED_VIOLATIONS", 1)
    bounded = check(memoryRoot=str(root), codeRoot=str(code))["validation"]
    assert len(bounded["violations"]) == 1 and bounded["violationsTruncated"] is True
    assert bounded["violations"][0]["reportOnly"] is False  # a refusal is listed first
    assert bounded["refusalCount"] == validation["refusalCount"]

    for too_little in ({}, {"memoryRoot": str(root)}, {"codeRoot": str(code)}):
        refused = check(**too_little)
        assert (refused["state"], refused["refusalCode"]) == (
            "refused",
            "selected_input_unavailable",
        ), refused
        assert "contractPath" in refused["refusalDetail"]
    gone = check(memoryRoot=str(root), codeRoot=str(tmp_path / "no-code"))
    assert gone["refusalCode"] == "selected_input_unavailable"
    unreadable = check(memoryRoot=str(root), codeRoot=str(code), baseCommits=("no-such-commit",))
    assert unreadable["refusalCode"] == "snapshot_unavailable"

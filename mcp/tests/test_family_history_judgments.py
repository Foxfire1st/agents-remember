"""Authored family effects survive writing; historical omissions never license new judgments."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_currentness.observe import open_code_tree
from agents_remember.application.knowledge_gate.predicates import GateContext, family_row_open
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.cli.__main__ import main
from agents_remember.memory.knowledge_index import KnowledgeIndexCache
from agents_remember.memory_quality.knowledge_validator import (
    CodeDirectory,
    KnowledgeTree,
    KnowledgeValidationError,
    ValidationReport,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
    require_valid_commit,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.memory_quality.knowledge_validator.validator import LeafCommit
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    HistoryFile,
    merged_leaf_history,
)
from agents_remember.worktrees.services import LeafPublication
from knowledge_writer_test_support import (
    BASE_FAMILY,
    BASE_INVARIANT,
    LEAF_ID,
    TASK_ID,
    World,
    build_world,
    commit_all,
    git,
    read_json,
    tree_bytes,
    write,
)

FAMILY_PATH = f"knowledge/families/{BASE_FAMILY}-landing.json"
HISTORY_PATH = f"knowledge/history/{LEAF_ID}.json"
OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)


def _world(tmp_path: Path) -> World:
    world = build_world(tmp_path)
    family = read_json(world.memory, FAMILY_PATH)
    family["revision"] = 6  # deliberately different from its member's actual revision, 1
    write(world.memory, {FAMILY_PATH: canonical_text(family)})
    commit_all(world.memory, "family at own revision six")
    return world


def _judgment(**fields: Any) -> dict[str, Any]:
    return {
        "subject": BASE_FAMILY,
        "disposition": "changed",
        "reason": "The guarantee was restated.",
        "examined": [BASE_INVARIANT],
        **fields,
    }


def _update(guarantee: str = "A landing records both halves together.") -> dict[str, Any]:
    return {
        "key": "family",
        "kind": "family",
        "id": BASE_FAMILY,
        "fields": {"guarantee": guarantee},
    }


def _write(world: World, document: Any):
    return write_knowledge(
        WriteRequest(world.memory, world.code, OWNER, "notes/handoff.json", document, commit=True)
    )


def _stored_row(**fields: Any) -> dict[str, Any]:
    return {
        "id": "ROW-ABC123",
        **_judgment(),
        "items": [],
        "examined": [{"id": BASE_INVARIANT, "revision": 1}],
        **fields,
    }


def _history(row: dict[str, Any], *, closed: bool = True, leaf: str = LEAF_ID) -> str:
    return canonical_text(
        {"schema": "ar-history/v1", "leaf": leaf, "closed": closed, "rows": [row]}
    )


def _validate(
    world: World, *, bases: tuple[KnowledgeTree, ...], publication: bool | LeafCommit = True
) -> ValidationReport:
    report = require_valid_commit(
        knowledge_tree_from_directory(world.memory),
        bases=bases,
        code=CodeDirectory("paired code", world.code),
        leaf_publication=publication,
    )
    assert report is not None
    return report


@pytest.mark.integration
def test_cli_retains_each_authored_family_effect_and_own_revision_after_index_rebuild(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = _world(tmp_path)
    handoff = world.task_root / "handoff.json"
    row_id = None
    for effect in ("strengthen", "weaken", "clarify", "replace"):
        handoff.write_text(
            json.dumps({"records": [_update()], "history": [_judgment(effect=effect)]})
        )
        assert (
            main(
                [
                    "knowledge-ingest",
                    "--contract",
                    str(world.contract),
                    "--list",
                    str(handoff),
                    "--authorization-ref",
                    "family judgment",
                    "--commit",
                    "--json",
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert result["state"] == "written", result
        (row,) = read_json(world.memory, HISTORY_PATH)["rows"]
        assert row["effect"] == effect and row["revision"] == 7
        assert row["examined"] == [{"id": BASE_INVARIANT, "revision": 1}]
        assert row_id in (None, row["id"])
        row_id = row["id"]
        # Fresh independent caches rebuild through the ordinary index owner, retaining the full row.
        for rebuild in ("first", "rebuilt"):
            with KnowledgeIndexCache(tmp_path / effect / rebuild).for_directory(
                world.memory
            ) as index:
                indexed = index.history_rows_about(BASE_FAMILY)
                assert indexed.index.complete
                (stored,) = indexed.value
                assert stored.document == row
                assert (stored.owner, stored.path, stored.id, stored.closed) == (
                    LEAF_ID,
                    HISTORY_PATH,
                    row["id"],
                    False,
                )


def test_bad_authored_family_inputs_refuse_without_changing_files_refs_or_index(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    cache = KnowledgeIndexCache(tmp_path / "index")
    with cache.for_directory(world.memory) as index:
        index_key = index.state.key
    memory_before, refs_before, index_before = (
        tree_bytes(world.memory),
        git(world.memory, "show-ref"),
        tree_bytes(tmp_path / "index"),
    )
    invalid = [
        (_judgment(), f"family {BASE_FAMILY}: a changed row records the curator's judgment"),
        (_judgment(effect=""), "needs an 'effect', one of restore, clarify"),
        (_judgment(effect=None), "needs an 'effect', one of restore, clarify"),
        (_judgment(effect=12), "needs an 'effect', one of restore, clarify"),
        (_judgment(effect="net-stronger"), f"family {BASE_FAMILY}: effect: Input should be"),
        (_judgment(effect="clarify", revision=7), "writer owns"),
        (_judgment(effect="clarify", subject="FAM-UNKNOWN"), "unknown subject"),
        (_judgment(effect="clarify", examined=["INV-UNKNOWN"]), "not a stored invariant"),
    ]
    invalid += [
        (
            _judgment(disposition=disposition, effect=effect),
            f"family {BASE_FAMILY}: a {disposition} row records no effect",
        )
        for disposition in ("rerouted", "assigned", "no_impact", "retired")
        for effect in ("clarify", None)
    ]
    for row, message in invalid:
        refused = _write(world, {"records": [_update()], "history": [row]})
        assert refused.state == "refused" and message in refused.render(), refused.render()
        assert (
            tree_bytes(world.memory) == memory_before
            and git(world.memory, "show-ref") == refs_before
        )
        assert tree_bytes(tmp_path / "index") == index_before
    with cache.for_directory(world.memory) as index:
        assert index.state.key == index_key and not index.history_rows_about(BASE_FAMILY).value


def test_nonmeaning_family_rows_record_own_revision_without_authoring_an_effect(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    for disposition in ("rerouted", "assigned", "no_impact", "retired"):
        written = _write(world, {"history": [_judgment(disposition=disposition)]})
        assert written.state == "written", written.render()
        (row,) = read_json(world.memory, HISTORY_PATH)["rows"]
        assert row["revision"] == 6 and "effect" not in row


def test_publication_refuses_incomplete_or_wrong_new_family_judgments_even_if_closed(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    base = knowledge_tree_from_git(world.memory, "HEAD")
    for closed in (False, True):
        for fields in (
            {},
            {"revision": 6},
            {"effect": "clarify"},
            {"revision": 1, "effect": "clarify"},
        ):
            write(world.memory, {HISTORY_PATH: _history(_stored_row(**fields), closed=closed)})
            before = tree_bytes(world.memory)
            with pytest.raises(KnowledgeValidationError) as refused:
                _validate(world, bases=(base,))
            assert "R48-family-judgment" in str(refused.value) or "R22.1-shape" in str(
                refused.value
            )
            assert tree_bytes(world.memory) == before
    # The writer's whole-tree validation also refuses an open file holding an incomplete new row; a
    # manually closed one is judged where it is published (the publication-route test below).
    write(world.memory, {HISTORY_PATH: _history(_stored_row(), closed=False)})
    before = tree_bytes(world.memory)
    refused = _write(world, {})
    assert refused.state == "refused" and "R48-family-judgment" in refused.render()
    assert tree_bytes(world.memory) == before


def test_only_actual_unchanged_base_merge_or_closed_frozen_rows_keep_historical_omissions(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    empty_base = knowledge_tree_from_git(world.memory, "HEAD")
    old_bytes = _history(_stored_row()).encode()
    write(world.memory, {HISTORY_PATH: old_bytes})
    commit_all(world.memory, "actual old closed family judgment")
    recorded = knowledge_tree_from_git(world.memory, "HEAD")
    for bases, publication in (
        ((recorded,), True),
        ((empty_base, recorded), True),
        ((empty_base,), LeafCommit(frozen=(recorded,))),
    ):
        assert _validate(world, bases=bases, publication=publication).ok
        assert (world.memory / HISTORY_PATH).read_bytes() == old_bytes
    with KnowledgeIndexCache(tmp_path / "index").for_directory(world.memory) as index:
        (row,) = index.history_rows_about(BASE_FAMILY).value
        assert "effect" not in row.document and "revision" not in row.document
    for edited in (_history(_stored_row(reason="An edited judgment.")), None):
        if edited is None:
            (world.memory / HISTORY_PATH).unlink()
        else:
            write(world.memory, {HISTORY_PATH: edited})
        with pytest.raises(KnowledgeValidationError, match=r"R22\.7-history-frozen"):
            _validate(world, bases=(recorded,))
    write(world.memory, {HISTORY_PATH: old_bytes})
    copied_path = "knowledge/history/260928-MIK-L98.json"
    write(world.memory, {copied_path: _history(_stored_row(), leaf="260928-MIK-L98")})
    with pytest.raises(KnowledgeValidationError, match="R48-family-judgment"):
        _validate(world, bases=(recorded,))


def test_editing_or_reusing_an_old_open_row_cannot_preserve_missing_fields(tmp_path: Path) -> None:
    world = _world(tmp_path)
    write(world.memory, {HISTORY_PATH: _history(_stored_row(), closed=False)})
    commit_all(world.memory, "old open judgment")
    base = knowledge_tree_from_git(world.memory, "HEAD")
    assert _validate(world, bases=(base,)).ok
    write(world.memory, {HISTORY_PATH: _history(_stored_row(reason="Edited"), closed=False)})
    with pytest.raises(KnowledgeValidationError, match="R48-family-judgment"):
        _validate(world, bases=(base,))
    # A frozen authority only attests files that it actually holds closed.
    with pytest.raises(KnowledgeValidationError, match="R48-family-judgment"):
        _validate(world, bases=(), publication=LeafCommit(frozen=(base,)))


def test_later_attempt_keeps_closed_facts_and_currentness_checks_only_its_governing_revision(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    assert (
        _write(world, {"records": [_update()], "history": [_judgment(effect="weaken")]}).state
        == "written"
    )
    first = read_json(world.memory, HISTORY_PATH)
    first["closed"] = True
    write(world.memory, {HISTORY_PATH: canonical_text(first)})
    closed_bytes = (world.memory / HISTORY_PATH).read_bytes()
    commit_all(world.memory, "first actual family judgment closes")
    changed = {
        "records": [_update("The pair includes an explicit matching identity.")],
        "history": [_judgment(effect="clarify")],
    }
    before = tree_bytes(world.memory)
    refused = _write(world, {"records": changed["records"]})
    # No row is yet present in the new attempt; record authoring itself need not manufacture one.
    assert refused.state == "written", refused.render()
    assert _write(world, changed).state == "written"
    second_path = f"knowledge/history/{LEAF_ID}-attempt-2.json"
    second = read_json(world.memory, second_path)
    (row,) = second["rows"]
    assert (row["revision"], row["effect"]) == (8, "clarify")
    assert row["id"] != first["rows"][0]["id"]
    assert (world.memory / HISTORY_PATH).read_bytes() == closed_bytes
    histories = [HistoryFile.model_validate(first), HistoryFile.model_validate(second)]
    governing = merged_leaf_history(reversed(histories))
    side = KnowledgeSide.from_tree(
        "candidate", "candidate", knowledge_tree_from_directory(world.memory)
    )
    context = GateContext(LEAF_ID, governing, side, open_code_tree(None))
    item = {
        "subject": BASE_FAMILY,
        "facts": {"members": [{"id": BASE_INVARIANT}], "reachedBy": ["guarantee-changed"]},
    }
    assert family_row_open(item, context) is None
    # A later actual candidate changes the family, while preserving the recorded judgment unchanged.
    family = read_json(world.memory, FAMILY_PATH)
    family.update(revision=9, guarantee="A later family revision.")
    files = {**side.files, FAMILY_PATH: canonical_text(family).encode()}
    later = KnowledgeSide.from_tree("later", "later", KnowledgeTree("later", files))
    drift = family_row_open(item, replace(context, candidate=later))
    assert drift is not None and "own family revision 8" in drift
    assert f"name {BASE_FAMILY} again in the hand-off's 'history'" in drift
    uncovered = family_row_open(item, replace(context, open_invariants=frozenset({BASE_INVARIANT})))
    assert uncovered is not None and "members without a current invariant row" in uncovered
    with KnowledgeIndexCache(tmp_path / "index").for_directory(world.memory) as index:
        rows = index.history_rows_about(BASE_FAMILY).value
        assert {(r.path, r.document["revision"], r.document["effect"]) for r in rows} == {
            (HISTORY_PATH, 7, "weaken"),
            (second_path, 8, "clarify"),
        }
    assert before[HISTORY_PATH] == closed_bytes


def test_writer_refuses_own_revision_drift_until_the_current_row_is_authored_again(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    assert _write(world, {"history": [_judgment(disposition="assigned")]}).state == "written"
    before = tree_bytes(world.memory)
    refused = _write(world, {"records": [_update()]})
    assert refused.state == "refused" and "own revision 6" in refused.render()
    assert f"{BASE_FAMILY} is at revision 7" in refused.render()
    assert tree_bytes(world.memory) == before
    fixed = _write(world, {"records": [_update()], "history": [_judgment(effect="strengthen")]})
    assert fixed.state == "written", fixed.render()


def _legacy_leaf_files(*, open_leaf: str | None = None) -> dict[str, str]:
    """Closed legacy history files, each with an unlabelled changed family row (like L37 and L40)."""

    files = {}
    for number, leaf in enumerate(("260928-MIK-L37", "260928-MIK-L40"), start=1):
        row = _stored_row(id=f"ROW-AAA00{number}")
        files[f"knowledge/history/{leaf}.json"] = _history(row, closed=leaf != open_leaf, leaf=leaf)
    return files


def _route(world: World, tree: str, bases: list[str]) -> str | None:
    return GitKnowledgeValidation().refusal(
        memory_repository=world.memory,
        candidate_tree=tree,
        bases=bases,
        code_repository=world.code,
        code_commit=git(world.code, "rev-parse", "HEAD"),
    )


def test_a_master_landing_does_not_judge_closed_legacy_rows_its_base_lacks(tmp_path: Path) -> None:
    world = _world(tmp_path)
    base = git(world.memory, "rev-parse", "HEAD")
    write(world.memory, dict(_legacy_leaf_files()))
    commit_all(world.memory, "the master's closed leaf history")
    master = git(world.memory, "rev-parse", "HEAD")
    assert _route(world, master, [base]) is None
    # Without any base the closed files are equally historical (the product's standalone check).
    assert _route(world, master, []) is None
    assert _validate(world, bases=(), publication=False).ok


def test_a_master_landing_still_judges_an_open_history_file_it_publishes(tmp_path: Path) -> None:
    world = _world(tmp_path)
    base = git(world.memory, "rev-parse", "HEAD")
    write(world.memory, dict(_legacy_leaf_files(open_leaf="260928-MIK-L40")))
    commit_all(world.memory, "the master's history, one file still open")
    master = git(world.memory, "rev-parse", "HEAD")
    refused = _route(world, master, [base])
    assert refused is not None and "1 violation(s)" in refused
    assert "knowledge/history/260928-MIK-L40.json" in refused and "ROW-AAA002" in refused
    assert "R48-family-judgment" in refused and "ROW-AAA001" not in refused


@pytest.mark.parametrize("closed", [False, True])
def test_a_leaf_publication_refuses_its_own_unlabelled_changed_family_row(
    tmp_path: Path, closed: bool
) -> None:
    world = _world(tmp_path)
    base = git(world.memory, "rev-parse", "HEAD")
    write(world.memory, {HISTORY_PATH: _history(_stored_row(), closed=closed)})
    commit_all(world.memory, "the leaf's own history")
    candidate = git(world.memory, "rev-parse", "HEAD")
    refused = GitKnowledgeValidation().leaf_refusal(
        memory_repository=world.memory,
        publication=LeafPublication(candidate, (base,)),
        code_repository=world.code,
        code_commit=git(world.code, "rev-parse", "HEAD"),
    )
    assert refused is not None and "R48-family-judgment" in refused
    assert HISTORY_PATH in refused and "ROW-ABC123" in refused


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"disposition": "changed", "revision": 6}, "records only one of its own revision"),
        ({"disposition": "changed", "effect": "clarify"}, "records only one of its own revision"),
        ({"disposition": "assigned", "effect": "clarify"}, "which only a changed row has"),
    ],
)
def test_a_half_recorded_or_misplaced_family_judgment_names_the_family_and_the_fix(
    fields: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError) as refused:
        FamilyRow.model_validate({**_stored_row(), **fields})
    assert message in str(refused.value) and f"family {BASE_FAMILY}" in str(refused.value)


def test_a_hand_closed_unfrozen_file_has_a_way_out_through_the_public_calls(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    base = git(world.memory, "rev-parse", "HEAD")
    write(world.memory, {HISTORY_PATH: _history(_stored_row())})  # closed by hand, no effect
    commit_all(world.memory, "the leaf's hand-closed history")

    def publish() -> str | None:
        return GitKnowledgeValidation().leaf_refusal(
            memory_repository=world.memory,
            publication=LeafPublication(git(world.memory, "rev-parse", "HEAD"), (base,)),
            code_repository=world.code,
            code_commit=git(world.code, "rev-parse", "HEAD"),
        )

    step = 'set "closed": false in it'
    refused = publish()
    assert refused is not None and "R48-family-judgment" in refused and step in refused
    # The writer's base is the committed HEAD, so the leaf's line goes back to the parent line.
    git(world.memory, "reset", "-q", "--mixed", base)
    handoff = {"records": [_update()], "history": [_judgment(effect="clarify")]}
    still_closed = _write(world, handoff)
    assert still_closed.state == "refused" and step in still_closed.render()
    # The step both messages name: reopen the leaf's own file by hand, then name the family again.
    document = read_json(world.memory, HISTORY_PATH)
    write(world.memory, {HISTORY_PATH: canonical_text({**document, "closed": False})})
    written = _write(world, handoff)
    assert written.state == "written", written.render()
    (row,) = read_json(world.memory, HISTORY_PATH)["rows"]
    assert (row["effect"], row["revision"]) == ("clarify", 7)
    commit_all(world.memory, "the leaf's reopened history")
    assert publish() is None
    # A file that a base holds closed gets no such step: the writer targets the next attempt.
    closed = {**read_json(world.memory, HISTORY_PATH), "closed": True}
    write(world.memory, {HISTORY_PATH: canonical_text(closed)})
    commit_all(world.memory, "closeout")
    assert _write(world, {"history": [_judgment(effect="weaken")]}).state == "written"
    assert read_json(world.memory, HISTORY_PATH)["closed"] is True


def test_a_stale_non_changed_row_is_told_to_name_a_row_of_its_own_disposition(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    assert _write(world, {"history": [_judgment(disposition="assigned")]}).state == "written"
    refused = _write(world, {"records": [_update()]})
    assert refused.state == "refused" and "own revision 6" in refused.render()
    assert "a changed row names its effect again" not in refused.render()
    assert _write(
        world, {"records": [_update()], "history": [_judgment(effect="clarify")]}
    ).state == ("written")
    commit_all(world.memory, "changed row")
    family = read_json(world.memory, FAMILY_PATH)
    family["revision"] = 9
    write(world.memory, {FAMILY_PATH: canonical_text(family)})
    again = _write(world, {})
    assert again.state == "refused" and "a changed row names its effect again" in again.render()

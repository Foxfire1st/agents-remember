"""Reopened leaves: which history row governs a subject across attempts (L37 ruling of 2026-10-01).

Moved text-identical out of ``test_knowledge_reopen.py`` along its own section boundary so that file
stays well under the file-size limit. The fixtures and helpers (``world``, ``_write``,
``_public_closeout`` and the rest) stay there and are imported here.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import memo
from agents_remember.application.knowledge_worklist.route_conditions import family_route_item_open
from agents_remember.application.knowledge_writer import WriteRequest, write_knowledge
from agents_remember.models.knowledge_files.documents import (
    history_path,
)
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    merged_leaf_history,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.knowledge_gate import close_owner_history
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.modules import (
    closeout,
    closeout_external,
    record_landing,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from test_knowledge_closeout_gate import (
    CODE_A,
    LEAF,
    TRACES,
    A,
    Gated,
    _edit,
    _open,
    commit,
    family_row,
    git,
    trace_rows,
)
from test_knowledge_reopen import (
    FIRST,
    OWNER,
    SECOND,
    _close_out_and_integrate,
    _closeout,
    _Continued,
    _handoff,
    _public_closeout,
    _refused,
    _rows_for_the_edit,
    _write,
    ports,
    world,
)

__all__ = ["ports", "world"]  # the fixtures these cases use, defined in test_knowledge_reopen


def _restating(statement: str, guarantee: str) -> dict[str, Any]:
    """The hand-off sections that restate INV-AAAAAA and its family's guarantee."""

    entry = {
        "id": "A-1",
        "invariant_id": "INV-AAAAAA",
        "statement": statement,
        "kind": "clause",
        "target": [],
        "found_at": [],
        "disposition": "satisfied",
        "authority": {"task_document": "gate_case"},
        **dict.fromkeys(
            ("disposition_source", "evidence", "resolution", "validated_at", "record_action"), None
        ),
        "supersedes": None,
    }
    family = {
        "key": "F-1",
        "kind": "family",
        "id": "FAM-F00001",
        "fields": {"guarantee": guarantee},
    }
    return {"entries": [entry], "records": [family]}


def test_a_changed_record_is_governed_by_its_changed_row_in_every_attempt(
    world: _Continued, ports: None
) -> None:
    """L37 ruling of 2026-10-01T22:49:36, Q2. A leaf that closed out and continues may restate
    the label of a change it already made (a), may not let a later row of another disposition
    govern the changed record (b), and may change the record again: one ``changed`` row per
    revision step, the record two revisions ahead of the parent line (c)."""

    def revisions() -> tuple[int, int]:
        files = {
            name: json.loads(next((world.memory / "knowledge" / name).glob(pattern)).read_text())
            for name, pattern in (("invariants", "INV-AAAAAA-*.json"), ("families", "*.json"))
        }
        return files["invariants"]["revision"], files["families"]["revision"]

    _edit(world, CODE_A.replace("return value", "return -value"))
    unchanged, family, *traces = _rows_for_the_edit()["history"]
    family = {**family, "disposition": "changed", "effect": "clarify"}
    changed = {**unchanged, "disposition": "changed", "effect": "clarify"}
    first = _restating("Values land negated.", "Landing holds, negated.")
    _write(world, {**first, "history": [changed, family, *traces]})
    closed = _public_closeout(world).payload
    world.closed_out = (closed["code_commit"], closed["memory_content_commit"])
    assert revisions() == (2, 2)

    # (a) The same revision step, restated in attempt 2: its effect and reason are corrected.
    restated = {**changed, "effect": "replace", "reason": "The label of that change, corrected."}
    report = _write(world, _handoff(restated))
    assert [row.history for row in report.rows] == [SECOND]
    (row,) = json.loads((world.memory / SECOND).read_text())["rows"]
    assert (row["revision"], row["effect"], row["reason"]) == (2, "replace", restated["reason"])
    assert world.gate().ok and revisions() == (2, 2)

    # (b) A later row of another disposition would hide the change: the writer has no parent line
    # to see it by, so the routes refuse it -- the gate by the record's revision on the parent
    # line, the validator by the changed row in the leaf's earlier attempt -- and name the remedy.
    hiding = _handoff({**unchanged, "reason": "Judged again; nothing changed since."})
    _write(world, hiding)
    hidden = _open(world.gate())["touched_invariant INV-AAAAAA"]
    assert "a no_impact row governs an invariant this leaf changed" in hidden
    assert "revision 1 on the parent line, 2 in K_C" in hidden and "restate that changed" in hidden
    refusal = _refused(world, world.closed_out[0])
    assert "R09-history-rows" in refusal and "replaces this leaf's changed row" in refusal
    _write(world, _handoff(restated))
    assert world.gate().ok

    # (c) The record changes again: the leaf HEAD's revision plus one, with its own changed row.
    second = _restating("Values land negated, always.", "Landing holds, negated, always.")
    again = {**changed, "effect": "strengthen", "reason": "Restated after the closeout."}
    _write(world, {**second, "history": [again, family]})
    assert revisions() == (3, 3)  # two steps ahead of the parent line, which holds revision 1
    assert world.gate().ok, world.gate().refusal()
    memory = _closeout(world, world.closed_out[0]).memory_commit
    steps = [
        row
        for path in (FIRST, SECOND)
        for row in json.loads(git(world.memory, "show", f"{memory}:{path}"))["rows"]
        if row["subject"] == "INV-AAAAAA"
    ]
    assert [(row["disposition"], row["revision"]) for row in steps] == [
        ("changed", 2),
        ("changed", 3),
    ]
    code = world.closed_out[0]
    world.closed_out = (code, memory)
    assert record_landing._knowledge_gate_refusal(world.contract, code, memory) is None

    # Review R5-2, as reproduced: after the second closeout a no_impact row lands in attempt 3 and
    # would govern. The gate refuses it, and so does the validator on its own at a recorded
    # landing of a commit made by hand, a route the worklist does not reach.
    report = _write(world, hiding)
    assert [row.history for row in report.rows] == [history_path(LEAF, 3)]
    assert "a no_impact row governs" in _open(world.gate())["touched_invariant INV-AAAAAA"]
    close_owner_history(world.memory, LEAF)
    by_hand = commit(world.memory, {}, trailer=code)
    refusal = record_landing._knowledge_gate_refusal(world.contract, code, by_hand) or ""
    assert "R09-history-rows" in refusal and "replaces this leaf's changed row" in refusal
    assert f"(revision 3, effect {again['effect']})" in refusal  # the step it would hide


def test_a_change_on_the_parent_line_may_be_answered_by_any_later_row(
    world: _Continued, ports: None
) -> None:
    """Review R5-2, the validator rule's two limits. A change that landed on the leaf's parent
    line is no longer the leaf's to keep visible: reopened, the leaf may answer the invariant with
    a ``no_impact`` row. And the rule judges a leaf's publication only: a master's landing, judged
    against the line as it was before its leaves landed, is not refused for one of them."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    unchanged, family, *traces = _rows_for_the_edit()["history"]
    family = {**family, "disposition": "changed", "effect": "clarify"}
    rows = [{**unchanged, "disposition": "changed", "effect": "clarify"}, family, *traces]
    _write(
        world, {**_restating("Values land negated.", "Landing holds, negated."), "history": rows}
    )
    before_the_leaf = world.memory_base
    _close_out_and_integrate(world)

    _edit(world, CODE_A.replace("return value", "return +value"))
    _write(world, _rows_for_the_edit(traces=False))  # attempt 2: a no_impact row governs
    assert world.gate().ok, world.gate().refusal()
    code = commit(world.code, {})
    memory = _closeout(world, code).memory_commit  # the validator at the leaf's closeout passes
    judged = {
        "memory_repository": world.memory,
        "candidate_tree": memory,
        "bases": (before_the_leaf,),
        "paired_code": PairedCode(repository=world.code, commit=code),
    }
    assert memory_commit_refusal(**judged) is None  # as a master lands it
    as_a_leaf = memory_commit_refusal(
        **judged, leaf_publication=True
    )  # were neither attempt landed
    assert "replaces this leaf's changed row" in (as_a_leaf or "")

    # Review R6-5 (N14). A changed row at an unchanged revision restates the leaf's latest earlier
    # row about the subject. That is attempt 2's no_impact row now, so attempt 3 restates nothing,
    # though attempt 1 holds a changed row at this very revision.
    relabelled = _handoff({**unchanged, "disposition": "changed", "effect": "strengthen"})
    refused = write_knowledge(
        WriteRequest(world.memory, world.code, OWNER, "notes/handoff.json", relabelled, commit=True)
    )
    assert refused.state == "refused", refused.render()
    assert "a changed row's revision is the K_B revision 2 plus one" in refused.render()


def test_a_family_whose_guarantee_the_leaf_changed_is_governed_by_its_changed_row(
    world: _Continued, ports: None
) -> None:
    """L37 ruling of 2026-10-02T01:04:49. A family has one governing row, the leaf's latest about
    it. While the leaf changed the family's guarantee that row is its ``changed`` row: no row of
    another disposition answers the family in the attempt that makes the change, and none replaces
    the ``changed`` row in a later attempt. A record change that leaves the guarantee alone asks
    for no ``changed`` row."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    unchanged, family, *traces = _rows_for_the_edit()["history"]
    renamed = {"key": "F-0", "kind": "family", "id": "FAM-F00001", "fields": {"title": "Landed"}}
    _write(world, {"records": [renamed], "history": [unchanged, family, *traces]})
    reached = next(one for one in world.gate().worklist["items"] if one["kind"] == "reached_family")
    assert "record-changed" in reached["facts"]["reachedBy"] and world.gate().ok

    restated = _restating("Values land negated.", "Landing holds, negated.")
    changed = {**unchanged, "disposition": "changed", "effect": "clarify"}
    _write(world, {**restated, "history": [changed, family]})  # the family row says no_impact
    result = world.gate()
    reached = next(one for one in result.worklist["items"] if one["kind"] == "reached_family")
    assert "guarantee-changed" in reached["facts"]["reachedBy"]
    opened = _open(result)["reached_family FAM-F00001"]
    assert "a no_impact row governs a family whose guarantee this leaf changed" in opened
    _write(world, _handoff({**family, "disposition": "changed", "effect": "clarify"}))
    assert world.gate().ok, world.gate().refusal()
    closed = _public_closeout(world).payload
    world.closed_out = (closed["code_commit"], closed["memory_content_commit"])

    # After the closeout, a route row or a no_impact row would replace the changed row. The gate
    # refuses it by the guarantee on the parent line, the validator by the frozen changed row.
    for disposition in ("rerouted", "no_impact"):
        report = _write(world, _handoff({**family, "disposition": disposition}))
        assert [row.history for row in report.rows] == [SECOND]
        opened = _open(world.gate())["reached_family FAM-F00001"]
        assert f"a {disposition} row governs a family whose guarantee" in opened
        refusal = _refused(world, world.closed_out[0])
        assert "R09-history-rows" in refusal and "(the family's own change)" in refusal
        assert f"the {disposition} row about FAM-F00001 replaces this leaf's changed row" in refusal
    _write(
        world, _handoff({**family, "disposition": "changed", "effect": "clarify"})
    )  # named again: it governs
    assert world.gate().ok, world.gate().refusal()
    assert _closeout(world, world.closed_out[0]).memory_commit != world.closed_out[1]


def test_a_route_condition_reads_the_one_governing_family_row_across_attempts() -> None:
    """MIK-R06 rule 3 reads "the latest family row": an answer the frozen attempt gave counts until
    a later attempt's row about the family replaces it. A ``changed`` row that replaces it answers
    the condition too; a ``no_impact`` row does not, and the row is then named again."""

    def governing(*dispositions: str) -> HistoryFile | None:
        files = []
        for attempt, disposition in enumerate(dispositions, start=1):
            row = {**family_row({}), "id": f"ROW-00000{attempt}", "items": []}
            document = {"leaf": LEAF, "closed": attempt < len(dispositions)}
            numbered = {"attempt": attempt} if attempt > 1 else {}
            rows = {"rows": [{**row, "disposition": disposition}]}
            files.append(HistoryFile.model_validate(document | numbered | rows))
        return merged_leaf_history(files)

    item = {"subject": "FAM-F00001#route_unassigned", "facts": {"recordSatisfiesRoutes": True}}
    assert not family_route_item_open(item, governing("assigned"))
    assert not family_route_item_open(item, governing("assigned", "changed"))
    assert family_route_item_open(item, governing("assigned", "no_impact"))
    assert not family_route_item_open(item, governing("assigned", "no_impact", "assigned"))
    assert family_route_item_open(item, governing("no_impact")) and family_route_item_open(
        item, None
    )


def test_the_writer_fills_a_rows_items_from_the_leafs_worklist(world: Gated, ports: None) -> None:
    """L37 P1c, C4 (MIK-R07 rule 1): a row whose hand-off names no item lists the worklist items it
    answers, by each kind's own satisfying-row rule. Named items are kept, and a missing or
    malformed worklist fills nothing and refuses nothing."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    worklist = world.gate().worklist
    ids: dict[str, list[str]] = {}
    for item in worklist["items"]:
        ids.setdefault(f"{item['kind']} {item['subject']}", []).append(item["id"])

    def stored() -> dict[str, list[str]]:
        rows = json.loads((world.memory / FIRST).read_text())["rows"]
        return {row["subject"]: row["items"] for row in rows}

    _write(world, _rows_for_the_edit())  # no worklist: as before, nothing is filled in
    assert set(map(tuple, stored().values())) == {()}
    named = "sha256:" + "0" * 64
    invariant, family, *traces = _rows_for_the_edit()["history"]
    odd = {"kind": "touched_invariant", "subject": "INV-AAAAAA", "id": "not an item id"}
    worklist = {**worklist, "items": [*worklist["items"], odd, {**odd, "id": 7}, "not an item"]}
    _write(world, _handoff({**invariant, "items": [named]}, family, *traces), worklist)
    items = stored()
    assert items["INV-AAAAAA"] == [named]  # what the hand-off names is kept
    assert items["FAM-F00001"] == sorted(ids["reached_family FAM-F00001"])
    assert all(items[subject] == ids[f"onboarding_trace {subject}"] for subject in TRACES)
    _write(world, _handoff(invariant), worklist)
    assert stored()["INV-AAAAAA"] == ids["touched_invariant INV-AAAAAA"]
    assert world.gate().ok  # the field is informational: the gate matches rows by subject


def test_a_history_file_closed_by_a_hand_commit_is_not_frozen(world: Gated, ports: None) -> None:
    """Only a recorded closeout freezes a history file (L09 review R1, finding 1). A file closed
    and committed by hand, whose row contradicts K_C, is still read at the closeout, whatever was
    committed on top of it; and the gate's memo tells two leaf heads apart."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    before = world.reanchor("RLZ-A00001")
    stale = world.invariant_row("INV-AAAAAA", before)
    stale["covers"][0]["after"] = before["RLZ-A00001"]  # the entry's old anchor: contradicts K_C
    examined = {"INV-AAAAAA": 1, "INV-BBBBBB": 1}
    world.rows(stale, family_row(examined), *trace_rows(*TRACES), closed=True)
    code = commit(world.code, {})
    commit(world.memory, {}, trailer=code)  # the closed file, committed by hand
    commit(world.memory, {"notes.md": "one more commit\n"}, trailer=code)
    refusal = _refused(world, code)
    assert "R09-history-rows" in refusal and "RLZ-A00001" in refusal

    tip, candidate = git(world.memory, "rev-parse", "main"), world.candidate()
    keys = {memo.memo_key(world.contract, candidate, tip, head) for head in ("a" * 40, "b" * 40)}
    assert len(keys) == 2 and None not in keys


def test_a_recovered_closeout_is_gated_like_a_fresh_one(world: Gated, ports: None) -> None:
    """A closeout that resumes with a memory commit already made records that commit: the gate
    judges it again. A commit the gate passed passes; one made without the gate is refused."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _rows_for_the_edit())
    code = commit(world.code, {})
    gated = _closeout(world, code).memory_commit
    passed = LifecycleOperationRecoveryCommits(codeCommit=code, memoryContentCommit=gated)
    assert _closeout(world, code, refused=True, recovered=passed).memory_commit == gated

    _edit(world, CODE_A.replace("return value", "return +value"))
    later = commit(world.code, {})
    ungated = commit(world.memory, {"notes.md": "committed by hand\n"}, trailer=later)
    slipped = LifecycleOperationRecoveryCommits(codeCommit=later, memoryContentCommit=ungated)
    refusal = _refused(world, later, recovered=slipped)
    assert "[knowledge-item-open] touched_invariant INV-AAAAAA" in refusal

    # The finalization of a recovered closeout asks the same gate before it writes the contract.
    contract = world.contract
    args = WorktreeArgs(contract_path=world.contract_path(), recovery_commits=slipped)
    proved = mock.Mock(side_effect=AssertionError("finalized an ungated memory commit"))
    with (
        mock.patch.object(closeout, "load_contract", return_value=contract),
        mock.patch.object(closeout, "require_ordinary_worktree"),
        mock.patch.object(closeout, "_closeout_approval_note", return_value="approved"),
        mock.patch.object(closeout, "publish_closeout_under_authority", lambda _c, run: run()),
        mock.patch.object(closeout, "prove_closeout_recovery_commits", proved),
        pytest.raises(RuntimeError, match="knowledge-item-open"),
    ):
        closeout._recover_closeout_finalization(contract, args)
    proved.assert_not_called()


def test_the_closeouts_own_writes_cannot_trip_its_gate(world: _Continued, ports: None) -> None:
    """L37 P1c, C9. The closeout writes memory content itself: the entity fingerprints, the route
    indexes, the ledger cache's ignore rule and the history closing (a converted tree gets no
    onboarding or overview stamps). Each is on disk before the gate reads the tree, the tree the
    gate judges is the tree committed, and the leaf's worklist is the same before and after."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _rows_for_the_edit())
    catalog = world.memory / "onboarding/entities.md"
    catalog.write_text(
        "# Entities\n\n| Entity | Algorithm | Fingerprint | EvidencePaths |\n"
        f"| --- | --- | --- | --- |\n| `landing` | `git-blob-set-v1` | `stale` | `{A}` |\n",
        encoding="utf-8",
    )
    code = commit(world.code, {})
    before, authored = world.gate(), world.candidate().memory
    assert before.ok
    judged: list[str] = []
    gate = closeout_external.leaf_gate_refusal

    def judging(contract: Any, *, code_tree: str, memory_tree: str) -> str | None:
        judged.append(memory_tree)
        return gate(contract, code_tree=code_tree, memory_tree=memory_tree)

    with mock.patch.object(closeout_external, "leaf_gate_refusal", judging):
        outcome = _closeout(world, code, own_writes=True)

    committed = outcome.memory_commit
    assert judged == [git(world.memory, "rev-parse", f"{committed}^{{tree}}")]  # the exact tree
    # What the closeout itself wrote: the committed tree against the tree the curator left.
    wrote = git(world.memory, "diff", "--name-only", authored, committed).splitlines()
    indexes = [  # real memory repositories ignore these caches
        "onboarding/overview.index.json",
        "onboarding/pkg/overview.index.json",
    ]
    assert wrote == [".gitignore", FIRST, "onboarding/entities.md", *indexes]  # no card is stamped
    assert "/memory.md" in git(world.memory, "show", f"{committed}:.gitignore")
    assert "`stale`" not in git(world.memory, "show", f"{committed}:onboarding/entities.md")
    assert [one["entity"] for one in outcome.refreshed_entities] == ["landing"]
    assert outcome.route_index_refresh["written"] >= 1
    assert json.loads(git(world.memory, "show", f"{committed}:{FIRST}"))["closed"] is True
    after = world.gate()  # over the committed pair: no item opened, no row went stale
    assert after.ok and after.worklist["digest"] == before.worklist["digest"]
    assert after.worklist["items"] == before.worklist["items"]

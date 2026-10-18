"""MIK-R178: the closeout refuses a memory fault before it commits the code.

Rule 1 (one verdict before the code commit): the closeout's candidate verdict, which the preview
and the apply's preflight both read, asks every refusal the memory refresh reaches from the code
candidate tree and the memory worktree's tree alone -- entity rows first. Rule 2: a refusal names
the catalog file, the entity row and every evidence path the candidate lacks, and says what clears
it. Rule 3: a row is recomputed when its evidence touches a changed path, when its evidence list
differs from the same row at the leaf's memory base, or when it is new, over the code commit's
tree. Rule 4: the curator's memory-quality check reports such a row as an actionable finding.
Rule 5: an add-only leaf closes out as today.

The two worlds are borrowed whole: the closeout cases use the mandatory gate's converted leaf on
real repositories, and the memory-quality case uses the curator run's own harness, so each case
exercises the route it names rather than a stub of it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from agents_remember.application.knowledge_worklist.leaf import recompute_leaf_worklist
from agents_remember.worktrees.modules import closeout, closeout_external, onboarding
from agents_remember.worktrees.modules.args import WorktreeArgs
from test_knowledge_closeout_gate import (
    CODE_A,
    A,
    B,
    Gated,
    _edit,
    build_gated,
    commit,
    family_row,
    git,
    trace_rows,
)
from test_knowledge_reopen import (
    _Context,
    _effective,
    _public_closeout,
    ports,
)
from test_onboarding_trace_gate import (
    C,
    Leaf,
    _run_controller,
    leaf,
)

__all__ = ["leaf", "ports", "world"]

GONE = "pkg/gone.py"
GONE_CARD = f"onboarding/{GONE}.md"
GONE_SUBJECT = f"onboarding:{GONE}"
OVERVIEW = "onboarding:pkg/overview"
CATALOG = "onboarding/entities.md"
EVIDENCE_ROW = "Evidence Holder"
UNTOUCHED_ROW = "Untouched Holder"
CODE_GONE = "def gone():\n    return 3\n"
STALE = "sha256:" + "0" * 64


@pytest.fixture
def world(tmp_path: Path) -> Gated:
    return build_gated(tmp_path)


# -- the fixtures the cases read -------------------------------------------------------------


def _catalog(*rows: tuple[str, tuple[str, ...]]) -> str:
    """A fingerprint catalog with one row per ``(entity, evidence paths)``, in the parser's shape."""

    lines = [
        "# Entities",
        "",
        "## Entity Fingerprints",
        "",
        "| Entity | Algorithm | Fingerprint | Evidence Paths |",
        "| --- | --- | --- | --- |",
    ]
    for entity, evidence in rows:
        listed = "; ".join(f"`{one}`" for one in evidence)
        lines.append(f"| `{entity}` | `git-blob-set-v1` | `{STALE}` | {listed} |")
    return "\n".join(lines) + "\n"


def _extended(world: Gated, catalog: str, *, gone: bool) -> Gated:
    """The fixture with a catalog on both official lines, and optionally a carded deleted file.

    Every addition is committed on ``main`` first and fast-forwarded into both ``leaf`` branches,
    so the rows the leaf re-points are rows of its memory base rather than new ones.
    """

    code_files = {GONE: CODE_GONE} if gone else {}
    memory_files = {CATALOG: catalog}
    if gone:
        memory_files[GONE_CARD] = "# gone\n"
    git(world.code, "checkout", "-q", "main")
    code_base = commit(world.code, code_files)
    git(world.code, "checkout", "-q", "leaf")
    git(world.code, "merge", "-q", "--ff-only", "main")
    git(world.memory, "checkout", "-q", "main")
    memory_base = commit(world.memory, memory_files, trailer=code_base)
    git(world.memory, "checkout", "-q", "leaf")
    git(world.memory, "merge", "-q", "--ff-only", "main")
    return replace(world, code_base=code_base, memory_base=memory_base)


def _deletion_rows(world: Gated, *, edited: bool) -> list[dict[str, Any]]:
    """The rows a deletion needs: its card's trace, its route's, and the deleted hunk's own row.

    A delete-only hunk admits only a ``no_invariant`` row (MIK-R10 rule 5): the curator's word for
    a change no realization covers. Without it the gate itself refuses, and the entity refusal
    this file proves would never be the one that answers.
    """

    subjects = [GONE_SUBJECT, OVERVIEW, *(["onboarding:pkg/a.py"] if edited else [])]
    found = recompute_leaf_worklist(world.contract)
    assert found is not None, "the leaf's worklist is unreadable"
    hunks = [
        {
            "subject": item["facts"]["row"],
            "disposition": "no_invariant",
            "reason": "The file is deleted; no realization covers it.",
        }
        for item in found[0]["items"]
        if item["kind"] == "unexplained_hunk" and item["facts"].get("path") == GONE
    ]
    return [*trace_rows(world, *subjects), *hunks]


def _answer(world: Gated, *, edit: bool, gone: bool = True) -> None:
    """The leaf's own change, and the rows that answer it: ``land`` when asked, plus the deletion.

    The deletion (when the fixture holds one) is the fault under test, and the worklist does not
    carry a deleted path, so only the row's own evidence can put it in the refresh's way.
    """

    if edit:
        _edit(world, CODE_A.replace("return value", "return -value"))
        before = world.reanchor("RLZ-A00001", "RLZ-A00002")
    rows = (
        _deletion_rows(world, edited=edit)
        if gone
        else trace_rows(world, "onboarding:pkg/a.py", OVERVIEW)
    )
    if not edit:
        world.rows(*rows)
        return
    world.rows(
        world.invariant_row("INV-AAAAAA", before),
        family_row({"INV-AAAAAA": 1, "INV-BBBBBB": 1}),
        *rows,
    )


def _row_line(catalog: str, entity: str) -> str:
    (line,) = [row for row in catalog.splitlines() if row.startswith(f"| `{entity}`")]
    return line


def _row_fingerprint(catalog: str, entity: str = EVIDENCE_ROW) -> str:
    """The row's fingerprint value, read out of the catalog's own text."""

    cells = [
        cell.strip().strip("`") for cell in _row_line(catalog, entity).strip().strip("|").split("|")
    ]
    return cells[2]


def _committed_catalog(world: Gated, memory_commit: str) -> str:
    return git(world.memory, "show", f"{memory_commit}:{CATALOG}")


def _value_at(world: Gated, code_commit: str, *evidence: str) -> str:
    """``git-blob-set-v1`` over ``evidence`` at the code commit's tree, computed independently.

    Read straight from Git rather than from the function under test, so the assertion is an oracle
    for the algorithm (a path and its NUL, a newline, one SHA-256) and not a restatement of it.
    """

    tree = git(world.code, "rev-parse", f"{code_commit}^{{tree}}")
    lines = [
        f"{path}\0{git(world.code, 'rev-parse', f'{tree}:{path}')}\n" for path in sorted(evidence)
    ]
    return "sha256:" + hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _preview(world: Gated) -> dict[str, Any]:
    """The real preview over the fixture's two trees, with only its context resolution pinned."""

    contract = world.contract
    args = WorktreeArgs(contract_path=contract.contract_path, closeout_input=_effective())
    context = _Context(world.code, world.memory / "onboarding")
    with (
        mock.patch.object(closeout_external, "contract_context", return_value=context),
        mock.patch.object(closeout, "contract_context", return_value=context),
        mock.patch.object(onboarding, "contract_context", return_value=context),
        mock.patch.object(closeout, "report_operation_progress"),
    ):
        return closeout.closeout_preview_payload(contract, args)


# -- rule 1 and rule 2: one verdict, before the code commit, naming row and path --------------


def test_a_deleted_evidence_path_stops_the_preview_and_the_preflight_before_the_code_commit(
    world: Gated, ports: None
) -> None:
    """The L56 shape: a leaf changes one cited file and deletes another an entity row cites.

    The row is recomputed because its evidence touches the changed path, and the deleted path is
    what the refresh cannot read. The preview answers ``knowledge-gate-refused`` naming the
    catalog, the row and the path, and the apply refuses at its preflight -- so no code commit is
    published, both sides stay uncommitted, and no reader meets a raw Git message after a commit
    that cannot be taken back.
    """

    world = _extended(world, _catalog((EVIDENCE_ROW, (A, GONE))), gone=True)
    (world.code / GONE).unlink()
    _answer(world, edit=True)
    assert world.gate().ok, world.gate().refusal()

    preview = _preview(world)
    assert preview["state"] == "knowledge-gate-refused", preview
    rendered = " ".join(preview["knowledge_gate"]["findings"]) + " " + str(preview["summary"])
    assert EVIDENCE_ROW in rendered
    assert GONE in rendered
    assert CATALOG in rendered
    assert "re-point the row's evidence list" in rendered

    code_head = git(world.code, "rev-parse", "HEAD")
    memory_head = git(world.memory, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError) as refused:
        _public_closeout(world)
    assert "cites evidence paths the code candidate does not hold" in str(refused.value)
    assert EVIDENCE_ROW in str(refused.value) and GONE in str(refused.value)
    assert git(world.code, "rev-parse", "HEAD") == code_head  # nothing was committed
    assert git(world.memory, "rev-parse", "HEAD") == memory_head
    assert GONE in git(world.code, "status", "--porcelain")


# -- rule 3: an edited evidence list is recomputed, over the code commit's tree ---------------


@pytest.mark.parametrize(
    "edit", [True, False], ids=["keeps-a-changed-path", "unchanged-files-only"]
)
def test_a_repointed_row_is_recomputed_and_the_closeout_lands(
    world: Gated, ports: None, edit: bool
) -> None:
    """L56's repair: the row is re-pointed, the closeout lands, and the tool computes the value.

    ``unchanged-files-only`` names ``pkg/b.py``, which the leaf does not touch, and the leaf
    changes nothing else but the deletion the worklist does not carry -- so the old changed-path
    rule alone would leave the re-pointed row's stale value in place. Rule 3 recomputes it because
    its evidence list differs from the same row at the leaf's memory base.
    """

    world = _extended(world, _catalog((EVIDENCE_ROW, (A, GONE))), gone=True)
    (world.code / GONE).unlink()
    _answer(world, edit=edit)
    evidence = A if edit else B
    (world.memory / CATALOG).write_text(_catalog((EVIDENCE_ROW, (evidence,))), encoding="utf-8")
    assert world.gate().ok, world.gate().refusal()

    payload = _public_closeout(world).payload
    committed = _committed_catalog(world, payload["memory_content_commit"])
    assert _row_fingerprint(committed) == _value_at(world, payload["code_commit"], evidence)
    assert _row_fingerprint(committed) != STALE


# -- rule 5: an add-only leaf closes out as today ---------------------------------------------


def test_an_add_only_leaf_closes_out_as_today_and_keeps_an_unchanged_rows_value(
    world: Gated, ports: None
) -> None:
    """No deletion anywhere: the preview promises the closeout, it lands, and the row whose
    evidence touches nothing changed keeps its recorded value -- unchanged rows and the algorithm
    are as they were."""

    world = _extended(
        world,
        _catalog((EVIDENCE_ROW, (A,)), (UNTOUCHED_ROW, (B,))),
        gone=False,
    )
    _answer(world, edit=True, gone=False)
    assert _preview(world)["state"] == "would-closeout"

    payload = _public_closeout(world).payload
    committed = _committed_catalog(world, payload["memory_content_commit"])
    assert _row_fingerprint(committed) == _value_at(world, payload["code_commit"], A)
    assert _row_fingerprint(committed, UNTOUCHED_ROW) == STALE


# -- rule 4: the curator's memory-quality check names the row ---------------------------------


def test_the_memory_quality_check_names_an_entity_row_citing_a_path_the_candidate_lacks(
    leaf: Leaf,
) -> None:
    """Rule 4: measured against the leaf's code candidate, the row is an actionable finding.

    The finding names the catalog file, the row and the path, and it counts toward
    ``curatorActionableCount`` -- the curator meets it while it can still re-point the row.
    """

    catalog = leaf.memory / CATALOG
    (leaf.code / C).unlink()  # the candidate lacks the path the second row cites
    catalog.write_text(_catalog((EVIDENCE_ROW, (B,))), encoding="utf-8")
    baseline, _ = _run_controller(leaf, (C,))
    assert [
        row for row in baseline["repairFindings"] if row.get("check") == "entity-evidence"
    ] == []

    catalog.write_text(_catalog((EVIDENCE_ROW, (C,))), encoding="utf-8")
    refused, _ = _run_controller(leaf, (C,))

    finding = next(
        row for row in refused["repairFindings"] if row.get("check") == "entity-evidence"
    )
    assert finding["code"] == "entity-evidence-absent"
    assert finding["path"].endswith(CATALOG)
    assert EVIDENCE_ROW in finding["message"] and C in finding["message"]
    # The deletion's own findings are in both runs; the row is the one finding the pair differs by.
    assert refused["curatorActionableCount"] == baseline["curatorActionableCount"] + 1


# -- rule 2: an unexpected Git failure names the step, the path and the tree ------------------


def test_an_unexpected_git_failure_in_the_refresh_names_its_step_path_and_tree(
    tmp_path: Path,
) -> None:
    """No bare Git message: the refusal says which step failed, which path it read and at which
    tree, so a reader cannot take it for a read in the memory repository (the L56 confusion)."""

    code = tmp_path / "code"
    code.mkdir()
    git(code, "init", "-q", "-b", "main")
    git(code, "config", "user.email", "fixture@example.invalid")
    git(code, "config", "user.name", "fixture")
    commit(code, {A: CODE_A})
    tree = git(code, "rev-parse", "HEAD^{tree}")
    memory = tmp_path / "memory"
    (memory / "onboarding").mkdir(parents=True)
    (memory / "onboarding" / "entities.md").write_text(
        _catalog((EVIDENCE_ROW, (A,))), encoding="utf-8"
    )
    context = _Context(code, memory / "onboarding")

    with (
        mock.patch.object(
            onboarding, "compute_git_blob_set_fingerprint", side_effect=RuntimeError("boom")
        ),
        pytest.raises(RuntimeError) as raised,
    ):
        onboarding.refresh_entity_fingerprints_for_context(
            context, [A], code_commit=git(code, "rev-parse", "HEAD")
        )

    detail = str(raised.value)
    assert "step entity-fingerprint refresh" in detail
    assert A in detail
    assert tree in detail
    assert re.search(r"row 'Evidence Holder'", detail)


# -- L176-R1-F01: evidence the refresh would not recompute is still checked -------------------


def test_a_row_whose_only_evidence_the_leaf_deletes_is_refused_before_the_code_commit(
    world: Gated, ports: None
) -> None:
    """F01: the row is not planned -- nothing about it changed -- and its only path is deleted.

    Selection by the refresh plan let this case through: the row's evidence touched no changed
    path, so it was never a planned row, so the candidate verdict never asked whether the row's
    evidence still exists. The closeout published the code and the memory with a row pointing at a
    file the candidate deleted. Every row is evidence-checked now, planned or not: the preview says
    ``knowledge-gate-refused`` naming the catalog, the row and the path, and the apply refuses with
    both sides uncommitted.
    """

    world = _extended(world, _catalog((EVIDENCE_ROW, (GONE,))), gone=True)
    (world.code / GONE).unlink()
    _answer(world, edit=False)  # the deletion is answered; nothing else changes
    assert world.gate().ok, world.gate().refusal()

    plan = onboarding.entity_fingerprint_refresh_plan_for_context(
        _Context(world.code, world.memory / "onboarding"), []
    )
    assert plan["required"] == []  # the row is invisible to the old selection
    assert [item["entity"] for item in plan["unsupported"]] == []

    preview = _preview(world)
    assert preview["state"] == "knowledge-gate-refused", preview
    rendered = " ".join(preview["knowledge_gate"]["findings"]) + " " + str(preview["summary"])
    assert EVIDENCE_ROW in rendered and GONE in rendered and CATALOG in rendered
    assert "re-point the row's evidence list" in rendered

    code_head = git(world.code, "rev-parse", "HEAD")
    memory_head = git(world.memory, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError) as refused:
        _public_closeout(world)
    assert "cites evidence paths the code candidate does not hold" in str(refused.value)
    assert git(world.code, "rev-parse", "HEAD") == code_head
    assert git(world.memory, "rev-parse", "HEAD") == memory_head


def test_re_pointing_a_kept_row_lets_that_leaf_close_out(world: Gated, ports: None) -> None:
    """F01's clearing action: re-point the row at a retained file and the same leaf lands."""

    world = _extended(world, _catalog((EVIDENCE_ROW, (GONE, B))), gone=True)
    (world.code / GONE).unlink()
    _answer(world, edit=False)
    (world.memory / CATALOG).write_text(_catalog((EVIDENCE_ROW, (B,))), encoding="utf-8")
    assert world.gate().ok, world.gate().refusal()

    payload = _public_closeout(world).payload
    committed = _committed_catalog(world, payload["memory_content_commit"])
    assert _row_fingerprint(committed) == _value_at(world, payload["code_commit"], B)


# -- L176-R1-F02: the route-index leg refuses on tree content too -----------------------------


def test_an_unreadable_route_index_is_refused_before_the_code_commit(
    world: Gated, ports: None
) -> None:
    """F02: the cache the refresh must re-render is held as bytes it cannot decode.

    The refresh reads an existing index as UTF-8 and re-raises. That refusal is decided by the
    candidate memory tree's own bytes -- nothing environmental -- and it used to arrive after the
    code commit, leaving a half-landed leaf. The candidate verdict asks the route-index leg now: the
    preview refuses naming the step and the path, the apply refuses before publication, and both
    heads stay where they were.
    """

    world = _extended(world, _catalog((EVIDENCE_ROW, (A,))), gone=False)
    _answer(world, edit=True, gone=False)
    index = world.memory / "onboarding" / "pkg" / "overview.index.json"
    index.write_bytes(b"\xff\xfe\n")
    assert world.gate().ok, world.gate().refusal()

    preview = _preview(world)
    assert preview["state"] == "knowledge-gate-refused", preview
    rendered = " ".join(preview["knowledge_gate"]["findings"]) + " " + str(preview["summary"])
    assert "route-index plan" in rendered
    assert "overview.index.json" in rendered

    code_head = git(world.code, "rev-parse", "HEAD")
    memory_head = git(world.memory, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError) as refused:
        _public_closeout(world)
    assert "route-index plan" in str(refused.value)
    assert "overview.index.json" in str(refused.value)
    assert git(world.code, "rev-parse", "HEAD") == code_head
    assert git(world.memory, "rev-parse", "HEAD") == memory_head


# -- L176-R1-F03: one refusal enumerates every cause of the same row --------------------------


def _unsupported_catalog(*rows: tuple[str, tuple[str, ...]]) -> str:
    """``_catalog`` with an algorithm this closeout cannot evaluate on every row."""

    return _catalog(*rows).replace("`git-blob-set-v1`", "`unsupported-v1`")


def test_a_compound_row_names_its_algorithm_and_every_absent_path_in_one_refusal(
    world: Gated, ports: None
) -> None:
    """F03: an unsupported algorithm diverted the row out of the missing-evidence check.

    An operator was told to fix the algorithm and would then meet the undisclosed absent path on
    the next attempt. Both causes are independently knowable from the same two trees, so one
    refusal names both, with the catalog, the row and the remedy for each.
    """

    world = _extended(world, _unsupported_catalog((EVIDENCE_ROW, (A, GONE))), gone=True)
    (world.code / GONE).unlink()
    _answer(world, edit=True)  # pkg/a.py changes, so the row is planned and unsupported
    assert world.gate().ok, world.gate().refusal()

    preview = _preview(world)
    assert preview["state"] == "knowledge-gate-refused", preview
    rendered = " ".join(preview["knowledge_gate"]["findings"]) + " " + str(preview["summary"])
    assert "unsupported algorithm 'unsupported-v1'" in rendered
    assert GONE in rendered
    assert EVIDENCE_ROW in rendered and CATALOG in rendered
    assert "re-point the row's evidence list" in rendered

    code_head = git(world.code, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError) as refused:
        _public_closeout(world)
    assert "unsupported algorithm 'unsupported-v1'" in str(refused.value)
    assert GONE in str(refused.value)
    assert git(world.code, "rev-parse", "HEAD") == code_head


def test_an_unsupported_row_with_all_evidence_present_names_only_the_algorithm(
    world: Gated, ports: None
) -> None:
    """F03's control: no absent path is invented for a row whose evidence the candidate holds."""

    world = _extended(world, _unsupported_catalog((EVIDENCE_ROW, (A,))), gone=False)
    _answer(world, edit=True, gone=False)
    assert world.gate().ok, world.gate().refusal()

    preview = _preview(world)
    assert preview["state"] == "knowledge-gate-refused", preview
    rendered = " ".join(preview["knowledge_gate"]["findings"])
    assert "unsupported algorithm 'unsupported-v1'" in rendered
    assert "cites evidence paths" not in rendered


def test_a_planned_row_without_a_value_is_refused_naming_the_clearing_action(
    world: Gated, ports: None
) -> None:
    """F03's second control: the missing-value refusal survives the all-rows evidence check."""

    catalog = _catalog((EVIDENCE_ROW, (A,))).replace(STALE, "")
    world = _extended(world, catalog, gone=False)
    _answer(world, edit=True, gone=False)
    assert world.gate().ok, world.gate().refusal()

    preview = _preview(world)
    assert preview["state"] == "knowledge-gate-refused", preview
    rendered = " ".join(preview["knowledge_gate"]["findings"])
    assert "has no fingerprint value" in rendered
    assert EVIDENCE_ROW in rendered

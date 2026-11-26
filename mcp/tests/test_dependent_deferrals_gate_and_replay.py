"""Dependent deferrals: gate, sync merge, MQC Lever-1 flags, reader views, DPD-GHEXS9 replay (L52-R5..R8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_gate.deferrals import (
    CONFIRMED_DEFERRED_WITHOUT_EDIT,
    DEFERRAL_DUE_FILE,
    DEFERRAL_DUE_FILE_WARNING,
    DEFERRAL_DUE_TASK,
    DEFERRAL_ORPHANED,
    deferral_gate_findings,
)
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.conversion.crossing import merge_trees
from agents_remember.memory_quality.knowledge_validator import (
    CodePathSet,
    KnowledgeTree,
    validate_tree,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    history_path,
    record_path,
)
from dependent_deferrals_test_support import (
    HERMES_CODE,
    HERMES_DOC,
    LEAF_50,
    LEAF_55,
    LEAF_56,
    LEAF_60,
    LEAF_CAP_5,
    LEAF_CAP_6,
    LEAF_ORIGIN,
    SETTINGS_DOC,
    TASK_BUG,
    TASK_CAP,
    UNRELATED_CODE,
    build_deferral_world,
    commit_memory,
    confirm_op,
    defer_op,
    defer_spec,
    deferral_entry,
    dependent_doc,
    gate_trees,
    layout_bytes_for,
    lift_op,
    read_dependents,
    redefer_op,
    row_op,
    run_mqc,
    seed_deferred_world,
    seed_tasks,
    write_dep,
    write_dependent_file,
)
from knowledge_writer_test_support import CODE_FILE, commit_all, git


def test_06_deferral_fires_on_task_or_file_blob_whichever_first_and_never_in_creating_leaf(
    tmp_path: Path,
) -> None:
    """Test 6: deferral never fires in creating leaf; fires on target task or watched-file edit first."""

    d1 = deferral_entry("DFR-F00001", {"task": LEAF_60, "file": CODE_FILE}, created_by=LEAF_50)
    world, code_base, mem_base, _ = seed_deferred_world(tmp_path, "DPD-F00001", [d1])

    (world.code / CODE_FILE).write_text(
        (world.code / CODE_FILE).read_text() + "# edit\n", encoding="utf-8"
    )
    code_changed = commit_all(world.code, "edit watched file")
    changed = gate_trees(
        world, base_code=code_base, cand_code=code_changed, base_mem=mem_base, cand_mem=mem_base
    )
    unchanged = gate_trees(
        world, base_code=code_base, cand_code=code_base, base_mem=mem_base, cand_mem=mem_base
    )

    assert deferral_gate_findings(changed, LEAF_50) == ([], [])
    assert [f.code for f in deferral_gate_findings(unchanged, LEAF_60)[0]] == [DEFERRAL_DUE_TASK]
    assert [f.code for f in deferral_gate_findings(changed, LEAF_55)[0]] == [DEFERRAL_DUE_FILE]


def test_07_file_changed_on_another_line_makes_deferral_due_after_merge(tmp_path: Path) -> None:
    """Test 7: merging another branch that modifies condition.file makes deferral due in reader and MQC."""

    d1 = deferral_entry("DFR-M00001", {"file": CODE_FILE})
    world, _code_base, _mem_base, _ = seed_deferred_world(tmp_path, "DPD-M00001", [d1])

    git(world.code, "checkout", "-q", "-b", "other-line")
    (world.code / CODE_FILE).write_text(
        (world.code / CODE_FILE).read_text() + "# other\n", encoding="utf-8"
    )
    commit_all(world.code, "other line edit")
    git(world.code, "checkout", "-q", "main")
    git(world.code, "merge", "-q", "--no-ff", "other-line", "-m", "merge other line")
    merged_code = git(world.code, "rev-parse", "HEAD")

    row = next(r for r in read_dependents(world)["rows"] if r["id"] == "DPD-M00001")
    assert row["state"] == "deferred" and row["deferrals"][0]["state"] == "due"

    mqc = run_mqc(world, owner=LEAF_56, code_base=merged_code)
    assert mqc["ok"] is True and mqc["findingCount"] == 0
    assert any(f.get("kind") == "deferral_due_warning" for f in mqc["reportOnlyFindings"])


def test_08_due_deferral_blocks_only_the_leaf_it_is_due_in_not_unrelated_leaf(
    tmp_path: Path,
) -> None:
    """Test 8: due deferral blocks target leaf, while an unrelated leaf closes cleanly."""

    d1 = deferral_entry("DFR-B00001", {"task": LEAF_60, "file": CODE_FILE})
    world, code_base, mem_base, _ = seed_deferred_world(tmp_path, "DPD-B00001", [d1])

    (world.code / UNRELATED_CODE).write_text("VALUE = 2\n", encoding="utf-8")
    code_unrel = commit_all(world.code, "unrelated edit")
    trees = gate_trees(
        world, base_code=code_base, cand_code=code_unrel, base_mem=mem_base, cand_mem=mem_base
    )

    assert deferral_gate_findings(trees, LEAF_56) == ([], [])
    assert [f.code for f in deferral_gate_findings(trees, LEAF_60)[0]] == [DEFERRAL_DUE_TASK]


def test_09_orphaned_deferral_warns_and_blocks_nothing_including_cross_master_cap_l5(
    tmp_path: Path,
) -> None:
    """Test 9: terminal cross-master 261124-CAP-L5 (Completed) and L6 (abandoned) warn, never block, and can re-home."""

    d_done = deferral_entry("DFR-R00001", {"task": LEAF_CAP_5}, owner=TASK_CAP)
    d_abnd = deferral_entry("DFR-R00002", {"task": LEAF_CAP_6}, owner=TASK_CAP)
    world, code_base, mem_base, _ = seed_deferred_world(tmp_path, "DPD-R00001", [d_done, d_abnd])
    seed_tasks(world.root, cap_l5_status="Completed", cap_l6_status="abandoned")

    row = next(r for r in read_dependents(world)["rows"] if r["id"] == "DPD-R00001")
    assert {d["id"]: d["state"] for d in row["deferrals"]} == {
        "DFR-R00001": "orphaned",
        "DFR-R00002": "orphaned",
    }

    mqc = run_mqc(world, owner=LEAF_55, code_base=code_base)
    assert mqc["ok"] is True and mqc["findingCount"] == 0
    orphans = [f for f in mqc["reportOnlyFindings"] if f.get("kind") == "deferral_orphaned"]
    assert {f["deferralId"] for f in orphans} == {"DFR-R00001", "DFR-R00002"}

    trees = gate_trees(
        world, base_code=code_base, cand_code=code_base, base_mem=mem_base, cand_mem=mem_base
    )
    refusing, reports = deferral_gate_findings(trees, LEAF_55)
    assert refusing == [] and [r.code for r in reports] == [DEFERRAL_ORPHANED, DEFERRAL_ORPHANED]

    r_rehome = write_dep(
        world,
        [redefer_op("DPD-R00001", "DFR-R00001", "Re-homed after CAP-L5.", {"task": LEAF_60})],
        rows=[row_op("DPD-R00001", "re_deferred", "Re-homed to L60.")],
        leaf=LEAF_55,
        code_base=code_base,
    )
    assert r_rehome.state == "written", r_rehome.problems
    row_after = next(r for r in read_dependents(world)["rows"] if r["id"] == "DPD-R00001")
    assert (
        next(d for d in row_after["deferrals"] if d["condition"].get("task") == LEAF_60)["state"]
        == "open"
    )


def test_12_pre_base_file_change_warns_but_does_not_block_unrelated_leaf(tmp_path: Path) -> None:
    """Test 12: watched file changed before B -> non-blocking warning in gate and MQC for unrelated leaf."""

    d1 = deferral_entry("DFR-P00001", {"file": CODE_FILE})
    world, _code_v1, mem_base, _ = seed_deferred_world(tmp_path, "DPD-P00001", [d1])

    (world.code / CODE_FILE).write_text(
        (world.code / CODE_FILE).read_text() + "# pre-base\n", encoding="utf-8"
    )
    code_b = commit_all(world.code, "pre-base watched file edit")
    (world.code / UNRELATED_CODE).write_text("VALUE = 99\n", encoding="utf-8")
    code_c = commit_all(world.code, "unrelated leaf edit")

    trees = gate_trees(
        world, base_code=code_b, cand_code=code_c, base_mem=mem_base, cand_mem=mem_base
    )
    refusing, reports = deferral_gate_findings(trees, LEAF_56)
    assert refusing == [] and [r.code for r in reports] == [DEFERRAL_DUE_FILE_WARNING]

    mqc = run_mqc(world, owner=LEAF_56, code_base=code_b)
    assert mqc["ok"] is True and mqc["findingCount"] == 0
    assert any(f.get("kind") == "deferral_due_warning" for f in mqc["reportOnlyFindings"])


def test_13_lift_appears_for_reviewer_in_mqc_report_only_findings(tmp_path: Path) -> None:
    """Test 13: lifting a deferral writes deferral_lifted row and surfaces in MQC reportOnlyFindings."""

    d1 = deferral_entry("DFR-T00001", {"task": LEAF_60})
    world, code_base, _mem_base, _ = seed_deferred_world(tmp_path, "DPD-T00001", [d1])

    r_lift = write_dep(
        world,
        [lift_op("DPD-T00001", "DFR-T00001", "Verified no stale text.")],
        rows=[row_op("DPD-T00001", "deferral_lifted", "Verified prose matches code.")],
        leaf=LEAF_55,
        code_base=code_base,
    )
    assert r_lift.state == "written", r_lift.problems
    last = json.loads((world.memory / history_path(LEAF_55)).read_text(encoding="utf-8"))["rows"][
        -1
    ]
    assert last["disposition"] == "deferral_lifted" and last["deferralIds"] == ["DFR-T00001"]

    mqc = run_mqc(world, owner=LEAF_55, code_base=code_base)
    assert mqc["ok"] is True and mqc["findingCount"] == 0
    lifted = [f for f in mqc["reportOnlyFindings"] if f.get("kind") == "deferral_lifted"]
    assert (
        len(lifted) == 1
        and lifted[0]["recordId"] == "DPD-T00001"
        and lifted[0]["deferralIds"] == ["DFR-T00001"]
    )


def test_14_sync_merges_deferrals_by_id_both_directions_and_catches_invalid_confirmation_at_closeout(
    tmp_path: Path,
) -> None:
    """Test 14: 3-way merge merges deferrals by id both ways, resolved wins, and post-sync invalid confirm fails gate."""

    world, code_base = build_deferral_world(tmp_path)
    blob1 = git(world.code, "hash-object", CODE_FILE)
    dep_path = record_path("dependent", "DPD-S00001", "settings-json")
    base_doc = dependent_doc("DPD-S00001", stand_blob=blob1, confirmed_commit=code_base)
    d1 = deferral_entry("DFR-AAA111", {"task": LEAF_60}, passage="Passage A", owner=TASK_BUG)
    d2 = deferral_entry(
        "DFR-BBB222", {"task": LEAF_CAP_5}, passage="Passage B", owner=TASK_CAP, created_by=LEAF_55
    )

    def enc(doc: dict[str, Any]) -> dict[str, bytes]:
        return {dep_path: canonical_text(doc).encode("utf-8")}

    side_a, side_b = (
        {**base_doc, "revision": 2, "deferrals": [d1]},
        {**base_doc, "revision": 2, "deferrals": [d2]},
    )
    for left, right in ((side_a, side_b), (side_b, side_a)):
        merged = merge_trees(enc(base_doc), enc(left), enc(right), repository=world.memory)
        assert merged.conflicts == []
        assert [
            d["id"] for d in json.loads((merged.files[dep_path] or b"{}").decode())["deferrals"]
        ] == ["DFR-AAA111", "DFR-BBB222"]

    base_d1, resolved = {**base_doc, "revision": 2, "deferrals": [d1]}, {**base_doc, "revision": 3}
    for left, right in ((resolved, base_d1), (base_d1, resolved)):
        merged = merge_trees(enc(base_d1), enc(left), enc(right), repository=world.memory)
        assert merged.conflicts == [] and "deferrals" not in json.loads(
            (merged.files[dep_path] or b"{}").decode()
        )

    ours = {
        **base_doc,
        "revision": 2,
        "confirmed": {
            "task": LEAF_55,
            "role": "curator",
            "statement": "Pre-sync.",
            "commit": code_base,
        },
    }
    theirs = {**base_doc, "revision": 2, "deferrals": [d1]}
    merged_c = merge_trees(enc(base_doc), enc(ours), enc(theirs), repository=world.memory)
    assert merged_c.conflicts == []
    merged_bytes = merged_c.files[dep_path]
    assert merged_bytes is not None
    parsed = json.loads(merged_bytes.decode("utf-8"))
    assert parsed["confirmed"]["task"] == LEAF_55 and [d["id"] for d in parsed["deferrals"]] == [
        "DFR-AAA111"
    ]

    def _kt(label: str, raw: bytes) -> KnowledgeTree:
        return KnowledgeTree(
            label=label,
            files={
                LAYOUT_MARKER_PATH: layout_bytes_for(world.memory, [(dep_path, raw)]),
                dep_path: raw,
            },
        )

    val = validate_tree(
        _kt("merged", merged_bytes),
        bases=(_kt("ours", enc(ours)[dep_path]), _kt("theirs", enc(theirs)[dep_path])),
        code=CodePathSet(
            label="code", paths=frozenset({CODE_FILE, SETTINGS_DOC}), repository=world.code
        ),
        leaf_publication=False,
    )
    assert val.refusals == ()

    (world.memory / dep_path).parent.mkdir(parents=True, exist_ok=True)
    (world.memory / dep_path).write_bytes(merged_bytes)
    mem_merged = commit_memory(world.memory, "merged sync commit", code_commit=code_base)
    refusing, _ = deferral_gate_findings(
        gate_trees(
            world,
            base_code=code_base,
            cand_code=code_base,
            base_mem=mem_merged,
            cand_mem=mem_merged,
        ),
        LEAF_55,
    )
    assert [f.code for f in refusing] == [
        CONFIRMED_DEFERRED_WITHOUT_EDIT
    ] and "DFR-AAA111" in refusing[0].message


def test_15_flag_lists_pre_base_stale_confirmation_of_untouched_doc_with_moved_links(
    tmp_path: Path,
) -> None:
    """Test 15: MQC Lever-1 flags unedited confirmation of a pre-base-stale doc with preBaseMovedLinks."""

    world, code_v1 = build_deferral_world(tmp_path)
    blob1 = git(world.code, "hash-object", CODE_FILE)
    write_dependent_file(
        world.memory,
        "settings-json",
        dependent_doc("DPD-G00001", stand_blob=blob1, confirmed_commit=code_v1),
    )
    commit_memory(world.memory, "seed dependent at v1", code_commit=code_v1)

    (world.code / CODE_FILE).write_text(
        (world.code / CODE_FILE).read_text() + "# moved\n", encoding="utf-8"
    )
    code_b = commit_all(world.code, "move landing.py before B")

    report = write_dep(
        world,
        [confirm_op("DPD-G00001", LEAF_55, statement="Confirmed without editing.")],
        rows=[row_op("DPD-G00001", "changed", "Confirmed.", effect="clarify")],
        leaf=LEAF_55,
        code_base=code_b,
    )
    assert report.state == "written", report.problems

    mqc = run_mqc(world, owner=LEAF_55, code_base=code_b)
    assert mqc["ok"] is True and mqc["findingCount"] == 0
    flagged = [
        f for f in mqc["reportOnlyFindings"] if f.get("kind") == "pre_base_stale_confirmation"
    ]
    assert (
        len(flagged) == 1
        and flagged[0]["recordId"] == "DPD-G00001"
        and flagged[0]["confirmedBy"] == LEAF_55
    )
    assert any(link.get("path") == CODE_FILE for link in flagged[0]["preBaseMovedLinks"])


def test_16_reader_view_lists_every_deferral_with_state_and_marks_stale_only(
    tmp_path: Path,
) -> None:
    """Test 16: knowledge_read dependent & record views derive deferred state, deferral fields, and staleOnly."""

    world, _ = build_deferral_world(tmp_path)
    seed_tasks(world.root, cap_l5_status="Completed")
    (world.code / "docs/reference/current.md").write_text("# Current\n", encoding="utf-8")
    (world.code / "docs/reference/stale.md").write_text("# Stale\n", encoding="utf-8")
    code_base = commit_all(world.code, "add extra docs")
    blob_code = git(world.code, "hash-object", CODE_FILE)

    write_dependent_file(
        world.memory,
        "current",
        dependent_doc(
            "DPD-C00001",
            doc_path="docs/reference/current.md",
            stand_blob=blob_code,
            confirmed_commit=code_base,
        ),
    )
    write_dependent_file(
        world.memory,
        "stale",
        dependent_doc(
            "DPD-E00001",
            doc_path="docs/reference/stale.md",
            stand_blob="0" * 40,
            confirmed_commit=code_base,
        ),
    )
    write_dependent_file(
        world.memory,
        "deferred",
        dependent_doc(
            "DPD-D00001",
            stand_blob=blob_code,
            confirmed_commit=code_base,
            deferrals=[
                deferral_entry("DFR-D00001", {"task": LEAF_60}, passage="Open passage"),
                deferral_entry(
                    "DFR-D00002", {"file": CODE_FILE}, passage="Due passage", file_blob="1" * 40
                ),
                deferral_entry(
                    "DFR-D00003", {"task": LEAF_CAP_5}, passage="Orphan passage", owner=TASK_CAP
                ),
            ],
        ),
    )
    commit_memory(world.memory, "seed three dependents", code_commit=code_base)

    by_id = {row["id"]: row for row in read_dependents(world, stale_only=False)["rows"]}
    assert by_id["DPD-C00001"]["state"] == "current" and by_id["DPD-E00001"]["state"] == "stale"
    dfr_row = by_id["DPD-D00001"]
    assert dfr_row["state"] == "deferred" and dfr_row["deferred"] is True
    derived = {d["id"]: d for d in dfr_row["deferrals"]}
    assert {k: v["state"] for k, v in derived.items()} == {
        "DFR-D00001": "open",
        "DFR-D00002": "due",
        "DFR-D00003": "orphaned",
    }
    assert all(
        {"passage", "reason", "condition", "owner", "age", "state"} <= set(e)
        for e in derived.values()
    )

    stale_by_id = {row["id"]: row for row in read_dependents(world, stale_only=True)["rows"]}
    assert set(stale_by_id) == {"DPD-E00001", "DPD-D00001"}
    assert (
        stale_by_id["DPD-D00001"]["state"] == "deferred"
        and stale_by_id["DPD-D00001"]["deferred"] is True
    )

    rec_view = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="record",
            record_id="DPD-D00001",
            repository_root=str(world.code),
        ),
        coordination_root=str(world.root),
    )
    assert (
        rec_view["dependent"]["state"] == "deferred"
        and len(rec_view["dependent"]["deferrals"]) == 3
    )


def test_replay_dpd_ghexs9_hermes_pre_and_post_deferral(tmp_path: Path) -> None:
    """Replay: DPD-GHEXS9 / docs/install/hermes.md before deferral (flagged) vs after deferral (refused)."""

    world, _ = build_deferral_world(tmp_path)
    (world.code / "docs/install").mkdir(parents=True, exist_ok=True)
    (world.code / HERMES_DOC).write_text(
        "# Hermes Install\n\nLegacy install flow.\n", encoding="utf-8"
    )
    (world.code / HERMES_CODE).parent.mkdir(parents=True, exist_ok=True)
    (world.code / HERMES_CODE).write_text("INSTALL_MODE = 'legacy'\n", encoding="utf-8")
    code_v1 = commit_all(world.code, "hermes v1")
    hermes_blob_v1 = git(world.code, "hash-object", HERMES_CODE)

    write_dependent_file(
        world.memory,
        "hermes",
        dependent_doc(
            "DPD-GHEXS9",
            doc_path=HERMES_DOC,
            stand_path=HERMES_CODE,
            stand_blob=hermes_blob_v1,
            confirmed_task=LEAF_ORIGIN,
            confirmed_commit=code_v1,
        ),
    )
    mem_v1 = commit_memory(world.memory, "seed DPD-GHEXS9", code_commit=code_v1)

    (world.code / HERMES_CODE).write_text("INSTALL_MODE = 'native'\n", encoding="utf-8")
    code_b = commit_all(world.code, "hermes native install before B")

    confirm_handoff = [
        confirm_op(
            "DPD-GHEXS9",
            LEAF_55,
            doc_path=HERMES_DOC,
            stand_path=HERMES_CODE,
            statement="Untouched confirm.",
        )
    ]
    before = write_dep(
        world,
        confirm_handoff,
        rows=[row_op("DPD-GHEXS9", "changed", effect="clarify")],
        leaf=LEAF_55,
        code_base=code_b,
    )
    assert before.state == "written", before.problems
    mqc = run_mqc(world, owner=LEAF_55, code_base=code_b)
    assert mqc["ok"] is True and mqc["findingCount"] == 0
    flagged = [
        f for f in mqc["reportOnlyFindings"] if f.get("kind") == "pre_base_stale_confirmation"
    ]
    assert (
        len(flagged) == 1
        and flagged[0]["recordId"] == "DPD-GHEXS9"
        and flagged[0]["document"] == HERMES_DOC
    )
    assert any(link.get("path") == HERMES_CODE for link in flagged[0]["preBaseMovedLinks"])

    git(world.memory, "reset", "--hard", "-q", mem_v1)
    git(world.memory, "clean", "-fd", "-q")
    spec = defer_spec(
        "Hermes native install command",
        "hermes.md documents legacy flow until L60.",
        {"task": LEAF_60},
    )
    r_defer = write_dep(
        world,
        [defer_op("DPD-GHEXS9", spec)],
        rows=[row_op("DPD-GHEXS9", "deferred")],
        leaf=LEAF_50,
        code_base=code_b,
    )
    assert r_defer.state == "written", r_defer.problems
    commit_memory(world.memory, "add deferral on DPD-GHEXS9", code_commit=code_b)
    deferral_id = json.loads(
        next(world.memory.glob("knowledge/dependents/DPD-GHEXS9-*.json")).read_text()
    )["deferrals"][0]["id"]

    after = write_dep(
        world,
        confirm_handoff,
        rows=[row_op("DPD-GHEXS9", "changed", effect="clarify")],
        leaf=LEAF_55,
        code_base=code_b,
        commit=False,
    )
    assert after.state == "refused"
    msg = " ".join(p.message for p in after.problems)
    assert deferral_id in msg and "Hermes native install command" in msg and TASK_BUG in msg


def test_reader_derives_task_due_in_own_leaf_and_keeps_creating_leaf_file_open(
    tmp_path: Path,
) -> None:
    """Reader infers the open leaf: task deferral reads due in its leaf; file deferral stays open in creator."""

    d_task_l60 = deferral_entry(
        "DFR-K00001", {"task": LEAF_60}, passage="Task due in L60", created_by=LEAF_50
    )
    d_task_other = deferral_entry(
        "DFR-K00002", {"task": LEAF_56}, passage="Open in L60", created_by=LEAF_50
    )
    d_orphan = deferral_entry(
        "DFR-K00003", {"task": LEAF_CAP_5}, passage="Orphaned", owner=TASK_CAP, created_by=LEAF_50
    )
    d_file = deferral_entry(
        "DFR-K00004",
        {"file": CODE_FILE},
        passage="File watched",
        created_by=LEAF_50,
        file_blob="1" * 40,
    )
    world, code_base, _mem_base, _ = seed_deferred_world(
        tmp_path, "DPD-K00001", [d_task_l60, d_task_other, d_orphan, d_file]
    )
    seed_tasks(world.root, cap_l5_status="Completed")

    def _write_open_history(leaf: str) -> Path:
        target = world.memory / history_path(leaf)
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "schema": "ar-history/v1",
            "task": TASK_BUG,
            "leaf": leaf,
            "closed": False,
            "rows": [],
        }
        target.write_text(canonical_text(doc), encoding="utf-8")
        return target

    # Inside L60 (open history with empty rows): L60 task deferral is due, file deferral is due.
    hist_l60 = _write_open_history(LEAF_60)
    commit_memory(world.memory, "open L60 history", code_commit=code_base)
    list_row = next(r for r in read_dependents(world)["rows"] if r["id"] == "DPD-K00001")
    assert {d["id"]: d["state"] for d in list_row["deferrals"]} == {
        "DFR-K00001": "due",
        "DFR-K00002": "open",
        "DFR-K00003": "orphaned",
        "DFR-K00004": "due",
    }
    rec_view = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="record",
            record_id="DPD-K00001",
            repository_root=str(world.code),
        ),
        coordination_root=str(world.root),
    )
    assert {d["id"]: d["state"] for d in rec_view["dependent"]["deferrals"]} == {
        "DFR-K00001": "due",
        "DFR-K00002": "open",
        "DFR-K00003": "orphaned",
        "DFR-K00004": "due",
    }

    # Inside creating leaf L50: changed watched file stays 'open' (never due in creator).
    hist_l60.unlink()
    _write_open_history(LEAF_50)
    commit_memory(world.memory, "open L50 creator history", code_commit=code_base)
    creator_row = next(r for r in read_dependents(world)["rows"] if r["id"] == "DPD-K00001")
    assert {d["id"]: d["state"] for d in creator_row["deferrals"]} == {
        "DFR-K00001": "open",
        "DFR-K00002": "open",
        "DFR-K00003": "orphaned",
        "DFR-K00004": "open",
    }
    creator_rec = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="record",
            record_id="DPD-K00001",
            repository_root=str(world.code),
        ),
        coordination_root=str(world.root),
    )
    assert {d["id"]: d["state"] for d in creator_rec["dependent"]["deferrals"]} == {
        "DFR-K00001": "open",
        "DFR-K00002": "open",
        "DFR-K00003": "orphaned",
        "DFR-K00004": "open",
    }
